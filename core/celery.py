import logging
import os
import re
from celery import Celery
from celery.schedules import crontab
from django.conf import settings
from dotenv import find_dotenv, load_dotenv

# from kombu import Queue, Exchange

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")
# Celery CLI entrypoint can load this module before Django settings side effects.
# Ensure .env values are present here too (e.g. REDIS_CONNECTION_STRING).
load_dotenv(find_dotenv(), override=True)
app = Celery("core")
# app.config_from_object('django.conf:settings', namespace='CELERY')

logger = logging.getLogger(__name__)
ENV_PLACEHOLDER_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _resolve_env_value(raw_value: str | None) -> str | None:
    if not raw_value:
        return None

    resolved_value = ENV_PLACEHOLDER_PATTERN.sub(
        lambda match: os.environ.get(match.group(1), match.group(0)),
        raw_value,
    ).strip()

    if ENV_PLACEHOLDER_PATTERN.search(resolved_value):
        logger.warning("Unresolved environment placeholder in value: %s", raw_value)
        return None

    return resolved_value

# Load task modules from all registered Django apps.
app.autodiscover_tasks()
app.autodiscover_tasks(lambda: settings.INSTALLED_APPS)

REDIS_CONNECTION_STRING = _resolve_env_value(
    os.environ.get("REDIS_CONNECTION_STRING")
)
TASK_ALWAYS_EAGER = bool(getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False))

# Local (DEBUG=True) bo‘lsa — local Redis, serverda — REDIS_CONNECTION_STRING
if getattr(settings, "DEBUG", False):
    BROKER_URL = "redis://127.0.0.1:6379/0"
    RESULT_BACKEND = "redis://127.0.0.1:6379/0"
    logger.info("Celery: local Redis (DEBUG=True)")
else:
    BROKER_URL = REDIS_CONNECTION_STRING
    RESULT_BACKEND = REDIS_CONNECTION_STRING
    if BROKER_URL:
        logger.info("Celery: server Redis (REDIS_CONNECTION_STRING)")

if TASK_ALWAYS_EAGER:
    # Tests/development eager mode should not require Redis.
    BROKER_URL = BROKER_URL or "memory://localhost/"
    RESULT_BACKEND = RESULT_BACKEND or "cache+memory://"
elif not BROKER_URL:
    logger.warning(
        "Celery broker/backend URL is empty. Set REDIS_CONNECTION_STRING (server) yoki DEBUG=True (local)."
    )

app.conf.update(
    broker_url=BROKER_URL,
    result_backend=RESULT_BACKEND,
    task_ignore_result=not bool(RESULT_BACKEND),
    accept_content=["json"],
    task_serializer="json",
    result_serializer="json",
    task_always_eager=TASK_ALWAYS_EAGER,
    timezone="Asia/Tashkent",
    # task_default_queue='normal',
    # task_default_exchange='normal',
    # task_default_routing_key='normal',
)

app.conf.beat_schedule = {
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
    "send_pending_booking_payment_reminders": {
        "task": "booking.tasks.send_pending_booking_payment_reminders",
        "schedule": crontab(minute="*/5"),  # every 5 min — 24m / 6m / 1m left to pay
    },
    "send_partner_property_check_reminders": {
        "task": "users.send_partner_property_check_reminders",
        "schedule": crontab(hour=11, minute=0),  # daily at 11:00 (3-day gating inside task)
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
