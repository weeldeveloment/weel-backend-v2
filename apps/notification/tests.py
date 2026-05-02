from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.urls import resolve

from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from notification.raw_repository import mark_message_notifications_for_conversation
from notification.service import FCMService, NotificationService
from notification.serializers import PartnerNotificationSerializer
from notification.views import (
    ClientNotificationListView,
    ClientNotificationMarkAllAsReadView,
    ClientNotificationMarkAsReadView,
    FCMTokenUpdateView,
    PartnerNotificationListView,
    PartnerNotificationMarkAsReadView,
)


def _fake_send_response(*, success: bool, code: str | None = None):
    if success:
        return SimpleNamespace(success=True, exception=None)
    exception = SimpleNamespace(code=code) if code else Exception("unknown")
    return SimpleNamespace(success=False, exception=exception)


class FCMServiceTests(SimpleTestCase):
    @patch("notification.service.logger")
    def test_send_to_tokens_returns_none_for_empty_tokens(self, _mock_logger):
        result = FCMService.send_to_tokens(tokens=[], title="T", body="B")
        self.assertIsNone(result)

    @patch("notification.service.messaging.send_each_for_multicast", side_effect=RuntimeError("bad auth"))
    @patch("notification.service.messaging.MulticastMessage")
    @patch("notification.service.messaging.Notification")
    def test_send_to_tokens_returns_none_if_firebase_raises(
        self,
        _mock_notification,
        _mock_multicast,
        _mock_send,
    ):
        result = FCMService.send_to_tokens(tokens=["token-1"], title="T", body="B")
        self.assertIsNone(result)

    @patch("notification.service.FCMService._deactivate_invalid_tokens")
    @patch("notification.service.messaging.send_each_for_multicast")
    @patch("notification.service.messaging.MulticastMessage")
    @patch("notification.service.messaging.Notification")
    def test_send_to_tokens_deactivates_invalid_tokens(
        self,
        _mock_notification,
        _mock_multicast,
        mock_send_each,
        mock_deactivate,
    ):
        mock_send_each.return_value = SimpleNamespace(
            success_count=1,
            failure_count=1,
            responses=[
                _fake_send_response(success=True),
                _fake_send_response(success=False, code="registration-token-not-registered"),
            ],
        )
        result = FCMService.send_to_tokens(tokens=["ok-token", "bad-token"], title="T", body="B")

        self.assertEqual(result.success_count, 1)
        mock_deactivate.assert_called_once_with(["bad-token"])

    @patch("notification.service.execute")
    def test_deactivate_invalid_tokens_updates_users_table(self, mock_execute):
        FCMService._deactivate_invalid_tokens(["t1", "t2"])

        self.assertEqual(mock_execute.call_count, 1)


class NotificationServiceTests(SimpleTestCase):
    @patch("notification.service.execute")
    @patch("notification.service.FCMService.send_to_tokens")
    @patch("notification.service.fetch_all")
    @patch("notification.service.create_notification")
    def test_send_to_client_marks_notification_sent_on_success(
        self,
        mock_create_notification,
        mock_fetch_all,
        mock_send_to_tokens,
        mock_execute,
    ):
        mock_create_notification.return_value = {"id": 77}
        mock_fetch_all.return_value = [{"fcm_token": "tok-1"}]
        mock_send_to_tokens.return_value = SimpleNamespace(success_count=1, failure_count=0)

        client = SimpleNamespace(id=10)
        created = NotificationService.send_to_client(
            client=client,
            title="New booking",
            message="You have a new booking",
            notification_type="booking_new",
            data={"booking_id": 99},
        )

        self.assertEqual(created["id"], 77)
        mock_execute.assert_called_once()
        mock_create_notification.assert_called_once()
        self.assertEqual(
            mock_create_notification.call_args.kwargs.get("payload"),
            {"booking_id": 99},
        )

    @patch("notification.service.execute")
    @patch("notification.service.FCMService.send_to_tokens", return_value=None)
    @patch("notification.service.fetch_all", return_value=[])
    @patch("notification.service.create_notification", return_value={"id": 88})
    def test_send_to_client_keeps_pending_if_no_successful_delivery(
        self,
        _mock_create_notification,
        _mock_fetch_all,
        _mock_send_to_tokens,
        mock_execute,
    ):
        client = SimpleNamespace(id=11)
        NotificationService.send_to_client(
            client=client,
            title="Title",
            message="Body",
            notification_type="system",
        )
        mock_execute.assert_not_called()

    @patch("notification.service.execute")
    @patch("notification.service.FCMService.send_to_tokens")
    @patch("notification.service.fetch_all", return_value=[{"fcm_token": "pt-1"}])
    @patch("notification.service.create_notification")
    def test_send_to_partner_saves_and_sends(
        self,
        mock_create_notification,
        mock_fetch_all,
        mock_send_to_tokens,
        _mock_execute,
    ):
        mock_create_notification.return_value = {"id": 1}
        mock_send_to_tokens.return_value = SimpleNamespace(success_count=1, failure_count=0)
        partner = SimpleNamespace(id=12)

        result = NotificationService.send_to_partner(
            partner=partner,
            title="Admin message",
            message="Hello",
            notification_type="message",
            data={"conversation_id": 5},
        )

        self.assertEqual(result.get("id"), 1)
        mock_create_notification.assert_called_once()
        self.assertEqual(
            mock_create_notification.call_args.kwargs.get("payload"),
            {"conversation_id": 5},
        )

    def test_normalize_data_converts_values_to_strings_and_skips_none(self):
        payload = NotificationService._normalize_data({"a": 1, "b": None, 3: True})
        self.assertEqual(payload, {"a": "1", "3": "True"})

    def test_partner_notification_serializer_exposes_payload_as_data(self):
        row = {
            "guid": "11111111-1111-1111-1111-111111111111",
            "title": "Hi",
            "push_message": "Body",
            "notification_type": "message",
            "status": "pending",
            "created_at": "2026-01-01T10:00:00",
            "payload": {"conversation_id": 3},
        }
        data = PartnerNotificationSerializer(row).data
        self.assertEqual(data["data"]["conversation_id"], 3)


class NotificationViewsTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    @patch("notification.views.ClientDeviceService.register_device")
    def test_fcm_token_update_view_returns_200(self, mock_register_device):
        request = self.factory.post(
            "/api/notification/device/",
            {"fcm_token": "token-12345", "device_type": "ios"},
            format="json",
        )
        user = SimpleNamespace(id=100, role="client", is_active=True)
        force_authenticate(request, user=user, token="token")
        response = FCMTokenUpdateView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["detail"], "FCM token updated successfully")
        mock_register_device.assert_called_once()

    @patch("notification.views.count_client_notifications", return_value={"total": 4, "unread_count": 1})
    @patch("notification.views.list_client_notifications")
    def test_client_notification_list_returns_counts(
        self,
        mock_list_notifications,
        _mock_count_notifications,
    ):
        mock_list_notifications.return_value = [
            {
                "guid": "11111111-1111-1111-1111-111111111111",
                "title": "A",
                "push_message": "Body",
                "notification_type": "system",
                "status": "pending",
                "created_at": "2026-01-01T10:00:00",
            }
        ]
        request = self.factory.get("/api/notification/client/?page=1&limit=10")
        user = SimpleNamespace(id=103, role="client", is_active=True)
        force_authenticate(request, user=user, token="token")

        response = ClientNotificationListView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["total"], 4)
        self.assertEqual(response.data["unread_count"], 1)
        self.assertEqual(len(response.data["notifications"]), 1)

    @patch("notification.views.count_partner_notifications", return_value={"total": 3, "unread_count": 2})
    @patch("notification.views.list_partner_notifications")
    def test_partner_notification_list_returns_counts(
        self,
        mock_list_notifications,
        _mock_count_notifications,
    ):
        mock_list_notifications.return_value = [
            {
                "guid": "11111111-1111-1111-1111-111111111111",
                "title": "A",
                "push_message": "Body",
                "notification_type": "system",
                "status": "pending",
                "created_at": "2026-01-01T10:00:00",
            }
        ]
        request = self.factory.get("/api/notification/partner/?page=1&limit=10")
        user = SimpleNamespace(id=101, role="partner", is_active=True)
        force_authenticate(request, user=user, token="token")

        response = PartnerNotificationListView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["total"], 3)
        self.assertEqual(response.data["unread_count"], 2)
        self.assertEqual(len(response.data["notifications"]), 1)

    @patch("notification.views.mark_client_notifications_as_read", return_value=2)
    def test_client_mark_as_read_returns_marked_count(self, mock_mark_as_read):
        request = self.factory.post(
            "/api/notification/client/read/",
            {"notification_ids": ["11111111-1111-1111-1111-111111111111"]},
            format="json",
        )
        user = SimpleNamespace(id=104, role="client", is_active=True)
        force_authenticate(request, user=user, token="token")

        response = ClientNotificationMarkAsReadView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["marked_count"], 2)
        mock_mark_as_read.assert_called_once()

    @patch("notification.views.mark_client_notifications_as_read", return_value=9)
    def test_client_mark_all_as_read_returns_marked_count(self, mock_mark_as_read):
        request = self.factory.post("/api/notification/client/read-all/")
        user = SimpleNamespace(id=105, role="client", is_active=True)
        force_authenticate(request, user=user, token="token")

        response = ClientNotificationMarkAllAsReadView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["marked_count"], 9)

    @patch("notification.views.mark_partner_notifications_as_read", return_value=4)
    def test_partner_mark_as_read_returns_marked_count(self, mock_mark_as_read):
        request = self.factory.post(
            "/api/notification/partner/read/",
            {"notification_ids": ["11111111-1111-1111-1111-111111111111"]},
            format="json",
        )
        user = SimpleNamespace(id=102, role="partner", is_active=True)
        force_authenticate(request, user=user, token="token")

        response = PartnerNotificationMarkAsReadView.as_view()(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["marked_count"], 4)
        mock_mark_as_read.assert_called_once()


class NotificationUrlsTests(SimpleTestCase):
    def test_client_notification_read_urls_resolve(self):
        self.assertEqual(
            resolve("/api/notification/client/read/").func.view_class.__name__,
            "ClientNotificationMarkAsReadView",
        )
        self.assertEqual(
            resolve("/api/notification/client/read-all/").func.view_class.__name__,
            "ClientNotificationMarkAllAsReadView",
        )


class NotificationRawRepositoryTests(SimpleTestCase):
    @patch("notification.raw_repository.execute", return_value=2)
    @patch("notification.raw_repository.is_postgresql", return_value=True)
    def test_mark_message_notifications_uses_postgres_payload_filter(self, _mock_pg, mock_execute):
        count = mark_message_notifications_for_conversation(
            recipient_user_id=5,
            recipient_role="partner",
            conversation_id=42,
        )
        self.assertEqual(count, 2)
        self.assertIn("payload->>'conversation_id'", mock_execute.call_args[0][0])

    @patch("notification.raw_repository.execute", return_value=1)
    @patch("notification.raw_repository.is_postgresql", return_value=False)
    def test_mark_message_notifications_uses_sqlite_json_extract(self, _mock_pg, mock_execute):
        count = mark_message_notifications_for_conversation(
            recipient_user_id=5,
            recipient_role="client",
            conversation_id=9,
        )
        self.assertEqual(count, 1)
        self.assertIn("json_extract", mock_execute.call_args[0][0])

