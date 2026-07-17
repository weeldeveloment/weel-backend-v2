import os
import logging
import uuid

import requests

from typing import Optional
from decimal import Decimal
from django.conf import settings

from dotenv import load_dotenv, find_dotenv

from shared.raw.entities import RawUser

load_dotenv(find_dotenv())

logger = logging.getLogger(__name__)



class PlumAPIError(Exception):
    def __init__(self, message, status_code=400, payload=None):
        self.message = message
        self.status_code = status_code
        self.payload = payload
        super().__init__(message)


class PlumAPIService:
    def __init__(
        self,
        auth_token: Optional[str] = None,
        api_get_all_user_cards: Optional[str] = None,
        api_add_user_card: Optional[str] = None,
        api_verify_user_card: Optional[str] = None,
        api_remove_user_card: Optional[str] = None,
        api_create_hold: Optional[str] = None,
        api_charge_hold: Optional[str] = None,
        api_dismiss_hold: Optional[str] = None,
        api_confirm_hold: Optional[str] = None,
        api_resend_otp: Optional[str] = None,
    ):
        self.auth_token = auth_token or getattr(settings, "PLUM_AUTH_TOKEN")
        self.api_get_all_user_cards = api_get_all_user_cards or os.getenv(
            "PLUM_GET_USER_CARDS"
        )
        self.api_add_user_card = api_add_user_card or os.getenv("PLUM_ADD_USER_CARD")
        self.api_verify_user_card = api_verify_user_card or os.getenv(
            "PLUM_VERIFY_USER_CARD"
        )
        self.api_remove_user_card = api_remove_user_card or os.getenv(
            "PLUM_REMOVE_USER_CARD"
        )
        self.api_create_hold = api_create_hold or os.getenv("PLUM_CREATE_HOLD")
        self.api_confirm_hold = api_confirm_hold or os.getenv("PLUM_CONFIRM_HOLD")
        self.api_dismiss_hold = api_dismiss_hold or os.getenv("PLUM_DISMISS_HOLD")
        self.api_charge_hold = api_charge_hold or os.getenv("PLUM_CHARGE_HOLD")
        self.api_resend_otp = api_resend_otp or os.getenv("PLUM_RESEND_OTP")

        if not all(
            [
                self.auth_token,
                self.api_get_all_user_cards,
                self.api_add_user_card,
                self.api_verify_user_card,
                self.api_remove_user_card,
                self.api_create_hold,
                self.api_confirm_hold,
                self.api_dismiss_hold,
                self.api_charge_hold,
                self.api_resend_otp,
            ]
        ):
            logger.error("Plum service is not fully configured")

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Basic {self.auth_token}",
                "Content-Type": "application/json",
            }
        )

    def _handle_response(self, resp: requests.Response):
        try:
            data = resp.json()
        except ValueError:
            preview = (resp.text or "")[:500]
            logger.warning(
                "Plum API returned non-JSON response: status=%s, preview=%s",
                resp.status_code,
                preview,
            )
            if resp.status_code == 403:
                message = (
                    "Plum API returned 403 Forbidden (access denied). "
                    "Typical causes: server IP not whitelisted by Plum, invalid credentials, or nginx/proxy blocking the request."
                )
            else:
                message = (
                    "Invalid response from Plum API (response is not valid JSON). "
                    "Check server logs for details."
                )
            raise PlumAPIError(
                message=message,
                status_code=resp.status_code,
                payload={"raw_preview": preview} if preview else None,
            )

        if not resp.ok:
            message = self._get_plum_error_message(data)
            raise PlumAPIError(
                message=message,
                status_code=resp.status_code,
                payload=data,
            )

        if data.get("error"):
            message = self._get_plum_error_message(data)
            raise PlumAPIError(
                message=message,
                status_code=400,
                payload=data,
            )

        return data

    @staticmethod
    def _get_plum_error_message(data: dict) -> str:
        """Plum API xato javobidan foydalanuvchiga tushunarli xabar olish."""
        if not data or not isinstance(data, dict):
            return "Plum API error"
        # errorMessage.message — asosan ruscha tushuntirish (masalan "Карты Хумо временно не принимаются")
        err_msg = data.get("errorMessage")
        if isinstance(err_msg, dict) and err_msg.get("message"):
            return str(err_msg["message"]).strip()
        # errorCode.message — kod yoki qisqa xabar
        err_code = data.get("errorCode")
        if isinstance(err_code, dict) and err_code.get("message"):
            return str(err_code["message"]).strip()
        raw_error = data.get("error") or data.get("message")
        if raw_error:
            if isinstance(raw_error, (str, int, float)):
                value = str(raw_error).strip()
                if value.isdigit():
                    return f"Plum API error (code {value})"
                return value
            return str(raw_error)

        if isinstance(err_code, dict):
            code = err_code.get("code") or err_code.get("value")
            if code is not None:
                return f"Plum API error (code {code})"
        elif err_code is not None:
            return f"Plum API error (code {err_code})"

        return "Plum API error"

    def get_client_cards(self, client: RawUser):
        try:
            resp = self.session.get(
                f"{self.api_get_all_user_cards}?userId={str(client.guid)}",
                timeout=10,
            )
            return self._handle_response(resp)

        except PlumAPIError as e:
            logger.warning(
                f"Plum API error while fetching cards for client {client.guid}: "
                f"{e.message} (status={e.status_code})"
            )
            return {"result": {"cards": []}}

        except requests.RequestException as e:
            logger.error(f"Request failed while fetching user cards: {e}")
            return {"result": {"cards": []}}

    @staticmethod
    def _normalize_card_number(value: str) -> str:
        """Faqat raqamlarni qoldiradi (bo'shliq, tire olib tashlanadi)."""
        if not value:
            return ""
        return "".join(c for c in value if c.isdigit())

    @staticmethod
    def _normalize_expire_date(value: str) -> str:
        """MM/YY yoki MM-YY ni YYMM ga o'giradi (Plum odatda YYMM talab qiladi)."""
        if not value:
            return ""
        s = value.strip().replace(" ", "").replace("/", "").replace("-", "")
        digits = "".join(c for c in s if c.isdigit())
        if len(digits) == 4:
            # MMYY yoki YYMM bo'lishi mumkin: 1228 -> 2812 (Dec 2028), 2812 -> 2812
            mm, yy = int(digits[:2]), int(digits[2:])
            if 1 <= mm <= 12 and yy >= 0 and yy <= 99:
                return f"{digits[2:]}{digits[:2]}"  # MMYY -> YYMM
            return digits  # allaqachon YYMM deb qabul qilamiz
        return digits

    @staticmethod
    def _normalize_phone_for_plum(value: str) -> str:
        """+998... ni 998... qilib qaytaradi (Plum odatda + siz)."""
        if not value:
            return ""
        return value.replace("+", "").replace(" ", "").strip()

    def add_client_card(
        self,
        client: RawUser,
        card_number: str,
        expire_date: str,
        phone_number: Optional[str] = None,
    ):
        """Add card. OTP is sent to phone_number if provided, else client.phone_number."""
        raw_phone = phone_number if phone_number else str(client.phone_number)
        user_phone = self._normalize_phone_for_plum(raw_phone)
        card_num = self._normalize_card_number(card_number)
        exp = self._normalize_expire_date(expire_date)
        payload = {
            "userId": str(client.guid),
            "cardNumber": card_num,
            "expireDate": exp,
            "userPhone": user_phone,
        }
        try:
            resp = self.session.post(self.api_add_user_card, json=payload, timeout=10)
            return self._handle_response(resp)
        except requests.RequestException as e:
            logger.error(
                f"Create User Card failed: {e} | response={getattr(e.response, 'text', None)}"
            )
            raise

    def verify_client_card(self, session: str, otp: str):
        payload = {
            "session": session,
            "otp": otp,
            "isTrusted": 1,
        }
        try:
            resp = self.session.post(
                self.api_verify_user_card, json=payload, timeout=10
            )
            return self._handle_response(resp)
        except requests.RequestException as e:
            logger.error(
                f"Verify card failed: {e} | response={getattr(e.response, 'text', None)}"
            )
            raise

    def resend_otp_client(self, session: str):
        payload = {
            "session": session,
        }
        try:
            resp = self.session.post(self.api_resend_otp, json=payload, timeout=10)
            return self._handle_response(resp)
        except requests.RequestException as e:
            logger.error(
                f"Resend OTP failed: {e} | response={getattr(e.response, 'text', None)}"
            )
            raise

    def remove_client_card(self, user_card_id: str):
        """
        user_card_id - id field from get_client_cards method:
        {
        "result": {
            "cards": [
                {
                    "id": "!!!userCardId!!!",
        """
        try:
            resp = self.session.delete(
                f"{self.api_remove_user_card}?userCardId={user_card_id}"
            )
            return self._handle_response(resp)
        except requests.RequestException as e:
            logger.error(
                f"Remove card failed: {e} | response={getattr(e.response, 'text', None)}"
            )

    def _save_transaction(
        self, result: dict, tx_type: str, status: str
    ) -> dict:
        return {
            "transactionId": str(result.get("transactionId", "")),
            "holdId": str(result.get("holdId", "")),
            "amount": Decimal(str(result.get("amount", "0"))),
            "type": tx_type,
            "status": status,
            "extraId": result.get("extraId"),
            "cardId": str(result.get("cardId", "")) if result.get("cardId") is not None else None,
        }

    def create_hold(self, client: RawUser, card_id: str, amount: Decimal):
        payload = {
            "userId": str(client.guid),
            "cardId": card_id,
            "amount": str(amount),
            "extraId": f"hold_{uuid.uuid4()}",
        }
        try:
            resp = self.session.post(self.api_create_hold, json=payload, timeout=10)
            return self._handle_response(resp)
        except requests.RequestException as e:
            logger.error(
                f"Create Hold failed: {e} | response={getattr(e.response, 'text', None)}"
            )
            raise

    def confirm_hold_and_send_sms(
        self, session: str, otp: str
    ) -> dict | None:
        payload = {"session": session, "otp": otp}

        resp = self.session.post(
            self.api_confirm_hold,
            json=payload,
            timeout=10,
        )

        data = self._handle_response(resp)

        return self._save_transaction(
            data["result"],
            "HOLD",
            "HOLD_CONFIRMED",
        )

    def charge_hold(
        self, transaction_id: str, hold_id: str, charge_amount: Decimal
    ) -> dict | None:
        payload = {
            "transactionId": transaction_id,
            "holdId": hold_id,
            "chargeAmount": str(charge_amount),
        }

        resp = self.session.post(
            self.api_charge_hold,
            json=payload,
            timeout=10,
        )

        data = self._handle_response(resp)
        result = data.get("result") or data
        if not isinstance(result, dict):
            result = {
                "transactionId": transaction_id,
                "holdId": hold_id,
                "amount": charge_amount,
            }
        else:
            result = dict(result)
            result.setdefault("transactionId", transaction_id)
            result.setdefault("holdId", hold_id)
            result.setdefault("amount", charge_amount)
        try:
            tx = self._save_transaction(
                result,
                "CHRG",
                "CHARGED",
            )
            return tx
        except (KeyError, TypeError, ValueError) as e:
            logger.warning(
                "Plum charge succeeded but response format unexpected: %s; data=%s",
                e,
                data,
            )
            return None

    def dismiss_hold(self, transaction_id: str, hold_id: str):
        payload = {
            "transactionId": transaction_id,
            "holdId": hold_id,
        }

        response = self.session.post(
            self.api_dismiss_hold,
            json=payload,
            timeout=10,
        )
        return response


plum_api_service = PlumAPIService()
