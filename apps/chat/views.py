from __future__ import annotations

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from notification.service import NotificationService
from users.authentication import PartnerJWTAuthentication

from .authentication import RawAdminJWTAuthentication
from .raw_repository import (
    create_chat_message,
    get_active_actor,
    get_first_active_admin,
    get_or_create_conversation,
    list_conversations_for_actor,
    list_messages_for_conversation,
    mark_conversation_messages_read,
    mark_message_ids_read,
    touch_conversation,
)
from .serializers import ActorSerializer, ChatMessageSerializer, ConversationSerializer


class IsAuthenticatedActor(BasePermission):
    """Accept authenticated admin or partner actor."""

    def has_permission(self, request, view):
        return request.user is not None and request.auth is not None


def is_admin_actor(user) -> bool:
    return getattr(user, "role", None) == "admin"


def is_partner_actor(user) -> bool:
    return getattr(user, "role", None) == "partner"


class ChatViewSet(viewsets.GenericViewSet):
    authentication_classes = [RawAdminJWTAuthentication, PartnerJWTAuthentication]
    permission_classes = [IsAuthenticatedActor]
    serializer_class = ChatMessageSerializer

    @staticmethod
    def _room_name(actor_type: str, actor_id: int) -> str:
        return f"chat_{actor_type}_{actor_id}"

    @staticmethod
    def _push_ws_event(actor_type: str, actor_id: int, event_type: str, payload: dict):
        channel_layer = get_channel_layer()
        if not channel_layer:
            return
        async_to_sync(channel_layer.group_send)(
            ChatViewSet._room_name(actor_type, actor_id),
            {
                "type": event_type,
                "message": payload,
            },
        )

    @action(detail=False, methods=["get"])
    def conversations(self, request):
        """Get all conversations for the current actor."""
        user = request.user

        if is_admin_actor(user):
            items = list_conversations_for_actor(user.id, "admin")
            payload = [
                {
                    "counterpart": ActorSerializer.from_partner(item["counterpart"]),
                    "conversation_id": item["conversation_id"],
                    "last_message": item["last_message"],
                    "unread_count": item["unread_count"],
                }
                for item in items
            ]
        elif is_partner_actor(user):
            items = list_conversations_for_actor(user.id, "partner")
            payload = [
                {
                    "counterpart": ActorSerializer.from_admin(item["counterpart"]),
                    "conversation_id": item["conversation_id"],
                    "last_message": item["last_message"],
                    "unread_count": item["unread_count"],
                }
                for item in items
            ]
        else:
            return Response([], status=status.HTTP_200_OK)

        serializer = ConversationSerializer(payload, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=["get"], url_path="messages/(?P<partner_id>[^/.]+)")
    def messages(self, request, partner_id=None):
        """Get all messages with a specific counterpart."""
        user = request.user

        try:
            counterpart_id = int(partner_id)
        except (TypeError, ValueError):
            return Response({"error": "Invalid partner id"}, status=status.HTTP_400_BAD_REQUEST)

        if is_admin_actor(user):
            partner = get_active_actor(counterpart_id, "partner")
            if not partner:
                return Response({"error": "Partner not found"}, status=status.HTTP_404_NOT_FOUND)
            conversation = get_or_create_conversation(admin_user_id=user.id, partner_user_id=partner.id)
        elif is_partner_actor(user):
            admin_user = get_active_actor(counterpart_id, "admin")
            if not admin_user:
                return Response({"error": "Admin user not found"}, status=status.HTTP_404_NOT_FOUND)
            conversation = get_or_create_conversation(admin_user_id=admin_user.id, partner_user_id=user.id)
        else:
            return Response({"error": "Unauthorized actor"}, status=status.HTTP_403_FORBIDDEN)

        messages = list_messages_for_conversation(conversation.id)
        mark_conversation_messages_read(
            conversation_id=conversation.id,
            receiver_user_id=user.id,
            receiver_role=user.role,
        )

        serializer = ChatMessageSerializer(messages, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=["post"])
    def send(self, request):
        """Send a message to counterpart actor."""
        raw_receiver_id = request.data.get("receiver_id")
        content = (request.data.get("content") or "").strip()
        if raw_receiver_id in (None, "") or not content:
            return Response(
                {"error": "receiver_id and content are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            requested_receiver_id = int(raw_receiver_id)
        except (TypeError, ValueError):
            return Response({"error": "Invalid receiver_id"}, status=status.HTTP_400_BAD_REQUEST)

        sender = request.user
        if is_admin_actor(sender):
            partner = get_active_actor(requested_receiver_id, "partner")
            if not partner:
                return Response({"error": "Partner not found"}, status=status.HTTP_404_NOT_FOUND)

            conversation = get_or_create_conversation(admin_user_id=sender.id, partner_user_id=partner.id)
            message = create_chat_message(
                conversation_id=conversation.id,
                sender_user_id=sender.id,
                receiver_user_id=partner.id,
                sender_role="admin",
                receiver_role="partner",
                content=content,
            )
            touch_conversation(conversation.id)

            sender_name = (
                f"{(sender.first_name or '').strip()} {(sender.last_name or '').strip()}".strip()
                or sender.username
                or "Admin"
            )
            message_preview = content if len(content) <= 120 else f"{content[:117]}..."
            NotificationService.send_to_partner(
                partner=partner,
                title=sender_name,
                message=message_preview,
                notification_type="message",
                data={
                    "type": "chat_message",
                    "conversation_id": conversation.id,
                    "message_id": message.id,
                    "sender_id": sender.id,
                    "sender_type": "admin",
                    "receiver_id": partner.id,
                    "receiver_type": "partner",
                    "message_preview": message_preview,
                    "sender_name": sender_name,
                },
            )
        elif is_partner_actor(sender):
            admin_user = get_active_actor(requested_receiver_id, "admin")
            if not admin_user:
                admin_user = get_first_active_admin()
            if not admin_user:
                return Response({"error": "No admin user available"}, status=status.HTTP_400_BAD_REQUEST)

            conversation = get_or_create_conversation(admin_user_id=admin_user.id, partner_user_id=sender.id)
            message = create_chat_message(
                conversation_id=conversation.id,
                sender_user_id=sender.id,
                receiver_user_id=admin_user.id,
                sender_role="partner",
                receiver_role="admin",
                content=content,
            )
            touch_conversation(conversation.id)
        else:
            return Response({"error": "Unauthorized actor"}, status=status.HTTP_403_FORBIDDEN)

        data = ChatMessageSerializer(message).data

        receiver_type = data.get("receiver_type")
        receiver_id = data.get("receiver_id")
        sender_type = data.get("sender_type")
        sender_id = data.get("sender_id")

        if receiver_type and receiver_id:
            self._push_ws_event(receiver_type, int(receiver_id), "chat_message", data)
        if sender_type and sender_id:
            self._push_ws_event(sender_type, int(sender_id), "chat_message", data)

        return Response(data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"], url_path="read")
    def read_messages(self, request):
        message_ids = request.data.get("message_ids") or []
        if not isinstance(message_ids, list):
            return Response({"error": "message_ids must be a list"}, status=status.HTTP_400_BAD_REQUEST)

        user = request.user
        if not (is_admin_actor(user) or is_partner_actor(user)):
            return Response({"error": "Unauthorized actor"}, status=status.HTTP_403_FORBIDDEN)

        updated_count = mark_message_ids_read(
            message_ids=message_ids,
            receiver_user_id=user.id,
            receiver_role=user.role,
        )

        partner_id = request.data.get("partner_id")
        partner_type = request.data.get("partner_type")
        if partner_id and partner_type:
            try:
                self._push_ws_event(
                    str(partner_type),
                    int(partner_id),
                    "messages_read",
                    {
                        "partner_id": user.id,
                        "partner_type": "admin" if is_admin_actor(user) else "partner",
                        "message_ids": message_ids,
                    },
                )
            except (TypeError, ValueError):
                pass

        return Response({"updated": updated_count}, status=status.HTTP_200_OK)
