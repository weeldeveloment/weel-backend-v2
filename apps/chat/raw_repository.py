from __future__ import annotations

from typing import Any

from django.utils import timezone

from shared.raw.db import execute, fetch_all, fetch_one
from shared.raw.entities import RawChatConversation, RawChatMessage, RawUser
from users.raw_repository import fetch_users_by_ids, get_user_by_id, list_active_admin_ids


def get_active_actor(actor_id: int, role: str) -> RawUser | None:
    return get_user_by_id(actor_id, role=role, active_only=True)


def get_first_active_admin() -> RawUser | None:
    admin_ids = list_active_admin_ids(limit=1)
    if not admin_ids:
        return None
    return get_user_by_id(admin_ids[0], role="admin", active_only=True)


def get_or_create_conversation(admin_user_id: int, partner_user_id: int) -> RawChatConversation:
    existing = fetch_one(
        """
        SELECT *
        FROM public.chat_conversation
        WHERE admin_user_id = %s
          AND partner_user_id = %s
        LIMIT 1
        """,
        [admin_user_id, partner_user_id],
    )
    if existing is not None:
        return RawChatConversation.from_row(existing)

    now = timezone.now()
    inserted = fetch_one(
        """
        INSERT INTO public.chat_conversation (
            created_at,
            updated_at,
            admin_user_id,
            partner_user_id
        ) VALUES (%s, %s, %s, %s)
        ON CONFLICT (admin_user_id, partner_user_id) DO NOTHING
        RETURNING *
        """,
        [now, now, admin_user_id, partner_user_id],
    )
    if inserted is not None:
        return RawChatConversation.from_row(inserted)

    row = fetch_one(
        """
        SELECT *
        FROM public.chat_conversation
        WHERE admin_user_id = %s
          AND partner_user_id = %s
        LIMIT 1
        """,
        [admin_user_id, partner_user_id],
    )
    if row is None:
        raise RuntimeError("Failed to fetch conversation after create")
    return RawChatConversation.from_row(row)


def list_conversations_for_actor(actor_id: int, actor_role: str) -> list[dict[str, Any]]:
    if actor_role == "admin":
        rows = fetch_all(
            """
            SELECT *
            FROM public.chat_conversation
            WHERE admin_user_id = %s
            ORDER BY updated_at DESC, id DESC
            """,
            [actor_id],
        )
        counterpart_ids = [int(row["partner_user_id"]) for row in rows]
        counterpart_field = "partner_user_id"
    elif actor_role == "partner":
        rows = fetch_all(
            """
            SELECT *
            FROM public.chat_conversation
            WHERE partner_user_id = %s
            ORDER BY updated_at DESC, id DESC
            """,
            [actor_id],
        )
        counterpart_ids = [int(row["admin_user_id"]) for row in rows]
        counterpart_field = "admin_user_id"
    else:
        return []

    if not rows:
        return []

    conversations = [RawChatConversation.from_row(row) for row in rows]
    conversation_ids = [conversation.id for conversation in conversations]
    counterparts = fetch_users_by_ids(counterpart_ids)

    last_messages = fetch_all(
        """
        SELECT DISTINCT ON (conversation_id) *
        FROM public.chat_message
        WHERE conversation_id = ANY(%s)
        ORDER BY conversation_id, created_at DESC, id DESC
        """,
        [conversation_ids],
    )
    last_message_by_conversation: dict[int, RawChatMessage] = {}
    for row in last_messages:
        message = RawChatMessage.from_row(row)
        last_message_by_conversation[message.conversation_id] = message

    unread_rows = fetch_all(
        """
        SELECT conversation_id, COUNT(*)::int AS unread_count
        FROM public.chat_message
        WHERE conversation_id = ANY(%s)
          AND receiver_user_id = %s
          AND receiver_role = %s
          AND is_read = FALSE
        GROUP BY conversation_id
        """,
        [conversation_ids, actor_id, actor_role],
    )
    unread_count_by_conversation = {
        int(row["conversation_id"]): int(row["unread_count"]) for row in unread_rows
    }

    payload: list[dict[str, Any]] = []
    for conversation in conversations:
        counterpart_id = int(getattr(conversation, counterpart_field))
        counterpart = counterparts.get(counterpart_id)
        if counterpart is None:
            continue
        payload.append(
            {
                "counterpart": counterpart,
                "conversation_id": conversation.id,
                "last_message": last_message_by_conversation.get(conversation.id),
                "unread_count": unread_count_by_conversation.get(conversation.id, 0),
            }
        )

    return payload


def list_messages_for_conversation(conversation_id: int) -> list[RawChatMessage]:
    rows = fetch_all(
        """
        SELECT *
        FROM public.chat_message
        WHERE conversation_id = %s
        ORDER BY created_at ASC, id ASC
        """,
        [conversation_id],
    )
    return [RawChatMessage.from_row(row) for row in rows]


def mark_conversation_messages_read(
    conversation_id: int, receiver_user_id: int, receiver_role: str
) -> int:
    return execute(
        """
        UPDATE public.chat_message
        SET is_read = TRUE,
            updated_at = %s
        WHERE conversation_id = %s
          AND receiver_user_id = %s
          AND receiver_role = %s
          AND is_read = FALSE
        """,
        [timezone.now(), conversation_id, receiver_user_id, receiver_role],
    )


def create_chat_message(
    *,
    conversation_id: int,
    sender_user_id: int,
    receiver_user_id: int,
    sender_role: str,
    receiver_role: str,
    content: str,
) -> RawChatMessage:
    now = timezone.now()
    row = fetch_one(
        """
        INSERT INTO public.chat_message (
            content,
            is_read,
            created_at,
            updated_at,
            conversation_id,
            sender_user_id,
            receiver_user_id,
            sender_role,
            receiver_role
        ) VALUES (%s, FALSE, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        [
            content,
            now,
            now,
            conversation_id,
            sender_user_id,
            receiver_user_id,
            sender_role,
            receiver_role,
        ],
    )
    if row is None:
        raise RuntimeError("Failed to create chat message")
    return RawChatMessage.from_row(row)


def touch_conversation(conversation_id: int) -> None:
    execute(
        """
        UPDATE public.chat_conversation
        SET updated_at = %s
        WHERE id = %s
        """,
        [timezone.now(), conversation_id],
    )


def mark_message_ids_read(message_ids: list[int], receiver_user_id: int, receiver_role: str) -> int:
    normalized_ids = [int(message_id) for message_id in message_ids if str(message_id).isdigit()]
    if not normalized_ids:
        return 0

    return execute(
        """
        UPDATE public.chat_message
        SET is_read = TRUE,
            updated_at = %s
        WHERE id = ANY(%s)
          AND receiver_user_id = %s
          AND receiver_role = %s
          AND is_read = FALSE
        """,
        [timezone.now(), normalized_ids, receiver_user_id, receiver_role],
    )
