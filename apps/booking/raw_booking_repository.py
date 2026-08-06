from __future__ import annotations

import zlib
from datetime import timedelta
from typing import Any
from uuid import uuid4

from django.utils import timezone
from shared.raw.compat import get_table_name, is_postgresql, return_star
from shared.raw.db import execute, fetch_all, fetch_one

BOOKING_ALLOWED_STATUSES = {"pending", "confirmed", "cancelled", "completed"}


def get_verified_property_for_booking(property_guid: str) -> dict[str, Any] | None:
    row = fetch_one(
        f"""
        SELECT
            'apartment' AS property_kind,
            a.id AS property_id,
            a.guid,
            a.partner_user_id,
            a.title,
            a.price,
            a.currency,
            a.latitude,
            a.longitude,
            a.city,
            a.country,
            a.guests,
            u.username AS partner_username,
            u.first_name AS partner_first_name,
            u.last_name AS partner_last_name,
            u.phone_number AS partner_phone_number
        FROM {get_table_name("apartment")} a
        LEFT JOIN {get_table_name("users")} u ON u.id = a.partner_user_id
        WHERE a.guid = %s
          AND COALESCE(a.is_verified, FALSE) = TRUE
        """,
        [property_guid],
    )
    if row:
        return row
    return fetch_one(
        f"""
        SELECT
            'cottage' AS property_kind,
            c.id AS property_id,
            c.guid,
            c.partner_user_id,
            c.title,
            c.weekend_only_sunday_inclusive,
            COALESCE(current_price.price_per_person, c.price_per_person) AS price_per_person,
            COALESCE(current_price.price_on_working_days, c.price_on_working_days) AS price_on_working_days,
            COALESCE(current_price.price_on_weekends, c.price_on_weekends) AS price_on_weekends,
            c.currency,
            c.latitude,
            c.longitude,
            c.city,
            c.country,
            c.guests,
            u.username AS partner_username,
            u.first_name AS partner_first_name,
            u.last_name AS partner_last_name,
            u.phone_number AS partner_phone_number,
            COALESCE(monthly_prices.price_data, '[]'::jsonb) AS price
        FROM {get_table_name("cottage")} c
        LEFT JOIN {get_table_name("users")} u ON u.id = c.partner_user_id
        LEFT JOIN LATERAL (
            SELECT
                jsonb_agg(
                    jsonb_build_object(
                        'guid', cp.guid,
                        'month_from', cp.month_from,
                        'month_to', cp.month_to,
                        'price_per_person', cp.price_per_person,
                        'price_on_working_days', cp.price_on_working_days,
                        'price_on_weekends', cp.price_on_weekends
                    ) ORDER BY cp.month_from
                ) AS price_data
            FROM {get_table_name("cottage_price")} cp
            WHERE cp.cottage_id = c.id
        ) monthly_prices ON TRUE
        LEFT JOIN LATERAL (
            SELECT
                cp.price_per_person,
                cp.price_on_working_days,
                cp.price_on_weekends
            FROM {get_table_name("cottage_price")} cp
            WHERE cp.cottage_id = c.id
              AND CURRENT_DATE BETWEEN cp.month_from AND cp.month_to
            LIMIT 1
        ) current_price ON TRUE
        WHERE c.guid = %s
          AND COALESCE(c.is_verified, FALSE) = TRUE
        """,
        [property_guid],
    )


BOOKING_BASE_SELECT = f"""
    SELECT
        b.id,
        b.guid,
        b.guid AS booking_price_guid,
        b.created_at,
        b.updated_at,
        b.booking_number,
        b.check_in,
        b.check_out,
        b.adults,
        b.children,
        b.babies,
        b.status,
        b.cancellation_reason,
        b.confirmed_at,
        b.cancelled_at,
        b.completed_at,
        b.payment_reminder_stage,
        b.client_user_id,
        b.property_apartment_id,
        b.property_cottage_id,

        client_u.first_name AS client_first_name,
        client_u.last_name AS client_last_name,
        client_u.phone_number AS client_phone_number,
        client_u.username AS client_username,

        COALESCE(a.guid, c.guid) AS property_guid,
        COALESCE(a.title, c.title) AS property_title,
        COALESCE(a.img, c.img) AS property_img,
        COALESCE(a.latitude, c.latitude) AS property_latitude,
        COALESCE(a.longitude, c.longitude) AS property_longitude,
        COALESCE(a.city, c.city) AS property_city,
        COALESCE(a.country, c.country) AS property_country,
        COALESCE(a.currency, c.currency, 'UZS') AS property_currency,
        COALESCE(a.guests, c.guests) AS property_guests,
        CASE
            WHEN b.property_apartment_id IS NOT NULL THEN 'Apartment'
            ELSE 'Cottages'
        END AS property_type_title,

        partner_u.id AS partner_user_id,
        partner_u.username AS partner_username,
        partner_u.first_name AS partner_first_name,
        partner_u.last_name AS partner_last_name,
        partner_u.phone_number AS partner_phone_number,

        bp.subtotal AS booking_subtotal,
        bp.hold_amount AS booking_hold_amount,
        bp.charge_amount AS booking_charge_amount,
        bp.service_fee AS booking_service_fee,
        bp.service_fee_percentage AS booking_service_fee_percentage,
        bp.currency AS booking_currency

    FROM {get_table_name("booking")} b
    LEFT JOIN {get_table_name("users")} client_u ON client_u.id = b.client_user_id
    LEFT JOIN {get_table_name("apartment")} a ON a.id = b.property_apartment_id
    LEFT JOIN {get_table_name("cottage")} c ON c.id = b.property_cottage_id
    LEFT JOIN {get_table_name("users")} partner_u ON partner_u.id = COALESCE(a.partner_user_id, c.partner_user_id)
    LEFT JOIN LATERAL (
        SELECT
            COALESCE(SUM(th.amount), 0) AS subtotal,
            MAX(CASE WHEN COALESCE(th.type, '') <> 'CHRG' THEN th.amount END) AS hold_amount,
            MAX(CASE WHEN th.type = 'CHRG' THEN th.amount END) AS charge_amount,
            MAX(CASE WHEN COALESCE(th.type, '') <> 'CHRG' THEN th.amount END) AS service_fee,
            20::smallint AS service_fee_percentage,
            MAX(th.currency) AS currency
        FROM {get_table_name("transaction_history")} th
        WHERE th.booking_id = b.id
    ) bp ON TRUE
"""


def _normalize_statuses(statuses: list[str] | None) -> list[str]:
    if not statuses:
        return []
    normalized = [
        str(value).strip().lower() for value in statuses if str(value).strip()
    ]
    invalid = [value for value in normalized if value not in BOOKING_ALLOWED_STATUSES]
    if invalid:
        raise ValueError(
            f"Invalid status: {', '.join(invalid)}; allowed: {', '.join(sorted(BOOKING_ALLOWED_STATUSES))}"
        )
    return normalized


def list_client_bookings(
    client_user_id: int, statuses: list[str] | None = None
) -> list[dict[str, Any]]:
    normalized_statuses = _normalize_statuses(statuses)
    where = ["b.client_user_id = %s"]
    params: list[Any] = [client_user_id]
    if normalized_statuses:
        if is_postgresql():
            where.append("b.status = ANY(%s)")
            params.append(normalized_statuses)
        else:
            placeholders = ",".join(["%s"] * len(normalized_statuses))
            where.append(f"b.status IN ({placeholders})")
            params.extend(normalized_statuses)

    return fetch_all(
        f"""
        {BOOKING_BASE_SELECT}
        WHERE {" AND ".join(where)}
        ORDER BY b.created_at DESC, b.id DESC
        """,
        params,
    )


def get_client_booking_by_guid(
    booking_guid: str, client_user_id: int
) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        {BOOKING_BASE_SELECT}
        WHERE b.guid = %s
          AND b.client_user_id = %s
        LIMIT 1
        """,
        [booking_guid, client_user_id],
    )


def list_client_booking_history(client_user_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        {BOOKING_BASE_SELECT}
        WHERE b.client_user_id = %s
        ORDER BY b.created_at DESC, b.id DESC
        """,
        [client_user_id],
    )


def get_client_booking_history_detail(
    booking_guid: str, client_user_id: int
) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        {BOOKING_BASE_SELECT}
        WHERE b.guid = %s
          AND b.client_user_id = %s
        LIMIT 1
        """,
        [booking_guid, client_user_id],
    )


def list_partner_bookings(
    partner_user_id: int, statuses: list[str] | None = None
) -> list[dict[str, Any]]:
    normalized_statuses = _normalize_statuses(statuses)
    where = ["partner_u.id = %s"]
    params: list[Any] = [partner_user_id]
    if normalized_statuses:
        if is_postgresql():
            where.append("b.status = ANY(%s)")
            params.append(normalized_statuses)
        else:
            placeholders = ",".join(["%s"] * len(normalized_statuses))
            where.append(f"b.status IN ({placeholders})")
            params.extend(normalized_statuses)

    return fetch_all(
        f"""
        {BOOKING_BASE_SELECT}
        WHERE {" AND ".join(where)}
        ORDER BY b.created_at DESC, b.id DESC
        """,
        params,
    )


def get_booking_for_client_action(
    booking_guid: str, client_user_id: int
) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        {BOOKING_BASE_SELECT}
        WHERE b.guid = %s
          AND b.client_user_id = %s
        LIMIT 1
        """,
        [booking_guid, client_user_id],
    )


def get_booking_for_partner_action(
    booking_guid: str, partner_user_id: int
) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        {BOOKING_BASE_SELECT}
        WHERE b.guid = %s
          AND partner_u.id = %s
        LIMIT 1
        """,
        [booking_guid, partner_user_id],
    )


def get_booking_by_guid(booking_guid: str) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        {BOOKING_BASE_SELECT}
        WHERE b.guid = %s
        LIMIT 1
        """,
        [booking_guid],
    )


def _property_column_from_booking(booking_row: dict[str, Any]) -> tuple[str, int]:
    if booking_row.get("property_apartment_id"):
        return "property_apartment_id", int(booking_row["property_apartment_id"])
    if booking_row.get("property_cottage_id"):
        return "property_cottage_id", int(booking_row["property_cottage_id"])
    raise ValueError("Booking has no property reference")


def _property_ids(
    property_kind: str, property_id: int
) -> tuple[int | None, int | None]:
    if property_kind == "apartment":
        return property_id, None
    if property_kind == "cottage":
        return None, property_id
    raise ValueError("Invalid property kind")


def create_booking_row(
    *,
    client_user_id: int,
    property_kind: str,
    property_id: int,
    check_in,
    check_out,
    adults: int,
    children: int,
    babies: int,
    booking_number: str,
) -> dict[str, Any] | None:
    apartment_id, cottage_id = _property_ids(property_kind, property_id)
    now = timezone.now()
    returning_clause = "RETURNING *" if return_star() else ""
    row = fetch_one(
        f"""
        INSERT INTO {get_table_name("booking")} (
            guid,
            created_at,
            updated_at,
            booking_number,
            check_in,
            check_out,
            adults,
            children,
            babies,
            reminder_sent,
            status,
            payment_reminder_stage,
            client_user_id,
            property_apartment_id,
            property_cottage_id
        ) VALUES (
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            FALSE,
            'pending',
            NULL,
            %s,
            %s,
            %s
        )
        {returning_clause}
        """,
        [
            uuid4(),
            now,
            now,
            booking_number,
            check_in,
            check_out,
            adults,
            children,
            babies,
            client_user_id,
            apartment_id,
            cottage_id,
        ],
    )
    if row is None and not return_star():
        row = fetch_one(
            f"SELECT * FROM {get_table_name('booking')} WHERE booking_number = %s ORDER BY id DESC LIMIT 1",
            [booking_number],
        )
    return row


def list_pending_bookings_for_payment_reminders() -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        {BOOKING_BASE_SELECT}
        WHERE b.status = 'pending'
        ORDER BY b.created_at ASC, b.id ASC
        """
    )


def update_booking_payment_reminder_stage(booking_id: int, stage: str) -> int:
    return execute(
        f"""
        UPDATE {get_table_name("booking")}
        SET payment_reminder_stage = %s,
            updated_at = %s
        WHERE id = %s
        """,
        [stage, timezone.now(), booking_id],
    )


def lock_property_calendar(property_kind: str, property_id: int) -> None:
    """Serialize booking attempts for one property inside the current
    transaction.

    The availability check reads the calendar and the insert writes it, with
    no row to `SELECT ... FOR UPDATE` for dates that are still free — so two
    concurrent requests for the same dates could both see "available" and both
    book. Advisory locks release automatically at COMMIT/ROLLBACK.

    Mirrors `apps.b2b.repository.lock_employees_for_booking`; the namespace
    differs so the two lock spaces cannot collide.
    """
    lock_namespace = 911002
    # Kind is part of the key: apartment #5 and cottage #5 are different rows.
    # crc32, not hash(): str hashing is salted per process, so two workers
    # would derive different keys for the same property and never contend.
    key = (zlib.crc32(str(property_kind).encode()) ^ int(property_id)) & 0x7FFFFFFF
    execute("SELECT pg_advisory_xact_lock(%s, %s)", [lock_namespace, key])


def release_calendar_for_booking(booking_row: dict[str, Any]) -> int:
    property_column, property_id = _property_column_from_booking(booking_row)
    end_date = booking_row["check_out"] - timedelta(days=1)
    return execute(
        f"""
        DELETE FROM {get_table_name("calendar")}
        WHERE {property_column} = %s
          AND date BETWEEN %s AND %s
        """,
        [property_id, booking_row["check_in"], end_date],
    )


def update_booking_status(
    *,
    booking_id: int,
    status: str,
    cancellation_reason: str | None = None,
    set_confirmed: bool = False,
    set_cancelled: bool = False,
    set_completed: bool = False,
) -> dict[str, Any] | None:
    now = timezone.now()
    fields: list[str] = ["status = %s", "updated_at = %s"]
    params: list[Any] = [status, now]

    if set_confirmed:
        fields.append("confirmed_at = %s")
        params.append(now)
    if set_cancelled:
        fields.append("cancelled_at = %s")
        params.append(now)
        fields.append("cancellation_reason = %s")
        params.append(cancellation_reason)
    if set_completed:
        fields.append("completed_at = %s")
        params.append(now)

    params.append(booking_id)
    returning_clause = "RETURNING *" if return_star() else ""
    row = fetch_one(
        f"""
        UPDATE {get_table_name("booking")}
        SET {", ".join(fields)}
        WHERE id = %s
        {returning_clause}
        """,
        params,
    )
    if row is None and not return_star():
        row = fetch_one(
            f"SELECT * FROM {get_table_name('booking')} WHERE id = %s LIMIT 1",
            [booking_id],
        )
    return row


def count_admin_bookings(status: str | None = None, search: str | None = None) -> int:
    where = ["1 = 1"]
    params: list[Any] = []

    if status:
        # `status` may be a comma-separated list (e.g. "confirmed,checked_in")
        # so callers can group several statuses under one filter tab.
        normalized = _normalize_statuses(status.split(","))
        where.append("b.status = ANY(%s)")
        params.append(normalized)
    if search:
        like = f"%{search.strip()}%"
        where.append(
            "(COALESCE(b.booking_number, '') LIKE %s OR COALESCE(client_u.phone_number, '') LIKE %s)"
        )
        params.extend([like, like])

    row = fetch_one(
        f"""
        SELECT COUNT(*) AS total
        FROM {get_table_name("booking")} b
        LEFT JOIN {get_table_name("users")} client_u ON client_u.id = b.client_user_id
        WHERE """
        + " AND ".join(where),
        params,
    )
    return int(row["total"]) if row else 0


def list_admin_bookings(
    *,
    status: str | None = None,
    search: str | None = None,
    ordering: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    where = ["1 = 1"]
    params: list[Any] = []

    if status:
        normalized = _normalize_statuses(status.split(","))
        where.append("b.status = ANY(%s)")
        params.append(normalized)
    if search:
        like = f"%{search.strip()}%"
        where.append(
            "(COALESCE(b.booking_number, '') LIKE %s OR COALESCE(client_u.phone_number, '') LIKE %s)"
        )
        params.extend([like, like])

    order_map = {
        "created_at": "b.created_at",
        "check_in": "b.check_in",
        "status": "b.status",
    }
    raw_order = (ordering or "-created_at").strip()
    direction = "DESC"
    field = raw_order
    if raw_order.startswith("-"):
        direction = "DESC"
        field = raw_order[1:]
    elif raw_order.startswith("+"):
        direction = "ASC"
        field = raw_order[1:]
    else:
        direction = "ASC"
    order_column = order_map.get(field, "b.created_at")
    if order_column == "b.created_at" and field not in order_map:
        direction = "DESC"

    params.extend([limit, offset])

    return fetch_all(
        f"""
        {BOOKING_BASE_SELECT}
        WHERE {" AND ".join(where)}
        ORDER BY {order_column} {direction}, b.id DESC
        LIMIT %s OFFSET %s
        """,
        params,
    )
