#!/bin/sh
set -e

WEBHOOK_BASE="${WEBHOOK_BASE_URL:-https://dev.weel.uz}"

# Run webhook setup in background so the server starts immediately
# (health checks must pass before webhook setup finishes)
(
  echo "Setting up bot webhooks: $WEBHOOK_BASE"
  python manage.py setup_bot_webhook --base-url "$WEBHOOK_BASE" || echo "Warning: main bot webhook setup failed"
  python manage.py setup_hotel_bot_webhook "$WEBHOOK_BASE" || echo "Warning: hotel bot webhook setup failed"
  echo "Bot webhook setup complete"
) &

# Gunicorn with threaded workers (gthread).
# 4 workers × 4 threads = 16 concurrent requests with zero ASGI sync-emulation overhead.
# This eliminates the ~1.5s per-request penalty that Daphne imposes on sync views.
WORKERS="${GUNICORN_WORKERS:-4}"
THREADS="${GUNICORN_THREADS:-4}"
exec gunicorn core.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers "$WORKERS" \
  --threads "$THREADS" \
  --worker-class gthread \
  --timeout 60 \
  --access-logfile - \
  --capture-output \
  --enable-stdio-inheritance
