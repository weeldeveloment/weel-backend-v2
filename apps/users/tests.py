from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
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
            {"phone_number": "998901234567", "otp_code": "123456"},
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
