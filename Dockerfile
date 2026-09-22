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

# timeout 300: ИИ (бесплатные модели) может отвечать до ~2 минут
CMD gunicorn --workers 1 --threads 4 --timeout 300 -b 0.0.0.0:${PORT:-8080} "app:create_app()"
