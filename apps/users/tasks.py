import logging
from datetime import timedelta, timezone as dt_timezone

from core.celery import app
from django.conf import settings
from django.utils import timezone

from .services import EskizService, OTPRedisService, TelegramService
from .models.logs import SmsPurpose
from .raw_repository import (
    create_sms_log,
    deactivate_partner_telegram_user,
    get_latest_active_partner_telegram_user,
    table_capability_snapshot,
)
from shared.raw.compat import get_table_name
from shared.raw.db import fetch_all, table_exists

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


@app.task(
    name="users.send_partner_property_check_reminders",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 2},
)
def send_partner_property_check_reminders(self):
    caps = table_capability_snapshot()
    has_users = caps.get("users")
    has_cottage = caps.get("cottage")
    has_apartment = table_exists("apartment")
    has_smslog = caps.get("users_smslog")

    if not (has_users and (has_cottage or has_apartment)):
        result = {
            "sent": 0,
            "skipped_recent": 0,
            "failed": 0,
            "checked": 0,
            "detail": "Skipped: required normalized tables are missing (users + apartment/cottage).",
        }
        logger.info("Partner property reminder SMS task finished: %s", result)
        return result

    users_table = get_table_name("users")
    property_source_queries = []
    if has_cottage:
        cottage_table = get_table_name("cottage")
        property_source_queries.append(
            f"""
            SELECT c.partner_user_id AS partner_user_id
            FROM {cottage_table} c
            WHERE COALESCE(c.is_archived, FALSE) = FALSE
            """
        )
    if has_apartment:
        apartment_table = get_table_name("apartment")
        property_source_queries.append(
            f"""
            SELECT a.partner_user_id AS partner_user_id
            FROM {apartment_table} a
            WHERE COALESCE(a.is_archived, FALSE) = FALSE
            """
        )

    partners = fetch_all(
        f"""
        SELECT MIN(u.id) AS id, u.phone_number
        FROM {users_table} u
        JOIN (
            {" UNION ".join(property_source_queries)}
        ) p ON p.partner_user_id = u.id
        WHERE u.role = 'partner'
          AND u.is_active = TRUE
          AND u.phone_number IS NOT NULL
          AND u.phone_number <> ''
        GROUP BY u.phone_number
        ORDER BY MIN(u.id)
        """
    )

    eskiz_service = EskizService()
    sent_count = 0
    failed_count = 0
    skipped_recent_count = 0

    reminder_logs_by_phone: dict[str, object] = {}
    if has_smslog:
        smslog_table = get_table_name("users_smslog")
        reminder_logs = fetch_all(
            f"""
            SELECT l.phone_number, MAX(l.created_at) AS last_sent_at
            FROM {smslog_table} l
            WHERE l.purpose = %s
              AND l.is_sent = TRUE
            GROUP BY l.phone_number
            """,
            [SmsPurpose.PARTNER_PROPERTY_REMINDER.value],
        )
        reminder_logs_by_phone = {
            row["phone_number"]: row.get("last_sent_at")
            for row in reminder_logs
            if row.get("phone_number")
        }

    for partner in partners:
        last_sent_at = reminder_logs_by_phone.get(partner["phone_number"])
        if last_sent_at:
            if timezone.is_naive(last_sent_at):
                last_sent_at = timezone.make_aware(last_sent_at, timezone=dt_timezone.utc)
            if (last_sent_at + PARTNER_PROPERTY_REMINDER_INTERVAL) > timezone.now():
                skipped_recent_count += 1
                continue

        try:
            eskiz_service.send_text_sms(
                phone_number=partner["phone_number"],
                message=PARTNER_PROPERTY_CHECK_REMINDER_TEXT,
            )
            sent_count += 1
            if has_smslog:
                create_sms_log(
                    phone_number=partner["phone_number"],
                    purpose=SmsPurpose.PARTNER_PROPERTY_REMINDER,
                    is_sent=True,
                )
        except Exception:
            failed_count += 1
            if has_smslog:
                create_sms_log(
                    phone_number=partner["phone_number"],
                    purpose=SmsPurpose.PARTNER_PROPERTY_REMINDER,
                    is_sent=False,
                )
            logger.exception(
                "Partner property reminder SMS failed. partner_id=%s",
                partner["id"],
            )

    result = {
        "sent": sent_count,
        "skipped_recent": skipped_recent_count,
        "failed": failed_count,
        "checked": sent_count + skipped_recent_count + failed_count,
    }
    logger.info("Partner property reminder SMS task finished: %s", result)
    return result
