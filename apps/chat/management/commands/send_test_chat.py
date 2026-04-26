import os

from django.core.management.base import BaseCommand

from apps.chat.raw_repository import (
    create_chat_message,
    get_first_active_admin,
    get_or_create_conversation,
    touch_conversation,
)
from apps.notification.service import NotificationService
from apps.users.raw_repository import get_active_user_by_phone


class Command(BaseCommand):
    help = "Send a test chat message from admin to a client by phone number"

    def add_arguments(self, parser):
        parser.add_argument("phone", type=str, help="Client phone number")
        parser.add_argument("--message", type=str, default="Test message", help="Message content")

    def handle(self, *args, **options):
        phone = options["phone"]
        content = options["message"]

        client = get_active_user_by_phone(phone, role="client")
        if not client:
            self.stderr.write(self.style.ERROR(f"Active client with phone {phone} not found"))
            return

        admin = get_first_active_admin()
        if not admin:
            self.stderr.write(self.style.ERROR("No active admin found"))
            return

        conversation = get_or_create_conversation(
            admin_user_id=admin.id,
            counterpart_user_id=client.id,
            counterpart_role="client",
        )

        message = create_chat_message(
            conversation_id=conversation.id,
            sender_user_id=admin.id,
            receiver_user_id=client.id,
            sender_role="admin",
            receiver_role="client",
            content=content,
        )
        touch_conversation(conversation.id)

        sender_name = (
            f"{(admin.first_name or '').strip()} {(admin.last_name or '').strip()}".strip()
            or admin.username
            or "Admin"
        )
        message_preview = content if len(content) <= 120 else f"{content[:117]}..."
        notification_payload = {
            "type": "chat_message",
            "conversation_id": conversation.id,
            "message_id": message.id,
            "sender_id": admin.id,
            "sender_type": "admin",
            "receiver_id": client.id,
            "receiver_type": "client",
            "message_preview": message_preview,
            "sender_name": sender_name,
        }

        notification = NotificationService.send_to_client(
            client=client,
            title=sender_name,
            message=message_preview,
            notification_type="message",
            data=notification_payload,
        )

        self.stdout.write(self.style.SUCCESS(
            f"Message sent to client {client.id} ({phone}). "
            f"Message ID: {message.id}, "
            f"Notification ID: {notification.get('id') if notification else 'N/A'}"
        ))
