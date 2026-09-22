# -*- coding: utf-8 -*-
"""
Память ИИ: после каждой сделки записывается «урок»; перед новым сигналом
релевантные уроки подгружаются в промпт.
"""
import json
from datetime import datetime, timedelta

from extensions import db
from models import MemoryStore, Signal

PATTERNS_RU = {
    "rsi_oversold": "RSI перепродан (<30)",
    "rsi_overbought": "RSI перекуплен (>70)",
    "macd_cross_up": "бычье пересечение MACD",
    "macd_cross_down": "медвежье пересечение MACD",
    "bb_lower": "цена ниже нижней полосы Боллинджера",
    "bb_upper": "цена выше верхней полосы Боллинджера",
    "trend_up": "восходящий тренд (SMA20 > SMA50)",
    "trend_down": "нисходящий тренд (SMA20 < SMA50)",
    "sideways": "флэт / нет явного тренда",
    "unknown": "нет данных индикаторов",
}


def classify_pattern(ind):
    """Главный паттерн по индикаторам на момент сигнала."""
    if not ind:
        return "unknown"
    rsi = ind.get("rsi", 50) or 50
    if rsi <= 30:
        return "rsi_oversold"
    if rsi >= 70:
        return "rsi_overbought"
    cross = ind.get("macd_cross")
    if cross == "up":
        return "macd_cross_up"
    if cross == "down":
        return "macd_cross_down"
    if (ind.get("bollinger") or {}).get("position") == "below_lower":
        return "bb_lower"
    if (ind.get("bollinger") or {}).get("position") == "above_upper":
        return "bb_upper"
    trend = ind.get("trend")
    if trend == "up":
        return "trend_up"
    if trend == "down":
        return "trend_down"
    return "sideways"


def _lesson_text(asset, ptype, direction, sc, fc, last_won):
    outcome = "сделка шла по сигналу" if last_won else "сигнал ошибся"
    return ("%s: «%s» + %s — %s (выигрыши %d / проигрыши %d)." % (
        asset, PATTERNS_RU.get(ptype, ptype), direction, outcome, sc, fc))


class MemoryService:

    # ---------- уроки для текущего сигнала ----------
    @staticmethod
    def get_relevant(asset, limit=5):
        """Уроки, релевантные активу и ситуации (по успешности и надёжности)."""
        min_total = db.and_(MemoryStore.success_count + MemoryStore.fail_count >= 2)
        same = (MemoryStore.query
                .filter(MemoryStore.asset == asset, min_total)
                .order_by(MemoryStore.success_count.desc(), MemoryStore.fail_count.asc())
                .all())
        others = (MemoryStore.query
                  .filter(MemoryStore.asset != asset, min_total)
                  .order_by(MemoryStore.success_count.desc())
                  .limit(10)
                  .all())
        scored = []
        for m in list(same) + list(others):
            score = (m.success_rate / 100.0) * 0.7 + m.reliability * 0.3
            if m.asset == asset:
                score += 0.15
            scored.append((score, m))
        scored.sort(key=lambda x: -x[0])
        out = []
        for score, m in scored[:limit]:
            if score < 0.5 and m.asset != asset:
                continue
            out.append({
                "id": m.id,
                "asset": m.asset,
                "pattern_type": m.pattern_type,
                "direction": m.direction,
                "lesson": m.lesson,
                "success_rate": m.success_rate,
                "reliability": m.reliability,
            })
        return out

    # ---------- самообучение: запись урока после сделки ----------
    @staticmethod
    def learn_from_signal(signal_id):
        sig = db.session.get(Signal, signal_id)
        if not sig or sig.result not in ("won", "lost"):
            return None
        won = sig.result == "won"
        ind = (sig.market_data or {}).get("indicators") or {}
        ptype = classify_pattern(ind)
        conditions = json.dumps({
            "rsi": ind.get("rsi"),
            "trend": ind.get("trend"),
            "macd_cross": ind.get("macd_cross"),
            "bb_position": (ind.get("bollinger") or {}).get("position"),
            "volatility": ind.get("volatility"),
            "confidence": sig.confidence,
        }, ensure_ascii=False)

        m = (MemoryStore.query
             .filter(MemoryStore.asset == sig.asset,
                     MemoryStore.pattern_type == ptype,
                     MemoryStore.direction == sig.direction)
             .first())
        if m is None:
            m = MemoryStore(asset=sig.asset, pattern_type=ptype, direction=sig.direction,
                            success_count=0, fail_count=0)
            db.session.add(m)
        if m.success_count is None:
            m.success_count = 0
        if m.fail_count is None:
            m.fail_count = 0
        if won:
            m.success_count += 1
        else:
            m.fail_count += 1
        m.conditions = conditions
        m.lesson = _lesson_text(sig.asset, ptype, sig.direction,
                                m.success_count, m.fail_count, won)
        m.updated_at = datetime.utcnow()
        db.session.commit()
        return m

    # ---------- статистика для админки ----------
    @staticmethod
    def stats():
        total = MemoryStore.query.count()
        top = (MemoryStore.query
               .order_by(db.desc(MemoryStore.success_count + MemoryStore.fail_count),
                         db.desc(MemoryStore.success_count))
               .limit(10)
               .all())
        return {"total": total, "top": top}

    # ---------- авто-очистка «старой» памяти (можно вызывать по cron) ----------
    @staticmethod
    def prune_old(days=90, min_total=2):
        cutoff = datetime.utcnow() - timedelta(days=days)
        old = (MemoryStore.query
               .filter(MemoryStore.updated_at < cutoff,
                       db.and_(MemoryStore.success_count + MemoryStore.fail_count < min_total))
               .all())
        for m in old:
            db.session.delete(m)
        if old:
            db.session.commit()
        return len(old)
