#!/bin/sh
# Entrypoint: читаем PORT из окружения (требование RelaxDev: трафик только на 8080,
# порт не прибивать константой в самом gunicorn).
PORT="${PORT:-8080}"
exec gunicorn --workers 1 --threads 4 --timeout 300 -b "0.0.0.0:${PORT}" "app:app"
