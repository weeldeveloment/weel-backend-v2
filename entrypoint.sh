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

# django-prometheus with `uvicorn --workers N`: each worker process keeps its
# own counters, so a scrape of /metrics would answer from whichever worker took
# the request and the graphs would jump around. prometheus_client aggregates
# across processes when this directory is set; it must be empty at start or
# counters from the previous run are added in.
export PROMETHEUS_MULTIPROC_DIR="${PROMETHEUS_MULTIPROC_DIR:-/tmp/prometheus_multiproc}"
rm -rf "$PROMETHEUS_MULTIPROC_DIR"
mkdir -p "$PROMETHEUS_MULTIPROC_DIR"

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

# apps.hotels is raw-SQL too (no Django migrations), so its hotelios_* tables
# — including hotelios_hotel, which every /api/hotels/ request queries — only
# exist if this runs. It never had been wired in here, so every deploy left
# production without the tables and every hotel endpoint 500'd on
# "relation does not exist".
python manage.py create_hotels_tables

# apps.avia is raw SQL for the same reason, and has the same failure mode:
# without this the avia_booking tables do not exist and every flight booking,
# listing and status callback 500s on "relation does not exist" — while offer
# search keeps working, because searching never touches the database. That
# combination is what makes it easy to miss until the first customer books.
python manage.py create_avia_tables

# Non-fatal, but never silent: a failure here means unstyled admin pages, and
# hiding it behind `2>/dev/null || true` is why that goes unnoticed for weeks.
if ! python manage.py collectstatic --noinput; then
  echo "WARNING: collectstatic failed — static assets may be missing" >&2
fi

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
