import logging

from core.celery import app
from django.conf import settings
from django.utils import timezone

from users.services import TelegramService

logger = logging.getLogger(__name__)

# Must match B2BEmployeePassportPreviewView / B2BEmployeePassportPreviewStatusView
# in apps/b2b/views.py — same cache-key prefix and TTL on both ends.
PASSPORT_OCR_JOB_TTL_SECONDS = 600


@app.task(name="b2b.run_passport_ocr_job")
def run_passport_ocr_job(job_id: str, front_path: str, back_path: str) -> None:
    """Runs the (slow) passport OCR pipeline off the request/response cycle.

    ``front_path``/``back_path`` are default_storage keys the view already
    saved the uploaded images under (temporary — deleted here once OCR is
    done, regardless of outcome). The result is written to cache under the
    same key the view polls, so this task has no return value the caller
    needs.
    """
    from django.core.cache import cache
    from django.core.files.storage import default_storage

    from apps.b2b.passport_ocr import PassportOCRError, extract_passport_data

    cache_key = f"passport_ocr_job:{job_id}"
    job = cache.get(cache_key) or {}
    try:
        with default_storage.open(front_path) as front_file, default_storage.open(back_path) as back_file:
            passport_data = extract_passport_data(front_file, back_file)
        job["status"] = "done"
        job["data"] = passport_data
    except PassportOCRError as exc:
        job["status"] = "error"
        job["detail"] = str(exc)
    except Exception:
        logger.exception("Passport OCR background job failed unexpectedly: %s", job_id)
        job["status"] = "error"
        job["detail"] = "Не удалось распознать паспортные данные. Попробуйте другое фото."
    finally:
        cache.set(cache_key, job, PASSPORT_OCR_JOB_TTL_SECONDS)
        default_storage.delete(front_path)
        default_storage.delete(back_path)


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
        f"🏢 Kompaniya: {company_name or '—'}\n"
        f"📧 Email: {email or '—'}\n"
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
