#!/bin/sh
set -e

# Collect static files at runtime (needs SECRET_KEY + ALLOWED_HOSTS from env)
python manage.py collectstatic --noinput 2>/dev/null || true

WEBHOOK_BASE="${WEBHOOK_BASE_URL:-https://dev.weel.uz}"

python manage.py create_b2b_tables

# Run webhook setup in background so the web server starts immediately
(
  echo "Setting up hotel bot webhook: $WEBHOOK_BASE"
  python manage.py setup_hotel_bot_webhook "$WEBHOOK_BASE" 2>/dev/null || echo "Warning: hotel bot webhook setup failed"
  echo "Hotel bot webhook setup complete"
) &

# Start Celery worker in background for async task processing (SMS, notifications, etc.)
celery -A core worker \
  --loglevel="${CELERY_LOG_LEVEL:-info}" \
  --concurrency="${CELERY_CONCURRENCY:-2}" \
  --max-tasks-per-child="${CELERY_MAX_TASKS_PER_CHILD:-1000}" \
  &
CELERY_PID=$!

# Celery beat fires everything in core.celery.beat_schedule: story-view
# persistence, exchange-rate refresh, booking and review reminders. Without it
# those tasks are defined but never run.
#
# Only one beat process may exist across the whole deployment or every
# scheduled task runs once per replica. Set RUN_CELERY_BEAT=0 on the extra
# replicas (or move beat to its own service) when scaling past one.
BEAT_PID=""
if [ "${RUN_CELERY_BEAT:-1}" = "1" ]; then
  celery -A core beat \
    --loglevel="${CELERY_LOG_LEVEL:-info}" \
    --schedule=/tmp/celerybeat-schedule \
    &
  BEAT_PID=$!
fi

stop_all() {
  kill "$CELERY_PID" 2>/dev/null || true
  [ -n "$BEAT_PID" ] && kill "$BEAT_PID" 2>/dev/null || true
}
trap stop_all TERM INT

exec uvicorn core.asgi:application --host 0.0.0.0 --port 8000 --workers 4 --ws websockets
