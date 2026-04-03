#!/bin/bash
set -e

if [ -f /opt/venv/bin/activate ]; then
	. /opt/venv/bin/activate
fi

echo "Skipping Django migrations. Raw SQL schema is managed externally."

echo "Collecting static files..."
python manage.py collectstatic --noinput 2>/dev/null || true

echo "Setting up Telegram bot webhook..."
python manage.py setup_bot_webhook || echo "Warning: Bot webhook setup failed, continuing..."

echo "Starting Celery worker..."
celery -A core worker --loglevel=info --concurrency=2 --pool=solo &

echo "Starting Celery beat..."
celery -A core beat --loglevel=info &

echo "Starting Daphne (ASGI)..."
exec daphne -b 0.0.0.0 -p 8000 core.asgi:application
