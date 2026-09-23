# -*- coding: utf-8 -*-
"""
ИИ-сервис: ЕДИНСТВЕННЫЙ провайдер — dahl.global (OpenAI-совместимый API).

    POST https://inference.dahl.global/v1/chat/completions
    Headers:  Authorization: Bearer <ключ пользователя>
              Content-Type: application/json
    Body:     {"model": "zai-org/GLM-5.3-Flash", "messages": [...]}

Ключ: из профиля пользователя (введён при входе / в настройках),
при его отсутствии — глобальный DAHL_API_KEY из конфига.

Модели: цепочка фолбэка (config.DAHL_MODELS). Если основная модель
перегружена (429/5xx) — автоматически пробуются следующие в списке.
Ошибки ключа/баланса (401/402) фолбэк не вызывают.
"""
import json
import re
import time

import requests

import config


class AIServiceError(Exception):
    """Человекочитаемая ошибка — показывается пользователю как есть."""


class _ModelUnavailable(Exception):
    """Внутренняя: модель временно недоступна — пробуем следующую в цепочке."""


class AIService:
    provider = "dahl.global"

    def __init__(self):
        self.url = config.DAHL_API_URL
        self.model = config.DAHL_MODEL
        self.timeout = config.DAHL_TIMEOUT

    def _models(self):
        """Цепочка моделей: первичная (DAHL_MODEL) + остальная из DAHL_MODELS."""
        models = []
        for m in getattr(config, "DAHL_MODELS", None) or []:
            m = (m or "").strip()
            if m and m not in models:
                models.append(m)
        primary = (getattr(config, "DAHL_MODEL", None) or "").strip()
        if primary and primary not in models:
            models.insert(0, primary)
        return models or [self.model]

    # ---------------- низкоуровневый запрос ----------------
    def _chat(self, system_prompt, user_prompt, api_key, model, timeout=None, retries=None):
        if retries is None:
            retries = getattr(config, "DAHL_RETRIES", 0)
        wait = getattr(config, "DAHL_RETRY_WAIT", 10)
        headers = {
            "Authorization": "Bearer %s" % api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.3,
            "max_tokens": 1200,
        }
        r = None
        for attempt in range(retries + 1):
            try:
                r = requests.post(self.url, headers=headers, json=payload,
                                  timeout=timeout or self.timeout)
            except requests.exceptions.Timeout:
                raise _ModelUnavailable(
                    "не ответил вовремя (до %d сек)" % (timeout or self.timeout))
            except requests.exceptions.RequestException as exc:
                raise _ModelUnavailable("сетевая ошибка: %s" % exc.__class__.__name__)
            if r.status_code == 429 and attempt < retries:
                time.sleep(wait)
                continue
            break

        if r.status_code == 401:
            raise AIServiceError("API-ключ ИИ не принят (401). Проверьте ключ в ⚙️ Настройках.")
        if r.status_code == 402:
            raise AIServiceError("У ключа ИИ нет баланса (402). Пополните счёт dahl.global.")
        if r.status_code == 429:
            raise _ModelUnavailable("перегружен (429)")
        if r.status_code >= 500:
            raise _ModelUnavailable("ошибка сервера (HTTP %d)" % r.status_code)
        if r.status_code != 200:
            raise AIServiceError("Неожиданный ответ ИИ (HTTP %d). Попробуйте ещё раз." % r.status_code)

        try:
            content = r.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise _ModelUnavailable("не удалось прочитать ответ")
        if not content or not str(content).strip():
            raise _ModelUnavailable("пустой ответ")
        return str(content)

    # ---------------- JSON из ответа ----------------
    @staticmethod
    def extract_json(text):
        """Вытаскивает JSON-объект из ответа (терпит markdown и код-заборы)."""
        text = str(text).strip()
        fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
        if fence:
            text = fence.group(1).strip()
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None

    # ---------------- промпты ----------------
    SYSTEM_PROMPT = (
        "Ты — эксперт по бинарным опционам на платформе Pocket Option. "
        "Тебе присылают снимок рынка с техническими индикаторами, ты даёшь сигнал CALL/PUT. "
        "Будь честным: уверенность должна отражать реальную силу сигнала, не обещай 90%+. "
        "Если картина неясная — дай низкую уверенность. "
        "Ответь ОДНИМ валидным JSON-объектом без markdown и без комментариев, строго в формате: "
        '{"direction": "CALL" или "PUT", "confidence": целое 0-100, "timeframe": "1m" или "5m", '
        '"analysis": "2-4 предложения на русском: что ты видишь на графике и почему", '
        '"factors": ["3-5 коротких ключевых факторов на русском"]}.'
    )

    @staticmethod
    def build_user_prompt(market, lessons):
        ind = market.get("indicators") or {}
        bb = ind.get("bollinger") or {}
        lines = [
            "Актив: %s" % market.get("asset"),
            "Источник данных: %s" % market.get("source"),
            "Текущая цена: %s" % market.get("price"),
            "",
            "Индикаторы (по 1-минутным свечам):",
            "RSI(14) = %s" % ind.get("rsi"),
            "MACD = %s, signal = %s, histogram = %s, пересечение: %s" % (
                ind.get("macd"), ind.get("macd_signal"), ind.get("macd_hist"),
                {"up": "бычье", "down": "медвежье", "none": "нет"}.get(ind.get("macd_cross"), "нет")),
            "SMA20 = %s, SMA50 = %s" % (ind.get("sma20"), ind.get("sma50")),
            "Bollinger(20,2): верх = %s, низ = %s, цена: %s" % (
                bb.get("upper"), bb.get("lower"),
                {"above_upper": "выше верхней полосы",
                 "below_lower": "ниже нижней полосы",
                 "inside": "внутри полос"}.get(bb.get("position"), "внутри полос")),
            "Тренд: %s" % {"up": "восходящий", "down": "нисходящий", "sideways": "боковой"}.get(ind.get("trend"), "боковой"),
            "Волатильность: %s%%, ATR: %s" % (ind.get("volatility"), ind.get("atr")),
        ]
        if lessons:
            lines.append("")
            lines.append("Уроки из прошлых сделок (память ИИ) — учти, но не следи слепо:")
            for i, l in enumerate(lessons, 1):
                lines.append("%d. [%s | %s | успешность %.0f%%] %s" % (
                    i, l["asset"], l["direction"], l["success_rate"], l["lesson"]))
        lines.append("")
        lines.append("Дай сигнал в JSON.")
        return "\n".join(lines)

    # ---------------- главный метод ----------------
    def generate_signal(self, market, lessons, user_api_key):
        key = (user_api_key or "").strip() or config.DAHL_API_KEY
        if not key:
            raise AIServiceError(
                "Не задан API-ключ ИИ. Введите его при входе на сайт или в ⚙️ Настройках.")
        system = self.SYSTEM_PROMPT
        user_prompt = self.build_user_prompt(market, lessons)

        unavailable = []
        for model in self._models():
            try:
                content = self._chat(system, user_prompt, key, model)
            except AIServiceError:
                raise  # проблема с ключом/балансом — фолбэк не поможет
            except _ModelUnavailable as exc:
                unavailable.append("%s (%s)" % (model, exc))
                continue
            parsed = self.extract_json(content)
            if not parsed:
                unavailable.append("%s (ответ не в JSON)" % model)
                continue
            sig = self._normalize(parsed)
            if sig is None:
                unavailable.append("%s (неверный формат ответа)" % model)
                continue
            return {"signal": sig, "provider": self.provider, "model": model,
                    "raw": content[:3000]}
        raise AIServiceError(
            "Все модели ИИ временно недоступны: %s. Подождите минуту и повторите."
            % "; ".join(unavailable)[:220])

    @staticmethod
    def _normalize(parsed):
        direction = str(parsed.get("direction", "")).upper().strip()
        if direction not in ("CALL", "PUT"):
            return None
        try:
            confidence = int(round(float(parsed.get("confidence", 50))))
        except (TypeError, ValueError):
            confidence = 50
        confidence = max(5, min(95, confidence))
        timeframe = str(parsed.get("timeframe", "1m")).lower().strip()
        if timeframe not in config.TIMEFRAMES:
            timeframe = "1m"
        analysis = str(parsed.get("analysis", "")).strip()[:2000]
        raw_factors = parsed.get("factors")
        factors = ([str(f).strip()[:200] for f in raw_factors if str(f).strip()][:6]
                   if isinstance(raw_factors, list) else [])
        return {
            "direction": direction,
            "confidence": confidence,
            "timeframe": timeframe,
            "analysis": analysis or "ИИ не дал комментария.",
            "factors": factors,
        }

    # ---------------- проверка ключа (кнопка «🧪 Тест» в настройках) ----------------
    def test_key(self, api_key):
        key = (api_key or "").strip()
        if not key:
            return False, "Нет ключа для проверки."
        model = self._models()[0]
        try:
            content = self._chat("Ответь одним словом: OK", "Тест подключения.",
                                 key, model, timeout=60, retries=0)
            return True, "Ключ работает! Модель %s ответила: %r" % (model, content[:50])
        except AIServiceError as exc:
            return False, str(exc)
        except _ModelUnavailable as exc:
            return False, "Модель %s временно недоступна: %s. Попробуйте позже." % (model, exc)
