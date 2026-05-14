from __future__ import annotations

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.utils.translation import gettext_lazy as _
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import BasePermission
from rest_framework.response import Response

from notification.raw_repository import mark_message_notifications_for_conversation
from notification.service import NotificationService
from users.authentication import ClientJWTAuthentication, PartnerJWTAuthentication

from .authentication import RawAdminJWTAuthentication
from .raw_repository import (
    ChatSchemaNotReadyError,
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
    """Accept authenticated admin, partner or client actor."""

    def has_permission(self, request, view):
        return request.user is not None and request.auth is not None


def is_admin_actor(user) -> bool:
    return getattr(user, "role", None) == "admin"


def is_partner_actor(user) -> bool:
    return getattr(user, "role", None) == "partner"


def is_client_actor(user) -> bool:
    return getattr(user, "role", None) == "client"


class ChatViewSet(viewsets.GenericViewSet):
    authentication_classes = [RawAdminJWTAuthentication, PartnerJWTAuthentication, ClientJWTAuthentication]
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
            payload = []
            for item in items:
                counterpart = item["counterpart"]
                role = getattr(counterpart, "role", None)
                if role == "partner":
                    serialized = ActorSerializer.from_partner(counterpart)
                elif role == "client":
                    serialized = ActorSerializer.from_client(counterpart)
                else:
                    continue
                payload.append(
                    {
                        "counterpart": serialized,
                        "conversation_id": item["conversation_id"],
                        "last_message": item["last_message"],
                        "unread_count": item["unread_count"],
                    }
                )
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
        elif is_client_actor(user):
            items = list_conversations_for_actor(user.id, "client")
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
        requested_role = (
            request.query_params.get("role")
            or request.query_params.get("counterpart_role")
            or request.query_params.get("receiver_type")
            or request.query_params.get("partner_type")
            or ""
        ).lower()

        try:
            counterpart_id = int(partner_id)
        except (TypeError, ValueError):
            return Response({"error": _("Invalid partner id")}, status=status.HTTP_400_BAD_REQUEST)

        if is_admin_actor(user):
            target_role = "client" if requested_role == "client" else "partner"
            counterpart = get_active_actor(counterpart_id, target_role)
            if not counterpart:
                return Response({"error": _("%(role)s not found") % {"role": target_role.capitalize()}}, status=status.HTTP_404_NOT_FOUND)
            try:
                conversation = get_or_create_conversation(
                    admin_user_id=user.id,
                    counterpart_user_id=counterpart.id,
                    counterpart_role=target_role,
                )
            except ChatSchemaNotReadyError as exc:
                return Response({"error": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        elif is_partner_actor(user):
            admin_user = get_active_actor(counterpart_id, "admin")
            if not admin_user:
                return Response({"error": _("Admin user not found")}, status=status.HTTP_404_NOT_FOUND)
            conversation = get_or_create_conversation(
                admin_user_id=admin_user.id,
                counterpart_user_id=user.id,
                counterpart_role="partner",
            )
        elif is_client_actor(user):
            admin_user = get_active_actor(counterpart_id, "admin")
            if not admin_user:
                admin_user = get_first_active_admin()
            if not admin_user:
                return Response({"error": _("No admin user available")}, status=status.HTTP_400_BAD_REQUEST)

            conversation = get_or_create_conversation(
                admin_user_id=admin_user.id,
                counterpart_user_id=user.id,
                counterpart_role="client",
            )
        else:
            return Response({"error": _("Unauthorized actor")}, status=status.HTTP_403_FORBIDDEN)

        messages = list_messages_for_conversation(conversation.id)
        mark_conversation_messages_read(
            conversation_id=conversation.id,
            receiver_user_id=user.id,
            receiver_role=user.role,
        )
        mark_message_notifications_for_conversation(
            recipient_user_id=user.id,
            recipient_role=user.role,
            conversation_id=conversation.id,
        )

        serializer = ChatMessageSerializer(messages, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=["get"], url_path="recipient/admin")
    def admin_recipient(self, request):
        """Return the single active admin recipient for partner chat."""
        admin_user = get_first_active_admin()
        if not admin_user:
            return Response({"error": _("Admin user not found")}, status=status.HTTP_404_NOT_FOUND)

        return Response(ActorSerializer.from_admin(admin_user), status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"])
    def send(self, request):
        """Send a message to counterpart actor."""
        raw_receiver_id = request.data.get("receiver_id")
        receiver_type_param = (
            request.data.get("receiver_type")
            or request.data.get("counterpart_type")
            or request.data.get("partner_type")
            or ""
        ).strip().lower()
        content = (request.data.get("content") or "").strip()
        if raw_receiver_id in (None, "") or not content:
            return Response(
                {"error": _("receiver_id and content are required")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            requested_receiver_id = int(raw_receiver_id)
        except (TypeError, ValueError):
            return Response({"error": _("Invalid receiver_id")}, status=status.HTTP_400_BAD_REQUEST)

        sender = request.user
        if is_admin_actor(sender):
            target_role = "client" if receiver_type_param == "client" else "partner"
            counterpart = get_active_actor(requested_receiver_id, target_role)
            if not counterpart:
                return Response({"error": _("%(role)s not found") % {"role": target_role.capitalize()}}, status=status.HTTP_404_NOT_FOUND)

            try:
                conversation = get_or_create_conversation(
                    admin_user_id=sender.id,
                    counterpart_user_id=counterpart.id,
                    counterpart_role=target_role,
                )
            except ChatSchemaNotReadyError as exc:
                return Response({"error": str(exc)}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
            message = create_chat_message(
                conversation_id=conversation.id,
                sender_user_id=sender.id,
                receiver_user_id=counterpart.id,
                sender_role="admin",
                receiver_role=target_role,
                content=content,
            )
            touch_conversation(conversation.id)

            sender_name = (
                f"{(sender.first_name or '').strip()} {(sender.last_name or '').strip()}".strip()
                or sender.username
                or "Admin"
            )
            message_preview = content if len(content) <= 120 else f"{content[:117]}..."
            notification_payload = {
                "type": "chat_message",
                "conversation_id": conversation.id,
                "message_id": message.id,
                "sender_id": sender.id,
                "sender_type": "admin",
                "receiver_id": counterpart.id,
                "receiver_type": target_role,
                "message_preview": message_preview,
                "sender_name": sender_name,
            }
            if target_role == "partner":
                NotificationService.send_to_partner(
                    partner=counterpart,
                    title=sender_name,
                    message=message_preview,
                    notification_type="message",
                    data=notification_payload,
                )
            else:
                NotificationService.send_to_client(
                    client=counterpart,
                    title=sender_name,
                    message=message_preview,
                    notification_type="message",
                    data=notification_payload,
                )
        elif is_partner_actor(sender):
            admin_user = get_active_actor(requested_receiver_id, "admin")
            if not admin_user:
                admin_user = get_first_active_admin()
            if not admin_user:
                return Response({"error": _("No admin user available")}, status=status.HTTP_400_BAD_REQUEST)

            conversation = get_or_create_conversation(
                admin_user_id=admin_user.id,
                counterpart_user_id=sender.id,
                counterpart_role="partner",
            )
            message = create_chat_message(
                conversation_id=conversation.id,
                sender_user_id=sender.id,
                receiver_user_id=admin_user.id,
                sender_role="partner",
                receiver_role="admin",
                content=content,
            )
            touch_conversation(conversation.id)
        elif is_client_actor(sender):
            admin_user = get_active_actor(requested_receiver_id, "admin")
            if not admin_user:
                admin_user = get_first_active_admin()
            if not admin_user:
                return Response({"error": "No admin user available"}, status=status.HTTP_400_BAD_REQUEST)

            conversation = get_or_create_conversation(
                admin_user_id=admin_user.id,
                counterpart_user_id=sender.id,
                counterpart_role="client",
            )
            message = create_chat_message(
                conversation_id=conversation.id,
                sender_user_id=sender.id,
                receiver_user_id=admin_user.id,
                sender_role="client",
                receiver_role="admin",
                content=content,
            )
            touch_conversation(conversation.id)
        else:
            return Response({"error": _("Unauthorized actor")}, status=status.HTTP_403_FORBIDDEN)

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
            return Response({"error": _("message_ids must be a list")}, status=status.HTTP_400_BAD_REQUEST)

        user = request.user
        if not (is_admin_actor(user) or is_partner_actor(user) or is_client_actor(user)):
            return Response({"error": _("Unauthorized actor")}, status=status.HTTP_403_FORBIDDEN)

        updated_count = mark_message_ids_read(
            message_ids=message_ids,
            receiver_user_id=user.id,
            receiver_role=user.role,
        )

        raw_conversation_id = request.data.get("conversation_id")
        if raw_conversation_id is not None:
            try:
                mark_message_notifications_for_conversation(
                    recipient_user_id=user.id,
                    recipient_role=user.role,
                    conversation_id=int(raw_conversation_id),
                )
            except (TypeError, ValueError):
                pass

        counterpart_id = request.data.get("partner_id") or request.data.get("counterpart_id")
        counterpart_type = request.data.get("partner_type") or request.data.get("counterpart_type")
        if counterpart_id and counterpart_type:
            try:
                self._push_ws_event(
                    str(counterpart_type),
                    int(counterpart_id),
                    "messages_read",
                    {
                        "partner_id": user.id,
                        "partner_type": (
                            "admin"
                            if is_admin_actor(user)
                            else "partner"
                            if is_partner_actor(user)
                            else "client"
                        ),
                        "message_ids": message_ids,
                    },
                )
            except (TypeError, ValueError):
                pass

        return Response({"updated": updated_count}, status=status.HTTP_200_OK)
