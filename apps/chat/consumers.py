import json
from typing import Any
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

from notification.service import NotificationService
from users.raw_repository import get_active_user_by_subject
from users.tokens import TokenMetadata

from .raw_repository import (
    create_chat_message,
    get_active_actor,
    get_first_active_admin,
    get_or_create_conversation,
    mark_message_ids_read,
    touch_conversation,
)
from .serializers import ChatMessageSerializer

_WS_CLOSE_TOKEN_MISSING = 4401
_WS_CLOSE_TOKEN_INVALID = 4402

_ERROR_MESSAGES = {
    "invalid_json": "Invalid JSON payload.",
    "unknown_message_type": "Unknown message type.",
    "empty_content": "Message content cannot be empty.",
    "missing_receiver_id": "receiver_id is required for admin messages.",
    "invalid_receiver_id": "receiver_id must be a valid integer.",
    "invalid_receiver_type": "receiver_type must be one of: admin, partner, client.",
    "sender_not_found": "Sender account was not found or is inactive.",
    "receiver_not_found": "Receiver was not found or is inactive.",
    "no_admin_available": "No admin is available to receive the message.",
    "save_failed": "Could not save the message.",
    "read_incomplete_recipient": "read requires both partnerId and partnerType when notifying the other party.",
    "typing_missing_recipient": "typing requires partnerId and partnerType (or partner_id and partner_type).",
    "invalid_message_ids": "messageIds must be a non-empty list of integers.",
}


def _first_mapping(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def _coerce_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_int_list(value: Any) -> list[int] | None:
    if not isinstance(value, list):
        return None
    out: list[int] = []
    for item in value:
        n = _coerce_int(item)
        if n is None:
            return None
        out.append(n)
    return out


class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        query_params = parse_qs(self.scope["query_string"].decode())
        token = (query_params.get("token") or [None])[0]

        if not token:
            await self.close(code=_WS_CLOSE_TOKEN_MISSING)
            return

        actor = await self.get_actor_from_token(token)
        if not actor:
            await self.close(code=_WS_CLOSE_TOKEN_INVALID)
            return

        self.actor_type = actor["actor_type"]
        self.actor_id = actor["actor_id"]
        self.room_group_name = self._room_name(self.actor_type, self.actor_id)

        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "room_group_name"):
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def send_error(self, code: str, *, http_status: int | None = None):
        payload: dict[str, Any] = {
            "type": "error",
            "data": {
                "code": code,
                "message": _ERROR_MESSAGES.get(code, "An error occurred."),
            },
        }
        if http_status is not None:
            payload["data"]["http_status"] = http_status
        await self.send(text_data=json.dumps(payload))

    async def receive(self, text_data):
        try:
            data = json.loads(text_data or "{}")
        except json.JSONDecodeError:
            await self.send_error("invalid_json")
            return

        if not isinstance(data, dict):
            await self.send_error("invalid_json")
            return

        message_type = data.get("type")

        if message_type == "message":
            await self.handle_message(data.get("data") or {})
        elif message_type == "read":
            await self.handle_read(data.get("data") or {})
        elif message_type == "typing":
            await self.handle_typing(data.get("data") or {})
        elif message_type == "ping":
            await self.send(text_data=json.dumps({"type": "pong", "data": {}}))
        else:
            await self.send_error("unknown_message_type")

    async def handle_message(self, data):
        if not isinstance(data, dict):
            data = {}

        receiver_raw = _first_mapping(data, "receiver_id", "receiverId")
        receiver_type = _first_mapping(data, "receiver_type", "receiverType")
        content = (data.get("content") or "").strip()

        if not content:
            await self.send_error("empty_content")
            return

        if self.actor_type == "admin":
            receiver_id = _coerce_int(receiver_raw)
            if receiver_id is None:
                await self.send_error("missing_receiver_id")
                return
        else:
            if receiver_raw is None or receiver_raw == "":
                receiver_id = None
            else:
                receiver_id = _coerce_int(receiver_raw)
                if receiver_id is None:
                    await self.send_error("invalid_receiver_id")
                    return

            # Only admin chat is supported for partner/client. Ignore mistaken receiver_type
            # (e.g. mobile copied admin payload with receiver_type "partner").
            receiver_type = "admin"

        message, err = await self.save_message(
            sender_type=self.actor_type,
            sender_id=self.actor_id,
            receiver_id=receiver_id,
            receiver_type=receiver_type,
            content=content,
        )
        if err:
            await self.send_error(err)
            return

        await self.channel_layer.group_send(
            self._room_name(message["receiver_type"], message["receiver_id"]),
            {"type": "chat_message", "message": message},
        )

        await self.send(text_data=json.dumps({"type": "message", "data": message}))

    async def handle_read(self, data):
        if not isinstance(data, dict):
            data = {}

        partner_id = _coerce_int(_first_mapping(data, "partnerId", "partner_id"))
        partner_type = _first_mapping(data, "partnerType", "partner_type")
        raw_ids = _first_mapping(data, "messageIds", "message_ids") or []

        message_ids = _coerce_int_list(raw_ids) if raw_ids else []
        if raw_ids and message_ids is None:
            await self.send_error("invalid_message_ids")
            return
        if not message_ids:
            return

        await self.mark_messages_as_read(message_ids)

        has_partner = bool(partner_id) or bool(partner_type)
        if has_partner:
            if not partner_id or not partner_type:
                await self.send_error("read_incomplete_recipient")
                return
            await self.channel_layer.group_send(
                self._room_name(partner_type, partner_id),
                {
                    "type": "messages_read",
                    "partner_id": self.actor_id,
                    "partner_type": self.actor_type,
                    "message_ids": message_ids,
                },
            )

        await self.send(
            text_data=json.dumps({"type": "read_ack", "data": {"messageIds": message_ids}})
        )

    async def handle_typing(self, data):
        if not isinstance(data, dict):
            data = {}

        partner_id = _coerce_int(_first_mapping(data, "partnerId", "partner_id"))
        partner_type = _first_mapping(data, "partnerType", "partner_type")
        is_typing = _first_mapping(data, "isTyping", "is_typing")
        if is_typing is None:
            is_typing = False

        if not partner_id or not partner_type:
            await self.send_error("typing_missing_recipient")
            return

        is_typing_bool = bool(is_typing)
        await self.channel_layer.group_send(
            self._room_name(partner_type, partner_id),
            {
                "type": "user_typing",
                "user_id": self.actor_id,
                "user_type": self.actor_type,
                "is_typing": is_typing_bool,
            },
        )

        await self.send(
            text_data=json.dumps(
                {
                    "type": "typing_ack",
                    "data": {
                        "partnerId": partner_id,
                        "partnerType": partner_type,
                        "isTyping": is_typing_bool,
                    },
                }
            )
        )

    async def chat_message(self, event):
        await self.send(text_data=json.dumps({"type": "message", "data": event["message"]}))

    async def messages_read(self, event):
        await self.send(
            text_data=json.dumps(
                {
                    "type": "read",
                    "data": {
                        "partnerId": event["partner_id"],
                        "partnerType": event["partner_type"],
                        "messageIds": event["message_ids"],
                    },
                }
            )
        )

    async def user_typing(self, event):
        await self.send(
            text_data=json.dumps(
                {
                    "type": "typing",
                    "data": {
                        "userId": event["user_id"],
                        "userType": event["user_type"],
                        "isTyping": event["is_typing"],
                    },
                }
            )
        )

    @staticmethod
    def _room_name(actor_type, actor_id):
        return f"chat_{actor_type}_{actor_id}"

    @database_sync_to_async
    def get_actor_from_token(self, token):
        try:
            access_token = AccessToken(token)
            actor_type = access_token.get(TokenMetadata.TOKEN_USER_TYPE)
            subject = access_token.get(TokenMetadata.TOKEN_SUBJECT)

            if actor_type not in {"admin", "partner", "client"}:
                return None

            actor = get_active_user_by_subject(subject, role=actor_type)
            if not actor:
                return None

            return {"actor_type": actor_type, "actor_id": actor.id}
        except (TokenError, ValueError):
            return None

    @database_sync_to_async
    def save_message(self, sender_type, sender_id, receiver_id, receiver_type, content):
        try:
            sender_id = int(sender_id)
        except (TypeError, ValueError):
            return None, "save_failed"

        allowed_roles = {"admin", "partner", "client"}
        if sender_type not in allowed_roles:
            return None, "save_failed"

        try:
            if sender_type == "admin":
                try:
                    rid = int(receiver_id)
                except (TypeError, ValueError):
                    return None, "invalid_receiver_id"

                if not receiver_type:
                    receiver_type = "partner"
                if receiver_type not in allowed_roles or receiver_type == "admin":
                    return None, "invalid_receiver_type"

                admin = get_active_actor(sender_id, "admin")
                target = get_active_actor(rid, receiver_type)
                if not admin:
                    return None, "sender_not_found"
                if not target:
                    return None, "receiver_not_found"

                conversation = get_or_create_conversation(
                    admin_user_id=admin.id,
                    counterpart_user_id=target.id,
                    counterpart_role="partner" if receiver_type == "partner" else "client",
                )
                message = create_chat_message(
                    conversation_id=conversation.id,
                    sender_user_id=admin.id,
                    receiver_user_id=target.id,
                    sender_role="admin",
                    receiver_role=receiver_type,
                    content=content,
                )
                touch_conversation(conversation.id)

                sender_name = (
                    f"{(admin.first_name or '').strip()} {(admin.last_name or '').strip()}".strip()
                    or admin.username
                    or "Admin"
                )
                message_preview = content if len(content) <= 120 else f"{content[:117]}..."
                try:
                    notification_payload = {
                        "type": "chat_message",
                        "conversation_id": conversation.id,
                        "message_id": message.id,
                        "sender_id": admin.id,
                        "sender_type": "admin",
                        "receiver_id": target.id,
                        "receiver_type": receiver_type,
                        "message_preview": message_preview,
                        "sender_name": sender_name,
                    }
                    if receiver_type == "partner":
                        NotificationService.send_to_partner(
                            partner=target,
                            title=sender_name,
                            message=message_preview,
                            notification_type="message",
                            data=notification_payload,
                        )
                    elif receiver_type == "client":
                        NotificationService.send_to_client(
                            client=target,
                            title=sender_name,
                            message=message_preview,
                            notification_type="message",
                            data=notification_payload,
                        )
                except Exception as push_error:
                    print(f"Error sending partner push notification: {push_error}")
            elif sender_type == "partner":
                partner = get_active_actor(sender_id, "partner")
                if not partner:
                    return None, "sender_not_found"

                admin = None
                if receiver_id is not None:
                    admin = get_active_actor(receiver_id, "admin")
                if not admin:
                    admin = get_first_active_admin()
                if not admin:
                    return None, "no_admin_available"

                conversation = get_or_create_conversation(
                    admin_user_id=admin.id,
                    counterpart_user_id=partner.id,
                    counterpart_role="partner",
                )
                message = create_chat_message(
                    conversation_id=conversation.id,
                    sender_user_id=partner.id,
                    receiver_user_id=admin.id,
                    sender_role="partner",
                    receiver_role="admin",
                    content=content,
                )
                touch_conversation(conversation.id)
            else:
                client = get_active_actor(sender_id, "client")
                if not client:
                    return None, "sender_not_found"

                admin = None
                if receiver_id is not None:
                    admin = get_active_actor(receiver_id, "admin")
                if not admin:
                    admin = get_first_active_admin()
                if not admin:
                    return None, "no_admin_available"

                conversation = get_or_create_conversation(
                    admin_user_id=admin.id,
                    counterpart_user_id=client.id,
                    counterpart_role="client",
                )
                message = create_chat_message(
                    conversation_id=conversation.id,
                    sender_user_id=client.id,
                    receiver_user_id=admin.id,
                    sender_role="client",
                    receiver_role="admin",
                    content=content,
                )
                touch_conversation(conversation.id)

            payload = ChatMessageSerializer(message).data
            return payload, None
        except Exception as exc:
            print(f"Error saving message: {exc}")
            return None, "save_failed"

    @database_sync_to_async
    def mark_messages_as_read(self, message_ids):
        try:
            mark_message_ids_read(
                message_ids=message_ids,
                receiver_user_id=self.actor_id,
                receiver_role=self.actor_type,
            )
        except Exception as exc:
            print(f"Error marking messages as read: {exc}")
