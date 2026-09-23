# -*- coding: utf-8 -*-
"""Конфигурация проекта Pocket Signals."""
import os


class Config:
    # ---------- база ----------
    SECRET_KEY = os.environ.get("SECRET_KEY", "pocket-signals-2026-secret")
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _DB_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///" + os.path.join(BASE_DIR, "pocket_signals.db")
    )
    SQLALCHEMY_DATABASE_URI = _DB_URI
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    if _DB_URI.startswith("postgresql"):
        # PostgreSQL (например, управляемая БД RelaxDev, DATABASE_URL выдаётся сам)
        SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    else:
        # SQLite: допускаем доступ из фоновых тредов (проверка результата сделки)
        SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"check_same_thread": False, "timeout": 30}}
    MAX_CONTENT_LENGTH = 1024 * 1024

    # ---------- ИИ: dahl.global (единственный провайдер) ----------
    # OpenAI-совместимый эндпоинт: POST /v1/chat/completions
    DAHL_API_URL = os.environ.get(
        "DAHL_API_URL", "https://inference.dahl.global/v1/chat/completions"
    )
    DAHL_MODEL = os.environ.get("DAHL_MODEL", "zai-org/GLM-5.3-Flash")
    # Глобальный ключ (необязательно). Приоритет: ключ из профиля пользователя
    # (введён при входе / в настройках) > глобальный ключ.
    DAHL_API_KEY = os.environ.get("DAHL_API_KEY", "")
    DAHL_TIMEOUT = 120  # бесплатные модели отвечают медленно (до 2 минут)

    # ---------- Pocket Option: живые данные по WebSocket ----------
    # SSID вводит пользователь сам (при входе / в настройках), храним в БД.
    POCKET_WS_URLS = [
        "wss://ws.po038.pocketoption.online/ws",
        "wss://ws.po034.pocketoption.online/ws",
        "wss://ws.po033.pocketoption.online/ws",
    ]
    POCKET_TIMEOUT = 20      # таймаут одной попытки соединения, сек
    POCKET_CANDLES = 200     # сколько 1-минутных свечей тянем для индикаторов

    # ---------- тарифы ----------
    SUBSCRIPTION_PLANS = {
        "free":  {"name": "Free",  "price": 0,    "daily_limit": 3,  "desc": "Для знакомства с сервисом", "popular": False},
        "basic": {"name": "Basic", "price": 990,  "daily_limit": 10, "desc": "Для регулярной торговли", "popular": False},
        "pro":   {"name": "Pro",   "price": 2990, "daily_limit": 50, "desc": "Для активных трейдеров", "popular": True},
        "vip":   {"name": "VIP",   "price": 9990, "daily_limit": -1, "desc": "Безлимитные сигналы", "popular": False},
    }

    # ---------- админ (создаётся автоматически при первом старте) ----------
    ADMIN_USERNAME = "admin"
    ADMIN_EMAIL = "admin@pocketsignals.local"
    ADMIN_PASSWORD = "admin123"

    # ---------- активы (Pocket Option) ----------
    ASSETS = {
        "Валютные OTC": [
            "EURUSD-OTC", "GBPUSD-OTC", "USDJPY-OTC", "USDCAD-OTC",
            "AUDUSD-OTC", "EURGBP-OTC", "EURJPY-OTC", "NZDUSD-OTC",
            "USDCHF-OTC", "AUDCAD-OTC", "EURCHF-OTC", "GBPJPY-OTC",
        ],
        "Криптовалюта": ["EURBTC-OTC", "EURETH-OTC"],
        "Акции": ["AAPL", "TSLA", "GOOGL", "AMZN", "NVDA", "META"],
    }

    TIMEFRAMES = ["1m", "5m"]


# Модульные алиасы: внутренние модули пишут `import config`
# и обращаются как config.DAHL_API_URL, config.ASSETS и т.д.
for _name in [n for n in dir(Config) if n.isupper()]:
    globals()[_name] = getattr(Config, _name)
del _name
