# -*- coding: utf-8 -*-
"""Расширения Flask (SQLAlchemy, LoginManager).
Создаются ДО приложения, чтобы модули могли импортировать db без циклических импортов."""
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
login_manager = LoginManager()
