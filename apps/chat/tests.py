from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

from django.test import SimpleTestCase
from django.utils import timezone

from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from chat.views import ChatViewSet
from shared.raw.entities import RawChatMessage, RawUser


def _raw_user(user_id: int, role: str, username: str = "user") -> RawUser:
    now = timezone.now()
    return RawUser(
        id=user_id,
        role=role,
        email=f"{username}@mail.test",
        phone_number="998901234567",
        first_name="First",
        last_name="Last",
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


def _raw_message(
    *,
    message_id: int,
    conversation_id: int,
    sender_user_id: int,
    receiver_user_id: int,
    sender_role: str,
    receiver_role: str,
    content: str = "hello",
) -> RawChatMessage:
    now = timezone.now()
    return RawChatMessage(
        id=message_id,
        legacy_message_id=None,
        content=content,
        is_read=False,
        created_at=now,
        updated_at=now,
        conversation_id=conversation_id,
        sender_user_id=sender_user_id,
        receiver_user_id=receiver_user_id,
        sender_role=sender_role,
        receiver_role=receiver_role,
    )


class ChatViewSetTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    @patch("chat.views.ChatViewSet._push_ws_event")
    @patch("chat.views.NotificationService.send_to_partner")
    @patch("chat.views.touch_conversation")
    @patch("chat.views.create_chat_message")
    @patch("chat.views.get_or_create_conversation")
    @patch("chat.views.get_active_actor")
    def test_admin_send_triggers_partner_notification_and_returns_201(
        self,
        mock_get_active_actor,
        mock_get_or_create_conversation,
        mock_create_chat_message,
        mock_touch_conversation,
        mock_send_to_partner,
        mock_push_ws_event,
    ):
        admin = _raw_user(1, "admin", username="admin")
        partner = _raw_user(2, "partner", username="partner")

        mock_get_active_actor.return_value = partner
        mock_get_or_create_conversation.return_value = type("Conv", (), {"id": 99})()
        mock_create_chat_message.return_value = _raw_message(
            message_id=10,
            conversation_id=99,
            sender_user_id=admin.id,
            receiver_user_id=partner.id,
            sender_role="admin",
            receiver_role="partner",
            content="Need confirmation",
        )

        request = self.factory.post(
            "/api/chat/send/",
            {"receiver_id": partner.id, "content": "Need confirmation"},
            format="json",
        )
        force_authenticate(request, user=admin, token="auth-token")

        response = ChatViewSet.as_view({"post": "send"})(request)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["sender_type"], "admin")
        self.assertEqual(response.data["receiver_type"], "partner")
        mock_touch_conversation.assert_called_once_with(99)
        mock_send_to_partner.assert_called_once()
        self.assertGreaterEqual(mock_push_ws_event.call_count, 1)

    @patch("chat.views.get_first_active_admin", return_value=None)
    @patch("chat.views.get_active_actor", return_value=None)
    def test_partner_send_returns_400_when_no_admin_available(
        self,
        _mock_get_active_actor,
        _mock_get_first_admin,
    ):
        partner = _raw_user(5, "partner", username="p5")
        request = self.factory.post(
            "/api/chat/send/",
            {"receiver_id": 100, "content": "hello"},
            format="json",
        )
        force_authenticate(request, user=partner, token="auth-token")

        response = ChatViewSet.as_view({"post": "send"})(request)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("No admin user available", str(response.data))

    @patch("chat.views.ChatViewSet._push_ws_event")
    @patch("chat.views.touch_conversation")
    @patch("chat.views.create_chat_message")
    @patch("chat.views.get_or_create_conversation")
    @patch("chat.views.get_active_actor")
    def test_client_send_to_admin_returns_201_and_uses_client_conversation(
        self,
        mock_get_active_actor,
        mock_get_or_create_conversation,
        mock_create_chat_message,
        mock_touch_conversation,
        _mock_push_ws_event,
    ):
        client = _raw_user(7, "client", username="client7")
        admin = _raw_user(1, "admin", username="admin")

        mock_get_active_actor.return_value = admin
        mock_get_or_create_conversation.return_value = type("Conv", (), {"id": 120})()
        mock_create_chat_message.return_value = _raw_message(
            message_id=33,
            conversation_id=120,
            sender_user_id=client.id,
            receiver_user_id=admin.id,
            sender_role="client",
            receiver_role="admin",
            content="hello admin",
        )

        request = self.factory.post(
            "/api/chat/send/",
            {"receiver_id": admin.id, "receiver_type": "admin", "content": "hello admin"},
            format="json",
        )
        force_authenticate(request, user=client, token="auth-token")

        response = ChatViewSet.as_view({"post": "send"})(request)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["sender_type"], "client")
        self.assertEqual(response.data["receiver_type"], "admin")
        mock_get_or_create_conversation.assert_called_once_with(
            admin_user_id=admin.id,
            counterpart_user_id=client.id,
            counterpart_role="client",
        )
        mock_touch_conversation.assert_called_once_with(120)

    def test_read_messages_requires_list_payload(self):
        admin = _raw_user(1, "admin", username="admin")
        request = self.factory.post(
            "/api/chat/read/",
            {"message_ids": "not-a-list"},
            format="json",
        )
        force_authenticate(request, user=admin, token="auth-token")

        response = ChatViewSet.as_view({"post": "read_messages"})(request)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("must be a list", str(response.data))

    @patch("chat.views.mark_message_ids_read", return_value=3)
    @patch("chat.views.ChatViewSet._push_ws_event")
    def test_read_messages_returns_updated_count(
        self,
        _mock_push_event,
        mock_mark_read,
    ):
        partner = _raw_user(2, "partner", username="partner")
        request = self.factory.post(
            "/api/chat/read/",
            {
                "message_ids": [1, 2, 3],
                "partner_id": 1,
                "partner_type": "admin",
            },
            format="json",
        )
        force_authenticate(request, user=partner, token="auth-token")

        response = ChatViewSet.as_view({"post": "read_messages"})(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["updated"], 3)
        mock_mark_read.assert_called_once()

    @patch("chat.views.list_conversations_for_actor")
    def test_conversations_for_admin_return_serialized_payload(self, mock_list_conversations):
        admin = _raw_user(1, "admin", username="admin")
        partner = _raw_user(2, "partner", username="partner")
        last_message = _raw_message(
            message_id=1,
            conversation_id=10,
            sender_user_id=1,
            receiver_user_id=2,
            sender_role="admin",
            receiver_role="partner",
        )
        mock_list_conversations.return_value = [
            {
                "counterpart": partner,
                "conversation_id": 10,
                "last_message": last_message,
                "unread_count": 1,
            }
        ]

        request = self.factory.get("/api/chat/conversations/")
        force_authenticate(request, user=admin, token="auth-token")

        response = ChatViewSet.as_view({"get": "conversations"})(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["counterpart"]["role"], "partner")
        self.assertEqual(response.data[0]["conversation_id"], 10)

    @patch("chat.views.list_conversations_for_actor")
    def test_conversations_for_client_return_admin_counterpart(self, mock_list_conversations):
        client = _raw_user(8, "client", username="client8")
        admin = _raw_user(1, "admin", username="admin")
        last_message = _raw_message(
            message_id=4,
            conversation_id=21,
            sender_user_id=client.id,
            receiver_user_id=admin.id,
            sender_role="client",
            receiver_role="admin",
        )
        mock_list_conversations.return_value = [
            {
                "counterpart": admin,
                "conversation_id": 21,
                "last_message": last_message,
                "unread_count": 0,
            }
        ]

        request = self.factory.get("/api/chat/conversations/")
        force_authenticate(request, user=client, token="auth-token")

        response = ChatViewSet.as_view({"get": "conversations"})(request)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data[0]["counterpart"]["role"], "admin")
        self.assertEqual(response.data[0]["conversation_id"], 21)

