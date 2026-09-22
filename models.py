# -*- coding: utf-8 -*-
"""Модели БД: User, Signal, Notification, MemoryStore."""
import json
from datetime import datetime, date

from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

from . import config
from . import db


class User(UserMixin, db.Model):
    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    is_approved = db.Column(db.Boolean, default=False, nullable=False)
    is_admin = db.Column(db.Boolean, default=False, nullable=False)
    subscription_plan = db.Column(db.String(20), default="free", nullable=False)
    signals_used_today = db.Column(db.Integer, default=0, nullable=False)
    signals_date = db.Column(db.Date, default=None)
    last_login = db.Column(db.DateTime, default=None)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Ключи, введённые при входе / в настройках (персистентно хранятся в профиле)
    ai_api_key = db.Column(db.String(255), default="", nullable=False)   # dahl.global
    pocket_ssid = db.Column(db.Text, default="", nullable=False)         # 42["auth",{"session":...}]

    signals = db.relationship("Signal", backref="user", lazy="dynamic")
    notifications = db.relationship("Notification", backref="user", lazy="dynamic")

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def plan(self):
        return config.SUBSCRIPTION_PLANS.get(self.subscription_plan) or config.SUBSCRIPTION_PLANS["free"]

    def reset_daily_limit_if_new_day(self):
        today = date.today()
        if self.signals_date != today:
            self.signals_date = today
            self.signals_used_today = 0

    def remaining_signals(self):
        """Сколько сигналов осталось сегодня; None = безлимит."""
        self.reset_daily_limit_if_new_day()
        limit = self.plan()["daily_limit"]
        if limit < 0:
            return None
        return max(0, limit - self.signals_used_today)

    def consume_signal(self):
        self.reset_daily_limit_if_new_day()
        self.signals_used_today += 1
        self.signals_date = date.today()


class Signal(db.Model):
    __tablename__ = "signal"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    asset = db.Column(db.String(40), nullable=False)
    direction = db.Column(db.String(10), nullable=False)           # CALL / PUT
    confidence = db.Column(db.Integer, default=50, nullable=False)
    timeframe = db.Column(db.String(10), default="1m", nullable=False)
    entry_price = db.Column(db.Float, default=0.0, nullable=False)
    analysis = db.Column(db.Text, default="")
    factors = db.Column(db.Text, default="")                       # JSON-список факторов
    ai_reasoning = db.Column(db.Text, default="")                  # сырой ответ модели
    market_snapshot = db.Column(db.Text, default="")               # JSON-снимок рынка
    result = db.Column(db.String(10), default="pending", nullable=False)  # pending / won / lost
    result_note = db.Column(db.String(120), default="")
    provider = db.Column(db.String(80), default="")
    memory_used = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    resolved_at = db.Column(db.DateTime, default=None)

    @property
    def market_data(self):
        try:
            return json.loads(self.market_snapshot or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}

    @property
    def factors_list(self):
        try:
            data = json.loads(self.factors or "[]")
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, TypeError):
            return []


class Notification(db.Model):
    __tablename__ = "notification"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    type = db.Column(db.String(20), default="info")                # info / success / warning
    title = db.Column(db.String(120), default="")
    message = db.Column(db.Text, default="")
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class MemoryStore(db.Model):
    __tablename__ = "memory"

    id = db.Column(db.Integer, primary_key=True)
    pattern_type = db.Column(db.String(40), nullable=False, index=True)
    asset = db.Column(db.String(40), nullable=False, index=True)
    conditions = db.Column(db.Text, default="")                    # JSON: значения индикаторов
    lesson = db.Column(db.Text, default="")
    direction = db.Column(db.String(10), nullable=False)
    success_count = db.Column(db.Integer, default=0, nullable=False)
    fail_count = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @property
    def total(self):
        return self.success_count + self.fail_count

    @property
    def success_rate(self):
        return round(100.0 * self.success_count / self.total, 1) if self.total else 0.0

    @property
    def reliability(self):
        """Успешность с пенальти за маленький выборку (сходится к реальной оценке)."""
        if not self.total:
            return 0.0
        rate = self.success_count / self.total
        weight = min(1.0, self.total / 5.0)
        return round(rate * weight + (1.0 - weight) * 0.5, 3)

    @property
    def conditions_dict(self):
        try:
            return json.loads(self.conditions or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}
