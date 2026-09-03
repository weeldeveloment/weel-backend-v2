from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

from django.conf import settings
from django.test import SimpleTestCase, override_settings
from django.urls import resolve
from django.utils import timezone

from rest_framework import status
from rest_framework.test import APIRequestFactory
from rest_framework_simplejwt.exceptions import TokenError

from shared.raw.entities import RawUser
from users.models.logs import SmsPurpose
from users.raw_repository import normalized_phone_candidates
from users.serializers import (
    ClientOTPLoginVerifySerializer,
    ClientOTPRegistrationVerifySerializer,
)
from users.services import EskizService, OTPRedisService
from users.tasks import send_otp_sms_eskiz, send_partner_telegram_msg
from users.views import (
    ClientSendOTPLoginView,
    ClientVerifyOTPLoginView,
    UserTokenRefreshView,
    deactivate_account,
)


def _raw_user(
    *,
    user_id: int,
    role: str,
    phone_number: str = "998901112233",
    first_name: str = "A",
    last_name: str = "B",
    username: str | None = None,
    email: str | None = None,
) -> RawUser:
    now = timezone.now()
    return RawUser(
        id=user_id,
        role=role,
        email=email,
        phone_number=phone_number,
        first_name=first_name,
        last_name=last_name,
        username=username,
        avatar=None,
        is_active=True,
        is_verified=False,
        verified_at=None,
        verified_by_user_id=None,
        created_at=now,
        updated_at=now,
        legacy_admin_id=None,
        legacy_client_id=None,
        legacy_partner_id=None,
    )


class NormalizedPhoneCandidatesTests(SimpleTestCase):
    def test_adds_plus_variant_for_plain_number(self):
        self.assertEqual(
            normalized_phone_candidates("998901234567"),
            ["998901234567", "+998901234567"],
        )

    def test_adds_plain_variant_for_plus_number(self):
        self.assertEqual(
            normalized_phone_candidates("+998901234567"),
            ["+998901234567", "998901234567"],
        )

    def test_empty_input_returns_empty_list(self):
        self.assertEqual(normalized_phone_candidates(""), [])


class OTPSerializersTests(SimpleTestCase):
    @patch("users.serializers.get_active_user_by_phone")
    @patch("users.serializers.OTPRedisService.verify_otp")
    def test_client_login_verify_accepts_otp_code_alias(
        self,
        mock_verify_otp,
        mock_get_active_user,
    ):
        mock_verify_otp.return_value = (True, "ok")
        mock_get_active_user.return_value = _raw_user(user_id=7, role="client")

        serializer = ClientOTPLoginVerifySerializer(
            data={
                "phone_number": "998901234567",
                "otp-code": "1234",
            }
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["otp_code"], "1234")
        self.assertEqual(serializer.validated_data["client"].id, 7)

    @patch("users.serializers.OTPRedisService.verify_otp")
    def test_client_login_verify_returns_error_on_invalid_otp(self, mock_verify_otp):
        mock_verify_otp.return_value = (False, "OTP is invalid")

        serializer = ClientOTPLoginVerifySerializer(
            data={"phone_number": "998901234567", "otp_code": "0000"}
        )

        self.assertFalse(serializer.is_valid())
        self.assertIn("OTP is invalid", str(serializer.errors))

    @patch("users.serializers.exists_user_by_phone")
    @patch("users.serializers.OTPRedisService.verify_otp")
    @patch("users.serializers.OTPRedisService.get_registration_data")
    def test_client_register_verify_puts_registration_data_to_validated_data(
        self,
        mock_get_registration_data,
        mock_verify_otp,
        mock_exists_user_by_phone,
    ):
        mock_get_registration_data.return_value = {
            "first_name": "Ali",
            "last_name": "Valiyev",
            "phone_number": "998901234567",
        }
        mock_verify_otp.return_value = (True, "ok")
        mock_exists_user_by_phone.return_value = False

        serializer = ClientOTPRegistrationVerifySerializer(
            data={"phone_number": "998901234567", "otp_code": "1234"}
        )

        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertIn("registration_data", serializer.validated_data)


class UserViewsTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    @patch("users.views.get_active_user_by_phone", return_value=None)
    def test_client_send_otp_login_returns_404_when_client_missing(self, _mock_get_user):
        request = self.factory.post(
            "/api/user/client/login/",
            {"phone_number": "998901234567"},
            format="json",
        )
        response = ClientSendOTPLoginView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn("detail", response.data)

    @patch("users.views.send_otp_sms_eskiz.delay")
    @patch("users.views.OTPRedisService.create_otp", return_value="123456")
    @patch("users.views.OTPRedisService.is_test_phone_for_purpose", return_value=False)
    @patch("users.views.get_active_user_by_phone")
    def test_client_send_otp_login_returns_success_payload(
        self,
        mock_get_user,
        _mock_is_test,
        _mock_create_otp,
        _mock_delay,
    ):
        mock_get_user.return_value = _raw_user(user_id=11, role="client")
        request = self.factory.post(
            "/api/user/client/login/",
            {"phone_number": "998901234567"},
            format="json",
        )

        response = ClientSendOTPLoginView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["phone_number"], "998901234567")
        self.assertIn("expires_in", response.data)

    @patch("users.views.create_client_tokens", return_value={"access": "a1", "refresh": "r1"})
    @patch("users.views.ClientOTPLoginVerifySerializer")
    def test_client_verify_login_returns_tokens_and_client_payload(
        self,
        mock_serializer_cls,
        _mock_create_tokens,
    ):
        client = _raw_user(user_id=21, role="client", first_name="Test", last_name="User")
        serializer = MagicMock()
        serializer.validated_data = {"client": client}
        serializer.is_valid.return_value = True
        mock_serializer_cls.return_value = serializer

        request = self.factory.post(
            "/api/user/client/login/verify/",
            {"phone_number": "998901234567", "otp_code": "1234"},
            format="json",
        )
        response = ClientVerifyOTPLoginView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["access"], "a1")
        self.assertEqual(response.data["refresh"], "r1")
        self.assertEqual(response.data["client"]["phone_number"], client.phone_number)

    @patch("users.views.rotate_tokens", side_effect=TokenError("bad token"))
    def test_user_token_refresh_returns_401_on_token_error(self, _mock_rotate):
        request = self.factory.post(
            "/api/user/refresh/",
            {"refresh": "bad"},
            format="json",
        )
        response = UserTokenRefreshView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertIn("detail", response.data)

    @patch("users.views.rotate_tokens", return_value={"access": "new-a", "refresh": "new-r"})
    def test_user_token_refresh_returns_new_tokens(self, _mock_rotate):
        request = self.factory.post(
            "/api/user/refresh/",
            {"refresh": "valid"},
            format="json",
        )
        response = UserTokenRefreshView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"access": "new-a", "refresh": "new-r"})


class DeactivateAccountTests(SimpleTestCase):
    @patch("users.views.soft_deactivate_user")
    @patch("users.views.CustomRefreshToken")
    def test_deactivate_account_blacklists_token_and_soft_deactivates_supported_roles(
        self,
        mock_refresh_cls,
        mock_soft_deactivate,
    ):
        user = _raw_user(user_id=31, role="partner")

        deactivate_account(user, refresh_token="refresh-token")

        mock_refresh_cls.assert_called_once_with("refresh-token")
        mock_refresh_cls.return_value.blacklist.assert_called_once()
        mock_soft_deactivate.assert_called_once_with(user)

    @patch("users.views.soft_deactivate_user")
    def test_deactivate_account_ignores_non_client_partner_roles(self, mock_soft_deactivate):
        user = _raw_user(user_id=32, role="admin")
        deactivate_account(user, refresh_token=None)
        mock_soft_deactivate.assert_not_called()


class UserTasksTests(SimpleTestCase):
    @patch("users.tasks.table_capability_snapshot", return_value={"users_smslog": True})
    @patch("users.tasks.create_sms_log")
    @patch("users.tasks.EskizService")
    def test_send_otp_sms_eskiz_logs_success_when_smslog_table_available(
        self,
        mock_eskiz_service_cls,
        mock_create_sms_log,
        _mock_caps,
    ):
        mock_eskiz_service_cls.return_value.send_sms.return_value = {"status_code": 200}

        result = send_otp_sms_eskiz(
            phone_number="998901234567",
            purpose=SmsPurpose.LOGIN,
            otp_code="123456",
        )

        self.assertEqual(result["status_code"], 200)
        mock_create_sms_log.assert_called_with(
            phone_number="998901234567",
            purpose=SmsPurpose.LOGIN,
            is_sent=True,
        )

    @patch("users.tasks.table_capability_snapshot", return_value={"users_partnertelegramuser": False})
    def test_send_partner_telegram_msg_skips_when_table_absent(self, _mock_caps):
        result = send_partner_telegram_msg(partner_id=9, message="hello")
        self.assertIn("Skipped", result)

    @patch("users.tasks.table_capability_snapshot", return_value={"users_partnertelegramuser": True})
    @patch("users.tasks.get_latest_active_partner_telegram_user", return_value=None)
    def test_send_partner_telegram_msg_skips_when_no_active_binding(
        self,
        _mock_get_tg_user,
        _mock_caps,
    ):
        result = send_partner_telegram_msg(partner_id=9, message="hello")
        self.assertIn("No active Telegram account", result)


class UsersUrlsTests(SimpleTestCase):
    def test_client_profile_url_resolves_to_correct_view(self):
        match = resolve("/api/user/client/profile/")
        self.assertEqual(match.func.view_class.__name__, "ClientProfileView")

    def test_client_logout_url_resolves_to_correct_view(self):
        match = resolve("/api/user/client/logout/")
        self.assertEqual(match.func.view_class.__name__, "ClientLogoutView")

    def test_token_refresh_url_resolves_to_correct_view(self):
        match = resolve("/api/user/refresh/")
        self.assertEqual(match.func.view_class.__name__, "UserTokenRefreshView")


class EskizServiceTests(SimpleTestCase):
    def _make_response(self, status_code, json_data=None, ok=True):
        resp = MagicMock()
        resp.status_code = status_code
        resp.ok = ok
        if json_data is not None:
            resp.json.return_value = json_data
        else:
            resp.json.side_effect = ValueError("no json")
        return resp

    @override_settings(
        ESKIZ_EMAIL="test@weel.uz",
        ESKIZ_PASSWORD="secret",
        ESKIZ_LOGIN_URL="https://notify.eskiz.uz/api/auth/login",
        ESKIZ_SMS_SEND_URL="https://notify.eskiz.uz/api/message/sms/send",
    )
    @patch("users.services.cache")
    @patch("users.services.requests.post")
    def test_send_sms_success_on_first_try(self, mock_post, mock_cache):
        mock_cache.get.return_value = "cached-token"
        mock_post.side_effect = [
            self._make_response(200, {"id": "msg-123", "status": "success"}),
        ]

        service = EskizService()
        result = service.send_sms("+998901234567", "1234")

        self.assertEqual(result["status_code"], 200)
        self.assertEqual(result["provider_message_id"], "msg-123")
        mock_post.assert_called_once()

    @override_settings(
        ESKIZ_EMAIL="test@weel.uz",
        ESKIZ_PASSWORD="secret",
        ESKIZ_LOGIN_URL="https://notify.eskiz.uz/api/auth/login",
        ESKIZ_SMS_SEND_URL="https://notify.eskiz.uz/api/message/sms/send",
    )
    @patch("users.services.cache")
    @patch("users.services.requests.post")
    def test_send_sms_retries_on_401_and_succeeds(self, mock_post, mock_cache):
        mock_cache.get.return_value = "stale-token"
        mock_post.side_effect = [
            self._make_response(401, {"error": "unauthorized"}, ok=False),
            self._make_response(200, {"id": "msg-456", "status": "waiting"}),
        ]

        service = EskizService()
        result = service.send_sms("+998901234567", "5678")

        self.assertEqual(result["provider_message_id"], "msg-456")
        self.assertEqual(mock_post.call_count, 2)
        mock_cache.delete.assert_called_once_with(EskizService.ESKIZ_TOKEN_KEY)

    @override_settings(
        ESKIZ_EMAIL="test@weel.uz",
        ESKIZ_PASSWORD="secret",
        ESKIZ_LOGIN_URL="https://notify.eskiz.uz/api/auth/login",
        ESKIZ_SMS_SEND_URL="https://notify.eskiz.uz/api/message/sms/send",
    )
    @patch("users.services.cache")
    @patch("users.services.requests.post")
    def test_send_sms_double_401_raises_value_error(self, mock_post, mock_cache):
        mock_cache.get.return_value = "stale-token"
        mock_post.side_effect = [
            self._make_response(401, {"error": "unauthorized"}, ok=False),
            self._make_response(401, {"error": "still bad"}, ok=False),
        ]

        service = EskizService()
        with self.assertRaises(ValueError):
            service.send_sms("+998901234567", "9999")

    @override_settings(
        ESKIZ_EMAIL="test@weel.uz",
        ESKIZ_PASSWORD="secret",
        ESKIZ_LOGIN_URL="https://notify.eskiz.uz/api/auth/login",
        ESKIZ_SMS_SEND_URL="https://notify.eskiz.uz/api/message/sms/send",
        ESKIZ_SENDER="WEEL",
    )
    @patch("users.services.cache")
    @patch("users.services.requests.post")
    def test_send_sms_includes_sender_in_payload(self, mock_post, mock_cache):
        mock_cache.get.return_value = "cached-token"
        mock_post.side_effect = [
            self._make_response(200, {"id": "msg-789"}),
        ]

        service = EskizService()
        service.send_sms("+998901234567", "0000")

        call_kwargs = mock_post.call_args
        sent_data = call_kwargs[1]["data"] if "data" in call_kwargs[1] else call_kwargs[0][1]
        self.assertEqual(sent_data["from"], "WEEL")

    @override_settings(
        ESKIZ_EMAIL="test@weel.uz",
        ESKIZ_PASSWORD="secret",
        ESKIZ_LOGIN_URL="https://notify.eskiz.uz/api/auth/login",
        ESKIZ_SMS_SEND_URL="https://notify.eskiz.uz/api/message/sms/send",
    )
    @patch("users.services.cache")
    @patch("users.services.requests.post")
    def test_send_sms_adds_unicode_flag_for_non_ascii(self, mock_post, mock_cache):
        mock_cache.get.return_value = "cached-token"
        mock_post.side_effect = [
            self._make_response(200, {"id": "msg-abc"}),
        ]

        service = EskizService()
        service.send_sms("+998901234567", "1234")

        call_kwargs = mock_post.call_args
        sent_data = call_kwargs[1]["data"] if "data" in call_kwargs[1] else call_kwargs[0][1]
        self.assertEqual(sent_data["unicode"], "1")

    @override_settings(
        ESKIZ_EMAIL="test@weel.uz",
        ESKIZ_PASSWORD="secret",
        ESKIZ_LOGIN_URL="https://notify.eskiz.uz/api/auth/login",
        ESKIZ_SMS_SEND_URL="https://notify.eskiz.uz/api/message/sms/send",
    )
    @patch("users.services.cache")
    @patch("users.services.requests.post")
    def test_send_text_sms_reuses_send_sms_with_empty_code(self, mock_post, mock_cache):
        mock_cache.get.return_value = "cached-token"
        mock_post.side_effect = [
            self._make_response(200, {"id": "msg-def"}),
        ]

        service = EskizService()
        result = service.send_text_sms("+998901234567", "Hello plain text")

        self.assertEqual(result["provider_message_id"], "msg-def")
        call_kwargs = mock_post.call_args
        sent_data = call_kwargs[1]["data"] if "data" in call_kwargs[1] else call_kwargs[0][1]
        self.assertEqual(sent_data["message"], "Hello plain text")

    @override_settings(
        ESKIZ_EMAIL="test@weel.uz",
        ESKIZ_PASSWORD="secret",
        ESKIZ_LOGIN_URL="https://notify.eskiz.uz/api/auth/login",
        ESKIZ_SMS_SEND_URL="https://notify.eskiz.uz/api/message/sms/send",
    )
    @patch("users.services.cache")
    @patch("users.services.requests.post")
    def test_otp_sms_body_matches_the_moderated_template(self, mock_post, mock_cache):
        """The wording Eskiz approved, character for character.

        Their moderation matches the whole body. A message that drifts from the
        registered template is refused at the gateway and the user never gets a
        code, so this is worth pinning even though it looks like a test of a
        string literal: the literal *is* the contract.

        Registered as: Код верификации для входа в приложение WEEL - 000000
        """
        mock_cache.get.return_value = "cached-token"
        mock_post.side_effect = [self._make_response(200, {"id": "msg-tpl"})]

        service = EskizService()
        service.send_sms("+998901234567", "482913")

        call_kwargs = mock_post.call_args
        sent_data = call_kwargs[1]["data"] if "data" in call_kwargs[1] else call_kwargs[0][1]
        self.assertEqual(
            sent_data["message"],
            "Код верификации для входа в приложение WEEL - 482913",
        )

    def test_otp_template_placeholder_is_as_wide_as_the_b2b_code(self):
        """The newly registered template reserves six digits, which is b2b's.

        Eskiz moderates the whole body, so each code length is its own
        template: the four-digit one was approved long ago and still carries
        the client and partner flows, and `000000` is the b2b one. Generating
        a b2b code of any other width would produce a body matching neither.
        """
        approved_placeholder = "000000"
        self.assertEqual(
            len(approved_placeholder),
            OTPRedisService.otp_length(SmsPurpose.B2B_LOGIN),
        )
        self.assertEqual(
            settings.ESKIZ_OTP_TEMPLATE.format(code=approved_placeholder),
            "Код верификации для входа в приложение WEEL - 000000",
        )

    def test_only_b2b_gets_the_longer_code(self):
        """Six digits for b2b, four everywhere else.

        The b2b dashboard and the b2b mobile app were rebuilt for six together.
        The client and partner frontends were not, and their serializers pin
        `min_length == max_length == OTP_LENGTH` — so a six-digit code on those
        purposes would not be a nicer code, it would be a lockout.
        """
        self.assertEqual(OTPRedisService.otp_length(SmsPurpose.B2B_LOGIN), 6)
        for purpose in (
            SmsPurpose.LOGIN,
            SmsPurpose.REGISTER,
            SmsPurpose.PARTNER_LOGIN,
            SmsPurpose.PARTNER_REGISTER,
            SmsPurpose.ACCOUNT_DELETE,
        ):
            with self.subTest(purpose=purpose):
                self.assertEqual(OTPRedisService.otp_length(purpose), 4)

    def test_generated_code_is_as_long_as_its_purpose_asks(self):
        self.assertEqual(len(OTPRedisService.generate_otp(SmsPurpose.B2B_LOGIN)), 6)
        self.assertEqual(len(OTPRedisService.generate_otp(SmsPurpose.LOGIN)), 4)
        # No purpose at all is the legacy length, not a crash: `generate_otp`
        # is a classmethod old code may still call bare.
        self.assertEqual(len(OTPRedisService.generate_otp()), 4)

    def test_the_bypass_code_matches_its_purpose_width(self):
        """A bypass of the wrong width is rejected on length, never compared."""
        self.assertEqual(OTPRedisService.test_bypass_otp(SmsPurpose.B2B_LOGIN), "000000")
        self.assertEqual(OTPRedisService.test_bypass_otp(SmsPurpose.LOGIN), "0000")

    def test_a_ready_made_message_bypasses_the_otp_template(self):
        """`send_text_sms` carries its own wording — reminders are not codes."""
        service = EskizService(otp_template="unused {code}")
        self.assertEqual(service.otp_template, "unused {code}")

    def test_provider_accepts_message_with_id(self):
        self.assertTrue(EskizService._provider_accepts_message({"id": "abc"}))

    def test_provider_accepts_message_with_success_status(self):
        self.assertTrue(EskizService._provider_accepts_message({"status": "success"}))

    def test_provider_accepts_message_with_data_id(self):
        self.assertTrue(EskizService._provider_accepts_message({"data": {"id": "xyz"}}))

    def test_provider_rejects_empty_body(self):
        self.assertFalse(EskizService._provider_accepts_message({}))

    def test_provider_rejects_none(self):
        self.assertFalse(EskizService._provider_accepts_message(None))


class EskizAppHashTests(SimpleTestCase):
    """The SMS Retriever suffix that makes Android autofill tap-free."""

    def _service(self):
        return EskizService()

    def test_absent_by_default(self):
        # Off until Eskiz has moderated a template carrying the line; sending
        # it early gets the message refused and the user no code at all.
        with override_settings(ESKIZ_ANDROID_APP_HASH=""):
            self.assertEqual(self._service()._with_app_hash("Код - 123456"), "Код - 123456")

    @override_settings(ESKIZ_ANDROID_APP_HASH="FA+9qCX9VSu")
    def test_appended_on_its_own_last_line(self):
        # Retriever only matches a hash at the end of the body.
        self.assertEqual(
            self._service()._with_app_hash("Код - 123456"),
            "Код - 123456\nFA+9qCX9VSu",
        )

    @override_settings(ESKIZ_ANDROID_APP_HASH=" FA+9qCX9VSu , 3W6vLmQ2xYz ")
    def test_both_apps_share_one_line(self):
        # One moderated template serves two separately signed b2b apps.
        self.assertEqual(
            self._service()._with_app_hash("Код - 123456"),
            "Код - 123456\nFA+9qCX9VSu 3W6vLmQ2xYz",
        )

    @override_settings(
        ESKIZ_ANDROID_APP_HASH="FA+9qCX9VSu",
        ESKIZ_EMAIL="test@weel.uz",
        ESKIZ_PASSWORD="secret",
        ESKIZ_LOGIN_URL="https://notify.eskiz.uz/api/auth/login",
        ESKIZ_SMS_SEND_URL="https://notify.eskiz.uz/api/message/sms/send",
    )
    @patch("users.services.cache")
    @patch("users.services.requests.post")
    def test_plain_text_sms_is_left_alone(self, mock_post, mock_cache):
        # `send_text_sms` is not an OTP and no app is listening for it.
        mock_cache.get.return_value = "cached-token"
        mock_post.return_value = MagicMock(
            status_code=200, ok=True, **{"json.return_value": {"status": "success"}}
        )

        self._service().send_text_sms("+998901234567", "Buyurtmangiz tayyor")

        self.assertEqual(
            mock_post.call_args.kwargs["data"]["message"], "Buyurtmangiz tayyor"
        )
