#!/bin/sh
set -e

# What this container runs. Splitting the roles lets the web API scale without
# also multiplying Celery beat, which must exist exactly once across the whole
# deployment or every scheduled task fires once per replica.
#
#   all    — web + worker + beat in one process tree (default, single-container
#            deploys; keep RUN_CELERY_BEAT=0 on every replica past the first)
#   web    — API only
#   worker — Celery worker only
#   beat   — Celery beat only
WEEL_ROLE="${WEEL_ROLE:-all}"

run_web() {
  exec uvicorn core.asgi:application \
    --host 0.0.0.0 --port 8000 \
    --workers "${UVICORN_WORKERS:-4}" \
    --ws websockets
}

run_worker() {
  exec celery -A core worker \
    --loglevel="${CELERY_LOG_LEVEL:-info}" \
    --concurrency="${CELERY_CONCURRENCY:-2}" \
    --max-tasks-per-child="${CELERY_MAX_TASKS_PER_CHILD:-1000}"
}

run_beat() {
  exec celery -A core beat \
    --loglevel="${CELERY_LOG_LEVEL:-info}" \
    --schedule=/tmp/celerybeat-schedule
}

# Workers and beat attach to an already-migrated database; only the roles that
# serve or bootstrap the schema should touch it.
if [ "$WEEL_ROLE" = "worker" ]; then
  run_worker
fi

if [ "$WEEL_ROLE" = "beat" ]; then
  run_beat
fi

# Schema first, and it must succeed. Ten apps carry real Django migrations
# (users, property, payment, chat, platform, ...). Starting the API against an
# un-migrated database fails later, at request time, as "column does not exist".
echo "Applying database migrations..."
python manage.py migrate --noinput

python manage.py create_b2b_tables

# Amenity icons. Only rows still on the placeholder are touched, so this is a
# no-op once applied and hand-uploaded artwork survives it. Non-fatal: a
# missing icon is a cosmetic problem, not a reason to refuse to serve.
if ! python manage.py seed_service_icons --apply; then
  echo "WARNING: amenity icon seeding failed — some amenities will show the placeholder icon" >&2
fi

# Non-fatal, but never silent: a failure here means unstyled admin pages, and
# hiding it behind `2>/dev/null || true` is why that goes unnoticed for weeks.
if ! python manage.py collectstatic --noinput; then
  echo "WARNING: collectstatic failed — static assets may be missing" >&2
fi

WEBHOOK_BASE="${WEBHOOK_BASE_URL:-https://dev.weel.uz}"

# Backgrounded so the web server starts immediately; Telegram being slow or
# down must not hold up the API.
(
  echo "Setting up hotel bot webhook: $WEBHOOK_BASE"
  if python manage.py setup_hotel_bot_webhook "$WEBHOOK_BASE"; then
    echo "Hotel bot webhook setup complete"
  else
    echo "WARNING: hotel bot webhook setup failed — the bot will not receive updates" >&2
  fi
) &

if [ "$WEEL_ROLE" = "web" ]; then
  run_web
fi

# WEEL_ROLE=all — everything in this container.
celery -A core worker \
  --loglevel="${CELERY_LOG_LEVEL:-info}" \
  --concurrency="${CELERY_CONCURRENCY:-2}" \
  --max-tasks-per-child="${CELERY_MAX_TASKS_PER_CHILD:-1000}" \
  &
CELERY_PID=$!

# Celery beat fires everything in core.celery.beat_schedule: story-view
# persistence, exchange-rate refresh, booking and review reminders. Without it
# those tasks are defined but never run.
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

run_web
