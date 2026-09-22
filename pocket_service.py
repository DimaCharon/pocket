# -*- coding: utf-8 -*-
"""
Рыночные данные Pocket Signals.

- LIVE: реальные свечи по WebSocket Pocket Option (SSID пользователя,
  введённый при входе / в настройках и сохранённый в профиле).
- SIMULATED: случайное блуждание, если SSID не задан или Pocket Option
  не отвечает (например, IP сервера заблокирован PO) — с честной пометкой.

Индикаторы (RSI, MACD, SMA, Bollinger, ATR, волатильность, тренд) считаются
из свечей через pandas — одинаково для живых данных и симуляции.
"""
import asyncio
import hashlib
import inspect
import json
import os
import random
import time

import numpy as np
import pandas as pd

from . import config

try:
    import websockets
except ImportError:  # без библиотеки живой режим не работает, симуляция — да
    websockets = None


def _outbound_proxy():
    """Прокси для исходящих запросов (RelaxDev проставляет HTTP(S)_PROXY
    после подключения RELAXDEV_PROXY_URL; requests подхватывает сам,
    а websockets — нет, поэтому подаём явно)."""
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
                "RELAXDEV_PROXY_URL"):
        value = (os.environ.get(var) or "").strip()
        if value:
            return value
    return None


# ================= индикаторы =================

def compute_indicators(candles):
    """candles — список [ts, open, high, low, close, volume]. Возвращает dict индикаторов."""
    if not candles or len(candles) < 30:
        return {}
    df = pd.DataFrame(candles, columns=["ts", "open", "high", "low", "close", "volume"]).tail(300)
    close = df["close"].astype(float)
    price = float(close.iloc[-1])

    # --- RSI(14), метод Уайлдера ---
    delta = close.diff()
    gain = delta.clip(lower=0.0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0.0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0.0, np.nan)
    rsi = 100.0 - 100.0 / (1.0 + rs)
    rsi[(gain == 0) & (loss == 0)] = 50.0
    rsi_val = float(rsi.iloc[-1]) if pd.notna(rsi.iloc[-1]) else 50.0

    # --- MACD(12, 26, 9) ---
    macd_line = close.ewm(span=12, adjust=False).mean() - close.ewm(span=26, adjust=False).mean()
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    hist = macd_line - signal_line
    macd_cross = "none"
    if len(hist) >= 2:
        prev_h, cur_h = float(hist.iloc[-2]), float(hist.iloc[-1])
        if prev_h <= 0 < cur_h:
            macd_cross = "up"
        elif prev_h >= 0 > cur_h:
            macd_cross = "down"

    # --- SMA / Bollinger(20,2) ---
    sma20 = float(close.rolling(20).mean().iloc[-1])
    sma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else sma20
    std20 = float(close.rolling(20).std().iloc[-1] or 0.0)
    bb_upper, bb_lower = sma20 + 2 * std20, sma20 - 2 * std20
    if price > bb_upper:
        bb_pos = "above_upper"
    elif price < bb_lower:
        bb_pos = "below_lower"
    else:
        bb_pos = "inside"

    # --- тренд / волатильность / ATR ---
    if price > sma20 and sma20 > sma50:
        trend = "up"
    elif price < sma20 and sma20 < sma50:
        trend = "down"
    else:
        trend = "sideways"
    returns = close.pct_change().dropna()
    volatility = float(returns.tail(14).std() * 100.0) if len(returns) >= 14 else 0.0
    high, low = df["high"].astype(float), df["low"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()],
                   axis=1).max(axis=1)
    atr = float(tr.ewm(alpha=1 / 14, adjust=False).mean().iloc[-1])

    return {
        "rsi": round(rsi_val, 1),
        "macd": round(float(macd_line.iloc[-1]), 6),
        "macd_signal": round(float(signal_line.iloc[-1]), 6),
        "macd_hist": round(float(hist.iloc[-1]), 6),
        "macd_cross": macd_cross,
        "sma20": round(sma20, 5),
        "sma50": round(sma50, 5),
        "bollinger": {"upper": round(bb_upper, 5), "mid": round(sma20, 5),
                      "lower": round(bb_lower, 5), "position": bb_pos},
        "trend": trend,
        "volatility": round(volatility, 3),
        "atr": round(atr, 5),
        "candles": int(len(df)),
    }


class PocketService:

    SOURCE_SIM = "SIMULATED"
    SOURCE_LIVE = "POCKET OPTION (LIVE)"

    # ---------- публичное API ----------

    def get_market(self, asset, ssid=None):
        """Снимок рынка: цена, свечи, индикаторы, источник."""
        ssid = (ssid or "").strip()
        if ssid and websockets is not None:
            try:
                return self._fetch_live(asset, ssid, need_candles=True)
            except Exception as exc:
                sim = self._simulate(asset)
                sim["source"] = "SIMULATED (Pocket Option не ответил: %s)" % str(exc)[:160]
                return sim
        return self._simulate(asset)

    def get_price(self, asset, ssid=None):
        """Текущая цена (для проверки результата сделки). None, если недоступна."""
        ssid = (ssid or "").strip()
        if not ssid or websockets is None:
            return None
        try:
            data = self._fetch_live(asset, ssid, need_candles=False)
            return data.get("price") or None
        except Exception:
            return None

    def check_connection(self, ssid):
        """Проверка SSID — кнопка «🧪 Тест подключения» в настройках."""
        ssid = (ssid or "").strip()
        if not ssid:
            return False, "Нет SSID для проверки. Вставьте его в поле выше."
        if websockets is None:
            return False, "Не установлена библиотека websockets (pip install websockets)."
        try:
            data = self._fetch_live("EURUSD-OTC", ssid, need_candles=False)
            return True, "Подключение к Pocket Option успешно! Цена EURUSD-OTC: %s" % data.get("price")
        except Exception as exc:
            return False, "Не удалось подключиться: %s" % str(exc)[:220]

    # ---------- живой режим: WebSocket Pocket Option ----------

    def _fetch_live(self, asset, ssid, need_candles=True):
        if websockets is None:
            raise RuntimeError("websockets не установлен")
        return asyncio.run(self._fetch_live_async(asset, ssid, need_candles))

    async def _fetch_live_async(self, asset, ssid, need_candles):
        errors = []
        for url in config.POCKET_WS_URLS:
            try:
                return await self._try_ws(url, asset, ssid, need_candles)
            except Exception as exc:
                errors.append("%s: %s" % (url.split(".")[0], str(exc)[:90]))
        raise RuntimeError("; ".join(errors) or "не удалось подключиться к Pocket Option")

    async def _try_ws(self, url, asset, ssid, need_candles):
        timeout = config.POCKET_TIMEOUT
        seq = 42  # порядковый номер: 42 = auth (он уже внутри SSID)
        conn_kwargs = {"open_timeout": 6, "close_timeout": 5, "ping_interval": 15}
        proxy = _outbound_proxy()
        if proxy and "proxy" in inspect.signature(websockets.connect).parameters:
            conn_kwargs["proxy"] = proxy
        async with websockets.connect(url, **conn_kwargs) as ws:
            # 1) приветствие сервера (17["auth",...])
            await asyncio.wait_for(ws.recv(), timeout=timeout)
            # 2) авторизация SSID пользователя (строка, скопированная из DevTools)
            await asyncio.wait_for(ws.send(ssid), timeout=timeout)
            if not await self._wait_auth(ws, timeout):
                raise RuntimeError("Pocket Option отклонил сессию: SSID недействителен или просрочен")
            await ws.send('42["ping",{}]')

            price = None
            candles = None
            if need_candles:
                seq += 1
                start_ms = int((time.time() - config.POCKET_CANDLES * 60) * 1000)
                await ws.send(json.dumps(
                    [seq, ["get-candles",
                           {"pair": asset, "timeframe": 60, "start": start_ms}]]))
                candles = await self._wait_data(ws, "candles", timeout)
                if not candles:
                    raise RuntimeError("нет свечей по %s (актив отсутствует в списке?)" % asset)
                price = float(candles[-1][4])
            seq += 1
            await ws.send(json.dumps(
                [seq, ["get-price", {"request_id": seq, "pair": asset}]]))
            p = await self._wait_price(ws, timeout)
            if p:
                price = p

            return {
                "asset": asset,
                "source": self.SOURCE_LIVE,
                "price": round(price, 6) if price else 0.0,
                "candles": [round(float(c[4]), 6) for c in candles[-30:]] if candles else [],
                "indicators": compute_indicators(candles) if (need_candles and candles) else {},
            }

    @staticmethod
    async def _wait_auth(ws, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(1.0, deadline - time.time()))
            frame = PocketService._parse_frame(raw)
            if frame and frame[1] == "auth" and isinstance(frame[2], dict):
                return bool(frame[2].get("success"))
        return False

    @staticmethod
    async def _wait_data(ws, wanted, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(1.0, deadline - time.time()))
            frame = PocketService._parse_frame(raw)
            if frame and frame[1] == wanted and isinstance(frame[2], list):
                return frame[2]
        return None

    @staticmethod
    async def _wait_price(ws, timeout):
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(1.0, deadline - time.time()))
            frame = PocketService._parse_frame(raw)
            if frame and frame[1] == "price" and isinstance(frame[2], dict):
                try:
                    return float(frame[2]["price"])
                except (KeyError, TypeError, ValueError):
                    return None
        return None

    @staticmethod
    def _parse_frame(raw):
        try:
            frame = json.loads(raw)
            if isinstance(frame, list) and len(frame) >= 3:
                return frame
        except (json.JSONDecodeError, TypeError):
            pass
        return None

    # ---------- симуляция ----------

    @staticmethod
    def _base_price(asset):
        h = int(hashlib.md5(asset.encode()).hexdigest()[:8], 16)
        if asset.endswith("BTC-OTC"):
            return 50000 + (h % 30000)
        if asset.endswith("ETH-OTC"):
            return 2500 + (h % 2000)
        if not asset.endswith("-OTC") and len(asset) <= 6:  # акции
            return 10 + h % 400
        return 1.0 + (h % 100) / 100.0  # ~1.00 – 1.99

    def _simulate(self, asset):
        # seed зависит от актива и 5-минутного окна → «рынок» стабилен внутри окна
        seed = int(hashlib.md5(("%s:%d" % (asset, int(time.time() // 300))).encode()).hexdigest()[:10], 16)
        rng = random.Random(seed)
        price = self._base_price(asset)
        vol = price * 0.0008
        drift = rng.uniform(-1.0, 1.0) * vol * 0.3
        now_ms = int(time.time() * 1000)
        t0 = now_ms - config.POCKET_CANDLES * 60 * 1000
        candles = []
        for i in range(config.POCKET_CANDLES):
            o = price
            c = max(price * 0.9, o + drift + rng.gauss(0, vol))
            h = max(o, c) + abs(rng.gauss(0, vol * 0.5))
            l = min(o, c) - abs(rng.gauss(0, vol * 0.5))
            candles.append([t0 + i * 60000, round(o, 6), round(h, 6), round(l, 6),
                            round(c, 6), rng.randint(10, 500)])
            price = c
        return {
            "asset": asset,
            "source": self.SOURCE_SIM,
            "price": round(price, 6),
            "candles": [round(c[4], 6) for c in candles[-30:]],
            "indicators": compute_indicators(candles),
        }
