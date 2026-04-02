"""
Tests for the notification app: models, serializers, service (mocked FCM), views, tasks.
"""

import logging
from unittest.mock import patch, MagicMock

from django.test import TestCase

from rest_framework import status
from rest_framework.test import APIClient

from users.models.clients import Client

from .models import Notification
from .serializers import ClientDeviceSerializer
from .service import NotificationService, FCMService

# Less log noise when running notification tests
logging.getLogger("notification.service").setLevel(logging.WARNING)
logging.getLogger("django.request").setLevel(logging.ERROR)


# ──────────────────────────────────────────────
# Test helpers
# ──────────────────────────────────────────────


def make_client(**kwargs):
    defaults = {
        "first_name": "Test",
        "last_name": "Client",
        "phone_number": "+998901234567",
        "is_active": True,
    }
    defaults.update(kwargs)
    return Client.objects.create(**defaults)


# ──────────────────────────────────────────────
# 1. Model tests
# ──────────────────────────────────────────────


class NotificationModelTests(TestCase):
    def test_create_notification_with_recipient(self):
        client = make_client()
        notification = Notification.objects.create(
            recipient=client,
            title="Test",
            push_message="Body",
            notification_type=Notification.NotificationType.SYSTEM,
            status=Notification.Status.PENDING,
            is_for_every_one=False,
        )
        self.assertEqual(notification.recipient, client)
        self.assertFalse(notification.is_for_every_one)
        self.assertEqual(notification.status, Notification.Status.PENDING)

    def test_create_broadcast_notification_recipient_null(self):
        notification = Notification.objects.create(
            recipient=None,
            title="Broadcast",
            push_message="Hello all",
            notification_type=Notification.NotificationType.SYSTEM,
            status=Notification.Status.PENDING,
            is_for_every_one=True,
        )
        self.assertIsNone(notification.recipient)
        self.assertTrue(notification.is_for_every_one)

    def test_recipient_consistency_constraint_violation(self):
        from django.db import IntegrityError
        client = make_client()
        with self.assertRaises(IntegrityError):
            Notification.objects.create(
                recipient=client,
                title="Bad",
                push_message="Bad",
                notification_type=Notification.NotificationType.SYSTEM,
                is_for_every_one=True,  # must be recipient=None
            )

    def test_notification_str(self):
        client = make_client()
        notification = Notification.objects.create(
            recipient=client,
            title="Title",
            push_message="Msg",
            notification_type=Notification.NotificationType.REMINDER,
            is_for_every_one=False,
        )
        self.assertIn("Title", str(notification))


# ──────────────────────────────────────────────
# 2. Serializer tests
# ──────────────────────────────────────────────


class ClientDeviceSerializerTests(TestCase):
    def test_valid_data_ios(self):
        s = ClientDeviceSerializer(data={"fcm_token": "token123", "device_type": "ios"})
        self.assertTrue(s.is_valid(), s.errors)

    def test_valid_data_android(self):
        s = ClientDeviceSerializer(
            data={"fcm_token": "token456", "device_type": "android"}
        )
        self.assertTrue(s.is_valid(), s.errors)

    def test_invalid_device_type(self):
        s = ClientDeviceSerializer(
            data={"fcm_token": "token", "device_type": "windows"}
        )
        self.assertFalse(s.is_valid())
        self.assertIn("device_type", s.errors)

    def test_missing_fcm_token(self):
        s = ClientDeviceSerializer(data={"device_type": "ios"})
        self.assertFalse(s.is_valid())
        self.assertIn("fcm_token", s.errors)


# ──────────────────────────────────────────────
# 3. Service tests (mocked FCM)
# ──────────────────────────────────────────────


class NotificationServiceTests(TestCase):
    @patch("notification.service.FCMService.send_to_tokens")
    def test_send_to_client_creates_notification_and_sends(
        self, mock_send_to_tokens
    ):
        mock_send_to_tokens.return_value = MagicMock(success_count=1, failure_count=0)
        client = make_client()
        notification = NotificationService.send_to_client(
            client=client,
            title="Test Title",
            message="Test body",
            notification_type=Notification.NotificationType.SYSTEM,
            data={"key": "value"},
        )
        self.assertIsNotNone(notification)
        self.assertEqual(notification.recipient, client)
        self.assertEqual(notification.title, "Test Title")
        self.assertEqual(notification.push_message, "Test body")
        self.assertEqual(notification.status, Notification.Status.SENT)
        self.assertFalse(notification.is_for_every_one)
        mock_send_to_tokens.assert_called_once()
        call_kw = mock_send_to_tokens.call_args[1]
        self.assertEqual(call_kw["title"], "Test Title")
        self.assertEqual(call_kw["body"], "Test body")
        self.assertEqual(call_kw["data"], {"key": "value"})

    @patch("notification.service.FCMService.send_to_tokens")
    def test_send_to_client_keeps_pending_when_fcm_fails(self, mock_send_to_tokens):
        mock_send_to_tokens.return_value = None
        client = make_client()

        notification = NotificationService.send_to_client(
            client=client,
            title="Test Title",
            message="Test body",
            notification_type=Notification.NotificationType.SYSTEM,
            data={"key": "value"},
        )

        self.assertEqual(notification.status, Notification.Status.PENDING)


class NotificationServiceSendBroadcastTests(TestCase):
    @patch("notification.service.messaging.send")
    @patch("notification.service.logger")
    def test_send_broadcast_updates_status_to_sent(self, mock_logger, mock_send):
        notification = Notification.objects.create(
            recipient=None,
            title="Broadcast",
            push_message="Hello all",
            notification_type=Notification.NotificationType.SYSTEM,
            status=Notification.Status.PENDING,
            is_for_every_one=True,
        )
        NotificationService.send_broadcast(notification)
        notification.refresh_from_db()
        self.assertEqual(notification.status, Notification.Status.SENT)
        mock_send.assert_called_once()


class FCMServiceTests(TestCase):
    @patch("notification.service.messaging.send_each_for_multicast")
    def test_send_to_tokens_empty_list_returns_none(self, mock_send):
        result = FCMService.send_to_tokens(tokens=[], title="T", body="B")
        self.assertIsNone(result)
        mock_send.assert_not_called()

    @patch("notification.service.messaging.send_each_for_multicast")
    def test_send_to_tokens_calls_firebase(self, mock_send):
        mock_send.return_value = MagicMock(success_count=1, failure_count=0)
        result = FCMService.send_to_tokens(
            tokens=["token1"],
            title="Title",
            body="Body",
            data={"k": "v"},
        )
        self.assertIsNotNone(result)
        mock_send.assert_called_once()

    @patch("notification.service.logger.exception")
    @patch("notification.service.messaging.send_each_for_multicast")
    def test_send_to_tokens_returns_none_when_firebase_raises(
        self, mock_send, mock_log_exception
    ):
        mock_send.side_effect = RuntimeError("bad auth")
        result = FCMService.send_to_tokens(tokens=["token1"], title="T", body="B")
        self.assertIsNone(result)
        mock_log_exception.assert_called_once()


# ──────────────────────────────────────────────
# 4. API / View tests
# ──────────────────────────────────────────────


class FCMTokenUpdateViewTests(TestCase):
    def setUp(self):
        self.api = APIClient()

    def test_update_fcm_token_unauthenticated(self):
        response = self.api.post(
            "/api/notification/device/",
            data={"fcm_token": "token123", "device_type": "ios"},
            format="json",
        )
        self.assertIn(response.status_code, (401, 403))

    @patch("notification.views.ClientDeviceService.register_device")
    def test_update_fcm_token_authenticated_success(self, mock_register):
        from django.conf import settings
        from rest_framework_simplejwt.tokens import AccessToken
        from users.tokens import TokenMetadata

        client = make_client()
        access = AccessToken()
        access[TokenMetadata.TOKEN_SUBJECT] = str(client.guid)
        access[TokenMetadata.TOKEN_ISSUER] = getattr(settings, "JWT_ISSUER", "weel")
        access[TokenMetadata.TOKEN_USER_TYPE] = "client"
        access[TokenMetadata.TOKEN_TYPE_CLAIM] = "access"
        self.api.credentials(HTTP_AUTHORIZATION=f"Bearer {str(access)}")

        response = self.api.post(
            "/api/notification/device/",
            data={"fcm_token": "fcm_token_abc", "device_type": "android"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("detail", response.data)
        mock_register.assert_called_once()
        call_kw = mock_register.call_args[1]
        self.assertEqual(call_kw["client"], client)
        self.assertEqual(call_kw["fcm_token"], "fcm_token_abc")
        self.assertEqual(call_kw["device_type"], "android")


# ──────────────────────────────────────────────
# 5. URL test
# ──────────────────────────────────────────────


# ──────────────────────────────────────────────
# 6. Task tests
# ──────────────────────────────────────────────


class SendBookingRemindersTaskTests(TestCase):
    @patch("notification.tasks.NotificationService.send_to_client")
    def test_send_booking_reminders_calls_send_to_client_for_matching_bookings(
        self, mock_send_to_client
    ):
        from datetime import timedelta
        from django.utils import timezone

        from booking.models import Booking
        from booking.tests import BookingTestMixin
        from notification.tasks import send_booking_reminders

        tomorrow = timezone.localdate() + timedelta(days=1)
        client = make_client()
        prop = BookingTestMixin.make_property()
        booking = Booking.objects.create(
            client=client,
            property=prop,
            check_in=tomorrow,
            check_out=tomorrow + timedelta(days=1),
            status=Booking.BookingStatus.CONFIRMED,
            reminder_sent=False,
        )
        send_booking_reminders()
        self.assertEqual(mock_send_to_client.call_count, 1)
        booking.refresh_from_db()
        self.assertTrue(booking.reminder_sent)


class NotificationURLTests(TestCase):
    def test_device_url_resolves(self):
        from django.urls import reverse
        url = reverse("notification:update-fcm-token")
        self.assertIn("device", url)
