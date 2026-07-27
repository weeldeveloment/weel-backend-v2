from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from django.utils import timezone

from shared.raw.db import execute, fetch_all, fetch_one
from apps.pms.raw.tables import (
    PMS_BOOKINGCOM_CONNECTION_TABLE,
    PMS_BOOKINGCOM_ROOM_MAPPING_TABLE,
    PMS_BOOKINGCOM_SYNC_ERROR_TABLE,
    PMS_BOOKINGCOM_SYNC_RUN_TABLE,
    PMS_BOOKING_TABLE,
    PMS_PROPERTY_TABLE,
    PMS_ROOM_TABLE,
)


def _to_pg_json(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return value


def get_connection(property_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        SELECT *
        FROM {PMS_BOOKINGCOM_CONNECTION_TABLE}
        WHERE property_id = %s
        LIMIT 1
        """,
        [property_id],
    )


def list_enabled_connections() -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT c.*
        FROM {PMS_BOOKINGCOM_CONNECTION_TABLE} c
        JOIN {PMS_PROPERTY_TABLE} p ON p.id = c.property_id
        WHERE c.enabled = TRUE AND p.is_active = TRUE
        ORDER BY c.id ASC
        """
    )


def upsert_connection(
    property_id: int,
    *,
    enabled: bool,
    bookingcom_property_id: str,
    api_url: str,
    api_token: str | None = None,
    username: str | None = None,
    password: str | None = None,
) -> dict[str, Any]:
    existing = get_connection(property_id)
    now = timezone.now()
    payload: dict[str, Any] = {
        "enabled": enabled,
        "bookingcom_property_id": bookingcom_property_id,
        "api_url": api_url,
        "updated_at": now,
    }
    if api_token is not None:
        payload["api_token"] = api_token
    if username is not None:
        payload["username"] = username
    if password is not None:
        payload["password"] = password

    if existing:
        sets = ", ".join(f"{key} = %s" for key in payload)
        return fetch_one(
            f"""
            UPDATE {PMS_BOOKINGCOM_CONNECTION_TABLE}
            SET {sets}
            WHERE property_id = %s
            RETURNING *
            """,
            [*payload.values(), property_id],
        ) or {}

    return fetch_one(
        f"""
        INSERT INTO {PMS_BOOKINGCOM_CONNECTION_TABLE}
        (
            property_id,
            enabled,
            bookingcom_property_id,
            api_url,
            api_token,
            username,
            password,
            created_at,
            updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        [
            property_id,
            enabled,
            bookingcom_property_id,
            api_url,
            api_token,
            username,
            password,
            now,
            now,
        ],
    ) or {}


def delete_connection(property_id: int) -> bool:
    return (
        execute(
            f"DELETE FROM {PMS_BOOKINGCOM_CONNECTION_TABLE} WHERE property_id = %s",
            [property_id],
        )
        > 0
    )


def replace_room_mappings(property_id: int, mappings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    execute(
        f"DELETE FROM {PMS_BOOKINGCOM_ROOM_MAPPING_TABLE} WHERE property_id = %s",
        [property_id],
    )
    now = timezone.now()
    rows: list[dict[str, Any]] = []
    for item in mappings:
        row = fetch_one(
            f"""
            INSERT INTO {PMS_BOOKINGCOM_ROOM_MAPPING_TABLE}
            (
                property_id,
                external_room_id,
                room_id,
                room_type_id,
                is_active,
                created_at,
                updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING *
            """,
            [
                property_id,
                item["external_room_id"],
                item.get("room_id"),
                item.get("room_type_id"),
                item.get("is_active", True),
                now,
                now,
            ],
        )
        if row:
            rows.append(row)
    return rows


def list_room_mappings(property_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT *
        FROM {PMS_BOOKINGCOM_ROOM_MAPPING_TABLE}
        WHERE property_id = %s
        ORDER BY id ASC
        """,
        [property_id],
    )


def get_room_mapping(property_id: int, external_room_id: str) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        SELECT *
        FROM {PMS_BOOKINGCOM_ROOM_MAPPING_TABLE}
        WHERE property_id = %s AND external_room_id = %s AND is_active = TRUE
        LIMIT 1
        """,
        [property_id, external_room_id],
    )


def list_rooms_by_type(property_id: int, room_type_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT *
        FROM {PMS_ROOM_TABLE}
        WHERE property_id = %s AND room_type_id = %s AND is_active = TRUE
        ORDER BY id ASC
        """,
        [property_id, room_type_id],
    )


def get_booking_by_external_reference(
    property_id: int,
    *,
    provider: str,
    external_reservation_id: str,
) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        SELECT *
        FROM {PMS_BOOKING_TABLE}
        WHERE property_id = %s
          AND external_provider = %s
          AND external_reservation_id = %s
        LIMIT 1
        """,
        [property_id, provider, external_reservation_id],
    )


def start_sync_run(
    property_id: int,
    *,
    connection_id: int | None,
    triggered_by: str,
    sync_cursor_from: datetime | None,
) -> dict[str, Any]:
    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO {PMS_BOOKINGCOM_SYNC_RUN_TABLE}
        (
            property_id,
            connection_id,
            triggered_by,
            status,
            sync_cursor_from,
            started_at,
            created_at,
            updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        [property_id, connection_id, triggered_by, "running", sync_cursor_from, now, now, now],
    ) or {}


def finish_sync_run(
    sync_run_id: int,
    *,
    status: str,
    stats: dict[str, Any],
    sync_cursor_to: datetime | None,
    error_message: str | None = None,
) -> dict[str, Any] | None:
    now = timezone.now()
    return fetch_one(
        f"""
        UPDATE {PMS_BOOKINGCOM_SYNC_RUN_TABLE}
        SET status = %s,
            stats = %s,
            sync_cursor_to = %s,
            error_message = %s,
            finished_at = %s,
            updated_at = %s
        WHERE id = %s
        RETURNING *
        """,
        [status, _to_pg_json(stats), sync_cursor_to, error_message, now, now, sync_run_id],
    )


def log_sync_error(
    *,
    sync_run_id: int,
    property_id: int,
    code: str,
    message: str,
    external_reservation_id: str | None = None,
    external_room_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO {PMS_BOOKINGCOM_SYNC_ERROR_TABLE}
        (
            sync_run_id,
            property_id,
            external_reservation_id,
            external_room_id,
            code,
            message,
            payload,
            created_at,
            updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        [
            sync_run_id,
            property_id,
            external_reservation_id,
            external_room_id,
            code,
            message,
            _to_pg_json(payload or {}),
            now,
            now,
        ],
    )


def get_latest_sync_run(property_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        SELECT *
        FROM {PMS_BOOKINGCOM_SYNC_RUN_TABLE}
        WHERE property_id = %s
        ORDER BY id DESC
        LIMIT 1
        """,
        [property_id],
    )


def list_recent_sync_errors(property_id: int, limit: int = 10) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT *
        FROM {PMS_BOOKINGCOM_SYNC_ERROR_TABLE}
        WHERE property_id = %s
        ORDER BY id DESC
        LIMIT %s
        """,
        [property_id, limit],
    )


def mark_connection_sync_state(
    property_id: int,
    *,
    last_sync_status: str,
    last_successful_sync_at: datetime | None = None,
    last_error: str | None = None,
) -> dict[str, Any] | None:
    now = timezone.now()
    return fetch_one(
        f"""
        UPDATE {PMS_BOOKINGCOM_CONNECTION_TABLE}
        SET last_sync_status = %s,
            last_successful_sync_at = COALESCE(%s, last_successful_sync_at),
            last_synced_at = %s,
            last_error = %s,
            updated_at = %s
        WHERE property_id = %s
        RETURNING *
        """,
        [last_sync_status, last_successful_sync_at, now, last_error, now, property_id],
    )
