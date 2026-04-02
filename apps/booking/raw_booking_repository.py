from __future__ import annotations

from typing import Any

from datetime import timedelta

from django.utils import timezone

from shared.raw.db import execute, fetch_all, fetch_one


BOOKING_ALLOWED_STATUSES = {"pending", "confirmed", "cancelled", "completed"}

BOOKING_BASE_SELECT = """
    SELECT
        b.id,
        b.guid,
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
        bp.service_fee_percentage AS booking_service_fee_percentage

    FROM public.booking b
    LEFT JOIN public.users client_u ON client_u.id = b.client_user_id
    LEFT JOIN public.apartment a ON a.id = b.property_apartment_id
    LEFT JOIN public.cottage c ON c.id = b.property_cottage_id
    LEFT JOIN public.users partner_u ON partner_u.id = COALESCE(a.partner_user_id, c.partner_user_id)
    LEFT JOIN LATERAL (
        SELECT
            COALESCE(SUM(th.amount), 0) AS subtotal,
            MAX(CASE WHEN COALESCE(th.type, '') <> 'CHRG' THEN th.amount END) AS hold_amount,
            MAX(CASE WHEN th.type = 'CHRG' THEN th.amount END) AS charge_amount,
            MAX(CASE WHEN COALESCE(th.type, '') <> 'CHRG' THEN th.amount END) AS service_fee,
            20::smallint AS service_fee_percentage
        FROM public.transaction_history th
        WHERE th.booking_id = b.id
    ) bp ON TRUE
"""


def _normalize_statuses(statuses: list[str] | None) -> list[str]:
    if not statuses:
        return []
    normalized = [str(value).strip().lower() for value in statuses if str(value).strip()]
    invalid = [value for value in normalized if value not in BOOKING_ALLOWED_STATUSES]
    if invalid:
        raise ValueError(
            f"Invalid status: {', '.join(invalid)}; allowed: {', '.join(sorted(BOOKING_ALLOWED_STATUSES))}"
        )
    return normalized


def list_client_bookings(client_user_id: int, statuses: list[str] | None = None) -> list[dict[str, Any]]:
    normalized_statuses = _normalize_statuses(statuses)
    where = ["b.client_user_id = %s"]
    params: list[Any] = [client_user_id]
    if normalized_statuses:
        where.append("b.status = ANY(%s)")
        params.append(normalized_statuses)

    return fetch_all(
        f"""
        {BOOKING_BASE_SELECT}
        WHERE {' AND '.join(where)}
        ORDER BY b.created_at DESC, b.id DESC
        """,
        params,
    )


def get_client_booking_by_guid(booking_guid: str, client_user_id: int) -> dict[str, Any] | None:
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


def get_client_booking_history_detail(booking_guid: str, client_user_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        {BOOKING_BASE_SELECT}
        WHERE b.guid = %s
          AND b.client_user_id = %s
        LIMIT 1
        """,
        [booking_guid, client_user_id],
    )


def list_partner_bookings(partner_user_id: int, statuses: list[str] | None = None) -> list[dict[str, Any]]:
    normalized_statuses = _normalize_statuses(statuses)
    where = ["partner_u.id = %s"]
    params: list[Any] = [partner_user_id]
    if normalized_statuses:
        where.append("b.status = ANY(%s)")
        params.append(normalized_statuses)

    return fetch_all(
        f"""
        {BOOKING_BASE_SELECT}
        WHERE {' AND '.join(where)}
        ORDER BY b.created_at DESC, b.id DESC
        """,
        params,
    )


def get_booking_for_client_action(booking_guid: str, client_user_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        {BOOKING_BASE_SELECT}
        WHERE b.guid = %s
          AND b.client_user_id = %s
        LIMIT 1
        """,
        [booking_guid, client_user_id],
    )


def get_booking_for_partner_action(booking_guid: str, partner_user_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        {BOOKING_BASE_SELECT}
        WHERE b.guid = %s
          AND partner_u.id = %s
        LIMIT 1
        """,
        [booking_guid, partner_user_id],
    )


def _property_column_from_booking(booking_row: dict[str, Any]) -> tuple[str, int]:
    if booking_row.get("property_apartment_id"):
        return "property_apartment_id", int(booking_row["property_apartment_id"])
    if booking_row.get("property_cottage_id"):
        return "property_cottage_id", int(booking_row["property_cottage_id"])
    raise ValueError("Booking has no property reference")


def release_calendar_for_booking(booking_row: dict[str, Any]) -> int:
    property_column, property_id = _property_column_from_booking(booking_row)
    end_date = booking_row["check_out"] - timedelta(days=1)
    return execute(
        f"""
        DELETE FROM public.calendar
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
    return fetch_one(
        f"""
        UPDATE public.booking
        SET {', '.join(fields)}
        WHERE id = %s
        RETURNING *
        """,
        params,
    )


def get_latest_transaction_history_for_booking(booking_id: int) -> dict[str, Any] | None:
    return fetch_one(
        """
        SELECT *
        FROM public.transaction_history
        WHERE booking_id = %s
        ORDER BY id DESC
        LIMIT 1
        """,
        [booking_id],
    )


def mark_latest_transaction_dismissed(booking_id: int) -> int:
    return execute(
        """
        UPDATE public.transaction_history th
        SET status = 'DISMISSED',
            updated_at = %s
        WHERE th.id = (
            SELECT id
            FROM public.transaction_history
            WHERE booking_id = %s
            ORDER BY id DESC
            LIMIT 1
        )
        """,
        [timezone.now(), booking_id],
    )


def create_charge_transaction_from_latest(
    *,
    booking_row: dict[str, Any],
    transaction_id: str | None,
    hold_id: str | None,
    amount,
    card_id: str | None = None,
    extra_id: str | None = None,
) -> dict[str, Any] | None:
    now = timezone.now()
    return fetch_one(
        """
        INSERT INTO public.transaction_history (
            booking_id,
            client_user_id,
            partner_user_id,
            amount,
            currency,
            transaction_id,
            hold_id,
            type,
            status,
            card_id,
            extra_id,
            created_at,
            updated_at
        ) VALUES (
            %s,
            %s,
            %s,
            %s,
            'UZS',
            %s,
            %s,
            'CHRG',
            'CHARGED',
            %s,
            %s,
            %s,
            %s
        )
        RETURNING *
        """,
        [
            int(booking_row["id"]),
            int(booking_row["client_user_id"]),
            int(booking_row["partner_user_id"]) if booking_row.get("partner_user_id") else None,
            amount,
            transaction_id,
            hold_id,
            card_id,
            extra_id,
            now,
            now,
        ],
    )


def count_admin_bookings(status: str | None = None, search: str | None = None) -> int:
    where = ["1 = 1"]
    params: list[Any] = []

    if status:
        normalized = _normalize_statuses([status])
        where.append("b.status = %s")
        params.append(normalized[0])
    if search:
        like = f"%{search.strip()}%"
        where.append(
            "(COALESCE(b.booking_number, '') ILIKE %s OR COALESCE(client_u.phone_number, '') ILIKE %s)"
        )
        params.extend([like, like])

    row = fetch_one(
        """
        SELECT COUNT(*)::int AS total
        FROM public.booking b
        LEFT JOIN public.users client_u ON client_u.id = b.client_user_id
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
        normalized = _normalize_statuses([status])
        where.append("b.status = %s")
        params.append(normalized[0])
    if search:
        like = f"%{search.strip()}%"
        where.append(
            "(COALESCE(b.booking_number, '') ILIKE %s OR COALESCE(client_u.phone_number, '') ILIKE %s)"
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
        WHERE {' AND '.join(where)}
        ORDER BY {order_column} {direction}, b.id DESC
        LIMIT %s OFFSET %s
        """,
        params,
    )
