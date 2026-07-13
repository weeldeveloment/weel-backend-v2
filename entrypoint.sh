#!/bin/sh
set -e

WEBHOOK_BASE="${WEBHOOK_BASE_URL:-https://dev.weel.uz}"

# Run webhook setup in background so server starts immediately
# (health checks must pass before webhook setup finishes)
(
  echo "Setting up bot webhooks: $WEBHOOK_BASE"
  python manage.py setup_bot_webhook --base-url "$WEBHOOK_BASE" 2>/dev/null || echo "Warning: main bot webhook setup failed"
  python manage.py setup_hotel_bot_webhook "$WEBHOOK_BASE" 2>/dev/null || echo "Warning: hotel bot webhook setup failed"
  echo "Bot webhook setup complete"
) &

exec uvicorn core.asgi:application --host 0.0.0.0 --port 8000 --workers 4 --ws websockets
