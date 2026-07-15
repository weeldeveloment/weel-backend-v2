#!/bin/sh
set -e

# Collect static files at runtime (needs SECRET_KEY + ALLOWED_HOSTS from env)
python manage.py collectstatic --noinput 2>/dev/null || true

WEBHOOK_BASE="${WEBHOOK_BASE_URL:-https://dev.weel.uz}"

python manage.py create_b2b_tables

# Run webhook setup in background so daphne starts immediately
# (health checks must pass before webhook setup finishes)
(
  echo "Setting up hotel bot webhook: $WEBHOOK_BASE"
  python manage.py setup_hotel_bot_webhook "$WEBHOOK_BASE" 2>/dev/null || echo "Warning: hotel bot webhook setup failed"
  echo "Hotel bot webhook setup complete"
) &

exec uvicorn core.asgi:application --host 0.0.0.0 --port 8000 --workers 4 --ws websockets
