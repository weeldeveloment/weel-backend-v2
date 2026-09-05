import logging
import os
from celery import Celery
from celery.schedules import crontab
from django.conf import settings
from dotenv import find_dotenv, load_dotenv

# from kombu import Queue, Exchange

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
# Celery CLI entrypoint can load this module before Django settings side effects.
# Ensure .env values are present here too (e.g. REDIS_CONNECTION_STRING).
load_dotenv(find_dotenv(), override=True)

# Initialize OpenTelemetry for Celery workers (before Django apps import).
from core.telemetry import init_telemetry
init_telemetry()

TASK_MODULES = [
    # Workspace task and calendar notifications. Named explicitly because
    # autodiscovery only reaches `<app>.tasks` — `apps.b2b.tasks` — and this
    # is a sub-package of it. A beat entry sends by name, so a worker that
    # never imported the module answers with "Received unregistered task".
    "apps.b2b.workspace.tasks",
    # Meta lead ads. Same reason as the line above — a sub-package of
    # `apps.b2b`, which autodiscovery never reaches on its own.
    "apps.b2b.integrations.tasks",
    # Weel AI's nightly reports — see apps/b2b/workspace/analyst_tasks.py.
    "apps.b2b.workspace.analyst_tasks",
    "stories.tasks",
    "notification.tasks",
    "payment.tasks",
    "users.tasks",
]

app = Celery("core", include=TASK_MODULES)
# app.config_from_object('django.conf:settings', namespace='CELERY')

logger = logging.getLogger(__name__)

# Load task modules from all registered Django apps.
app.autodiscover_tasks()
app.autodiscover_tasks(lambda: settings.INSTALLED_APPS)
app.conf.imports = tuple(TASK_MODULES)

REDIS_CONNECTION_STRING = (os.environ.get("REDIS_CONNECTION_STRING") or "").strip()
if not REDIS_CONNECTION_STRING or REDIS_CONNECTION_STRING in {
    "redis_connection_string",
    "REDIS_CONNECTION_STRING",
    "${REDIS_CONNECTION_STRING}",
}:
    raise RuntimeError("REDIS_CONNECTION_STRING must be set for Celery.")

TASK_ALWAYS_EAGER = bool(getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False))

BROKER_URL = REDIS_CONNECTION_STRING
RESULT_BACKEND = REDIS_CONNECTION_STRING
logger.info("Celery: Redis from REDIS_CONNECTION_STRING")

app.conf.update(
    broker_url=BROKER_URL,
    result_backend=RESULT_BACKEND,
    task_ignore_result=not bool(RESULT_BACKEND),
    accept_content=["json"],
    task_serializer="json",
    result_serializer="json",
    task_always_eager=TASK_ALWAYS_EAGER,
    timezone="Asia/Tashkent",
    # Task events feed the celery-exporter in monitoring/ (task rate, failures,
    # runtime, queue length in Grafana). Without them the exporter sees nothing
    # and every Celery alert fires "no workers". The cost is one small message
    # per task state change on the broker.
    worker_send_task_events=True,
    task_send_sent_event=True,
    # Keep Django's LOGGING (JSON to stdout) inside the worker as well instead
    # of Celery replacing the root handlers with its own plain-text one.
    worker_hijack_root_logger=False,
    # task_default_queue='normal',
    # task_default_exchange='normal',
    # task_default_routing_key='normal',
)

app.conf.beat_schedule = {
    # Rings that outlived their window — the safety net behind the per-call
    # countdown task; see `apps/b2b/workspace/calls.py::expire_stale`.
    "expire_ringing_calls": {
        "task": "b2b.workspace.expire_ringing_calls",
        "schedule": 60.0,
    },
    # Conferences nobody closed. Every ten minutes rather than every minute:
    # the window is four hours, so a sweep a minute would only ask the same
    # question six hundred times before it could answer differently.
    "end_stale_conferences": {
        "task": "b2b.workspace.end_stale_conferences",
        "schedule": crontab(minute="*/10"),
    },
    "persist_story_views": {
        "task": "stories.tasks.persist_story_views",
        "schedule": crontab(minute="*/10"),  # every 10 minutes
    },
    "update_exchange_rate": {
        "task": "payment.tasks.update_exchange_rate",
        "schedule": crontab(hour="*/24"),  # every 24 hours
    },
    "send_booking_reminders": {
        "task": "notification.tasks.send_booking_reminders",
        "schedule": crontab(hour=10, minute=0),  # every 10 hours
    },
    "send_review_reminders": {
        "task": "notification.tasks.send_review_reminders",
        "schedule": crontab(hour=11, minute=30),  # daily at 11:30
    },
    "send_partner_property_check_reminders": {
        "task": "users.send_partner_property_check_reminders",
        "schedule": crontab(hour=11, minute=0),  # daily at 11:00 (3-day gating inside task)
    },
    "expire_stale_activity_bookings": {
        "task": "activities.expire_stale_pending_bookings",
        "schedule": crontab(minute="*/2"),  # hold TTL is 5 min — check often to release fast
    },
    "sync_trip_statuses": {
        "task": "b2b.sync_trip_statuses",
        "schedule": crontab(hour=0, minute=5),  # daily just after midnight (Asia/Tashkent)
    },
    # Flights: payment answers `paid` and the carrier issues the ticket up to
    # ten minutes later. Bookhara pushes a status callback when one is
    # registered for the account, but that lives on their side and can be
    # missing, so the paid orders are chased as well. Re-reading is idempotent.
    "avia_poll_ticketing_status": {
        "task": "avia.poll_ticketing_status",
        "schedule": crontab(minute="*/2"),
    },
    # Hotels: a confirmed booking sits at PENDING until the hotel answers, and
    # Hotelios has no callback for it — asking is the only way to find out.
    "hotels_poll_booking_statuses": {
        "task": "hotels.poll_booking_statuses",
        "schedule": crontab(minute="*/10"),
    },
    # Holds nobody paid for. They count against our credit limit at Hotelios
    # and, at some hotels, against real availability.
    "hotels_release_abandoned_drafts": {
        "task": "hotels.release_abandoned_drafts",
        "schedule": crontab(minute="*/15"),
    },
    # The static catalogue — a thousand hotels and their room types. Nightly,
    # at an hour when nobody is searching.
    "hotels_sync_inventory": {
        "task": "hotels.sync_inventory",
        "schedule": crontab(hour=3, minute=30),
    },
    # Connected mail accounts. No-ops unless B2B_MAIL_ENABLED is on.
    "b2b_mail_sync": {
        "task": "b2b.mail.sync_all_accounts",
        # Every minute. This is how fast a reply lands in the chat section, and
        # a minute is about the longest an inbox can lag before it feels
        # broken. Providers rate-limit IMAP per account, not per client, so the
        # cost of this scales with connected accounts rather than with us.
        "schedule": crontab(minute="*"),
    },
    # Calendar reminders: 30 minutes ahead, 10 minutes ahead, and as the event
    # starts. Every minute is the resolution the feature needs — a reminder
    # that lands three minutes late has missed the point of being a reminder.
    # The pass itself is cheap: one indexed range query per offset, and it
    # only touches events whose start is inside that window.
    "b2b_workspace_event_reminders": {
        "task": "b2b.workspace.send_event_reminders",
        "schedule": crontab(minute="*"),
    },
    # Meta lead ads. The webhook is how leads actually arrive; this is the
    # catch-up pass for the ones that did not — a subscription added after a
    # campaign started, an hour this server was unreachable, a delivery Meta
    # gave up retrying. Every ten minutes, and a no-op unless a workspace has
    # connected an account.
    "b2b_integrations_sync_meta": {
        "task": "b2b.integrations.sync_meta_pages",
        "schedule": crontab(minute="*/10"),
    },
    # A Meta token lasts about sixty days and cannot be renewed without the
    # person signing in again. Daily, so the workspace is asked to reconnect
    # a week before the leads would otherwise stop arriving.
    "b2b_integrations_meta_tokens": {
        "task": "b2b.integrations.refresh_meta_tokens",
        "schedule": crontab(hour=6, minute=15),
    },
    # Retire the guest rows whose secondment has run out. Hourly rather than
    # by the minute: this is housekeeping, not the boundary — access itself is
    # checked against the window on every request, so a row that lingers an
    # extra fifty minutes is untidy and not unsafe.
    "b2b_workspace_expire_secondments": {
        "task": "b2b.workspace.expire_secondments",
        "schedule": crontab(minute=5),
    },
    # Weel AI: every workspace's daily report, plus the weekly on Monday,
    # the monthly on the 1st and the yearly on 1 January — the pass works
    # out which are due. Fixed at the start of the working day so the
    # owner reads yesterday before today happens. A no-op without a key,
    # ours or the workspace's own.
    "b2b_workspace_analyst_reports": {
        "task": "b2b.workspace.analyst_reports",
        "schedule": crontab(hour=settings.B2B_ANALYST_HOUR, minute=0),
    },
}

# app.conf.task_queues = (
#     Queue('high', Exchange('high'), routing_key='high'),
#     Queue('normal', Exchange('normal'), routing_key='normal'),
#     Queue('low', Exchange('low'), routing_key='low'),
# )
# app.conf.task_routes = {
#     # -- HIGH PRIORITY QUEUE -- #
#     'notification.tasks.push_notification': {'queue': 'high'},
#     'notification.tasks.push_notification_for_everyone': {'queue': 'high'},
#     # -- LOW PRIORITY QUEUE -- #
#     'products.tasks.target': {'queue': 'low'},
#     'products.tasks.thrive': {'queue': 'low'},
#     'products.tasks.ulta': {'queue': 'low'},
#     'products.tasks.dermstore': {'queue': 'low'},
# }
