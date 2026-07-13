#!/bin/sh
set -e

WEBHOOK_BASE="${WEBHOOK_BASE_URL:-https://dev.weel.uz}"

# Run webhook setup in background so daphne starts immediately
# (health checks must pass before webhook setup finishes)
(
  echo "Setting up bot webhooks: $WEBHOOK_BASE"
  python manage.py setup_bot_webhook --base-url "$WEBHOOK_BASE" || echo "Warning: main bot webhook setup failed"
  python manage.py setup_hotel_bot_webhook "$WEBHOOK_BASE" || echo "Warning: hotel bot webhook setup failed"
  echo "Bot webhook setup complete"
) &

# Limit thread pool to cap DB connections per container (sync views each use one thread).
# With CONN_MAX_AGE=0, connections close after each request so this is a safety net.
DAPHNE_THREADS="${DAPHNE_THREADS:-10}"
exec daphne -b 0.0.0.0 -p 8000 -t "$DAPHNE_THREADS" core.asgi:application
