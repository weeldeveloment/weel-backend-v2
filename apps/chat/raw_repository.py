from __future__ import annotations

from typing import Any

from django.utils import timezone

from admin_auth.policy import is_email_allowed_for_admin
from shared.raw.compat import get_table_name, is_postgresql, return_star
from shared.raw.db import execute, fetch_all, fetch_one
from shared.raw.entities import RawChatConversation, RawChatMessage, RawUser
from users.raw_repository import fetch_users_by_ids, get_user_by_id, list_active_admin_ids


_client_conversation_schema_ready = False


def _ensure_client_conversation_schema() -> None:
    global _client_conversation_schema_ready
    if _client_conversation_schema_ready:
        return

    if not is_postgresql():
        _client_conversation_schema_ready = True
        return

    table_name = get_table_name("chat_conversation")
    execute(
        f"""
        ALTER TABLE {table_name}
        ADD COLUMN IF NOT EXISTS client_user_id BIGINT NULL
        """
    )
    execute(
        f"""
        CREATE INDEX IF NOT EXISTS chat_conversation_client_updated_idx
            ON {table_name} (client_user_id, updated_at DESC)
        """
    )
    execute(
        f"""
        CREATE UNIQUE INDEX IF NOT EXISTS chat_conversation_admin_client_unique
            ON {table_name} (admin_user_id, client_user_id)
            WHERE client_user_id IS NOT NULL
        """
    )
    _client_conversation_schema_ready = True


def get_active_actor(actor_id: int, role: str) -> RawUser | None:
    return get_user_by_id(actor_id, role=role, active_only=True)


def get_allowed_admin_by_id(actor_id: int) -> RawUser | None:
    admin = get_user_by_id(actor_id, role="admin", active_only=True)
    if not admin:
        return None
    if not is_email_allowed_for_admin(getattr(admin, "email", None)):
        return None
    return admin


def get_first_active_admin() -> RawUser | None:
    admin_ids = list_active_admin_ids(limit=100)
    if not admin_ids:
        return None
    for admin_id in admin_ids:
        admin = get_allowed_admin_by_id(admin_id)
        if admin:
            return admin
    return None


def get_or_create_conversation(*, admin_user_id: int, counterpart_user_id: int, counterpart_role: str) -> RawChatConversation:
    if counterpart_role not in {"partner", "client"}:
        raise ValueError("counterpart_role must be 'partner' or 'client'")

    if counterpart_role == "client":
        _ensure_client_conversation_schema()

    counterpart_column = "partner_user_id" if counterpart_role == "partner" else "client_user_id"

    existing = fetch_one(
        f"""
        SELECT *
        FROM {get_table_name("chat_conversation")}
        WHERE admin_user_id = %s
          AND {counterpart_column} = %s
        LIMIT 1
        """,
        [admin_user_id, counterpart_user_id],
    )
    if existing is not None:
        return RawChatConversation.from_row(existing)

    now = timezone.now()
    partner_value = counterpart_user_id if counterpart_role == "partner" else None
    client_value = counterpart_user_id if counterpart_role == "client" else None

    inserted = fetch_one(
        f"""
        INSERT INTO {get_table_name("chat_conversation")} (
            created_at,
            updated_at,
            admin_user_id,
            partner_user_id,
            client_user_id
        ) VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        {"RETURNING *" if return_star() else ""}
        """,
        [now, now, admin_user_id, partner_value, client_value],
    )
    if inserted is not None:
        return RawChatConversation.from_row(inserted)

    row = fetch_one(
        f"""
        SELECT *
        FROM {get_table_name("chat_conversation")}
        WHERE admin_user_id = %s
          AND {counterpart_column} = %s
        LIMIT 1
        """,
        [admin_user_id, counterpart_user_id],
    )
    if row is None:
        raise RuntimeError("Failed to fetch conversation after create")
    return RawChatConversation.from_row(row)


def list_conversations_for_actor(actor_id: int, actor_role: str) -> list[dict[str, Any]]:
    if actor_role in {"client", "admin"}:
        _ensure_client_conversation_schema()

    if actor_role == "admin":
        rows = fetch_all(
            f"""
            SELECT *
            FROM {get_table_name("chat_conversation")}
            WHERE admin_user_id = %s
            ORDER BY updated_at DESC, id DESC
            """,
            [actor_id],
        )
    elif actor_role == "partner":
        rows = fetch_all(
            f"""
            SELECT *
            FROM {get_table_name("chat_conversation")}
            WHERE partner_user_id = %s
            ORDER BY updated_at DESC, id DESC
            """,
            [actor_id],
        )
    elif actor_role == "client":
        rows = fetch_all(
            f"""
            SELECT *
            FROM {get_table_name("chat_conversation")}
            WHERE client_user_id = %s
            ORDER BY updated_at DESC, id DESC
            """,
            [actor_id],
        )
    else:
        return []

    if not rows:
        return []

    conversations = [RawChatConversation.from_row(row) for row in rows]
    conversation_ids = [conversation.id for conversation in conversations]

    counterpart_ids: list[int] = []
    if actor_role == "admin":
        for conversation in conversations:
            if conversation.partner_user_id is not None:
                counterpart_ids.append(int(conversation.partner_user_id))
            elif conversation.client_user_id is not None:
                counterpart_ids.append(int(conversation.client_user_id))
    else:
        counterpart_ids = [int(conversation.admin_user_id) for conversation in conversations]

    counterparts = fetch_users_by_ids(counterpart_ids)

    if is_postgresql():
        last_messages = fetch_all(
            f"""
            SELECT DISTINCT ON (conversation_id) *
            FROM {get_table_name("chat_message")}
            WHERE conversation_id = ANY(%s)
            ORDER BY conversation_id, created_at DESC, id DESC
            """,
            [conversation_ids],
        )
    else:
        placeholders = ','.join(['%s'] * len(conversation_ids))
        last_messages = fetch_all(
            f"""
            SELECT *
            FROM {get_table_name("chat_message")}
            WHERE conversation_id IN ({placeholders})
            ORDER BY conversation_id, created_at DESC, id DESC
            """,
            conversation_ids,
        )
    last_message_by_conversation: dict[int, RawChatMessage] = {}
    for row in last_messages:
        message = RawChatMessage.from_row(row)
        last_message_by_conversation[message.conversation_id] = message

    if is_postgresql():
        unread_rows = fetch_all(
            f"""
            SELECT conversation_id, COUNT(*) AS unread_count
            FROM {get_table_name("chat_message")}
            WHERE conversation_id = ANY(%s)
              AND receiver_user_id = %s
              AND receiver_role = %s
              AND is_read = FALSE
            GROUP BY conversation_id
            """,
            [conversation_ids, actor_id, actor_role],
        )
    else:
        placeholders = ','.join(['%s'] * len(conversation_ids))
        unread_rows = fetch_all(
            f"""
            SELECT conversation_id, COUNT(*) AS unread_count
            FROM {get_table_name("chat_message")}
            WHERE conversation_id IN ({placeholders})
              AND receiver_user_id = %s
              AND receiver_role = %s
              AND is_read = FALSE
            GROUP BY conversation_id
            """,
            conversation_ids + [actor_id, actor_role],
        )
    unread_count_by_conversation = {
        int(row["conversation_id"]): int(row["unread_count"]) for row in unread_rows
    }

    payload: list[dict[str, Any]] = []
    for conversation in conversations:
        if actor_role == "admin":
            counterpart_id = conversation.partner_user_id or conversation.client_user_id
        else:
            counterpart_id = conversation.admin_user_id

        if counterpart_id is None:
            continue

        counterpart = counterparts.get(int(counterpart_id))
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
        f"""
        SELECT *
        FROM {get_table_name("chat_message")}
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
        f"""
        UPDATE {get_table_name("chat_message")}
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
        f"""
        INSERT INTO {get_table_name("chat_message")} (
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
        {"RETURNING *" if return_star() else ""}
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
    if row is None and not return_star():
        row = fetch_one(
            f"SELECT * FROM {get_table_name('chat_message')} WHERE conversation_id = %s AND sender_user_id = %s ORDER BY id DESC LIMIT 1",
            [conversation_id, sender_user_id],
        )
    if row is None:
        raise RuntimeError("Failed to create chat message")
    return RawChatMessage.from_row(row)


def touch_conversation(conversation_id: int) -> None:
    execute(
        f"""
        UPDATE {get_table_name("chat_conversation")}
        SET updated_at = %s
        WHERE id = %s
        """,
        [timezone.now(), conversation_id],
    )


def mark_message_ids_read(message_ids: list[int], receiver_user_id: int, receiver_role: str) -> int:
    normalized_ids = [int(message_id) for message_id in message_ids if str(message_id).isdigit()]
    if not normalized_ids:
        return 0

    if is_postgresql():
        return execute(
            f"""
            UPDATE {get_table_name("chat_message")}
            SET is_read = TRUE,
                updated_at = %s
            WHERE id = ANY(%s)
              AND receiver_user_id = %s
              AND receiver_role = %s
              AND is_read = FALSE
            """,
            [timezone.now(), normalized_ids, receiver_user_id, receiver_role],
        )
    else:
        placeholders = ','.join(['%s'] * len(normalized_ids))
        return execute(
            f"""
            UPDATE {get_table_name("chat_message")}
            SET is_read = TRUE,
                updated_at = %s
            WHERE id IN ({placeholders})
              AND receiver_user_id = %s
              AND receiver_role = %s
              AND is_read = FALSE
            """,
            [timezone.now()] + normalized_ids + [receiver_user_id, receiver_role],
        )
