from __future__ import annotations

import uuid
from typing import Any

from django.utils import timezone

from shared.raw.db import execute, fetch_all, fetch_one


NOTIFICATION_TABLE = "public.notification"


def create_notification(
    *,
    recipient_user_id: int | None,
    recipient_role: str | None,
    title: str | None,
    push_message: str | None,
    notification_type: str,
    status: str = "pending",
    is_for_every_one: bool = False,
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
            recipient_role
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
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


def count_partner_notifications(partner_user_id: int) -> dict[str, int]:
    row = fetch_one(
        f"""
        SELECT
            COUNT(*)::int AS total,
            COUNT(*) FILTER (WHERE COALESCE(status, '') <> 'read')::int AS unread_count
        FROM {NOTIFICATION_TABLE}
        WHERE recipient_role = 'partner'
          AND recipient_user_id = %s
        """,
        [partner_user_id],
    )
    return {
        "total": int((row or {}).get("total", 0)),
        "unread_count": int((row or {}).get("unread_count", 0)),
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
              AND guid::text = ANY(%s)
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

