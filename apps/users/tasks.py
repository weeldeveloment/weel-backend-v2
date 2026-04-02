import logging
from datetime import timedelta

from core.celery import app

from .services import EskizService, OTPRedisService, TelegramService
from .models.logs import SmsPurpose, SmsLog
from .raw_repository import table_capability_snapshot
from shared.raw.db import fetch_all

logger = logging.getLogger(__name__)

PARTNER_PROPERTY_CHECK_REMINDER_TEXT = (
    "Assalomu alaykum! Weel kompaniyasidan eslatma: iltimos, narxlar va kalendarni "
    "muntazam tekshirib, yangilab turishni unutmang. Rahmat!"
)
PARTNER_PROPERTY_REMINDER_INTERVAL = timedelta(days=3)


@app.task(
    name="send_otp_sms_eskiz",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def send_otp_sms_eskiz(
    self,
    phone_number: str,
    purpose: SmsPurpose,
    otp_code: str = None,
    message_template: str = None,
):
    logger.info("Starting send_otp_sms_eskiz task for %s", phone_number)
    try:
        if otp_code is None:
            otp_code = OTPRedisService.get_existing_otp(phone_number, purpose)
            if not otp_code:
                logger.error(
                    "No OTP found for %s with purpose %s", phone_number, purpose.value
                )
                return {"error": "OTP not found"}

        eskiz_service = EskizService()
        result = eskiz_service.send_sms(phone_number, otp_code, message_template)

        logger.info("Eskiz SMS result: %s", result)

        if table_capability_snapshot().get("users_smslog"):
            try:
                SmsLog.objects.create(
                    phone_number=phone_number,
                    purpose=purpose,
                    is_sent=True,
                )
            except Exception as log_error:
                logger.error("Failed to log SMS: %s", str(log_error))

        logger.info("OTP SMS task completed for %s", phone_number)

        return result
    except Exception as exp:
        if table_capability_snapshot().get("users_smslog"):
            try:
                SmsLog.objects.create(
                    phone_number=phone_number,
                    purpose=purpose,
                    is_sent=False,
                )
            except Exception as log_error:
                logger.error("Failed to log SMS error: %s", str(log_error))

        logger.error("OTP task failed: %s, retrying...", exp)
        raise self.retry(exc=exp)


@app.task(
    name="send_partner_telegram_msg",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def send_partner_telegram_msg(self, partner_id: int, message: str):
    if not table_capability_snapshot().get("users_partnertelegramuser"):
        return "Skipped: Partner telegram table is absent in normalized schema."

    from .models.partners import PartnerTelegramUser  # Local import is safer in tasks

    # 1. Fetch the "Last" and "Active" Telegram User for this Partner
    # We use .filter() instead of .get() so it returns None instead of crashing if missing
    tg_user = (
        PartnerTelegramUser.objects.filter(partner_id=partner_id, is_active=True)
        .order_by("-id")
        .first()
    )  # Gets the most recently created one

    # 2. Skip logic: If no user found, just stop.
    if not tg_user:
        return f"Skipped: No active Telegram account found for Partner ID {partner_id}"

    # 3. Proceed with sending
    service = TelegramService()
    success, result = service.send_message(tg_user.telegram_user_id, message)

    if result == "blocked":
        # Optional: Mark this specific TG user as inactive
        tg_user.is_active = False
        tg_user.save()
        return f"Skipped: Partner {partner_id} has blocked the bot."

    return f"Sent to Partner {partner_id} (TG: {tg_user.telegram_user_id})"


@app.task(
    name="users.send_partner_property_check_reminders",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 2},
)
def send_partner_property_check_reminders(self):
    caps = table_capability_snapshot()
    if not (caps.get("users") and caps.get("cottage")):
        result = {
            "sent": 0,
            "skipped_recent": 0,
            "failed": 0,
            "checked": 0,
            "detail": "Skipped: required normalized tables are missing.",
        }
        logger.info("Partner property reminder SMS task finished: %s", result)
        return result

    partners = fetch_all(
        """
        SELECT DISTINCT u.id, u.phone_number
        FROM public.users u
        JOIN public.cottage c ON c.partner_user_id = u.id
        WHERE u.role = 'partner'
          AND u.is_active = TRUE
          AND COALESCE(c.is_archived, FALSE) = FALSE
          AND u.phone_number IS NOT NULL
          AND u.phone_number <> ''
        ORDER BY u.id
        """
    )

    eskiz_service = EskizService()
    sent_count = 0
    failed_count = 0

    for partner in partners:
        try:
            eskiz_service.send_text_sms(
                phone_number=partner["phone_number"],
                message=PARTNER_PROPERTY_CHECK_REMINDER_TEXT,
            )
            sent_count += 1
        except Exception:
            failed_count += 1
            logger.exception(
                "Partner property reminder SMS failed. partner_id=%s",
                partner["id"],
            )

    result = {
        "sent": sent_count,
        "skipped_recent": 0,
        "failed": failed_count,
        "checked": sent_count + failed_count,
    }
    logger.info("Partner property reminder SMS task finished: %s", result)
    return result
