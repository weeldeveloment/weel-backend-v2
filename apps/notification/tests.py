from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.urls import resolve

from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from notification.raw_repository import mark_message_notifications_for_conversation
from notification.service import (
    B2BFirebaseNotConfigured,
    FCMService,
    NotificationService,
    b2b_firebase_app,
)
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

    @patch("notification.service.messaging.send_each")
    @patch("notification.service.messaging.send_each_for_multicast")
    def test_send_to_tokens_badges_each_phone_with_its_own_count(
        self, mock_multicast, mock_send_each
    ):
        """With `badge_for`, every token gets a message carrying its own
        unread count on both platforms — iOS badges are absolute, so one
        multicast number would be wrong for everyone but one person."""
        mock_send_each.return_value = SimpleNamespace(
            responses=[_fake_send_response(success=True)] * 2,
            success_count=2,
            failure_count=0,
        )

        FCMService.send_to_tokens(
            tokens=["tok-a", "tok-b"],
            title="Title",
            body="Body",
            android_channel_id="weel_workspace",
            badge_for=lambda tokens: {"tok-a": 3, "tok-b": 1},
        )

        mock_multicast.assert_not_called()
        messages = mock_send_each.call_args.args[0]
        by_token = {m.token: m for m in messages}
        self.assertEqual(set(by_token), {"tok-a", "tok-b"})
        self.assertEqual(by_token["tok-a"].apns.payload.aps.badge, 3)
        self.assertEqual(by_token["tok-b"].apns.payload.aps.badge, 1)
        self.assertEqual(by_token["tok-a"].android.notification.notification_count, 3)
        self.assertEqual(by_token["tok-b"].android.notification.notification_count, 1)
        # The rest of the payload is the same message it always was.
        self.assertEqual(by_token["tok-a"].notification.title, "Title")
        self.assertEqual(by_token["tok-a"].apns.payload.aps.sound, "default")

    @patch("notification.service.messaging.send_each")
    @patch("notification.service.messaging.send_each_for_multicast")
    def test_a_token_with_no_count_is_sent_without_a_badge(
        self, mock_multicast, mock_send_each
    ):
        mock_send_each.return_value = SimpleNamespace(
            responses=[_fake_send_response(success=True)],
            success_count=1,
            failure_count=0,
        )

        FCMService.send_to_tokens(
            tokens=["tok-a"],
            title="Title",
            body="Body",
            badge_for=lambda tokens: {},
        )

        (message,) = mock_send_each.call_args.args[0]
        self.assertIsNone(message.apns.payload.aps.badge)

    @patch("notification.service.logger")
    @patch("notification.service.messaging.send_each")
    @patch("notification.service.messaging.send_each_for_multicast")
    def test_a_badge_lookup_that_fails_does_not_cost_the_push(
        self, mock_multicast, mock_send_each, _mock_logger
    ):
        """A badge is decoration; the message goes out as a plain multicast
        when the count cannot be read."""
        mock_multicast.return_value = SimpleNamespace(
            responses=[_fake_send_response(success=True)],
            success_count=1,
            failure_count=0,
        )

        def explode(tokens):
            raise RuntimeError("db down")

        FCMService.send_to_tokens(
            tokens=["tok-a"], title="Title", body="Body", badge_for=explode
        )

        mock_send_each.assert_not_called()
        mock_multicast.assert_called_once()

    @patch("notification.service.FCMService._deactivate_invalid_tokens")
    @patch("notification.service.messaging.send_each")
    def test_badged_sends_still_clear_dead_tokens(self, mock_send_each, mock_deactivate):
        mock_send_each.return_value = SimpleNamespace(
            responses=[
                _fake_send_response(success=True),
                _fake_send_response(success=False, code="unregistered"),
            ],
            success_count=1,
            failure_count=1,
        )

        FCMService.send_to_tokens(
            tokens=["tok-a", "tok-dead"],
            title="Title",
            body="Body",
            badge_for=lambda tokens: {t: 1 for t in tokens},
        )

        mock_deactivate.assert_called_once_with(["tok-dead"])
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

    @patch("notification.service.messaging.send_each_for_multicast")
    @patch("notification.service.messaging.MulticastMessage")
    @patch("notification.service.messaging.Notification")
    def test_send_to_tokens_uses_the_default_app_unless_told_otherwise(
        self,
        _mock_notification,
        _mock_multicast,
        mock_send_each,
    ):
        """Every consumer and partner send goes through this path unchanged."""
        mock_send_each.return_value = SimpleNamespace(
            success_count=1, failure_count=0, responses=[_fake_send_response(success=True)]
        )
        FCMService.send_to_tokens(tokens=["t"], title="T", body="B")

        self.assertIsNone(mock_send_each.call_args.kwargs["app"])

    @patch("notification.service.messaging.send_each_for_multicast")
    @patch("notification.service.messaging.MulticastMessage")
    @patch("notification.service.messaging.Notification")
    def test_send_to_tokens_forwards_the_app_it_is_given(
        self,
        _mock_notification,
        _mock_multicast,
        mock_send_each,
    ):
        """A B2B token is only addressable from the B2B project's app."""
        mock_send_each.return_value = SimpleNamespace(
            success_count=1, failure_count=0, responses=[_fake_send_response(success=True)]
        )
        app = object()
        FCMService.send_to_tokens(tokens=["t"], title="T", body="B", app=app)

        self.assertIs(mock_send_each.call_args.kwargs["app"], app)

    @patch("notification.service.FCMService._deactivate_invalid_tokens")
    @patch("notification.service.messaging.send_each_for_multicast")
    @patch("notification.service.messaging.MulticastMessage")
    @patch("notification.service.messaging.Notification")
    def test_send_to_tokens_clears_invalid_tokens_where_the_caller_says(
        self,
        _mock_notification,
        _mock_multicast,
        mock_send_each,
        mock_default_deactivate,
    ):
        """A workspace token lives in `b2b_employee`, not in `public.users`."""
        mock_send_each.return_value = SimpleNamespace(
            success_count=0,
            failure_count=1,
            responses=[_fake_send_response(success=False, code="unregistered")],
        )
        clear = MagicMock()
        FCMService.send_to_tokens(
            tokens=["dead-token"], title="T", body="B", deactivate_invalid=clear
        )

        clear.assert_called_once_with(["dead-token"])
        # The consumer table is left alone — that token was never in it.
        mock_default_deactivate.assert_not_called()

    @patch("notification.service.messaging.send_each_for_multicast")
    @patch("notification.service.messaging.MulticastMessage")
    @patch("notification.service.messaging.Notification")
    def test_send_to_tokens_survives_a_failing_cleanup(
        self,
        _mock_notification,
        _mock_multicast,
        mock_send_each,
    ):
        """The message is already delivered; tidying up must not undo that."""
        mock_send_each.return_value = SimpleNamespace(
            success_count=1,
            failure_count=1,
            responses=[
                _fake_send_response(success=True),
                _fake_send_response(success=False, code="unregistered"),
            ],
        )
        result = FCMService.send_to_tokens(
            tokens=["ok", "dead"],
            title="T",
            body="B",
            deactivate_invalid=MagicMock(side_effect=RuntimeError("db down")),
        )

        self.assertEqual(result.success_count, 1)


    @patch("notification.service.execute")
    def test_deactivate_invalid_tokens_updates_users_table(self, mock_execute):
        FCMService._deactivate_invalid_tokens(["t1", "t2"])

        self.assertEqual(mock_execute.call_count, 1)


class B2BFirebaseAppTests(SimpleTestCase):
    """Which Firebase project a B2B push is sent from.

    The workspace app is registered in a project of its own, and an FCM token
    is only addressable by the project that issued it.
    """

    def test_refuses_to_send_when_no_b2b_project_is_configured(self):
        # It used to fall back to the default app. That could never deliver a
        # workspace token — the default project did not issue it — so the
        # fallback only turned a missing credential into a per-token
        # `SenderId mismatch`. Failing here names the actual cause instead.
        with self.settings(FIREBASE_B2B_APP=None):
            with self.assertRaises(B2BFirebaseNotConfigured):
                b2b_firebase_app()

    def test_returns_the_b2b_app_once_one_is_configured(self):
        app = object()
        with self.settings(FIREBASE_B2B_APP=app):
            self.assertIs(b2b_firebase_app(), app)


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

