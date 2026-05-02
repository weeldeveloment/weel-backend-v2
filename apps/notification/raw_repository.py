from __future__ import annotations

import json
import uuid
from typing import Any

from django.utils import timezone

from shared.raw.compat import get_table_name, is_postgresql
from shared.raw.db import execute, fetch_all, fetch_one

NOTIFICATION_TABLE = get_table_name("notification")


def _payload_for_db(payload: dict[str, Any] | None) -> Any:
    blob: dict[str, Any] = dict(payload or {})
    if is_postgresql():
        try:
            from psycopg2.extras import Json

            return Json(blob)
        except ImportError:
            pass
    return json.dumps(blob, ensure_ascii=False)


def _safe_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def create_notification(
    *,
    recipient_user_id: int | None,
    recipient_role: str | None,
    title: str | None,
    push_message: str | None,
    notification_type: str,
    status: str = "pending",
    is_for_every_one: bool = False,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO {NOTIFICATION_TABLE} (
            guid,
            created_at,
            updated_at,
            title,
            push_message,
            notification_type,
            status,
            is_for_every_one,
            recipient_user_id,
            recipient_role,
            payload
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        __RETURNING_MARKER__
        """,
        [
            uuid.uuid4(),
            now,
            now,
            title,
            push_message,
            notification_type,
            status,
            is_for_every_one,
            recipient_user_id,
            recipient_role,
            _payload_for_db(payload),
        ],
    )


def list_partner_notifications(partner_user_id: int, *, limit: int, offset: int) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT *
        FROM {NOTIFICATION_TABLE}
        WHERE recipient_role = 'partner'
          AND recipient_user_id = %s
        ORDER BY created_at DESC
        LIMIT %s OFFSET %s
        """,
        [partner_user_id, limit, offset],
    )


def list_client_notifications(client_user_id: int, *, limit: int, offset: int) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT *
        FROM {NOTIFICATION_TABLE}
        WHERE recipient_role = 'client'
          AND recipient_user_id = %s
        ORDER BY created_at DESC
        LIMIT %s OFFSET %s
        """,
        [client_user_id, limit, offset],
    )


def count_partner_notifications(partner_user_id: int) -> dict[str, int]:
    row = fetch_one(
        f"""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN COALESCE(status, '') <> 'read' THEN 1 ELSE 0 END) AS unread_count
        FROM {NOTIFICATION_TABLE}
        WHERE recipient_role = 'partner'
          AND recipient_user_id = %s
        """,
        [partner_user_id],
    )
    return {
        "total": _safe_int((row or {}).get("total", 0), 0),
        "unread_count": _safe_int((row or {}).get("unread_count", 0), 0),
    }


def count_client_notifications(client_user_id: int) -> dict[str, int]:
    row = fetch_one(
        f"""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN COALESCE(status, '') <> 'read' THEN 1 ELSE 0 END) AS unread_count
        FROM {NOTIFICATION_TABLE}
        WHERE recipient_role = 'client'
          AND recipient_user_id = %s
        """,
        [client_user_id],
    )
    return {
        "total": _safe_int((row or {}).get("total", 0), 0),
        "unread_count": _safe_int((row or {}).get("unread_count", 0), 0),
    }


def mark_partner_notifications_as_read(partner_user_id: int, notification_guids: list[str] | None = None) -> int:
    now = timezone.now()
    if notification_guids:
        return execute(
            f"""
            UPDATE {NOTIFICATION_TABLE}
            SET status = 'read',
                updated_at = %s
            WHERE recipient_role = 'partner'
              AND recipient_user_id = %s
              AND guid = ANY(%s::uuid[])
              AND COALESCE(status, '') <> 'read'
            """,
            [now, partner_user_id, notification_guids],
        )

    return execute(
        f"""
        UPDATE {NOTIFICATION_TABLE}
        SET status = 'read',
            updated_at = %s
        WHERE recipient_role = 'partner'
          AND recipient_user_id = %s
          AND COALESCE(status, '') <> 'read'
        """,
        [now, partner_user_id],
    )


def mark_client_notifications_as_read(client_user_id: int, notification_guids: list[str] | None = None) -> int:
    now = timezone.now()
    if notification_guids:
        return execute(
            f"""
            UPDATE {NOTIFICATION_TABLE}
            SET status = 'read',
                updated_at = %s
            WHERE recipient_role = 'client'
              AND recipient_user_id = %s
              AND guid = ANY(%s::uuid[])
              AND COALESCE(status, '') <> 'read'
            """,
            [now, client_user_id, notification_guids],
        )

    return execute(
        f"""
        UPDATE {NOTIFICATION_TABLE}
        SET status = 'read',
            updated_at = %s
        WHERE recipient_role = 'client'
          AND recipient_user_id = %s
          AND COALESCE(status, '') <> 'read'
        """,
        [now, client_user_id],
    )


def mark_message_notifications_for_conversation(
    *,
    recipient_user_id: int,
    recipient_role: str,
    conversation_id: int,
) -> int:
    """Mark push notifications tied to a chat conversation as read (messages section)."""
    now = timezone.now()
    if is_postgresql():
        return execute(
            f"""
            UPDATE {NOTIFICATION_TABLE}
            SET status = 'read',
                updated_at = %s
            WHERE recipient_user_id = %s
              AND recipient_role = %s
              AND notification_type = 'message'
              AND COALESCE(status, '') <> 'read'
              AND (payload->>'conversation_id') = %s
            """,
            [now, recipient_user_id, recipient_role, str(int(conversation_id))],
        )
    return execute(
        f"""
        UPDATE {NOTIFICATION_TABLE}
        SET status = 'read',
            updated_at = %s
        WHERE recipient_user_id = %s
          AND recipient_role = %s
          AND notification_type = 'message'
          AND COALESCE(status, '') <> 'read'
          AND CAST(json_extract(payload, '$.conversation_id') AS INTEGER) = %s
        """,
        [now, recipient_user_id, recipient_role, int(conversation_id)],
    )
