# -*- coding: utf-8 -*-
"""WSGI-точка входа для gunicorn: gunicorn wsgi:app
(экземпляр приложения создаётся при импорте пакета app)."""
from app import app  # noqa: F401
