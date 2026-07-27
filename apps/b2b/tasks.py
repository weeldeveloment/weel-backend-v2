import logging

from core.celery import app
from django.conf import settings
from django.utils import timezone

from users.services import TelegramService

logger = logging.getLogger(__name__)


@app.task(name="b2b.sync_trip_statuses")
def sync_trip_statuses():
    """Daily job: flips `draft`/`pending` trips to `active` once their
    start_date arrives, and `active` trips to `completed` once their
    end_date has passed. Trip status is a stored column (not derived from
    dates on read), so without this it stays stale forever."""
    from apps.b2b.repository import sync_trip_statuses_for_date

    today = timezone.localdate()
    updated = sync_trip_statuses_for_date(today)
    logger.info("sync_trip_statuses: updated %s trip(s) for %s", updated, today)
    return updated


def _send_b2b_lead_telegram_notification(lead_id, full_name, company_name, email, phone_number):
    token = settings.B2B_LEAD_BOT_TOKEN
    chat_id = settings.B2B_LEAD_TELEGRAM_CHAT_ID
    if not token or not chat_id:
        logger.warning("B2B_LEAD_BOT_TOKEN/B2B_LEAD_TELEGRAM_CHAT_ID not configured; skipping lead #%s", lead_id)
        return "Skipped: B2B_LEAD_BOT_TOKEN/B2B_LEAD_TELEGRAM_CHAT_ID is not configured."

    text = (
        f"🆕 <b>Yangi hamkorlik so'rovi</b>\n\n"
        f"👤 Ism: {full_name}\n"
        f"🏢 Kompaniya: {company_name}\n"
        f"📧 Email: {email}\n"
        f"📞 Tel: {phone_number}"
    )
    service = TelegramService(token=token)
    return service.send_message(int(chat_id), text)


@app.task(
    name="send_b2b_lead_telegram_msg",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def send_b2b_lead_telegram_msg(self, lead_id, full_name, company_name, email, phone_number):
    return _send_b2b_lead_telegram_notification(lead_id, full_name, company_name, email, phone_number)
