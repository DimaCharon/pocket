# -*- coding: utf-8 -*-
"""Точка входа для локального запуска: python run.py"""
import os

from app import app

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")), debug=False)
