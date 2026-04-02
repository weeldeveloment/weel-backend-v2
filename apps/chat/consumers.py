import json
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


class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        query_params = parse_qs(self.scope["query_string"].decode())
        token = (query_params.get("token") or [None])[0]

        if not token:
            await self.close()
            return

        actor = await self.get_actor_from_token(token)
        if not actor:
            await self.close()
            return

        self.actor_type = actor["actor_type"]
        self.actor_id = actor["actor_id"]
        self.room_group_name = self._room_name(self.actor_type, self.actor_id)

        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "room_group_name"):
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

    async def receive(self, text_data):
        data = json.loads(text_data or "{}")
        message_type = data.get("type")

        if message_type == "message":
            await self.handle_message(data.get("data", {}))
        elif message_type == "read":
            await self.handle_read(data.get("data", {}))
        elif message_type == "typing":
            await self.handle_typing(data.get("data", {}))
        elif message_type == "ping":
            await self.send(text_data=json.dumps({"type": "pong", "data": {}}))

    async def handle_message(self, data):
        receiver_id = data.get("receiver_id")
        receiver_type = data.get("receiver_type")
        content = (data.get("content") or "").strip()

        if not receiver_id or not content:
            return

        message = await self.save_message(
            sender_type=self.actor_type,
            sender_id=self.actor_id,
            receiver_id=receiver_id,
            receiver_type=receiver_type,
            content=content,
        )
        if not message:
            return

        await self.channel_layer.group_send(
            self._room_name(message["receiver_type"], message["receiver_id"]),
            {"type": "chat_message", "message": message},
        )

        await self.send(text_data=json.dumps({"type": "message", "data": message}))

    async def handle_read(self, data):
        partner_id = data.get("partnerId")
        partner_type = data.get("partnerType")
        message_ids = data.get("messageIds") or []

        if message_ids:
            await self.mark_messages_as_read(message_ids)

            if partner_id and partner_type:
                await self.channel_layer.group_send(
                    self._room_name(partner_type, partner_id),
                    {
                        "type": "messages_read",
                        "partner_id": self.actor_id,
                        "partner_type": self.actor_type,
                        "message_ids": message_ids,
                    },
                )

    async def handle_typing(self, data):
        partner_id = data.get("partnerId")
        partner_type = data.get("partnerType")
        is_typing = data.get("isTyping", False)

        if partner_id and partner_type:
            await self.channel_layer.group_send(
                self._room_name(partner_type, partner_id),
                {
                    "type": "user_typing",
                    "user_id": self.actor_id,
                    "user_type": self.actor_type,
                    "is_typing": is_typing,
                },
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

            if actor_type not in {"admin", "partner"}:
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
            receiver_id = int(receiver_id)
        except (TypeError, ValueError):
            return None

        if sender_type not in {"admin", "partner"}:
            return None
        if receiver_type and receiver_type not in {"admin", "partner"}:
            return None

        try:
            if sender_type == "admin":
                admin = get_active_actor(sender_id, "admin")
                partner = get_active_actor(receiver_id, "partner")
                if not admin or not partner:
                    return None

                conversation = get_or_create_conversation(
                    admin_user_id=admin.id,
                    partner_user_id=partner.id,
                )
                message = create_chat_message(
                    conversation_id=conversation.id,
                    sender_user_id=admin.id,
                    receiver_user_id=partner.id,
                    sender_role="admin",
                    receiver_role="partner",
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
                    NotificationService.send_to_partner(
                        partner=partner,
                        title=sender_name,
                        message=message_preview,
                        data={
                            "type": "chat_message",
                            "conversation_id": conversation.id,
                            "message_id": message.id,
                            "sender_id": admin.id,
                            "sender_type": "admin",
                            "receiver_id": partner.id,
                            "receiver_type": "partner",
                            "message_preview": message_preview,
                            "sender_name": sender_name,
                        },
                    )
                except Exception as push_error:
                    print(f"Error sending partner push notification: {push_error}")
            else:
                partner = get_active_actor(sender_id, "partner")
                admin = get_active_actor(receiver_id, "admin")
                if not admin:
                    admin = get_first_active_admin()
                if not admin or not partner:
                    return None

                conversation = get_or_create_conversation(
                    admin_user_id=admin.id,
                    partner_user_id=partner.id,
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

            payload = ChatMessageSerializer(message).data
            return payload
        except Exception as exc:
            print(f"Error saving message: {exc}")
            return None

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
