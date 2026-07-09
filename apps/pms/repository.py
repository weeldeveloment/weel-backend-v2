from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from django.utils import timezone

from shared.raw.db import execute, fetch_all, fetch_one, table_exists
from apps.pms.raw.tables import (
    PMS_PROPERTY_TABLE,
    PMS_PROPERTY_IMAGE_TABLE,
    PMS_ROOM_TABLE,
    PMS_CALENDAR_SLOT_TABLE,
    PMS_GUEST_TABLE,
    PMS_BOOKING_TABLE,
    PMS_BOOKING_HISTORY_TABLE,
    PMS_RATE_TABLE,
    PMS_REVIEW_TABLE,
)

def _to_pg_array(v: Any) -> str:
    """Convert a Python list to a PostgreSQL text[] literal: {"a","b"}"""
    if not isinstance(v, list):
        return v
    escaped = []
    for x in v:
        s = str(x).replace(chr(92), chr(92)*2).replace(chr(34), chr(92)+chr(34))
        escaped.append(f'"{s}"')
    return "{" + ",".join(escaped) + "}"


def _to_pg_json(v: Any) -> Any:
    """Convert a Python list/dict to a JSON string for JSONB columns."""
    if isinstance(v, (list, dict)):
        return json.dumps(v)
    return v


logger = logging.getLogger(__name__)

_PROPERTY_COLUMN_CACHE: dict[str, bool] = {}


def _t(name: str) -> str:
    return name


def _property_has_column(column_name: str) -> bool:
    cached = _PROPERTY_COLUMN_CACHE.get(column_name)
    if cached is not None:
        return cached

    row = fetch_one(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = %s
              AND column_name = %s
        ) AS exists
        """,
        [PMS_PROPERTY_TABLE, column_name],
    )
    exists = bool(row and row["exists"])
    _PROPERTY_COLUMN_CACHE[column_name] = exists
    return exists


# ─── Properties ──────────────────────────────────────────────────────────────


def create_property(*, organization_id: int, name: str, **kwargs: Any) -> dict[str, Any] | None:
    now = timezone.now()
    cols = ["organization_id", "name", "created_at", "updated_at"]
    vals = [organization_id, name, now, now]

    field_map = {
        "description_uz": str, "description_ru": str, "description_en": str,
        "address": str, "full_address": str, "city": str, "country": str,
        "latitude": lambda v: v, "longitude": lambda v: v,
        "star_rating": int, "weel_classification": str,
        "themes": _to_pg_array,
        "amenities": _to_pg_array,
        "legal_info": _to_pg_json,
        "check_in_time": lambda v: v, "check_out_time": lambda v: v,
        "cancellation_policy": str, "quiet_hours": bool,
        "alcohol_allowed": bool, "pets_allowed": bool,
        "currency": str, "timezone": str,
        "photos": _to_pg_array,
        "is_active": bool,
        "is_verified": bool,
        "verification_status": str,
    }

    for key, caster in field_map.items():
        if key in kwargs and kwargs[key] is not None:
            if not _property_has_column(key):
                continue
            cols.append(key)
            vals.append(caster(kwargs[key]))

    placeholders = ", ".join(["%s"] * len(cols))
    col_names = ", ".join(cols)

    return fetch_one(
        f"INSERT INTO {_t(PMS_PROPERTY_TABLE)} ({col_names}) VALUES ({placeholders}) RETURNING *",
        vals,
    )


def has_any_properties(*, organization_id: int, schema_name: str | None = None) -> bool:
    table = f"{schema_name}.{_t(PMS_PROPERTY_TABLE)}" if schema_name else _t(PMS_PROPERTY_TABLE)
    row = fetch_one(
        f"SELECT EXISTS(SELECT 1 FROM {table} WHERE organization_id = %s AND is_active = TRUE) AS exists_flag",
        [organization_id],
    )
    return bool(row and row["exists_flag"])


def list_properties(*, organization_id: int, is_active: bool = True) -> list[dict[str, Any]]:
    if is_active:
        return fetch_all(
            f"SELECT * FROM {_t(PMS_PROPERTY_TABLE)} WHERE organization_id = %s AND is_active = TRUE ORDER BY name ASC",
            [organization_id],
        )
    return fetch_all(
        f"SELECT * FROM {_t(PMS_PROPERTY_TABLE)} WHERE organization_id = %s ORDER BY name ASC",
        [organization_id],
    )


def get_property(property_id: int, organization_id: int | None = None) -> dict[str, Any] | None:
    if organization_id:
        return fetch_one(
            f"SELECT * FROM {_t(PMS_PROPERTY_TABLE)} WHERE id = %s AND organization_id = %s",
            [property_id, organization_id],
        )
    return fetch_one(
        f"SELECT * FROM {_t(PMS_PROPERTY_TABLE)} WHERE id = %s",
        [property_id],
    )


def update_property(property_id: int, organization_id: int | None = None, **kwargs: Any) -> dict[str, Any] | None:
    if not kwargs:
        return get_property(property_id, organization_id)

    pg_array_fields = {"amenities", "photos", "themes"}
    pg_json_fields = {"legal_info"}
    sanitized = {}
    for k, v in kwargs.items():
        if not _property_has_column(k):
            continue
        if k in pg_array_fields and isinstance(v, list):
            sanitized[k] = _to_pg_array(v)
        elif k in pg_json_fields and isinstance(v, (list, dict)):
            sanitized[k] = _to_pg_json(v)
        else:
            sanitized[k] = v

    if not sanitized:
        return get_property(property_id, organization_id)

    sets = ", ".join(f"{k} = %s" for k in sanitized)
    values = list(sanitized.values())
    values.append(timezone.now())
    values.append(property_id)

    if organization_id:
        values.append(organization_id)
        where = "WHERE id = %s AND organization_id = %s"
    else:
        where = "WHERE id = %s"

    return fetch_one(
        f"UPDATE {_t(PMS_PROPERTY_TABLE)} SET {sets}, updated_at = %s {where} RETURNING *",
        values,
    )


def delete_property(property_id: int, organization_id: int | None = None) -> bool:
    if organization_id:
        return execute(
            f"UPDATE {_t(PMS_PROPERTY_TABLE)} SET is_active = FALSE, updated_at = %s WHERE id = %s AND organization_id = %s",
            [timezone.now(), property_id, organization_id],
        ) > 0
    return execute(
        f"UPDATE {_t(PMS_PROPERTY_TABLE)} SET is_active = FALSE, updated_at = %s WHERE id = %s",
        [timezone.now(), property_id],
    ) > 0


def get_property_images(property_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        f"SELECT * FROM {_t(PMS_PROPERTY_IMAGE_TABLE)} WHERE property_id = %s ORDER BY \"order\" ASC",
        [property_id],
    )


def add_property_image(property_id: int, image_url: str, order: int = 0) -> dict[str, Any] | None:
    return fetch_one(
        f"INSERT INTO {_t(PMS_PROPERTY_IMAGE_TABLE)} (property_id, image_url, \"order\", created_at, updated_at) VALUES (%s, %s, %s, %s, %s) RETURNING *",
        [property_id, image_url, order, timezone.now(), timezone.now()],
    )


def delete_property_image(image_id: int) -> bool:
    return execute(
        f"DELETE FROM {_t(PMS_PROPERTY_IMAGE_TABLE)} WHERE id = %s",
        [image_id],
    ) > 0


# ─── Rooms ───────────────────────────────────────────────────────────────────


def create_room(*, property_id: int, **kwargs: Any) -> dict[str, Any] | None:
    now = timezone.now()
    cols = ["property_id", "created_at", "updated_at"]
    vals = [property_id, now, now]

    field_map = {
        "room_type_name": str, "room_type_preset": str,
        "room_number": str, "display_name": str,
        "floor": int, "area": lambda v: v, "bedroom_count": int,
        "beds": _to_pg_json,
        "amenities": _to_pg_array,
        "photos": _to_pg_array,
        "condition": str, "availability": str,
        "capacity": int, "meal_plan": str, "is_active": bool,
    }

    for key, caster in field_map.items():
        if key in kwargs and kwargs[key] is not None:
            cols.append(key)
            vals.append(caster(kwargs[key]))

    placeholders = ", ".join(["%s"] * len(cols))
    col_names = ", ".join(cols)

    return fetch_one(
        f"INSERT INTO {_t(PMS_ROOM_TABLE)} ({col_names}) VALUES ({placeholders}) RETURNING *",
        vals,
    )


def list_rooms(property_id: int, *, room_type_name: str | None = None, is_active: bool = True) -> list[dict[str, Any]]:
    conditions = ["property_id = %s"]
    params: list[Any] = [property_id]

    if room_type_name:
        conditions.append("room_type_name = %s")
        params.append(room_type_name)
    if is_active:
        conditions.append("is_active = TRUE")

    where = " AND ".join(conditions)
    return fetch_all(
        f"SELECT * FROM {_t(PMS_ROOM_TABLE)} WHERE {where} ORDER BY room_number ASC",
        params,
    )


def get_room(room_id: int, property_id: int | None = None) -> dict[str, Any] | None:
    if property_id:
        return fetch_one(
            f"SELECT * FROM {_t(PMS_ROOM_TABLE)} WHERE id = %s AND property_id = %s",
            [room_id, property_id],
        )
    return fetch_one(
        f"SELECT * FROM {_t(PMS_ROOM_TABLE)} WHERE id = %s",
        [room_id],
    )


def update_room(room_id: int, **kwargs: Any) -> dict[str, Any] | None:
    if not kwargs:
        return None

    pg_array_fields = {"amenities", "photos"}
    pg_json_fields = {"beds"}
    sanitized = {}
    for k, v in kwargs.items():
        if k in pg_array_fields and isinstance(v, list):
            sanitized[k] = _to_pg_array(v)
        elif k in pg_json_fields and isinstance(v, (list, dict)):
            sanitized[k] = _to_pg_json(v)
        else:
            sanitized[k] = v

    sets = ", ".join(f"{k} = %s" for k in sanitized)
    values = list(sanitized.values())
    values.append(timezone.now())
    values.append(room_id)

    return fetch_one(
        f"UPDATE {_t(PMS_ROOM_TABLE)} SET {sets}, updated_at = %s WHERE id = %s RETURNING *",
        values,
    )


def delete_room(room_id: int) -> bool:
    return execute(
        f"UPDATE {_t(PMS_ROOM_TABLE)} SET is_active = FALSE, updated_at = %s WHERE id = %s",
        [timezone.now(), room_id],
    ) > 0


def mass_update_rooms(property_id: int, updates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results = []
    for update in updates:
        room_id = update.pop("id", None)
        if not room_id:
            continue
        result = update_room(room_id, **update)
        if result:
            results.append(result)
    return results


# ─── Calendar ────────────────────────────────────────────────────────────────


def get_calendar_slots(
    *,
    property_id: int,
    from_date: date,
    to_date: date,
    room_id: int | None = None,
) -> list[dict[str, Any]]:
    conditions = ["cs.date BETWEEN %s AND %s"]
    params: list[Any] = [from_date, to_date]

    if room_id:
        conditions.append("cs.room_id = %s")
        params.append(room_id)
    else:
        conditions.append("cs.room_id = r.id")

    room_filter = "r.property_id = %s"
    params.append(property_id)

    where = " AND ".join(conditions)
    return fetch_all(
        f"""
        SELECT cs.*, r.room_number, r.room_type_name
        FROM {_t(PMS_CALENDAR_SLOT_TABLE)} cs
        JOIN {_t(PMS_ROOM_TABLE)} r ON r.id = cs.room_id
        WHERE {room_filter} AND {where}
        ORDER BY r.room_number ASC, cs.date ASC
        """,
        params,
    )


def get_or_create_calendar_slot(room_id: int, slot_date: date, status: str = "available") -> dict[str, Any]:
    existing = fetch_one(
        f"SELECT * FROM {_t(PMS_CALENDAR_SLOT_TABLE)} WHERE room_id = %s AND date = %s",
        [room_id, slot_date],
    )
    if existing:
        return existing

    return fetch_one(
        f"INSERT INTO {_t(PMS_CALENDAR_SLOT_TABLE)} (room_id, date, status, created_at, updated_at) VALUES (%s, %s, %s, %s, %s) RETURNING *",
        [room_id, slot_date, status, timezone.now(), timezone.now()],
    )


def block_dates(*, room_ids: list[int], from_date: date, to_date: date) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        INSERT INTO {_t(PMS_CALENDAR_SLOT_TABLE)} (room_id, date, status, created_at, updated_at)
        SELECT r.id, d.date, 'blocked', %s, %s
        FROM unnest(%s::int[]) AS r(id)
        CROSS JOIN generate_series(%s::date, %s::date, '1 day'::interval) AS d(date)
        ON CONFLICT (room_id, date) DO UPDATE SET status = 'blocked', updated_at = %s
        RETURNING *
        """,
        [timezone.now(), timezone.now(), room_ids, from_date, to_date, timezone.now()],
    )


def unblock_dates(*, room_ids: list[int], from_date: date, to_date: date) -> int:
    placeholders = ", ".join(["%s"] * len(room_ids))
    return execute(
        f"""
        UPDATE {_t(PMS_CALENDAR_SLOT_TABLE)}
        SET status = 'available', hold_expires_at = NULL, updated_at = %s
        WHERE room_id IN ({placeholders}) AND date BETWEEN %s AND %s AND status = 'blocked'
        """,
        [timezone.now(), *room_ids, from_date, to_date],
    )


def hold_dates(*, room_ids: list[int], from_date: date, to_date: date, hold_duration_minutes: int = 30) -> list[dict[str, Any]]:
    expires = timezone.now() + timedelta(minutes=hold_duration_minutes)
    now = timezone.now()
    return fetch_all(
        f"""
        INSERT INTO {_t(PMS_CALENDAR_SLOT_TABLE)} (room_id, date, status, hold_expires_at, created_at, updated_at)
        SELECT r.id, d.date, 'held', %s, %s, %s
        FROM unnest(%s::int[]) AS r(id)
        CROSS JOIN generate_series(%s::date, %s::date, '1 day'::interval) AS d(date)
        ON CONFLICT (room_id, date) DO UPDATE SET status = 'held', hold_expires_at = %s, updated_at = %s
        RETURNING *
        """,
        [expires, now, now, room_ids, from_date, to_date, expires, now],
    )


def unhold_dates(*, room_ids: list[int], from_date: date, to_date: date) -> int:
    placeholders = ", ".join(["%s"] * len(room_ids))
    return execute(
        f"""
        UPDATE {_t(PMS_CALENDAR_SLOT_TABLE)}
        SET status = 'available', hold_expires_at = NULL, updated_at = %s
        WHERE room_id IN ({placeholders}) AND date BETWEEN %s AND %s AND status = 'held'
        """,
        [timezone.now(), *room_ids, from_date, to_date],
    )


def expire_holds() -> int:
    return execute(
        f"""
        UPDATE {_t(PMS_CALENDAR_SLOT_TABLE)}
        SET status = 'available', hold_expires_at = NULL, updated_at = %s
        WHERE status = 'held' AND hold_expires_at < %s
        """,
        [timezone.now(), timezone.now()],
    )


def get_room_availability(
    *,
    property_id: int,
    from_date: date,
    to_date: date,
) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT r.id as room_id, r.room_number, r.room_type_name, r.capacity,
               cs.date, cs.status
        FROM {_t(PMS_ROOM_TABLE)} r
        LEFT JOIN {_t(PMS_CALENDAR_SLOT_TABLE)} cs ON cs.room_id = r.id
            AND cs.date BETWEEN %s AND %s
        WHERE r.property_id = %s AND r.is_active = TRUE
        ORDER BY r.room_number ASC, cs.date ASC
        """,
        [from_date, to_date, property_id],
    )


# ─── Guests ──────────────────────────────────────────────────────────────────


def create_guest(*, first_name: str, **kwargs: Any) -> dict[str, Any] | None:
    now = timezone.now()
    cols = ["first_name", "created_at", "updated_at"]
    vals = [first_name, now, now]

    field_map = {
        "last_name": str, "email": str, "phone": str,
        "id_document": _to_pg_json,
        "preferences": _to_pg_json,
        "is_vip": bool, "is_blacklisted": bool, "notes": str,
    }

    for key, caster in field_map.items():
        if key in kwargs and kwargs[key] is not None:
            cols.append(key)
            vals.append(caster(kwargs[key]))

    placeholders = ", ".join(["%s"] * len(cols))
    col_names = ", ".join(cols)

    return fetch_one(
        f"INSERT INTO {_t(PMS_GUEST_TABLE)} ({col_names}) VALUES ({placeholders}) RETURNING *",
        vals,
    )


def list_guests(*, search: str | None = None) -> list[dict[str, Any]]:
    if search:
        return fetch_all(
            f"""
            SELECT * FROM {_t(PMS_GUEST_TABLE)}
            WHERE first_name ILIKE %s OR last_name ILIKE %s OR email ILIKE %s OR phone ILIKE %s
            ORDER BY last_name ASC, first_name ASC
            """,
            [f"%{search}%", f"%{search}%", f"%{search}%", f"%{search}%"],
        )
    return fetch_all(
        f"SELECT * FROM {_t(PMS_GUEST_TABLE)} ORDER BY last_name ASC, first_name ASC"
    )


def get_guest(guest_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT * FROM {_t(PMS_GUEST_TABLE)} WHERE id = %s",
        [guest_id],
    )


def update_guest(guest_id: int, **kwargs: Any) -> dict[str, Any] | None:
    if not kwargs:
        return None

    pg_json_fields = {"id_document", "preferences"}
    sanitized = {}
    for k, v in kwargs.items():
        if k in pg_json_fields and isinstance(v, (list, dict)):
            sanitized[k] = _to_pg_json(v)
        else:
            sanitized[k] = v

    sets = ", ".join(f"{k} = %s" for k in sanitized)
    values = list(sanitized.values())
    values.append(timezone.now())
    values.append(guest_id)

    return fetch_one(
        f"UPDATE {_t(PMS_GUEST_TABLE)} SET {sets}, updated_at = %s WHERE id = %s RETURNING *",
        values,
    )


def find_or_create_guest(
    *,
    first_name: str,
    last_name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
) -> dict[str, Any]:
    if email:
        existing = fetch_one(
            f"SELECT * FROM {_t(PMS_GUEST_TABLE)} WHERE email = %s",
            [email],
        )
        if existing:
            return existing
    if phone:
        existing = fetch_one(
            f"SELECT * FROM {_t(PMS_GUEST_TABLE)} WHERE phone = %s",
            [phone],
        )
        if existing:
            return existing

    guest = create_guest(first_name=first_name, last_name=last_name, email=email, phone=phone)
    return guest or {}


# ─── Bookings ────────────────────────────────────────────────────────────────


def _generate_booking_number() -> str:
    import random
    return f"PMS-{datetime.now().strftime('%Y')}-{random.randint(10000, 99999)}"


def create_booking(
    *,
    property_id: int,
    room_id: int,
    check_in: date,
    check_out: date,
    **kwargs: Any,
) -> dict[str, Any] | None:
    now = timezone.now()
    booking_number = _generate_booking_number()

    cols = [
        "property_id", "room_id", "booking_number",
        "check_in", "check_out", "created_at", "updated_at",
    ]
    vals = [property_id, room_id, booking_number, check_in, check_out, now, now]

    field_map = {
        "guest_id": int, "status": str, "source": str, "meal_plan": str,
        "adult_count": int, "child_count": int, "rate": lambda v: v,
        "currency": str, "payment_status": str, "total_cost": lambda v: v,
        "hold_amount": lambda v: v,
        "confirmed_at": lambda v: v, "confirmation_deadline": lambda v: v,
        "b2b_company_id": int, "voucher_number": str,
        "notes": str, "created_by": int,
    }

    for key, caster in field_map.items():
        if key in kwargs and kwargs[key] is not None:
            cols.append(key)
            vals.append(caster(kwargs[key]))

    placeholders = ", ".join(["%s"] * len(cols))
    col_names = ", ".join(cols)

    booking = fetch_one(
        f"INSERT INTO {_t(PMS_BOOKING_TABLE)} ({col_names}) VALUES ({placeholders}) RETURNING *",
        vals,
    )

    if booking:
        _add_booking_history(
            booking_id=booking["id"],
            action="created",
            new_value={"status": booking.get("status", "new")},
            user_id=kwargs.get("created_by"),
        )

        execute(
            f"""
            INSERT INTO {_t(PMS_CALENDAR_SLOT_TABLE)} (room_id, date, status, created_at, updated_at)
            SELECT %s, d.date, 'occupied', %s, %s
            FROM generate_series(%s::date, %s::date - 1, '1 day'::interval) AS d(date)
            ON CONFLICT (room_id, date) DO UPDATE SET status = 'occupied', updated_at = %s
            """,
            [room_id, now, now, check_in, check_out, now],
        )

    return booking


def list_bookings(
    *,
    property_id: int,
    status: str | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    room_id: int | None = None,
) -> list[dict[str, Any]]:
    conditions = ["b.property_id = %s"]
    params: list[Any] = [property_id]

    if status:
        conditions.append("b.status = %s")
        params.append(status)
    if from_date:
        conditions.append("b.check_out >= %s")
        params.append(from_date)
    if to_date:
        conditions.append("b.check_in <= %s")
        params.append(to_date)
    if room_id:
        conditions.append("b.room_id = %s")
        params.append(room_id)

    where = " AND ".join(conditions)
    return fetch_all(
        f"""
        SELECT b.*, r.room_number, g.first_name as guest_first_name, g.last_name as guest_last_name
        FROM {_t(PMS_BOOKING_TABLE)} b
        LEFT JOIN {_t(PMS_ROOM_TABLE)} r ON r.id = b.room_id
        LEFT JOIN {_t(PMS_GUEST_TABLE)} g ON g.id = b.guest_id
        WHERE {where}
        ORDER BY b.check_in DESC
        """,
        params,
    )


def list_newest_bookings(property_id: int, limit: int = 10) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT b.*, r.room_number, g.first_name as guest_first_name, g.last_name as guest_last_name
        FROM {_t(PMS_BOOKING_TABLE)} b
        LEFT JOIN {_t(PMS_ROOM_TABLE)} r ON r.id = b.room_id
        LEFT JOIN {_t(PMS_GUEST_TABLE)} g ON g.id = b.guest_id
        WHERE b.property_id = %s
        ORDER BY b.created_at DESC
        LIMIT %s
        """,
        [property_id, limit],
    )


def get_booking(booking_id: int, property_id: int | None = None) -> dict[str, Any] | None:
    if property_id:
        return fetch_one(
            f"""
            SELECT b.*, r.room_number, g.first_name as guest_first_name, g.last_name as guest_last_name
            FROM {_t(PMS_BOOKING_TABLE)} b
            LEFT JOIN {_t(PMS_ROOM_TABLE)} r ON r.id = b.room_id
            LEFT JOIN {_t(PMS_GUEST_TABLE)} g ON g.id = b.guest_id
            WHERE b.id = %s AND b.property_id = %s
            """,
            [booking_id, property_id],
        )
    return fetch_one(
        f"SELECT * FROM {_t(PMS_BOOKING_TABLE)} WHERE id = %s",
        [booking_id],
    )


def update_booking(booking_id: int, **kwargs: Any) -> dict[str, Any] | None:
    if not kwargs:
        return None

    sets = ", ".join(f"{k} = %s" for k in kwargs)
    values = list(kwargs.values())
    values.append(timezone.now())
    values.append(booking_id)

    return fetch_one(
        f"UPDATE {_t(PMS_BOOKING_TABLE)} SET {sets}, updated_at = %s WHERE id = %s RETURNING *",
        values,
    )


def update_booking_with_guest(
    booking_id: int,
    *,
    guest_first_name: str | None = None,
    guest_last_name: str | None = None,
    user_id: int | None = None,
    **kwargs: Any,
) -> dict[str, Any] | None:
    booking = get_booking(booking_id)
    if not booking:
        return None

    guest_id = booking.get("guest_id")
    has_guest_update = guest_first_name is not None or guest_last_name is not None
    if has_guest_update:
        first_name = (guest_first_name or "").strip() or "Guest"
        last_name = (guest_last_name or "").strip() or None
        if guest_id:
            update_guest(guest_id, first_name=first_name, last_name=last_name)
        else:
            guest = create_guest(first_name=first_name, last_name=last_name)
            if guest:
                kwargs["guest_id"] = guest["id"]

    allowed_fields = {
        "room_id",
        "guest_id",
        "check_in",
        "check_out",
        "source",
        "meal_plan",
        "adult_count",
        "child_count",
        "rate",
        "currency",
        "payment_status",
        "total_cost",
        "b2b_company_id",
        "notes",
    }
    updates = {k: v for k, v in kwargs.items() if k in allowed_fields}

    if updates:
        update_booking(booking_id, **updates)

    if has_guest_update or updates:
        _add_booking_history(
            booking_id=booking_id,
            action="updated",
            previous_value={
                k: str(booking.get(k)) if booking.get(k) is not None else None
                for k in updates
            },
            new_value={
                **{
                    k: str(v) if v is not None else None
                    for k, v in updates.items()
                },
                **(
                    {
                        "guest_first_name": guest_first_name,
                        "guest_last_name": guest_last_name,
                    }
                    if has_guest_update
                    else {}
                ),
            },
            user_id=user_id,
        )

    return get_booking(booking_id, booking.get("property_id"))


def accept_booking(booking_id: int, user_id: int | None = None) -> dict[str, Any] | None:
    booking = update_booking(booking_id, status="confirmed")
    if booking:
        _add_booking_history(
            booking_id=booking_id,
            action="status_changed",
            previous_value={"status": "new"},
            new_value={"status": "confirmed"},
            user_id=user_id,
        )
    return booking


def cancel_booking(booking_id: int, user_id: int | None = None) -> dict[str, Any] | None:
    booking = get_booking(booking_id)
    if not booking:
        return None

    result = update_booking(booking_id, status="cancelled")
    if result:
        _add_booking_history(
            booking_id=booking_id,
            action="status_changed",
            previous_value={"status": booking.get("status")},
            new_value={"status": "cancelled"},
            user_id=user_id,
        )

        if booking.get("room_id") and booking.get("check_in") and booking.get("check_out"):
            check_in = booking["check_in"]
            if hasattr(check_in, "date"):
                check_in = check_in.date()
            check_out = booking["check_out"]
            if hasattr(check_out, "date"):
                check_out = check_out.date()
            execute(
                f"""
                UPDATE {_t(PMS_CALENDAR_SLOT_TABLE)}
                SET status = 'available', updated_at = %s
                WHERE room_id = %s
                  AND date >= %s
                  AND date < %s
                  AND status = 'occupied'
                """,
                [timezone.now(), booking["room_id"], check_in, check_out],
            )

    return result


def check_in_booking(booking_id: int, user_id: int | None = None) -> dict[str, Any] | None:
    booking = get_booking(booking_id)
    if not booking:
        return None

    result = update_booking(booking_id, status="checked_in")
    if result:
        _add_booking_history(
            booking_id=booking_id,
            action="status_changed",
            previous_value={"status": booking.get("status")},
            new_value={"status": "checked_in"},
            user_id=user_id,
        )

        room_id = booking.get("room_id")
        if room_id:
            update_room(room_id, condition="dirty", availability="occupied")

    return result


def check_out_booking(booking_id: int, user_id: int | None = None) -> dict[str, Any] | None:
    booking = get_booking(booking_id)
    if not booking:
        return None

    result = update_booking(booking_id, status="checked_out")
    if result:
        _add_booking_history(
            booking_id=booking_id,
            action="status_changed",
            previous_value={"status": booking.get("status")},
            new_value={"status": "checked_out"},
            user_id=user_id,
        )

        room_id = booking.get("room_id")
        if room_id:
            update_room(room_id, condition="dirty", availability="available")

    return result


def move_booking(
    booking_id: int,
    new_room_id: int,
    new_check_in: date | None = None,
    new_check_out: date | None = None,
    user_id: int | None = None,
) -> dict[str, Any] | None:
    booking = get_booking(booking_id)
    if not booking:
        return None

    old_room_id = booking.get("room_id")
    old_check_in = booking.get("check_in")
    old_check_out = booking.get("check_out")
    if hasattr(old_check_in, "date"):
        old_check_in = old_check_in.date()
    if hasattr(old_check_out, "date"):
        old_check_out = old_check_out.date()

    updates: dict[str, Any] = {"room_id": new_room_id}
    if new_check_in:
        updates["check_in"] = new_check_in
    if new_check_out:
        updates["check_out"] = new_check_out

    result = update_booking(booking_id, **updates)
    if result:
        _add_booking_history(
            booking_id=booking_id,
            action="moved",
            previous_value={
                "room_id": old_room_id,
                "check_in": str(old_check_in),
                "check_out": str(old_check_out),
            },
            new_value={
                "room_id": new_room_id,
                "check_in": str(new_check_in or old_check_in),
                "check_out": str(new_check_out or old_check_out),
            },
            user_id=user_id,
        )

        now = timezone.now()
        if old_room_id and old_check_in and old_check_out:
            execute(
                f"""
                UPDATE {_t(PMS_CALENDAR_SLOT_TABLE)}
                SET status = 'available', updated_at = %s
                WHERE room_id = %s
                  AND date >= %s AND date < %s
                  AND status = 'occupied'
                """,
                [now, old_room_id, old_check_in, old_check_out],
            )

        actual_check_in = new_check_in or old_check_in
        actual_check_out = new_check_out or old_check_out
        if actual_check_in and actual_check_out:
            execute(
                f"""
                INSERT INTO {_t(PMS_CALENDAR_SLOT_TABLE)} (room_id, date, status, created_at, updated_at)
                SELECT %s, d.date, 'occupied', %s, %s
                FROM generate_series(%s::date, %s::date - 1, '1 day'::interval) AS d(date)
                ON CONFLICT (room_id, date) DO UPDATE SET status = 'occupied', updated_at = %s
                """,
                [new_room_id, now, now, actual_check_in, actual_check_out, now],
            )

    return result


def change_meal_plan(booking_id: int, new_meal_plan: str, user_id: int | None = None) -> dict[str, Any] | None:
    booking = get_booking(booking_id)
    if not booking:
        return None

    result = update_booking(booking_id, meal_plan=new_meal_plan)
    if result:
        _add_booking_history(
            booking_id=booking_id,
            action="meal_plan_changed",
            previous_value={"meal_plan": booking.get("meal_plan")},
            new_value={"meal_plan": new_meal_plan},
            user_id=user_id,
        )
    return result


def get_booking_history(booking_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT * FROM {_t(PMS_BOOKING_HISTORY_TABLE)}
        WHERE booking_id = %s
        ORDER BY created_at ASC
        """,
        [booking_id],
    )


def _add_booking_history(
    *,
    booking_id: int,
    action: str,
    previous_value: dict | None = None,
    new_value: dict | None = None,
    user_id: int | None = None,
) -> dict[str, Any] | None:
    pv = _to_pg_json(previous_value) if previous_value else "{}"
    nv = _to_pg_json(new_value) if new_value else "{}"
    return fetch_one(
        f"""
        INSERT INTO {_t(PMS_BOOKING_HISTORY_TABLE)}
            (booking_id, action, previous_value, new_value, user_id, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        [booking_id, action, pv, nv, user_id, timezone.now(), timezone.now()],
    )


# ─── Rates ───────────────────────────────────────────────────────────────────


def create_rate(*, property_id: int, room_id: int, date_from: date, date_to: date, rate: Decimal, **kwargs: Any) -> dict[str, Any] | None:
    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO {_t(PMS_RATE_TABLE)}
            (id, property_id, room_id, date_from, date_to, rate, currency, min_stay, is_weekend_rate, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        [
            None, property_id, room_id, date_from, date_to, rate,
            kwargs.get("currency", "USD"), kwargs.get("min_stay", 1),
            kwargs.get("is_weekend_rate", False), now, now,
        ],
    )


def get_rate_by_id(rate_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT * FROM {_t(PMS_RATE_TABLE)} WHERE id = %s",
        [rate_id],
    )


def list_rates(property_id: int, *, room_id: int | None = None) -> list[dict[str, Any]]:
    if room_id:
        return fetch_all(
            f"SELECT * FROM {_t(PMS_RATE_TABLE)} WHERE property_id = %s AND room_id = %s ORDER BY date_from ASC",
            [property_id, room_id],
        )
    return fetch_all(
        f"SELECT * FROM {_t(PMS_RATE_TABLE)} WHERE property_id = %s ORDER BY date_from ASC",
        [property_id],
    )


def update_rate(rate_id: int, **kwargs: Any) -> dict[str, Any] | None:
    if not kwargs:
        return None

    sets = ", ".join(f"{k} = %s" for k in kwargs)
    values = list(kwargs.values())
    values.append(timezone.now())
    values.append(rate_id)

    return fetch_one(
        f"UPDATE {_t(PMS_RATE_TABLE)} SET {sets}, updated_at = %s WHERE id = %s RETURNING *",
        values,
    )


def delete_rate(rate_id: int) -> bool:
    return execute(
        f"DELETE FROM {_t(PMS_RATE_TABLE)} WHERE id = %s",
        [rate_id],
    ) > 0


def get_effective_rate(
    *,
    property_id: int,
    room_id: int,
    check_date: date,
) -> Decimal | None:
    rate = fetch_one(
        f"""
        SELECT rate FROM {_t(PMS_RATE_TABLE)}
        WHERE property_id = %s AND room_id = %s
        AND date_from <= %s AND date_to >= %s
        ORDER BY date_from DESC
        LIMIT 1
        """,
        [property_id, room_id, check_date, check_date],
    )
    if rate:
        return rate["rate"]
    return None


# ─── Reviews ─────────────────────────────────────────────────────────────────


def create_review(*, property_id: int, guest_name: str, rating: Decimal, text: str, **kwargs: Any) -> dict[str, Any] | None:
    now = timezone.now()
    categories = kwargs.get("categories", {})
    if isinstance(categories, dict):
        categories = _to_pg_json(categories)
    return fetch_one(
        f"""
        INSERT INTO {_t(PMS_REVIEW_TABLE)}
            (id, property_id, guest_name, rating, text, categories, is_complained, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        [
            None, property_id, guest_name, rating, text,
            categories, kwargs.get("is_complained", False),
            now, now,
        ],
    )


def list_reviews(property_id: int, *, rating: int | None = None, is_complained: bool | None = None) -> list[dict[str, Any]]:
    conditions = ["property_id = %s"]
    params: list[Any] = [property_id]

    if rating is not None:
        conditions.append("rating = %s")
        params.append(rating)
    if is_complained is not None:
        conditions.append("is_complained = %s")
        params.append(is_complained)

    where = " AND ".join(conditions)
    return fetch_all(
        f"SELECT * FROM {_t(PMS_REVIEW_TABLE)} WHERE {where} ORDER BY created_at DESC",
        params,
    )


def respond_to_review(review_id: int, response: str) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        UPDATE {_t(PMS_REVIEW_TABLE)}
        SET hotel_response = %s, response_date = %s, updated_at = %s
        WHERE id = %s
        RETURNING *
        """,
        [response, timezone.now(), timezone.now(), review_id],
    )


def complain_review(review_id: int, reason: str) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        UPDATE {_t(PMS_REVIEW_TABLE)}
        SET is_complained = TRUE, complaint_reason = %s, updated_at = %s
        WHERE id = %s
        RETURNING *
        """,
        [reason, timezone.now(), review_id],
    )


# ─── Analytics ────────────────────────────────────────────────────────────────


def get_analytics(
    *,
    property_id: int,
    date_from: date,
    date_to: date,
    metric: str = "revenue",
    category: str | None = None,
    floor: str | None = None,
    search: str | None = None,
) -> dict[str, Any]:
    prev_date_from = date_from - (date_to - date_from) - timedelta(days=1)
    prev_date_to = date_from - timedelta(days=1)
    days_in_period = (date_to - date_from).days + 1

    def _calc_change(current: float, previous: float) -> tuple[float, float]:
        change = current - previous
        pct = (change / previous * 100) if previous > 0 else 0
        return round(change, 2), round(pct, 1)

    # --- KPIs: single query for current + previous ---
    total_rooms = fetch_one(
        f"SELECT COUNT(*) as count FROM {_t(PMS_ROOM_TABLE)} WHERE property_id = %s AND is_active = TRUE",
        [property_id],
    ) or {"count": 1}
    room_count = total_rooms["count"]

    kpi_row = fetch_one(
        f"""
        SELECT
            COUNT(*) FILTER (WHERE period = 'current' AND b.status NOT IN ('cancelled', 'no_show')) AS cur_check_ins,
            COALESCE(SUM(total_cost::numeric) FILTER (WHERE period = 'current' AND b.status NOT IN ('cancelled', 'no_show')), 0) AS cur_revenue,
            COUNT(*) FILTER (WHERE period = 'current') AS cur_bookings,
            COALESCE(SUM(total_cost::numeric) FILTER (WHERE period = 'previous' AND b.status NOT IN ('cancelled', 'no_show')), 0) AS prev_revenue,
            COUNT(*) FILTER (WHERE period = 'previous' AND b.status NOT IN ('cancelled', 'no_show')) AS prev_check_ins,
            COUNT(*) FILTER (WHERE period = 'previous') AS prev_bookings,
            COALESCE(MAX(b.currency) FILTER (WHERE period = 'current'), 'UZS') AS currency
        FROM (
            SELECT b.*, 'current' AS period
            FROM {_t(PMS_BOOKING_TABLE)} b
            WHERE b.property_id = %s AND b.check_in BETWEEN %s AND %s
            UNION ALL
            SELECT b.*, 'previous' AS period
            FROM {_t(PMS_BOOKING_TABLE)} b
            WHERE b.property_id = %s AND b.check_in BETWEEN %s AND %s
        ) b
        """,
        [property_id, date_from, date_to, property_id, prev_date_from, prev_date_to],
    ) or {"cur_check_ins": 0, "cur_revenue": 0, "cur_bookings": 0, "prev_revenue": 0, "prev_check_ins": 0, "prev_bookings": 0, "currency": "UZS"}

    occ_current = fetch_one(
        f"""
        SELECT COUNT(DISTINCT cs.date || '-' || cs.room_id) as count
        FROM {_t(PMS_CALENDAR_SLOT_TABLE)} cs
        JOIN {_t(PMS_ROOM_TABLE)} r ON r.id = cs.room_id
        WHERE r.property_id = %s AND cs.date BETWEEN %s AND %s AND cs.status = 'occupied'
        """,
        [property_id, date_from, date_to],
    ) or {"count": 0}
    occ_prev = fetch_one(
        f"""
        SELECT COUNT(DISTINCT cs.date || '-' || cs.room_id) as count
        FROM {_t(PMS_CALENDAR_SLOT_TABLE)} cs
        JOIN {_t(PMS_ROOM_TABLE)} r ON r.id = cs.room_id
        WHERE r.property_id = %s AND cs.date BETWEEN %s AND %s AND cs.status = 'occupied'
        """,
        [property_id, prev_date_from, prev_date_to],
    ) or {"count": 0}

    cur_occ = (occ_current["count"] / (room_count * days_in_period) * 100) if room_count > 0 and days_in_period > 0 else 0
    prev_occ = (occ_prev["count"] / (room_count * max(days_in_period, 1)) * 100) if room_count > 0 else 0

    cur_guests = fetch_one(
        f"""
        SELECT COUNT(DISTINCT b.guest_id) as count
        FROM {_t(PMS_BOOKING_TABLE)} b
        WHERE b.property_id = %s AND b.check_in <= %s AND b.check_out > %s AND b.status = 'checked_in'
        """,
        [property_id, date_to, date_to],
    ) or {"count": 0}

    cur = {
        "check_ins": int(kpi_row["cur_check_ins"]),
        "revenue": float(kpi_row["cur_revenue"]),
        "bookings": int(kpi_row["cur_bookings"]),
        "occupancy": round(cur_occ, 1),
        "current_guests": int(cur_guests["count"]),
    }
    prev = {
        "check_ins": int(kpi_row["prev_check_ins"]),
        "revenue": float(kpi_row["prev_revenue"]),
        "bookings": int(kpi_row["prev_bookings"]),
        "occupancy": round(prev_occ, 1),
    }

    ci_ch, ci_pct = _calc_change(cur["check_ins"], prev["check_ins"])
    rev_ch, rev_pct = _calc_change(cur["revenue"], prev["revenue"])
    bk_ch, bk_pct = _calc_change(cur["bookings"], prev["bookings"])
    occ_ch, occ_pct = _calc_change(cur["occupancy"], prev["occupancy"])

    kpi = {
        "check_ins": {"value": cur["check_ins"], "change": ci_ch, "change_percent": ci_pct},
        "revenue": {"value": cur["revenue"], "change": rev_ch, "change_percent": rev_pct, "currency": kpi_row["currency"]},
        "bookings": {"value": cur["bookings"], "change": bk_ch, "change_percent": bk_pct},
        "occupancy": {"value": cur["occupancy"], "change": occ_ch, "change_percent": occ_pct},
        "current_guests": {"value": cur["current_guests"]},
    }

    # --- Chart: single query with generate_series ---
    offset_days = (date_to - date_from).days + 1
    chart_points = []
    if metric == "occupancy":
        occ_chart = fetch_all(
            f"""
            SELECT d.date::date AS dt, COUNT(DISTINCT cs.room_id) as occupied
            FROM generate_series(%s::date, %s::date, '1 day'::interval) AS d(date)
            LEFT JOIN {_t(PMS_CALENDAR_SLOT_TABLE)} cs ON cs.date = d.date::date AND cs.status = 'occupied'
            LEFT JOIN {_t(PMS_ROOM_TABLE)} r ON r.id = cs.room_id AND r.property_id = %s
            GROUP BY d.date::date
            ORDER BY d.date::date
            """,
            [date_from, date_to, property_id],
        )
        prev_occ_chart = fetch_all(
            f"""
            SELECT d.date::date AS dt, COUNT(DISTINCT cs.room_id) as occupied
            FROM generate_series(%s::date, %s::date, '1 day'::interval) AS d(date)
            LEFT JOIN {_t(PMS_CALENDAR_SLOT_TABLE)} cs ON cs.date = d.date::date AND cs.status = 'occupied'
            LEFT JOIN {_t(PMS_ROOM_TABLE)} r ON r.id = cs.room_id AND r.property_id = %s
            GROUP BY d.date::date
            ORDER BY d.date::date
            """,
            [prev_date_from, prev_date_to, property_id],
        )
        for i, row in enumerate(occ_chart):
            prev_val = None
            if i < len(prev_occ_chart):
                prev_val = (prev_occ_chart[i]["occupied"] / room_count * 100) if room_count > 0 else 0
            chart_points.append({
                "date": row["dt"].isoformat() if hasattr(row["dt"], "isoformat") else str(row["dt"]),
                "value": round((row["occupied"] / room_count * 100) if room_count > 0 else 0, 1),
                "previous_value": round(prev_val, 1) if prev_val is not None else None,
            })
    else:
        if metric == "check_ins":
            field = "COUNT(*) FILTER (WHERE b.status NOT IN ('cancelled', 'no_show'))"
            prev_field = field
        elif metric == "revenue":
            field = "COALESCE(SUM(b.total_cost::numeric) FILTER (WHERE b.status NOT IN ('cancelled', 'no_show')), 0)"
            prev_field = field
        elif metric == "bookings":
            field = "COUNT(*)"
            prev_field = field
        else:
            field = "COUNT(*)"
            prev_field = field

        cur_chart = fetch_all(
            f"""
            SELECT d.date::date AS dt, {field} AS value
            FROM generate_series(%s::date, %s::date, '1 day'::interval) AS d(date)
            LEFT JOIN {_t(PMS_BOOKING_TABLE)} b ON {f"b.check_in = d.date::date" if metric != "bookings" else "b.created_at::date = d.date::date"}
                AND b.property_id = %s
            GROUP BY d.date::date
            ORDER BY d.date::date
            """,
            [date_from, date_to, property_id],
        )
        prev_chart = fetch_all(
            f"""
            SELECT d.date::date AS dt, {prev_field} AS value
            FROM generate_series(%s::date, %s::date, '1 day'::interval) AS d(date)
            LEFT JOIN {_t(PMS_BOOKING_TABLE)} b ON {f"b.check_in = d.date::date" if metric != "bookings" else "b.created_at::date = d.date::date"}
                AND b.property_id = %s
            GROUP BY d.date::date
            ORDER BY d.date::date
            """,
            [prev_date_from, prev_date_to, property_id],
        )
        for i, row in enumerate(cur_chart):
            prev_val = float(prev_chart[i]["value"]) if i < len(prev_chart) else None
            chart_points.append({
                "date": row["dt"].isoformat() if hasattr(row["dt"], "isoformat") else str(row["dt"]),
                "value": float(row["value"]) if row["value"] is not None else 0,
                "previous_value": prev_val,
            })

    chart = {"points": chart_points, "metric": metric}

    # --- Room analytics: single batched query ---
    room_conditions = ["r.property_id = %s", "r.is_active = TRUE"]
    room_params: list[Any] = [property_id]
    if category:
        room_conditions.append("r.room_type_name = %s")
        room_params.append(category)
    if floor:
        room_conditions.append("r.floor = %s")
        room_params.append(int(floor))
    if search:
        room_conditions.append("r.room_number ILIKE %s")
        room_params.append(f"%{search}%")
    room_where = " AND ".join(room_conditions)

    rooms_raw = fetch_all(
        f"""
        SELECT
            r.id as room_id, r.room_number, r.room_type_name as category, r.floor,
            COALESCE(SUM(b.total_cost::numeric) FILTER (WHERE b.status NOT IN ('cancelled', 'no_show')), 0) as revenue,
            COUNT(b.id) FILTER (WHERE b.status NOT IN ('cancelled', 'no_show')) as booking_count,
            COALESCE(MAX(b.currency), 'UZS') as currency,
            COUNT(DISTINCT cs.date) FILTER (WHERE cs.status = 'occupied' AND cs.date BETWEEN %s AND %s) as cur_occ_nights,
            COUNT(DISTINCT cs.date) FILTER (WHERE cs.status = 'occupied' AND cs.date BETWEEN %s AND %s) as prev_occ_nights,
            COALESCE(SUM(b.total_cost::numeric) FILTER (WHERE b.status NOT IN ('cancelled', 'no_show') AND b.check_in BETWEEN %s AND %s), 0) as prev_revenue,
            COUNT(b.id) FILTER (WHERE b.status NOT IN ('cancelled', 'no_show') AND b.check_in BETWEEN %s AND %s) as prev_booking_count
        FROM {_t(PMS_ROOM_TABLE)} r
        LEFT JOIN {_t(PMS_BOOKING_TABLE)} b ON b.room_id = r.id AND b.check_in BETWEEN %s AND %s
        LEFT JOIN {_t(PMS_CALENDAR_SLOT_TABLE)} cs ON cs.room_id = r.id
        WHERE {room_where}
        GROUP BY r.id, r.room_number, r.room_type_name, r.floor
        ORDER BY r.room_number ASC
        """,
        [date_from, date_to, prev_date_from, prev_date_to, prev_date_from, prev_date_to, prev_date_from, prev_date_to, date_from, date_to] + room_params,
    )

    rooms = []
    for room in rooms_raw:
        revenue = float(room["revenue"])
        prev_rev = float(room["prev_revenue"] or 0)
        bcount = room["booking_count"]
        prev_bcount = room["prev_booking_count"] or 1

        cur_occ_pct = (room["cur_occ_nights"] / days_in_period * 100) if days_in_period > 0 else 0
        prev_occ_pct = (room["prev_occ_nights"] / max(days_in_period, 1) * 100) if days_in_period > 0 else 0

        adr = revenue / bcount if bcount > 0 else 0
        prev_adr = prev_rev / prev_bcount if prev_bcount > 0 else 0
        revpar = revenue / days_in_period if days_in_period > 0 else 0
        prev_revpar = prev_rev / max(days_in_period, 1) if days_in_period > 0 else 0

        occ_ch, occ_pct_v = _calc_change(cur_occ_pct, prev_occ_pct)
        rev_ch, rev_pct_v = _calc_change(revenue, prev_rev)
        adr_ch, adr_pct_v = _calc_change(adr, prev_adr)
        rp_ch, rp_pct_v = _calc_change(revpar, prev_revpar)

        rooms.append({
            "room_id": room["room_id"],
            "room_number": room["room_number"],
            "category": room["category"] or "Standard",
            "occupancy": {"value": round(cur_occ_pct, 1), "change": occ_ch, "change_percent": occ_pct_v},
            "revenue": {"value": revenue, "change": rev_ch, "change_percent": rev_pct_v, "currency": room["currency"]},
            "adr": {"value": round(adr, 2), "change": adr_ch, "change_percent": adr_pct_v, "currency": room["currency"]},
            "revpar": {"value": round(revpar, 2), "change": rp_ch, "change_percent": rp_pct_v, "currency": room["currency"]},
        })

    return {
        "kpi": kpi,
        "chart": chart,
        "rooms": rooms,
        "period": {
            "type": "custom",
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
        },
    }
