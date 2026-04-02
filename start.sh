#!/bin/bash
set -e

. /opt/venv/bin/activate

if [ "${SKIP_MIGRATIONS:-}" = "true" ] || [ "${SKIP_MIGRATIONS:-}" = "1" ]; then
	echo "Skipping migrations (SKIP_MIGRATIONS set)."
else
	echo "Running migrations..."
	python manage.py migrate --fake-initial --noinput
fi

echo "Creating test user (if not exists)..."
python manage.py create_test_user --phone=+998001234567 --first-name=Test --last-name=User

echo "Creating test partner (if not exists)..."
python manage.py create_test_partner --phone=+998901234568 --first-name=Test --last-name=Partner

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
