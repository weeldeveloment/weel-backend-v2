from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from shared.raw.db import execute, fetch_all, fetch_one

ACTIVE_BOOKING_STATUSES = ("pending_payment", "confirmed")


# ---------------------------------------------------------------------------
# Activity
# ---------------------------------------------------------------------------

def create_activity(*, partner_user_id: int, **fields: Any) -> dict[str, Any]:
    columns = ["partner_user_id", *fields.keys()]
    values = [partner_user_id, *fields.values()]
    placeholders = ", ".join(["%s"] * len(columns))
    return fetch_one(
        f"""
        INSERT INTO activity ({", ".join(columns)})
        VALUES ({placeholders})
        RETURNING *
        """,
        values,
    )


def update_activity(*, activity_id: int, **fields: Any) -> dict[str, Any] | None:
    if not fields:
        return fetch_activity(activity_id)
    set_clause = ", ".join(f"{col} = %s" for col in fields)
    return fetch_one(
        f"""
        UPDATE activity
        SET {set_clause}, updated_at = NOW()
        WHERE id = %s
        RETURNING *
        """,
        [*fields.values(), activity_id],
    )


def delete_activity(*, activity_id: int) -> None:
    execute("UPDATE activity SET is_active = FALSE, updated_at = NOW() WHERE id = %s", [activity_id])


def fetch_activity(activity_id: int) -> dict[str, Any] | None:
    return fetch_one("SELECT * FROM activity WHERE id = %s", [activity_id])


def fetch_activity_by_guid(guid: str) -> dict[str, Any] | None:
    return fetch_one("SELECT * FROM activity WHERE guid = %s", [guid])


def fetch_activities_for_partner(*, partner_user_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        "SELECT * FROM activity WHERE partner_user_id = %s ORDER BY created_at DESC",
        [partner_user_id],
    )


def fetch_all_activities_admin() -> list[dict[str, Any]]:
    """Admin-panel moderation view — every activity across every partner."""
    return fetch_all("SELECT * FROM activity ORDER BY created_at DESC")


def fetch_active_activities(*, category: str | None = None) -> list[dict[str, Any]]:
    if category:
        return fetch_all(
            "SELECT * FROM activity WHERE is_active = TRUE AND category = %s ORDER BY created_at DESC",
            [category],
        )
    return fetch_all("SELECT * FROM activity WHERE is_active = TRUE ORDER BY created_at DESC")


# ---------------------------------------------------------------------------
# ActivityImage
# ---------------------------------------------------------------------------

def add_activity_image(*, activity_id: int, image_url: str, sort_order: int = 0) -> dict[str, Any]:
    return fetch_one(
        """
        INSERT INTO activity_image (activity_id, image_url, sort_order)
        VALUES (%s, %s, %s)
        RETURNING *
        """,
        [activity_id, image_url, sort_order],
    )


def fetch_activity_images(activity_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        "SELECT * FROM activity_image WHERE activity_id = %s ORDER BY sort_order",
        [activity_id],
    )


def delete_activity_image(*, image_id: int, activity_id: int) -> None:
    execute(
        "DELETE FROM activity_image WHERE id = %s AND activity_id = %s",
        [image_id, activity_id],
    )


# ---------------------------------------------------------------------------
# ActivityWorkingHours
# ---------------------------------------------------------------------------

def upsert_working_hours(*, activity_id: int, days: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`days` — a full week: [{weekday, is_day_off, opens_at, closes_at}, ...]."""
    rows = []
    for day in days:
        rows.append(fetch_one(
            """
            INSERT INTO activity_working_hours (activity_id, weekday, is_day_off, opens_at, closes_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (activity_id, weekday)
            DO UPDATE SET
                is_day_off = EXCLUDED.is_day_off,
                opens_at = EXCLUDED.opens_at,
                closes_at = EXCLUDED.closes_at,
                updated_at = NOW()
            RETURNING *
            """,
            [activity_id, day["weekday"], day.get("is_day_off", False), day.get("opens_at"), day.get("closes_at")],
        ))
    return rows


def fetch_working_hours(*, activity_id: int, weekday: int) -> dict[str, Any] | None:
    return fetch_one(
        "SELECT * FROM activity_working_hours WHERE activity_id = %s AND weekday = %s",
        [activity_id, weekday],
    )


def fetch_all_working_hours(activity_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        "SELECT * FROM activity_working_hours WHERE activity_id = %s ORDER BY weekday",
        [activity_id],
    )


# ---------------------------------------------------------------------------
# Tariff
# ---------------------------------------------------------------------------

def create_tariff(*, activity_id: int, **fields: Any) -> dict[str, Any]:
    columns = ["activity_id", *fields.keys()]
    values = [activity_id, *fields.values()]
    placeholders = ", ".join(["%s"] * len(columns))
    return fetch_one(
        f"""
        INSERT INTO tariff ({", ".join(columns)})
        VALUES ({placeholders})
        RETURNING *
        """,
        values,
    )


def update_tariff(*, tariff_id: int, **fields: Any) -> dict[str, Any] | None:
    if not fields:
        return fetch_tariff(tariff_id)
    set_clause = ", ".join(f"{col} = %s" for col in fields)
    return fetch_one(
        f"UPDATE tariff SET {set_clause}, updated_at = NOW() WHERE id = %s RETURNING *",
        [*fields.values(), tariff_id],
    )


def delete_tariff(*, tariff_id: int) -> None:
    execute("UPDATE tariff SET is_active = FALSE, updated_at = NOW() WHERE id = %s", [tariff_id])


def fetch_tariff(tariff_id: int) -> dict[str, Any] | None:
    return fetch_one("SELECT * FROM tariff WHERE id = %s", [tariff_id])


def fetch_tariff_by_guid(guid: str) -> dict[str, Any] | None:
    return fetch_one("SELECT * FROM tariff WHERE guid = %s", [guid])


def fetch_tariffs_for_activity(activity_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        "SELECT * FROM tariff WHERE activity_id = %s AND is_active = TRUE ORDER BY price",
        [activity_id],
    )


# ---------------------------------------------------------------------------
# Resource
# ---------------------------------------------------------------------------

def create_resource(*, activity_id: int, label: str, status: str = "active") -> dict[str, Any]:
    return fetch_one(
        """
        INSERT INTO resource (activity_id, label, status)
        VALUES (%s, %s, %s)
        RETURNING *
        """,
        [activity_id, label, status],
    )


def update_resource(*, resource_id: int, **fields: Any) -> dict[str, Any] | None:
    if not fields:
        return fetch_resource(resource_id)
    set_clause = ", ".join(f"{col} = %s" for col in fields)
    return fetch_one(
        f"UPDATE resource SET {set_clause}, updated_at = NOW() WHERE id = %s RETURNING *",
        [*fields.values(), resource_id],
    )


def delete_resource(*, resource_id: int) -> None:
    execute("DELETE FROM resource WHERE id = %s", [resource_id])


def fetch_resource(resource_id: int) -> dict[str, Any] | None:
    return fetch_one("SELECT * FROM resource WHERE id = %s", [resource_id])


def fetch_resource_by_guid(guid: str) -> dict[str, Any] | None:
    return fetch_one("SELECT * FROM resource WHERE guid = %s", [guid])


def fetch_active_resources_for_activity(activity_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        "SELECT * FROM resource WHERE activity_id = %s AND status = 'active' ORDER BY label",
        [activity_id],
    )


def fetch_resources_for_activity(activity_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        "SELECT * FROM resource WHERE activity_id = %s ORDER BY label",
        [activity_id],
    )


# ---------------------------------------------------------------------------
# ActivityBooking
# ---------------------------------------------------------------------------

def fetch_busy_intervals_for_resources(
    *, resource_ids: list[int], from_dt: datetime, to_dt: datetime,
) -> dict[int, list[tuple[datetime, datetime]]]:
    if not resource_ids:
        return {}
    rows = fetch_all(
        """
        SELECT resource_id, starts_at, blocked_until
        FROM activity_booking
        WHERE resource_id = ANY(%s)
          AND status = ANY(%s)
          AND blocked_until > %s
          AND starts_at < %s
        ORDER BY starts_at
        """,
        [resource_ids, list(ACTIVE_BOOKING_STATUSES), from_dt, to_dt],
    )
    busy: dict[int, list[tuple[datetime, datetime]]] = {rid: [] for rid in resource_ids}
    for row in rows:
        busy[row["resource_id"]].append((row["starts_at"], row["blocked_until"]))
    return busy


def insert_pending_booking(
    *,
    activity_id: int,
    tariff_id: int,
    resource_id: int,
    starts_at: datetime,
    ends_at: datetime,
    buffer_minutes_snapshot: int,
    blocked_until: datetime,
    price_snapshot: Decimal,
    client_user_id: int | None = None,
    guest_phone: str | None = None,
) -> dict[str, Any]:
    """
    Raises django.db.IntegrityError (ExclusionViolation) if the resource is
    already booked for an overlapping interval — the `no_overlapping_resource_booking`
    EXCLUDE constraint is the actual source of truth for correctness, this insert
    just attempts it optimistically after a Redis hold was acquired.
    """
    return fetch_one(
        """
        INSERT INTO activity_booking (
            activity_id, tariff_id, resource_id, client_user_id, guest_phone,
            starts_at, ends_at, buffer_minutes_snapshot, blocked_until,
            price_snapshot, status
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending_payment')
        RETURNING *
        """,
        [
            activity_id, tariff_id, resource_id, client_user_id, guest_phone,
            starts_at, ends_at, buffer_minutes_snapshot, blocked_until, price_snapshot,
        ],
    )


def fetch_available_resource_id(
    *, activity_id: int, starts_at: datetime, blocked_until: datetime,
) -> int | None:
    """First ACTIVE resource of the activity with no overlapping booking."""
    row = fetch_one(
        """
        SELECT r.id
        FROM resource r
        WHERE r.activity_id = %s
          AND r.status = 'active'
          AND NOT EXISTS (
              SELECT 1 FROM activity_booking b
              WHERE b.resource_id = r.id
                AND b.status = ANY(%s)
                AND b.starts_at < %s
                AND b.blocked_until > %s
          )
        ORDER BY r.id
        LIMIT 1
        """,
        [activity_id, list(ACTIVE_BOOKING_STATUSES), blocked_until, starts_at],
    )
    return row["id"] if row else None


def mark_booking_status(*, booking_id: int, status: str, cancellation_reason: str | None = None) -> dict[str, Any] | None:
    return fetch_one(
        """
        UPDATE activity_booking
        SET status = %s, cancellation_reason = %s, updated_at = NOW()
        WHERE id = %s
        RETURNING *
        """,
        [status, cancellation_reason, booking_id],
    )


def fetch_booking_by_guid(guid: str) -> dict[str, Any] | None:
    return fetch_one("SELECT * FROM activity_booking WHERE guid = %s", [guid])


def fetch_bookings_for_client(client_user_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        "SELECT * FROM activity_booking WHERE client_user_id = %s ORDER BY starts_at DESC",
        [client_user_id],
    )


def fetch_bookings_for_partner_activity(activity_id: int, *, from_dt: datetime, to_dt: datetime) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT * FROM activity_booking
        WHERE activity_id = %s AND starts_at < %s AND blocked_until > %s
        ORDER BY starts_at
        """,
        [activity_id, to_dt, from_dt],
    )


def expire_stale_pending_bookings(*, older_than: datetime) -> list[dict[str, Any]]:
    """
    Run periodically (Celery/cron) to expire unpaid bookings past the hold TTL.
    Returns the expired rows so the caller can also release their Redis holds
    (RETURNING makes this atomic — no separate SELECT-then-UPDATE race).
    """
    return fetch_all(
        """
        UPDATE activity_booking
        SET status = 'expired', updated_at = NOW()
        WHERE status = 'pending_payment' AND created_at < %s
        RETURNING *
        """,
        [older_than],
    )
