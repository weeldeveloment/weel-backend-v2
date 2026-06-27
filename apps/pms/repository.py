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
    PMS_ROOM_TYPE_TABLE,
    PMS_ROOM_TABLE,
    PMS_ROOM_IMAGE_TABLE,
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


# ─── Room Types ──────────────────────────────────────────────────────────────


def create_room_type(*, property_id: int, name: str, **kwargs: Any) -> dict[str, Any] | None:
    now = timezone.now()
    cols = ["property_id", "name", "created_at", "updated_at"]
    vals = [property_id, name, now, now]

    field_map = {
        "preset": str, "custom_name": str,
        "description": str, "base_rate": lambda v: v, "currency": str,
        "capacity": int,
        "amenities": _to_pg_array,
        "photos": _to_pg_array,
        "is_active": bool,
    }

    for key, caster in field_map.items():
        if key in kwargs and kwargs[key] is not None:
            cols.append(key)
            vals.append(caster(kwargs[key]))

    placeholders = ", ".join(["%s"] * len(cols))
    col_names = ", ".join(cols)

    return fetch_one(
        f"INSERT INTO {_t(PMS_ROOM_TYPE_TABLE)} ({col_names}) VALUES ({placeholders}) RETURNING *",
        vals,
    )


def list_room_types(property_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        f"SELECT * FROM {_t(PMS_ROOM_TYPE_TABLE)} WHERE property_id = %s ORDER BY name ASC",
        [property_id],
    )


def get_room_type(room_type_id: int, property_id: int | None = None) -> dict[str, Any] | None:
    if property_id:
        return fetch_one(
            f"SELECT * FROM {_t(PMS_ROOM_TYPE_TABLE)} WHERE id = %s AND property_id = %s",
            [room_type_id, property_id],
        )
    return fetch_one(
        f"SELECT * FROM {_t(PMS_ROOM_TYPE_TABLE)} WHERE id = %s",
        [room_type_id],
    )


def update_room_type(room_type_id: int, **kwargs: Any) -> dict[str, Any] | None:
    if not kwargs:
        return None

    pg_array_fields = {"amenities", "photos"}
    sanitized = {}
    for k, v in kwargs.items():
        if k in pg_array_fields and isinstance(v, list):
            sanitized[k] = _to_pg_array(v)
        else:
            sanitized[k] = v

    sets = ", ".join(f"{k} = %s" for k in sanitized)
    values = list(sanitized.values())
    values.append(timezone.now())
    values.append(room_type_id)

    return fetch_one(
        f"UPDATE {_t(PMS_ROOM_TYPE_TABLE)} SET {sets}, updated_at = %s WHERE id = %s RETURNING *",
        values,
    )


def delete_room_type(room_type_id: int) -> bool:
    return execute(
        f"UPDATE {_t(PMS_ROOM_TYPE_TABLE)} SET is_active = FALSE, updated_at = %s WHERE id = %s",
        [timezone.now(), room_type_id],
    ) > 0


# ─── Rooms ───────────────────────────────────────────────────────────────────


def create_room(*, property_id: int, **kwargs: Any) -> dict[str, Any] | None:
    now = timezone.now()
    cols = ["property_id", "created_at", "updated_at"]
    vals = [property_id, now, now]

    field_map = {
        "room_type_id": int, "room_number": str, "display_name": str,
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


def list_rooms(property_id: int, *, room_type_id: int | None = None, is_active: bool = True) -> list[dict[str, Any]]:
    conditions = ["property_id = %s"]
    params: list[Any] = [property_id]

    if room_type_id:
        conditions.append("room_type_id = %s")
        params.append(room_type_id)
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
            f"""
            SELECT r.*,
                   CASE WHEN rt.id IS NOT NULL
                        THEN JSONB_BUILD_OBJECT('id', rt.id, 'name', rt.name)
                        ELSE NULL
                   END AS room_type
            FROM {_t(PMS_ROOM_TABLE)} r
            LEFT JOIN {_t(PMS_ROOM_TYPE_TABLE)} rt ON r.room_type_id = rt.id
            WHERE r.id = %s AND r.property_id = %s
            """,
            [room_id, property_id],
        )
    return fetch_one(
        f"""
        SELECT r.*,
               CASE WHEN rt.id IS NOT NULL
                    THEN JSONB_BUILD_OBJECT('id', rt.id, 'name', rt.name)
                    ELSE NULL
               END AS room_type
        FROM {_t(PMS_ROOM_TABLE)} r
        LEFT JOIN {_t(PMS_ROOM_TYPE_TABLE)} rt ON r.room_type_id = rt.id
        WHERE r.id = %s
        """,
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


def get_room_images(room_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        f"SELECT * FROM {_t(PMS_ROOM_IMAGE_TABLE)} WHERE room_id = %s ORDER BY \"order\" ASC",
        [room_id],
    )


def add_room_image(room_id: int, image_url: str, order: int = 0) -> dict[str, Any] | None:
    return fetch_one(
        f"INSERT INTO {_t(PMS_ROOM_IMAGE_TABLE)} (room_id, image_url, \"order\", created_at, updated_at) VALUES (%s, %s, %s, %s, %s) RETURNING *",
        [room_id, image_url, order, timezone.now(), timezone.now()],
    )


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
        SELECT cs.*, r.room_number, r.room_type_id
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
    results = []
    for room_id in room_ids:
        d = from_date
        while d <= to_date:
            slot = get_or_create_calendar_slot(room_id, d, "blocked")
            results.append(slot)
            d += timedelta(days=1)
    return results


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
    results = []
    expires = timezone.now() + timedelta(minutes=hold_duration_minutes)
    for room_id in room_ids:
        d = from_date
        while d <= to_date:
            slot = get_or_create_calendar_slot(room_id, d, "held")
            fetch_one(
                f"UPDATE {_t(PMS_CALENDAR_SLOT_TABLE)} SET status = 'held', hold_expires_at = %s, updated_at = %s WHERE id = %s RETURNING *",
                [expires, timezone.now(), slot["id"]],
            )
            results.append(slot)
            d += timedelta(days=1)
    return results


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
        SELECT r.id as room_id, r.room_number, r.room_type_id, r.capacity,
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

        d = check_in
        while d < check_out:
            execute(
                f"""
                INSERT INTO {_t(PMS_CALENDAR_SLOT_TABLE)} (room_id, date, status, created_at, updated_at)
                VALUES (%s, %s, 'occupied', %s, %s)
                ON CONFLICT (room_id, date) DO UPDATE SET status = 'occupied', updated_at = %s
                """,
                [room_id, d, now, now, now],
            )
            d += timedelta(days=1)

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
            d = booking["check_in"]
            if hasattr(d, "date"):
                d = d.date()
            end = booking["check_out"]
            if hasattr(end, "date"):
                end = end.date()
            while d < end:
                execute(
                    f"""
                    UPDATE {_t(PMS_CALENDAR_SLOT_TABLE)}
                    SET status = 'available', updated_at = %s
                    WHERE room_id = %s AND date = %s AND status = 'occupied'
                    """,
                    [timezone.now(), booking["room_id"], d],
                )
                d += timedelta(days=1)

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

        if old_room_id and old_check_in and old_check_out:
            d = old_check_in
            while d < old_check_out:
                execute(
                    f"""
                    UPDATE {_t(PMS_CALENDAR_SLOT_TABLE)}
                    SET status = 'available', updated_at = %s
                    WHERE room_id = %s AND date = %s AND status = 'occupied'
                    """,
                    [timezone.now(), old_room_id, d],
                )
                d += timedelta(days=1)

        actual_check_in = new_check_in or old_check_in
        actual_check_out = new_check_out or old_check_out
        if actual_check_in and actual_check_out:
            d = actual_check_in
            while d < actual_check_out:
                execute(
                    f"""
                    INSERT INTO {_t(PMS_CALENDAR_SLOT_TABLE)} (room_id, date, status, created_at, updated_at)
                    VALUES (%s, %s, 'occupied', %s, %s)
                    ON CONFLICT (room_id, date) DO UPDATE SET status = 'occupied', updated_at = %s
                    """,
                    [new_room_id, d, timezone.now(), timezone.now(), timezone.now()],
                )
                d += timedelta(days=1)

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


def create_rate(*, property_id: int, room_type_id: int, date_from: date, date_to: date, rate: Decimal, **kwargs: Any) -> dict[str, Any] | None:
    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO {_t(PMS_RATE_TABLE)}
            (id, property_id, room_type_id, date_from, date_to, rate, currency, min_stay, is_weekend_rate, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        [
            None, property_id, room_type_id, date_from, date_to, rate,
            kwargs.get("currency", "USD"), kwargs.get("min_stay", 1),
            kwargs.get("is_weekend_rate", False), now, now,
        ],
    )


def list_rates(property_id: int, *, room_type_id: int | None = None) -> list[dict[str, Any]]:
    if room_type_id:
        return fetch_all(
            f"SELECT * FROM {_t(PMS_RATE_TABLE)} WHERE property_id = %s AND room_type_id = %s ORDER BY date_from ASC",
            [property_id, room_type_id],
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
    room_type_id: int,
    check_date: date,
) -> Decimal | None:
    rate = fetch_one(
        f"""
        SELECT rate FROM {_t(PMS_RATE_TABLE)}
        WHERE property_id = %s AND room_type_id = %s
        AND date_from <= %s AND date_to >= %s
        ORDER BY date_from DESC
        LIMIT 1
        """,
        [property_id, room_type_id, check_date, check_date],
    )
    if rate:
        return rate["rate"]

    room_type = fetch_one(
        f"SELECT base_rate FROM {_t(PMS_ROOM_TYPE_TABLE)} WHERE id = %s AND property_id = %s",
        [room_type_id, property_id],
    )
    if room_type:
        return room_type["base_rate"]
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
