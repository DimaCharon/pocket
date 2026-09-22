# -*- coding: utf-8 -*-
"""Pocket Signals: фабрика приложения, роуты, бизнес-логика."""
import json
import random
import re
import threading
import time
from datetime import datetime
from functools import wraps

from flask import (Flask, flash, redirect, render_template, request, url_for)
from flask_login import (LoginManager, current_user, login_required,
                         login_user, logout_user)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text

from . import config

db = SQLAlchemy()
login_manager = LoginManager()


def _migrate_db():
    """Добавляем новые колонки в уже существующую SQLite (безопасно, идемпотентно).
    Для PostgreSQL не нужен: схема создаётся целиком через create_all()."""
    if db.engine.dialect.name != "sqlite":
        return
    with db.engine.begin() as conn:
        ins = sa_inspect(conn)
        tables = {t: {c["name"] for c in ins.get_columns(t)} for t in ins.get_table_names()}
        wanted = {
            "user": {
                "ai_api_key": "VARCHAR(255) NOT NULL DEFAULT ''",
                "pocket_ssid": "TEXT NOT NULL DEFAULT ''",
            },
            "signal": {
                "factors": "TEXT NOT NULL DEFAULT ''",
                "result_note": "VARCHAR(120) NOT NULL DEFAULT ''",
                "provider": "VARCHAR(80) NOT NULL DEFAULT ''",
                "memory_used": "INTEGER NOT NULL DEFAULT 0",
                "resolved_at": "DATETIME",
            },
        }
        for table, cols in wanted.items():
            if table not in tables:
                continue
            for col, decl in cols.items():
                if col not in tables[table]:
                    conn.execute(text("ALTER TABLE %s ADD COLUMN %s %s" % (table, col, decl)))


def _ensure_admin():
    from .models import User
    if not User.query.filter_by(username=config.ADMIN_USERNAME).first():
        admin = User(
            username=config.ADMIN_USERNAME,
            email=config.ADMIN_EMAIL,
            is_approved=True,
            is_admin=True,
            subscription_plan="vip",
        )
        admin.set_password(config.ADMIN_PASSWORD)
        db.session.add(admin)
        db.session.commit()


def create_app():
    app = Flask(__name__)
    app.config.from_object(config.Config)

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "login"
    login_manager.unauthorized_message = "Войдите, чтобы продолжить."

    from . import models  # noqa: F401  (регистрирует модели)
    from .ai_service import AIService, AIServiceError
    from .memory_service import MemoryService
    from .models import Notification, Signal, User
    from .pocket_service import PocketService

    with app.app_context():
        db.create_all()
        _migrate_db()
        _ensure_admin()

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    # ---------------- вспомогательные ----------------

    def _notify(user, ntype, title, message):
        db.session.add(Notification(user_id=user.id, type=ntype,
                                    title=title, message=message))

    def _notify_admins(title, message):
        for a in User.query.filter_by(is_admin=True).all():
            db.session.add(Notification(user_id=a.id, type="info",
                                        title=title, message=message))
        db.session.commit()

    def _tf_minutes(tf):
        try:
            return max(1, int(str(tf).rstrip("mM ")))
        except ValueError:
            return 1

    def _fmt_price(p):
        return ("%.5f" % p) if p and p < 10 else ("%.2f" % p)

    def _resolve_signal(signal_id, asset, direction, entry_price, minutes, ssid):
        """Фоновый тред: ждёт истечения таймфрейма, проверяет результат
        (живые данные, если SSID жив; иначе эмуляция) и учит память."""
        try:
            time.sleep(max(60, minutes * 60) + 10)
            svc = PocketService()
            exit_price = svc.get_price(asset, ssid)
            if exit_price is not None and entry_price:
                won = (exit_price > entry_price) if direction == "CALL" \
                    else (exit_price < entry_price)
                note = "реальные данные: вход %s → выход %s" % (
                    _fmt_price(entry_price), _fmt_price(exit_price))
            else:
                won = random.random() < 0.55
                note = "эмуляция (нет реальных данных)"
            with app.app_context():
                sig = db.session.get(Signal, signal_id)
                if not sig or sig.result != "pending":
                    return
                sig.result = "won" if won else "lost"
                sig.result_note = note
                sig.resolved_at = datetime.utcnow()
                db.session.commit()
                MemoryService.learn_from_signal(signal_id)
        except Exception:
            pass  # не роняем процесс; сигнал останется pending

    def admin_required(f):
        @wraps(f)
        @login_required
        def wrapper(*args, **kwargs):
            if not current_user.is_admin:
                flash("Доступ запрещён.", "danger")
                return redirect(url_for("dashboard"))
            return f(*args, **kwargs)
        return wrapper

    # ---------------- служебные ----------------

    @app.route("/healthz")
    def healthz():
        return {"status": "ok", "time": datetime.utcnow().isoformat()}

    # ---------------- публичные страницы ----------------

    @app.route("/")
    def landing():
        return render_template("auth/index.html")

    # ---------------- auth ----------------

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            user = User.query.filter_by(username=username).first()
            if user and user.check_password(password):
                # сохраняем/обновляем ключи, если пользователь их ввёл
                new_key = (request.form.get("ai_api_key") or "").strip()
                new_ssid = (request.form.get("pocket_ssid") or "").strip()
                if new_key:
                    user.ai_api_key = new_key[:255]
                if new_ssid:
                    user.pocket_ssid = new_ssid[:4000]
                user.last_login = datetime.utcnow()
                db.session.commit()
                login_user(user)
                if not user.is_approved:
                    flash("Ваша аккаунт ожидает одобрения администратором.", "warning")
                    return redirect(url_for("pending"))
                next_page = request.args.get("next") or url_for("dashboard")
                if not next_page.startswith("/") or next_page.startswith("//"):
                    next_page = url_for("dashboard")
                flash("С возвращением, %s!" % user.username, "success")
                return redirect(next_page)
            flash("Неверный логин или пароль.", "danger")
        return render_template("auth/login.html")

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            email = (request.form.get("email") or "").strip()
            password = request.form.get("password") or ""
            password2 = request.form.get("password2") or ""
            errors = []
            if len(username) < 3:
                errors.append("Логин — минимум 3 символа.")
            if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
                errors.append("Введите корректный email.")
            if len(password) < 6:
                errors.append("Пароль — минимум 6 символов.")
            if password != password2:
                errors.append("Пароли не совпадают.")
            if User.query.filter_by(username=username).first():
                errors.append("Такой логин уже занят.")
            if User.query.filter_by(email=email).first():
                errors.append("Этот email уже используется.")
            if errors:
                for e in errors:
                    flash(e, "danger")
            else:
                u = User(username=username, email=email,
                         is_approved=False, subscription_plan="free")
                u.set_password(password)
                db.session.add(u)
                db.session.commit()
                _notify_admins("Новая заявка на доступ",
                               "Пользователь %s (%s) ждёт одобрения." % (username, email))
                flash("Заявка отправлена! Вход откроется после одобрения администратором.", "success")
                return redirect(url_for("login"))
        return render_template("auth/register.html")

    @app.route("/pending")
    @login_required
    def pending():
        return render_template("auth/pending.html")

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        flash("Вы вышли из аккаунта.", "info")
        return redirect(url_for("landing"))

    # ---------------- дашборд ----------------

    @app.route("/dashboard")
    @login_required
    def dashboard():
        if not current_user.is_approved:
            return redirect(url_for("pending"))
        highlight = request.args.get("highlight", type=int)
        last_signal = None
        if highlight:
            s = db.session.get(Signal, highlight)
            if s and s.user_id == current_user.id:
                last_signal = s
        signals = (Signal.query.filter_by(user_id=current_user.id)
                   .order_by(Signal.created_at.desc()).limit(50).all())
        notifs = (Notification.query.filter_by(user_id=current_user.id, is_read=False)
                  .order_by(Notification.created_at.desc()).limit(10).all())
        for n in notifs:
            n.is_read = True
        db.session.commit()
        return render_template("dashboard/index.html", signals=signals, notifs=notifs,
                               last_signal=last_signal, assets=config.ASSETS)

    @app.route("/get_signal", methods=["POST"])
    @login_required
    def get_signal():
        if not current_user.is_approved:
            return redirect(url_for("pending"))
        asset = (request.form.get("asset") or "").strip()
        valid_assets = {a for group in config.ASSETS.values() for a in group}
        if asset not in valid_assets:
            flash("Неизвестный актив.", "danger")
            return redirect(url_for("dashboard"))

        # 1) лимит тарифа
        remaining = current_user.remaining_signals()
        if remaining is not None and remaining <= 0:
            flash("Дневной лимит тарифа %s исчерпан. Счётчик сбрасывается в полночь. "
                  "Тариф можно сменить в разделе «Подписка»."
                  % current_user.plan()["name"], "warning")
            return redirect(url_for("dashboard"))

        # 2) рынок (живые данные, если у пользователя есть SSID)
        market = PocketService().get_market(asset, current_user.pocket_ssid)

        # 3) релевантная память
        lessons = MemoryService.get_relevant(asset, limit=5)

        # 4) ИИ (ключ из профиля пользователя)
        try:
            result = AIService().generate_signal(market, lessons, current_user.ai_api_key)
        except AIServiceError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("dashboard"))

        sig = result["signal"]
        signal = Signal(
            user_id=current_user.id,
            asset=asset,
            direction=sig["direction"],
            confidence=sig["confidence"],
            timeframe=sig["timeframe"],
            entry_price=market.get("price", 0.0),
            analysis=sig["analysis"],
            factors=json.dumps(sig["factors"], ensure_ascii=False),
            ai_reasoning=result["raw"],
            market_snapshot=json.dumps(market, ensure_ascii=False),
            provider="%s / %s" % (result["provider"], result["model"]),
            memory_used=len(lessons),
            result="pending",
        )
        db.session.add(signal)
        db.session.commit()
        current_user.consume_signal()
        db.session.commit()

        # 5) фоновая проверка результата + обучение памяти
        threading.Thread(
            target=_resolve_signal,
            args=(signal.id, asset, sig["direction"], market.get("price", 0.0),
                  _tf_minutes(sig["timeframe"]), current_user.pocket_ssid),
            daemon=True,
        ).start()

        flash("Сигнал готов: %s %s (уверенность %d%%)." % (
            asset, sig["direction"], sig["confidence"]), "success")
        return redirect(url_for("dashboard", highlight=signal.id))

    # ---------------- настройки (ключи) ----------------

    @app.route("/settings", methods=["GET", "POST"])
    @login_required
    def settings():
        if not current_user.is_approved:
            return redirect(url_for("pending"))
        if request.method == "POST":
            action = request.form.get("action", "save")
            if action == "test_ai":
                key = (request.form.get("ai_api_key") or "").strip()
                if not key:
                    flash("Нет ключа для проверки.", "warning")
                else:
                    ok, msg = AIService().test_key(key)
                    flash(msg, "success" if ok else "danger")
            elif action == "test_pocket":
                ssid = (request.form.get("pocket_ssid") or "").strip()
                ok, msg = PocketService().check_connection(ssid)
                flash(msg, "success" if ok else "danger")
            else:  # save
                if "ai_api_key" in request.form:
                    current_user.ai_api_key = (request.form.get("ai_api_key") or "").strip()[:255]
                if "pocket_ssid" in request.form:
                    current_user.pocket_ssid = (request.form.get("pocket_ssid") or "").strip()[:4000]
                db.session.commit()
                flash("Настройки сохранены. Ключи теперь хранятся в вашем профиле "
                      "и не пропадут при закрытии браузера.", "success")
            return redirect(url_for("settings"))
        return render_template("settings/index.html", dahl_model=config.DAHL_MODEL)

    # ---------------- подписка ----------------

    @app.route("/subscription")
    @login_required
    def subscription():
        if not current_user.is_approved:
            return redirect(url_for("pending"))
        return render_template("subscription/index.html",
                               plans=config.SUBSCRIPTION_PLANS,
                               current=current_user.subscription_plan)

    @app.route("/subscription/choose", methods=["POST"])
    @login_required
    def subscription_choose():
        if not current_user.is_approved:
            return redirect(url_for("pending"))
        plan = request.form.get("plan")
        if plan in config.SUBSCRIPTION_PLANS:
            current_user.subscription_plan = plan
            db.session.commit()
            _notify(current_user, "info", "Тариф изменён",
                    "Ваш тариф: %s (%s)." % (
                        config.SUBSCRIPTION_PLANS[plan]["name"],
                        config.SUBSCRIPTION_PLANS[plan]["desc"]))
            db.session.commit()
            flash("Тариф %s активирован (демо-режим: активация без оплаты)."
                  % config.SUBSCRIPTION_PLANS[plan]["name"], "success")
        return redirect(url_for("subscription"))

    # ---------------- админка ----------------

    @app.route("/admin")
    @admin_required
    def admin():
        users = User.query.order_by(User.created_at.desc()).all()
        pending_users = (User.query.filter_by(is_approved=False)
                         .order_by(User.created_at.desc()).all())
        signals_count = Signal.query.count()
        mem = MemoryService.stats()
        return render_template("admin/dashboard.html", users=users,
                               pending_users=pending_users,
                               signals_count=signals_count, mem=mem,
                               plans=config.SUBSCRIPTION_PLANS)

    @app.route("/admin/approve", methods=["POST"])
    @admin_required
    def admin_approve():
        uid = request.form.get("user_id", type=int)
        action = request.form.get("action")
        u = db.session.get(User, uid) if uid else None
        if u and not u.is_admin:
            if action == "approve":
                u.is_approved = True
                db.session.commit()
                _notify(u, "success", "Аккаунт одобрен",
                        "Доступ к Pocket Signals открыт. Добро пожаловать!")
                db.session.commit()
                flash("Пользователь %s одобрен." % u.username, "success")
            elif action == "reject":
                u.is_approved = False
                db.session.commit()
                flash("Заявка %s отклонена." % u.username, "warning")
        return redirect(url_for("admin"))

    @app.route("/admin/plan", methods=["POST"])
    @admin_required
    def admin_plan():
        uid = request.form.get("user_id", type=int)
        plan = request.form.get("plan")
        u = db.session.get(User, uid) if uid else None
        if u and plan in config.SUBSCRIPTION_PLANS:
            u.subscription_plan = plan
            _notify(u, "info", "Тариф изменён",
                    "Ваш тариф изменён на %s." % config.SUBSCRIPTION_PLANS[plan]["name"])
            db.session.commit()
            flash("Тариф обновлён.", "success")
        return redirect(url_for("admin"))

    # ---------------- ошибки ----------------

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("error.html", code=404,
                               message="Страница не найдена."), 404

    @app.errorhandler(500)
    def server_error(_e):
        return render_template("error.html", code=500,
                               message="Ошибка сервера. Попробуйте позже."), 500

    return app
