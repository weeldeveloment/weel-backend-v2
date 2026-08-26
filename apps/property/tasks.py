import logging
from datetime import timedelta
from types import SimpleNamespace

from celery import shared_task
from django.utils import timezone

from notification.service import NotificationService
from notification.raw_repository import create_notification
from shared.raw.db import fetch_all, table_exists
from users.raw_repository import table_capability_snapshot
from users.tasks import send_partner_telegram_msg
from .cottage_repository import list_partners_with_expired_cottage_prices


logger = logging.getLogger(__name__)

PRICE_REMINDER_INTERVAL = timedelta(days=7)

PRICE_REMINDER_TITLE = "Eslatma: Yangi oylik narxlar"
PRICE_REMINDER_BODY = (
    "Assalomu alaykum! Yangi oy boshlandi. Iltimos, dachangizning "
    "2 oylik narxlarini (joriy va keyingi oylik) yangilab qo'ying. "
    "Yangilanmagan narxlar mijozlar tomonidan ko'rinmaydi."
)


@shared_task(
    name="property.tasks.send_cottage_price_reminders",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 2},
)
def send_cottage_price_reminders(self):
    caps = table_capability_snapshot()
    has_cottage = table_exists("cottage")
    has_cottage_price = table_exists("cottage_price")
    has_users = caps.get("users")
    has_notification = caps.get("notification")

    if not (has_cottage and has_cottage_price and has_users):
        result = {
            "sent": 0,
            "skipped_recent": 0,
            "failed": 0,
            "checked": 0,
            "detail": "Skipped: required tables missing (cottage + cottage_price + users).",
        }
        logger.info("Cottage price reminder task finished: %s", result)
        return result

    partners = list_partners_with_expired_cottage_prices()

    sent_count = 0
    failed_count = 0
    skipped_recent_count = 0

    # Load recent reminder logs to avoid spam
    recent_logs: dict[int, object] = {}
    if has_notification:
        log_rows = fetch_all(
            """
            SELECT
                recipient_user_id AS partner_id,
                MAX(created_at) AS last_sent_at
            FROM public.notification
            WHERE notification_type = 'price_reminder'
              AND status = 'sent'
            GROUP BY recipient_user_id
            """
        )
        recent_logs = {
            int(row["partner_id"]): row.get("last_sent_at")
            for row in log_rows
            if row.get("partner_id")
        }

    for row in partners:
        partner_id = int(row["partner_id"])
        last_sent_at = recent_logs.get(partner_id)

        if last_sent_at and (last_sent_at + PRICE_REMINDER_INTERVAL) > timezone.now():
            skipped_recent_count += 1
            continue

        partner = SimpleNamespace(id=partner_id)

        try:
            # Push notification
            NotificationService.send_to_partner(
                partner=partner,
                title=PRICE_REMINDER_TITLE,
                message=PRICE_REMINDER_BODY,
                notification_type="price_reminder",
                data={"screen": "partner_cottages", "action": "update_prices"},
            )

            # Telegram message (best-effort, async)
            send_partner_telegram_msg.delay(
                partner_id=partner_id,
                message=PRICE_REMINDER_BODY,
            )

            sent_count += 1
        except Exception:
            failed_count += 1
            logger.exception(
                "Cottage price reminder failed. partner_id=%s",
                partner_id,
            )

    result = {
        "sent": sent_count,
        "skipped_recent": skipped_recent_count,
        "failed": failed_count,
        "checked": sent_count + skipped_recent_count + failed_count,
    }
    logger.info("Cottage price reminder task finished: %s", result)
    return result
