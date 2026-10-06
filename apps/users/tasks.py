import logging

from core.celery import app

from .services import EskizService, OTPRedisService, TelegramService
from .models.logs import SmsPurpose
from .raw_repository import (
    create_sms_log,
    deactivate_partner_telegram_user,
    get_latest_active_partner_telegram_user,
    table_capability_snapshot,
)

logger = logging.getLogger(__name__)

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

        logger.info("Using OTP ===================================> %s for %s", otp_code, phone_number)

        eskiz_service = EskizService()
        result = eskiz_service.send_sms(phone_number, otp_code, message_template)

        logger.info("Eskiz SMS result: %s", result)

        if table_capability_snapshot().get("users_smslog"):
            try:
                create_sms_log(
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
                create_sms_log(
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

    tg_user = get_latest_active_partner_telegram_user(partner_id=partner_id)

    # 2. Skip logic: If no user found, just stop.
    if not tg_user:
        return f"Skipped: No active Telegram account found for Partner ID {partner_id}"

    # 3. Proceed with sending
    service = TelegramService()
    success, result = service.send_message(int(tg_user["telegram_user_id"]), message)

    if result == "blocked":
        # Optional: Mark this specific TG user as inactive
        deactivate_partner_telegram_user(int(tg_user["id"]))
        return f"Skipped: Partner {partner_id} has blocked the bot."

    return f"Sent to Partner {partner_id} (TG: {tg_user['telegram_user_id']})"
