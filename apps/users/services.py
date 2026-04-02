import re
import os
import json
import random
import logging
import time
import urllib.parse

import requests
from typing import Optional
from init_data_py import InitData

from django.conf import settings
from django.core.cache import cache
from django.utils.translation import gettext_lazy as _
from django.contrib.auth.hashers import make_password, check_password

from .models.logs import SmsPurpose
from shared.utility import PASSWORD_REGEX
from shared.raw.entities import RawUser
from .raw_repository import table_capability_snapshot

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN_APP")

# If legacy tables are missing in production, we fallback to norm storage.
# Avoid spamming logs on every device registration attempt.

class EskizService:
    ESKIZ_TOKEN_KEY = "eskiz_service_token"
    RESPONSE_LOG_MAX_LENGTH = 1000

    def __init__(
        self,
        email: Optional[str] = None,
        password: Optional[str] = None,
        login_url: Optional[str] = None,
        send_sms_url: Optional[str] = None,
    ):
        self.email = email or getattr(settings, "ESKIZ_EMAIL", "")
        self.password = password or getattr(settings, "ESKIZ_PASSWORD", "")
        self.login_url = login_url or getattr(settings, "ESKIZ_LOGIN_URL", "")
        self.send_sms_url = send_sms_url or getattr(settings, "ESKIZ_SMS_SEND_URL", "")

        if not all([self.email, self.password, self.login_url, self.send_sms_url]):
            logger.warning("Eskiz service is not fully configured")

    @staticmethod
    def _mask_phone(phone_number: str) -> str:
        if not phone_number:
            return "unknown"

        visible_tail = phone_number[-4:]
        return f"{'*' * max(len(phone_number) - 4, 0)}{visible_tail}"

    @staticmethod
    def _mask_email(email: str) -> str:
        if not email or "@" not in email:
            return "unknown"

        local_part, domain = email.split("@", 1)
        if len(local_part) <= 2:
            masked_local = "*" * len(local_part)
        else:
            masked_local = f"{local_part[:2]}{'*' * (len(local_part) - 2)}"

        return f"{masked_local}@{domain}"

    @classmethod
    def _response_excerpt(cls, response: Optional[requests.Response]) -> str:
        if response is None:
            return ""

        try:
            body = response.json()
            text = json.dumps(body, ensure_ascii=False)
        except ValueError:
            text = response.text or ""

        return text[: cls.RESPONSE_LOG_MAX_LENGTH]

    def get_token(self):
        token = cache.get(self.ESKIZ_TOKEN_KEY)

        if token:
            return token

        payload = {"email": self.email, "password": self.password}
        started_at = time.monotonic()
        response = None

        logger.info(
            "Eskiz token request started. url=%s email=%s",
            self.login_url,
            self._mask_email(self.email),
        )
        try:
            response = requests.post(str(self.login_url), data=payload, timeout=10)
            elapsed_ms = int((time.monotonic() - started_at) * 1000)

            if not response.ok:
                logger.error(
                    "Eskiz token request failed. status=%s elapsed_ms=%s body=%s",
                    response.status_code,
                    elapsed_ms,
                    self._response_excerpt(response),
                )

            response.raise_for_status()
            data = response.json()
            token = data.get("data", {}).get("token")
            logger.debug(
                "Eskiz token response received. token=%s status=%s elapsed_ms=%s body=%s",
                token,
                response.status_code,
                elapsed_ms,
                self._response_excerpt(response),
            )
            if not token:
                logger.error(
                    "Eskiz token not found in response. elapsed_ms=%s body=%s",
                    elapsed_ms,
                    self._response_excerpt(response),
                )
                raise ValueError("Token not found in response")

            cache.set(self.ESKIZ_TOKEN_KEY, token, (3600 * 24 * 30) - 3600)
            logger.info("Successfully obtained new Eskiz token. elapsed_ms=%s", elapsed_ms)
            return token
        except requests.exceptions.RequestException as e:
            response = getattr(e, "response", response)
            logger.error(
                "Error getting Eskiz token. status=%s body=%s error=%s",
                getattr(response, "status_code", None),
                self._response_excerpt(response),
                str(e),
                exc_info=True,
            )
            raise
        except Exception as e:
            logger.error("Unexpected error getting Eskiz token: %s", str(e), exc_info=True)
            raise

    def send_sms(
        self, phone_number: str, code: str, message_template: Optional[str] = None
    ):
        token = self.get_token()
        if message_template:
            message = message_template.format(code=code)
        else:
            message = f"Код верификации для входа в приложение WEEL - {code}"

        # Eskiz odatda 998901234567 formatida qabul qiladi (+ siz)
        mobile_phone = (phone_number or "").replace("+", "").replace(" ", "").strip()
        payload = {"mobile_phone": mobile_phone, "message": message}
        masked_phone = self._mask_phone(phone_number)

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        }
        started_at = time.monotonic()
        response = None

        logger.info(
            "Eskiz SMS request started. url=%s phone=%s message_length=%s",
            self.send_sms_url,
            masked_phone,
            len(message),
        )
        try:
            response = requests.post(
                str(self.send_sms_url), json=payload, headers=headers, timeout=10
            )
            elapsed_ms = int((time.monotonic() - started_at) * 1000)

            if response.status_code == 401:
                logger.warning(
                    "Eskiz SMS returned 401. Refreshing token. phone=%s elapsed_ms=%s body=%s",
                    masked_phone,
                    elapsed_ms,
                    self._response_excerpt(response),
                )
                cache.delete(self.ESKIZ_TOKEN_KEY)
                token = self.get_token()
                headers["Authorization"] = f"Bearer {token}"
                retry_started_at = time.monotonic()
                response = requests.post(
                    self.send_sms_url, json=payload, headers=headers, timeout=10
                )

                elapsed_ms = int((time.monotonic() - retry_started_at) * 1000)

            if not response.ok:
                logger.error(
                    "Eskiz SMS request failed. phone=%s status=%s elapsed_ms=%s body=%s",
                    masked_phone,
                    response.status_code,
                    elapsed_ms,
                    self._response_excerpt(response),
                )

            response.raise_for_status()

            provider_message_id = None
            try:
                provider_message_id = response.json().get("data", {}).get("id")
            except ValueError:
                pass

            logger.info(
                "Eskiz SMS sent successfully. phone=%s status=%s elapsed_ms=%s provider_message_id=%s",
                masked_phone,
                response.status_code,
                elapsed_ms,
                provider_message_id,
            )
            return {
                "status_code": response.status_code,
                "detail": "The confirmation code sent successfully",
            }
        except requests.exceptions.RequestException as e:
            response = getattr(e, "response", response)
            logger.error(
                "Error sending Eskiz SMS. phone=%s status=%s body=%s error=%s",
                masked_phone,
                getattr(response, "status_code", None),
                self._response_excerpt(response),
                str(e),
                exc_info=True,
            )
            raise
        except Exception as e:
            logger.error(
                "Unexpected error sending Eskiz SMS. phone=%s error=%s",
                masked_phone,
                str(e),
                exc_info=True,
            )
            raise

    def send_text_sms(self, phone_number: str, message: str):
        """
        Sends a plain text SMS via Eskiz without OTP semantics.
        Reuses send_sms transport/auth flow.
        """
        return self.send_sms(phone_number=phone_number, code="", message_template=message)


class OTPRedisService:
    OTP_EXPIRE = 60
    MAX_ATTEMPTS = 3
    OTP_LENGTH = 4
    RESEND_COOLDOWN = 30
    REGISTRATION_DATA_EXPIRE = 600
    TEST_BYPASS_OTP = "0000"

    @classmethod
    def generate_otp(cls):
        return "".join([str(random.randint(0, 9)) for _ in range(cls.OTP_LENGTH)])

    @staticmethod
    def get_otp_key(phone_number: str, purpose: SmsPurpose):
        return f"otp:{purpose.value}:{phone_number}"

    @staticmethod
    def get_attempts_key(phone_number: str, purpose: SmsPurpose):
        return f"otp_attempts:{purpose.value}:{phone_number}"

    @staticmethod
    def get_registration_key(phone_number: str, purpose: SmsPurpose):
        return f"otp_registration:{purpose.value}:{phone_number}"

    @classmethod
    def create_otp(cls, phone_number: str, purpose: SmsPurpose):
        otp_code = cls.generate_otp()

        otp_data = {
            "otp_code": otp_code,
            "phone_number": phone_number,
            "purpose": purpose,
            "attempts": 0,
        }

        otp_key = cls.get_otp_key(phone_number, purpose)

        cache.set(otp_key, json.dumps(otp_data), cls.OTP_EXPIRE)

        attempts_key = cls.get_attempts_key(phone_number, purpose)
        cache.delete(attempts_key)

        logger.info("OTP created for %s with purpose %s", phone_number, purpose.value)
        return otp_code

    @classmethod
    def create_otp_with_data(cls, phone_number: str, purpose: SmsPurpose, data: dict):
        otp_code = cls.generate_otp()
        registration_key = cls.get_registration_key(phone_number, purpose)

        otp_data = {
            "otp_code": otp_code,
            "phone_number": phone_number,
            "purpose": purpose,
            "attempts": 0,
        }

        otp_key = cls.get_otp_key(phone_number, purpose)
        cache.set(otp_key, json.dumps(otp_data), cls.OTP_EXPIRE)
        cache.set(registration_key, json.dumps(data), cls.REGISTRATION_DATA_EXPIRE)

        attempts_key = cls.get_attempts_key(phone_number, purpose)
        cache.delete(attempts_key)

        logger.info(
            "OTP with data created for %s with purpose %s", phone_number, purpose.value
        )
        return otp_code

    @classmethod
    def get_existing_otp(cls, phone_number: str, purpose: SmsPurpose):
        otp_key = cls.get_otp_key(phone_number, purpose)
        otp_data_str = cache.get(otp_key)

        if not otp_data_str:
            return None

        try:
            otp_data = json.loads(otp_data_str)
            return otp_data.get("otp_code")
        except (json.JSONDecodeError, KeyError):
            return None

    @classmethod
    def get_registration_data(cls, phone_number: str, purpose: SmsPurpose):
        registration_key = cls.get_registration_key(phone_number, purpose)
        data = cache.get(registration_key)

        if not data:
            return None

        try:
            return json.loads(data)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def get_resend_key(phone_number: str, purpose: SmsPurpose):
        return f"otp_resend:{purpose.value}:{phone_number}"

    @staticmethod
    def _normalize_phone(phone: str) -> str:
        """+998001234567 yoki +998001234567 -> +998001234567"""
        return phone.replace("+", "").replace(" ", "").strip()

    @classmethod
    def is_test_phone_for_purpose(cls, phone_number: str, purpose: SmsPurpose) -> bool:
        if purpose == SmsPurpose.LOGIN:
            configured_phone = getattr(settings, "TEST_USER_PHONE_NUMBER", None)
        elif purpose == SmsPurpose.PARTNER_LOGIN:
            configured_phone = getattr(settings, "TEST_PARTNER_PHONE_NUMBER", None)
        else:
            return False

        return bool(
            configured_phone
            and cls._normalize_phone(phone_number)
            == cls._normalize_phone(configured_phone)
        )

    @classmethod
    def verify_otp(cls, phone_number: str, otp_code: str, purpose: SmsPurpose):
        if cls.is_test_phone_for_purpose(phone_number, purpose):
            if otp_code == cls.TEST_BYPASS_OTP:
                return True, _("OTP verified successfully")
            return False, _("Invalid OTP")

        otp_key = cls.get_otp_key(phone_number, purpose)
        attempts_key = cls.get_attempts_key(phone_number, purpose)

        otp_data_str = cache.get(otp_key)
        if not otp_data_str:
            return False, _("OTP expired. Please request a new one.")

        otp_data = json.loads(otp_data_str)

        attempts = cache.get(attempts_key, 0)
        if attempts >= cls.MAX_ATTEMPTS:
            cls.invalidate_otp(phone_number, purpose)
            return False, _("Too many attempts. Please resend OTP.")

        if otp_data["otp_code"] == otp_code:
            cls.invalidate_otp(phone_number, purpose)
            return True, _("OTP verified successfully")

        cache.set(attempts_key, attempts + 1, cls.OTP_EXPIRE)
        return False, _("Invalid OTP")

    @staticmethod
    def invalidate_otp(phone_number: str, purpose: SmsPurpose):
        otp_key = OTPRedisService.get_otp_key(phone_number, purpose)
        attempts_key = OTPRedisService.get_attempts_key(phone_number, purpose)
        cache.delete(otp_key)
        cache.delete(attempts_key)
        logger.info("OTP invalidated for %s", phone_number)

    @classmethod
    def can_resend(cls, phone_number: str, purpose: SmsPurpose):
        resend_key = cls.get_resend_key(phone_number, purpose)
        return not cache.get(resend_key)

    @classmethod
    def mark_resend(cls, phone_number: str, purpose: SmsPurpose):
        resend_key = cls.get_resend_key(phone_number, purpose)
        cache.set(resend_key, 1, cls.RESEND_COOLDOWN)


class PasswordService:
    @staticmethod
    def validate_password_strength(password: str) -> bool:
        return re.fullmatch(PASSWORD_REGEX, password) is not None

    @staticmethod
    def hash_password(raw_password: str):
        return make_password(raw_password)

    @staticmethod
    def verify_password(raw_password: str, hashed_password: str):
        return check_password(raw_password, hashed_password)


def get_telegram_user_from_request(request):
    raw_init_data = request.headers.get("X-Telegram-InitData")

    if not raw_init_data:
        return None

    raw_init_data = urllib.parse.unquote(raw_init_data)
    logging.info(f"X-Telegram-InitData: %s ", raw_init_data)

    try:
        init_data = InitData.parse(raw_init_data)
        if not init_data.validate(BOT_TOKEN, lifetime=3600):
            return None
        return init_data.user
    except Exception as e:
        logger.error("Failed to parse InitData: %s", e)
        return None


class TelegramBindingService:
    @staticmethod
    def bind_partner(partner, tg_user):
        """
        Binds a Telegram user to a Partner.
        Enforces 1-to-1: If this Telegram ID was used by someone else,
        that link is broken (stolen) for the new user.
        """
        caps = table_capability_snapshot()
        if not caps.get("users_partnertelegramuser"):
            logger.info(
                "Skipping Telegram binding: users_partnertelegramuser table not present in normalized schema."
            )
            return
        # Legacy path intentionally not used while ORM tables are absent.
        logger.info("Telegram binding table exists but ORM path is disabled in raw-sql mode.")


class ClientDeviceService:
    @staticmethod
    def register_device(
        client: RawUser,
        fcm_token: str,
        device_type: str,
    ):
        if not fcm_token:
            logger.warning(
                "Client device registration skipped: empty token. client_id=%s device_type=%s",
                getattr(client, "id", None),
                device_type,
            )
            return None

        caps = table_capability_snapshot()
        if not caps.get("client_devices"):
            logger.info(
                "Client device registration skipped: client_devices table is absent in normalized schema."
            )
            return None
        logger.info("client_devices table exists but ORM path is disabled in raw-sql mode.")
        return None


class PartnerDeviceService:
    @staticmethod
    def register_device(
        partner: RawUser,
        fcm_token: str,
        device_type: str,
    ):
        if not fcm_token:
            logger.warning(
                "Partner device registration skipped: empty token. partner_id=%s device_type=%s",
                getattr(partner, "id", None),
                device_type,
            )
            return None

        caps = table_capability_snapshot()
        if not caps.get("partner_devices"):
            logger.info(
                "Partner device registration skipped: partner_devices table is absent in normalized schema."
            )
            return None
        logger.info("partner_devices table exists but ORM path is disabled in raw-sql mode.")
        return None


class TelegramService:
    RESPONSE_LOG_MAX_LENGTH = 1000

    def __init__(self, token: Optional[str] = None):
        self.token = token or BOT_TOKEN
        self.base_url = f"https://api.telegram.org/bot{self.token}"
        if not self.token:
            logger.warning(
                "TELEGRAM_BOT_TOKEN_APP/BOT_TOKEN is not configured. Telegram service calls will fail."
            )

    @classmethod
    def _response_excerpt(cls, response: Optional[requests.Response]) -> str:
        if response is None:
            return ""

        try:
            body = response.json()
            text = json.dumps(body, ensure_ascii=False)
        except ValueError:
            text = response.text or ""

        return text[: cls.RESPONSE_LOG_MAX_LENGTH]

    def send_message(self, chat_id: int, text: str):
        if not self.token:
            raise ValueError("TELEGRAM_BOT_TOKEN_APP is not configured.")

        url = f"{self.base_url}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}

        try:
            response = requests.post(url, json=payload, timeout=10)
            data = response.json()

            if not response.ok:
                # CASE: User blocked the bot (Error 403)
                if data.get("error_code") == 403:
                    logger.warning(f"Skipping: User {chat_id} blocked the bot.")
                    return False, "blocked"  # <-- Return specific status, don't raise

                # CASE: Rate Limit (Error 429) -> Raise to trigger retry
                if data.get("error_code") == 429:
                    raise Exception(f"Rate limited: {data}")

                # Other errors -> Raise to trigger retry
                raise Exception(f"Telegram API Error: {data.get('description')}")

            return True, data

        except requests.exceptions.RequestException as e:
            # Network errors -> Raise to trigger retry
            logger.error(f"Network error: {e}")
            raise e
