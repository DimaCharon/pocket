FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080 \
    DATABASE_URL=sqlite:////data/pocket_signals.db

RUN mkdir -p /data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080
VOLUME ["/data"]

# JSON-формат CMD (рекомендация Docker — корректная передача сигналов).
# Порт берётся из окружения внутри entrypoint.sh (0.0.0.0:${PORT:-8080}).
CMD ["sh", "/app/entrypoint.sh"]
