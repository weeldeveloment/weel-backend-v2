#!/bin/sh
set -e

# Register Telegram bot webhooks
WEBHOOK_BASE="${WEBHOOK_BASE_URL:-https://api.weel.uz}"

echo "Setting up bot webhooks: $WEBHOOK_BASE"
python manage.py setup_bot_webhook "$WEBHOOK_BASE" || echo "Warning: main bot webhook setup failed"
python manage.py setup_hotel_bot_webhook "$WEBHOOK_BASE" || echo "Warning: hotel bot webhook setup failed"

# Start the application
exec daphne -b 0.0.0.0 -p 8000 core.asgi:application
