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

TASK_MODULES = [
    "booking.tasks",
    "property.tasks",
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
    "send_review_reminders": {
        "task": "notification.tasks.send_review_reminders",
        "schedule": crontab(hour=11, minute=30),  # daily at 11:30
    },
    "send_pending_booking_payment_reminders": {
        "task": "booking.tasks.send_pending_booking_payment_reminders",
        "schedule": crontab(minute="*/5"),  # every 5 min — 24m / 6m / 1m left to pay
    },
    "send_partner_property_check_reminders": {
        "task": "users.send_partner_property_check_reminders",
        "schedule": crontab(hour=11, minute=0),  # daily at 11:00 (3-day gating inside task)
    },
    "send_cottage_price_reminders": {
        "task": "property.tasks.send_cottage_price_reminders",
        "schedule": crontab(day_of_month=1, hour=9, minute=0),  # 1st of every month at 09:00
    },
    "expire_stale_activity_bookings": {
        "task": "activities.expire_stale_pending_bookings",
        "schedule": crontab(minute="*/2"),  # hold TTL is 5 min — check often to release fast
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
