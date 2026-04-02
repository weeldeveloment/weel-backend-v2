# Users app tests — main code unchanged

import logging
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.db.utils import ProgrammingError
from django.utils import timezone

from rest_framework import status
from rest_framework.test import APIClient, APIRequestFactory, force_authenticate
from rest_framework_simplejwt.tokens import AccessToken

from .models import Client, Partner
from .models.clients import ClientDevice, ClientSession
from .models.partners import (
    PartnerDevice,
    PartnerSession,
    PartnerDocument,
    DocumentType,
    PartnerTelegramUser,
)
from .models.logs import SmsLog, SmsPurpose
from .tokens import (
    TokenMetadata,
    get_user_ip,
    create_client_tokens,
    create_partner_tokens,
    rotate_tokens,
    decode_token,
)
from .serializers import (
    UserPhoneNumberSerializer,
    ResendOTPSerializer,
    ClientRegisterSerializer,
    ClientProfileSerializer,
    PartnerOTPRegisterSerializer,
    PartnerProfileSerializer,
    PartnerPassportUploadSerializer,
    TokenRefreshSerializer,
)
from .views import (
    ClientProfileView,
    ClientLogoutView,
    UserTokenRefreshView,
    OwnAccountView,
    deactivate_account,
)
from .authentication import ClientJWTAuthentication, PartnerJWTAuthentication
from property.models import Property, PropertyLocation, PropertyType

logging.getLogger("django.request").setLevel(logging.ERROR)


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────


def make_client(**kwargs):
    defaults = {
        "first_name": "Test",
        "last_name": "Client",
        "phone_number": f"+99890{uuid.uuid4().int % 10**7:07d}",
        "is_active": True,
    }
    defaults.update(kwargs)
    return Client.objects.create(**defaults)


def make_partner(**kwargs):
    defaults = {
        "first_name": "Test",
        "last_name": "Partner",
        "username": f"user_{uuid.uuid4().hex[:8]}",
        "phone_number": f"+99891{uuid.uuid4().int % 10**7:07d}",
        "is_active": True,
    }
    defaults.update(kwargs)
    return Partner.objects.create(**defaults)


# ──────────────────────────────────────────────
# Model tests
# ──────────────────────────────────────────────


class ClientModelTests(TestCase):
    def test_client_str(self):
        c = make_client(first_name="John", last_name="Doe")
        self.assertIn("John", str(c))
        self.assertIn("Doe", str(c))

    def test_client_repr(self):
        c = make_client(phone_number="+998901234567")
        self.assertIn("998901234567", repr(c))


class PartnerModelTests(TestCase):
    def test_partner_str(self):
        p = make_partner(first_name="Jane", last_name="Smith", username="janesmith")
        self.assertIn("Jane", str(p))
        self.assertIn("Smith", str(p))
        self.assertIn("janesmith", str(p))

    def test_document_type_choices(self):
        self.assertEqual(DocumentType.CERTIFICATE.value, "CERT")
        self.assertEqual(DocumentType.PASSPORT.value, "PASS")


class ClientDeviceModelTests(TestCase):
    def test_client_device_str(self):
        client = make_client()
        device = ClientDevice.objects.create(
            client=client,
            fcm_token=f"token_{uuid.uuid4().hex}",
            device_type=ClientDevice.ClientDeviceType.ANDROID,
        )
        self.assertIn("Client", str(device))
        self.assertIn("android", str(device))


class ClientSessionModelTests(TestCase):
    def test_client_session_str(self):
        client = make_client(first_name="Alice")
        session = ClientSession.objects.create(
            client=client,
            user_agent="Test",
            last_ip="127.0.0.1",
        )
        self.assertIn("Alice", str(session))


class SmsLogModelTests(TestCase):
    def test_sms_log_create(self):
        log = SmsLog.objects.create(
            phone_number="+998901234567",
            purpose=SmsPurpose.LOGIN,
            is_sent=True,
        )
        self.assertEqual(log.phone_number, "+998901234567")
        self.assertEqual(log.purpose, SmsPurpose.LOGIN)
        self.assertTrue(log.is_sent)

    def test_sms_purpose_values(self):
        self.assertEqual(SmsPurpose.LOGIN.value, "CL_LGN")
        self.assertEqual(SmsPurpose.REGISTER.value, "CL_RGR")
        self.assertEqual(SmsPurpose.PARTNER_LOGIN.value, "PR_LGN")
        self.assertEqual(SmsPurpose.PARTNER_REGISTER.value, "PR_RGR")
        self.assertEqual(SmsPurpose.PARTNER_PROPERTY_REMINDER.value, "PR_RMD")


# ──────────────────────────────────────────────
# Token tests
# ──────────────────────────────────────────────


class TokenMetadataTests(TestCase):
    def test_token_metadata_constants(self):
        self.assertEqual(TokenMetadata.TOKEN_TYPE_CLAIM, "type")
        self.assertEqual(TokenMetadata.TOKEN_SUBJECT, "sub")
        self.assertEqual(TokenMetadata.TOKEN_ISSUER, "iss")
        self.assertEqual(TokenMetadata.TOKEN_USER_TYPE, "user_type")


class GetUserIpTests(TestCase):
    def test_get_user_ip_from_x_forwarded_for(self):
        request = APIRequestFactory().get("/")
        request.META["HTTP_X_FORWARDED_FOR"] = "1.2.3.4, 5.6.7.8"
        self.assertEqual(get_user_ip(request), "1.2.3.4")

    def test_get_user_ip_from_remote_addr(self):
        request = APIRequestFactory().get("/")
        request.META["REMOTE_ADDR"] = "192.168.1.1"
        self.assertEqual(get_user_ip(request), "192.168.1.1")


class CreateClientTokensTests(TestCase):
    def test_create_client_tokens_returns_access_and_refresh(self):
        client = make_client()
        request = APIRequestFactory().post("/")
        request.META["REMOTE_ADDR"] = "127.0.0.1"
        request.META["HTTP_USER_AGENT"] = "Test"
        result = create_client_tokens(client, request)
        self.assertIn("access", result)
        self.assertIn("refresh", result)
        self.assertTrue(len(result["access"]) > 0)
        self.assertTrue(len(result["refresh"]) > 0)
        self.assertEqual(ClientSession.objects.filter(client=client).count(), 1)


class CreatePartnerTokensTests(TestCase):
    def test_create_partner_tokens_returns_access_and_refresh(self):
        partner = make_partner()
        request = APIRequestFactory().post("/")
        request.META["REMOTE_ADDR"] = "127.0.0.1"
        request.META["HTTP_USER_AGENT"] = "Test"
        result = create_partner_tokens(partner, request)
        self.assertIn("access", result)
        self.assertIn("refresh", result)
        self.assertEqual(PartnerSession.objects.filter(partner=partner).count(), 1)


class RotateTokensTests(TestCase):
    def test_rotate_tokens_returns_new_tokens(self):
        client = make_client()
        request = APIRequestFactory().post("/")
        request.META["REMOTE_ADDR"] = "127.0.0.1"
        request.META["HTTP_USER_AGENT"] = "Test"
        first = create_client_tokens(client, request)
        refresh_token = first["refresh"]
        new_tokens = rotate_tokens(refresh_token)
        self.assertIn("access", new_tokens)
        self.assertIn("refresh", new_tokens)
        self.assertNotEqual(new_tokens["access"], first["access"])
        self.assertNotEqual(new_tokens["refresh"], first["refresh"])

    def test_rotate_tokens_invalid_raises(self):
        with self.assertRaises(ValueError):
            rotate_tokens("invalid-token")


class DecodeTokenTests(TestCase):
    def test_decode_token_valid_returns_payload(self):
        client = make_client()
        access = AccessToken()
        access[TokenMetadata.TOKEN_SUBJECT] = str(client.guid)
        access[TokenMetadata.TOKEN_USER_TYPE] = "client"
        access[TokenMetadata.TOKEN_TYPE_CLAIM] = "access"
        payload = decode_token(str(access))
        self.assertEqual(payload.get(TokenMetadata.TOKEN_SUBJECT), str(client.guid))
        self.assertEqual(payload.get(TokenMetadata.TOKEN_USER_TYPE), "client")

    def test_decode_token_invalid_raises(self):
        from rest_framework_simplejwt.exceptions import InvalidToken
        with self.assertRaises(InvalidToken):
            decode_token("not.a.token")


# ──────────────────────────────────────────────
# Serializer tests
# ──────────────────────────────────────────────


class UserPhoneNumberSerializerTests(TestCase):
    def test_phone_must_start_with_998(self):
        serializer = UserPhoneNumberSerializer(data={"phone_number": "712345678"})
        self.assertFalse(serializer.is_valid())
        self.assertIn("phone_number", serializer.errors)

    def test_phone_998_valid(self):
        serializer = UserPhoneNumberSerializer(data={"phone_number": "998901234567"})
        self.assertTrue(serializer.is_valid())
        self.assertEqual(serializer.validated_data["phone_number"], "998901234567")


class ResendOTPSerializerTests(TestCase):
    def test_phone_must_start_with_998(self):
        serializer = ResendOTPSerializer(data={"phone_number": "712345678"})
        self.assertFalse(serializer.is_valid())
        self.assertIn("phone_number", serializer.errors)


class ClientRegisterSerializerTests(TestCase):
    def test_phone_already_exists_raises(self):
        make_client(phone_number="+998901234567")
        serializer = ClientRegisterSerializer(
            data={
                "phone_number": "+998901234567",
                "first_name": "New",
                "last_name": "User",
            }
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("phone_number", serializer.errors)

    def test_phone_unique_valid(self):
        serializer = ClientRegisterSerializer(
            data={
                "phone_number": f"+99890{uuid.uuid4().int % 10**7:07d}",
                "first_name": "New",
                "last_name": "User",
            }
        )
        self.assertTrue(serializer.is_valid())


class ClientProfileSerializerTests(TestCase):
    def test_profile_serializer_fields(self):
        client = make_client(first_name="John", last_name="Doe")
        serializer = ClientProfileSerializer(client)
        self.assertEqual(serializer.data["first_name"], "John")
        self.assertEqual(serializer.data["last_name"], "Doe")
        self.assertEqual(serializer.data["phone_number"], client.phone_number)
        self.assertIn("id", serializer.data)
        self.assertIn("avatar", serializer.data)


class PartnerOTPRegisterSerializerTests(TestCase):
    def test_phone_already_exists_raises(self):
        make_partner(phone_number="+998901234567")
        serializer = PartnerOTPRegisterSerializer(
            data={
                "phone_number": "+998901234567",
                "username": "newuser",
                "first_name": "New",
                "last_name": "Partner",
            }
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("phone_number", serializer.errors)

    def test_username_already_exists_raises(self):
        make_partner(username="takenuser")
        serializer = PartnerOTPRegisterSerializer(
            data={
                "phone_number": f"+99890{uuid.uuid4().int % 10**7:07d}",
                "username": "takenuser",
                "first_name": "New",
                "last_name": "Partner",
            }
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("username", serializer.errors)


class PartnerProfileSerializerTests(TestCase):
    def test_partner_profile_serializer_fields(self):
        partner = make_partner(username="partner1", first_name="Jane")
        serializer = PartnerProfileSerializer(partner)
        self.assertEqual(serializer.data["username"], "partner1")
        self.assertEqual(serializer.data["first_name"], "Jane")
        self.assertIn("id", serializer.data)
        self.assertIn("phone_number", serializer.data)
        self.assertIn("created_at", serializer.data)


class PartnerPassportUploadSerializerTests(TestCase):
    def test_document_size_over_5mb_raises(self):
        partner = make_partner()
        big_file = SimpleUploadedFile("pass.pdf", b"x" * (5 * 1024 * 1024 + 1), content_type="application/pdf")
        serializer = PartnerPassportUploadSerializer(
            data={"document": big_file},
            context={"partner": partner},
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("document", serializer.errors)


class TokenRefreshSerializerTests(TestCase):
    def test_refresh_required(self):
        serializer = TokenRefreshSerializer(data={})
        self.assertFalse(serializer.is_valid())
        self.assertIn("refresh", serializer.errors)


# ──────────────────────────────────────────────
# View / API tests
# ──────────────────────────────────────────────


class ClientProfileViewTests(TestCase):
    def test_client_profile_unauthenticated_returns_401_or_403(self):
        client = APIClient()
        response = client.get("/api/user/client/profile/")
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_client_profile_authenticated_returns_200(self):
        client_user = make_client(first_name="Profile", last_name="User")
        access = AccessToken()
        access[TokenMetadata.TOKEN_SUBJECT] = str(client_user.guid)
        access[TokenMetadata.TOKEN_ISSUER] = getattr(settings, "JWT_ISSUER", "weel")
        access[TokenMetadata.TOKEN_USER_TYPE] = "client"
        access[TokenMetadata.TOKEN_TYPE_CLAIM] = "access"
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {str(access)}")
        response = api.get("/api/user/client/profile/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["first_name"], "Profile")
        self.assertEqual(response.data["last_name"], "User")


class PartnerProfileDeleteViewTests(TestCase):
    def test_partner_profile_delete_unauthenticated_returns_401_or_403(self):
        api = APIClient()
        response = api.delete("/api/user/partner/profile/")
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_partner_profile_delete_deactivates_partner_and_cleans_sessions_devices(self):
        partner_user = make_partner(first_name="Delete", last_name="Me")
        device = PartnerDevice.objects.create(
            partner=partner_user,
            fcm_token=f"token_{uuid.uuid4().hex}",
            device_type=PartnerDevice.PartnerDeviceType.ANDROID,
            is_active=True,
        )
        PartnerSession.objects.create(
            partner=partner_user,
            user_agent="Test",
            last_ip="127.0.0.1",
        )
        PartnerTelegramUser.objects.create(
            partner=partner_user,
            telegram_user_id=10_000 + (uuid.uuid4().int % 1_000_000),
            is_active=True,
        )

        access = AccessToken()
        access[TokenMetadata.TOKEN_SUBJECT] = str(partner_user.guid)
        access[TokenMetadata.TOKEN_ISSUER] = getattr(settings, "JWT_ISSUER", "weel")
        access[TokenMetadata.TOKEN_USER_TYPE] = "partner"
        access[TokenMetadata.TOKEN_TYPE_CLAIM] = "access"
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {str(access)}")

        response = api.delete("/api/user/partner/profile/", data={}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(Partner.objects.filter(id=partner_user.id).exists())
        self.assertFalse(PartnerDevice.objects.filter(id=device.id).exists())
        self.assertFalse(PartnerSession.objects.filter(partner=partner_user).exists())
        self.assertFalse(
            PartnerTelegramUser.objects.filter(partner=partner_user, is_active=True).exists()
        )


class ClientLogoutViewTests(TestCase):
    def test_logout_without_token_returns_200(self):
        api = APIClient()
        response = api.post("/api/user/client/logout/", data={}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("detail", response.data)

    def test_logout_with_invalid_token_returns_400(self):
        api = APIClient()
        response = api.post(
            "/api/user/client/logout/",
            data={"refresh": "invalid.refresh.token"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class UserTokenRefreshViewTests(TestCase):
    def test_refresh_with_invalid_token_returns_4xx(self):
        api = APIClient()
        response = api.post(
            "/api/user/refresh/",
            data={"refresh": "invalid.refresh.token"},
            format="json",
        )
        self.assertIn(
            response.status_code,
            (status.HTTP_400_BAD_REQUEST, status.HTTP_401_UNAUTHORIZED),
            "Invalid refresh token must return 400 or 401",
        )


class OwnAccountViewTests(TestCase):
    def test_own_account_delete_unauthenticated_returns_401_or_403(self):
        api = APIClient()
        response = api.delete("/api/user/account/")
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_own_account_delete_as_client_removes_client(self):
        client_user = make_client(first_name="ToDeactivate", last_name="Client")
        access = AccessToken()
        access[TokenMetadata.TOKEN_SUBJECT] = str(client_user.guid)
        access[TokenMetadata.TOKEN_ISSUER] = getattr(settings, "JWT_ISSUER", "weel")
        access[TokenMetadata.TOKEN_USER_TYPE] = "client"
        access[TokenMetadata.TOKEN_TYPE_CLAIM] = "access"
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {str(access)}")
        response = api.delete("/api/user/account/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(Client.objects.filter(id=client_user.id).exists())

    def test_own_account_delete_as_partner_removes_partner(self):
        partner_user = make_partner(first_name="ToDeactivate", last_name="Partner")
        access = AccessToken()
        access[TokenMetadata.TOKEN_SUBJECT] = str(partner_user.guid)
        access[TokenMetadata.TOKEN_ISSUER] = getattr(settings, "JWT_ISSUER", "weel")
        access[TokenMetadata.TOKEN_USER_TYPE] = "partner"
        access[TokenMetadata.TOKEN_TYPE_CLAIM] = "access"
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f"Bearer {str(access)}")
        response = api.delete("/api/user/account/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(Partner.objects.filter(id=partner_user.id).exists())


class DeactivateAccountFallbackTests(TestCase):
    def test_partner_soft_deactivates_when_orm_and_raw_sql_delete_fail(self):
        partner_user = make_partner(phone_number="+998911234567")
        original_phone = partner_user.phone_number

        with patch("users.views.Partner.delete", side_effect=ProgrammingError("schema mismatch")):
            from django.db.backends.utils import CursorWrapper
            original_execute = CursorWrapper.execute

            def _execute(self, sql, params=None):
                if isinstance(sql, str) and "DELETE FROM users_partner" in sql:
                    raise IntegrityError("fk violation")
                return original_execute(self, sql, params)

            with patch("django.db.backends.utils.CursorWrapper.execute", new=_execute):
                deactivate_account(partner_user)

        partner_user.refresh_from_db()
        self.assertFalse(partner_user.is_active)
        self.assertNotEqual(partner_user.phone_number, original_phone)


# ──────────────────────────────────────────────
# Authentication tests
# ──────────────────────────────────────────────


class ClientJWTAuthenticationTests(TestCase):
    def test_no_header_returns_none(self):
        request = APIRequestFactory().get("/")
        auth = ClientJWTAuthentication()
        result = auth.authenticate(request)
        self.assertIsNone(result)


class PartnerJWTAuthenticationTests(TestCase):
    def test_no_header_returns_none(self):
        request = APIRequestFactory().get("/")
        auth = PartnerJWTAuthentication()
        result = auth.authenticate(request)
        self.assertIsNone(result)


# ──────────────────────────────────────────────
# Service tests (mocked)
# ──────────────────────────────────────────────


class OTPRedisServiceTests(TestCase):
    def test_get_otp_key(self):
        from .services import OTPRedisService
        key = OTPRedisService.get_otp_key("998901234567", SmsPurpose.LOGIN)
        self.assertIn("CL_LGN", key)
        self.assertIn("998901234567", key)

    def test_generate_otp_length(self):
        from .services import OTPRedisService
        code = OTPRedisService.generate_otp()
        self.assertEqual(len(code), OTPRedisService.OTP_LENGTH)
        self.assertTrue(code.isdigit())


class EskizServiceTests(TestCase):
    def test_mask_phone(self):
        from .services import EskizService
        # +998901234567 has 13 chars -> 9 stars + last 4 digits
        self.assertEqual(EskizService._mask_phone("+998901234567"), "*********4567")
        self.assertEqual(EskizService._mask_phone(""), "unknown")


# ──────────────────────────────────────────────
# Task tests (mocked)
# ──────────────────────────────────────────────


class SendOtpSmsEskizTaskTests(TestCase):
    @patch("users.tasks.EskizService")
    @patch("users.tasks.OTPRedisService.get_existing_otp")
    def test_send_otp_sms_eskiz_calls_eskiz_send_when_otp_provided(self, mock_get_otp, mock_eskiz_class):
        from users.tasks import send_otp_sms_eskiz
        mock_eskiz_class.return_value.send_sms.return_value = {"status_code": 200}
        result = send_otp_sms_eskiz("+998901234567", SmsPurpose.LOGIN, otp_code="1234")
        mock_eskiz_class.return_value.send_sms.assert_called_once_with(
            "+998901234567", "1234", None
        )
        self.assertEqual(result.get("status_code"), 200)


class SendPartnerTelegramMsgTaskTests(TestCase):
    def test_send_partner_telegram_msg_skipped_when_no_tg_user(self):
        from users.tasks import send_partner_telegram_msg
        partner = make_partner()
        result = send_partner_telegram_msg(partner.id, "Hello")
        self.assertIn("Skipped", result)
        self.assertIn("No active Telegram", result)


class SendPartnerPropertyReminderTaskTests(TestCase):
    @staticmethod
    def _svg_file(name="icon.svg"):
        return SimpleUploadedFile(
            name,
            b'<svg xmlns="http://www.w3.org/2000/svg"></svg>',
            content_type="image/svg+xml",
        )

    def _create_property_for_partner(self, partner):
        property_type = PropertyType.objects.create(
            title_en=f"Reminder type {uuid.uuid4().hex[:6]}",
            title_ru="Тип",
            title_uz="Tur",
            icon=self._svg_file(),
        )
        location = PropertyLocation.objects.create(
            latitude="41.2995",
            longitude="69.2401",
            city="Tashkent",
            country="Uzbekistan",
        )
        return Property.objects.create(
            title=f"Reminder property {uuid.uuid4().hex[:6]}",
            property_type=property_type,
            property_location=location,
            partner=partner,
            is_archived=False,
        )

    @patch("users.tasks.EskizService")
    def test_send_partner_property_check_reminders_sends_sms_for_partner_with_property(
        self, mock_eskiz_class
    ):
        from users.tasks import (
            PARTNER_PROPERTY_CHECK_REMINDER_TEXT,
            send_partner_property_check_reminders,
        )

        partner = make_partner(phone_number="+998901111111")
        self._create_property_for_partner(partner)

        mock_eskiz = mock_eskiz_class.return_value
        mock_eskiz.send_text_sms.return_value = {"status_code": 200}

        result = send_partner_property_check_reminders()

        mock_eskiz.send_text_sms.assert_called_once_with(
            phone_number=partner.phone_number,
            message=PARTNER_PROPERTY_CHECK_REMINDER_TEXT,
        )
        self.assertTrue(
            SmsLog.objects.filter(
                phone_number=partner.phone_number,
                purpose=SmsPurpose.PARTNER_PROPERTY_REMINDER,
                is_sent=True,
            ).exists()
        )
        self.assertEqual(result["sent"], 1)
        self.assertEqual(result["failed"], 0)

    @patch("users.tasks.EskizService")
    def test_send_partner_property_check_reminders_skips_recent_sms(
        self, mock_eskiz_class
    ):
        from users.tasks import send_partner_property_check_reminders

        partner = make_partner(phone_number="+998902222222")
        self._create_property_for_partner(partner)
        SmsLog.objects.create(
            phone_number=partner.phone_number,
            purpose=SmsPurpose.PARTNER_PROPERTY_REMINDER,
            is_sent=True,
        )

        result = send_partner_property_check_reminders()

        mock_eskiz_class.return_value.send_text_sms.assert_not_called()
        self.assertEqual(result["sent"], 0)
        self.assertEqual(result["skipped_recent"], 1)

    @patch("users.tasks.EskizService")
    def test_send_partner_property_check_reminders_sends_again_after_3_days(
        self, mock_eskiz_class
    ):
        from users.tasks import send_partner_property_check_reminders

        partner = make_partner(phone_number="+998903333333")
        self._create_property_for_partner(partner)
        old_log = SmsLog.objects.create(
            phone_number=partner.phone_number,
            purpose=SmsPurpose.PARTNER_PROPERTY_REMINDER,
            is_sent=True,
        )
        SmsLog.objects.filter(pk=old_log.pk).update(
            created_at=timezone.now() - timedelta(days=4)
        )

        mock_eskiz_class.return_value.send_text_sms.return_value = {"status_code": 200}

        result = send_partner_property_check_reminders()

        mock_eskiz_class.return_value.send_text_sms.assert_called_once()
        self.assertEqual(result["sent"], 1)


# ──────────────────────────────────────────────
# URL tests
# ──────────────────────────────────────────────


class UsersUrlTests(TestCase):
    def test_client_profile_resolves(self):
        from django.urls import resolve
        match = resolve("/api/user/client/profile/")
        self.assertEqual(match.func.view_class, ClientProfileView)

    def test_client_logout_resolves(self):
        from django.urls import resolve
        match = resolve("/api/user/client/logout/")
        self.assertEqual(match.func.view_class, ClientLogoutView)

    def test_refresh_resolves(self):
        from django.urls import resolve
        match = resolve("/api/user/refresh/")
        self.assertEqual(match.func.view_class, UserTokenRefreshView)

    def test_own_account_resolves(self):
        from django.urls import resolve
        match = resolve("/api/user/account/")
        self.assertEqual(match.func.view_class, OwnAccountView)
