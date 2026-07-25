from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from shared.raw.db import execute, fetch_all, fetch_one
from payment.exchange_rate import to_uzs

logger = logging.getLogger(__name__)


def _to_uzs_amount(amount: Any, currency: str | None) -> tuple[Decimal | None, str]:
    """Convert a USD room price to UZS at the live rate; everything else
    (already UZS, or no price at all) passes through unchanged. Callers
    display and charge whatever this returns, so the two always agree."""
    if amount is None:
        return None, (currency or "UZS")
    value = Decimal(str(amount))
    if (currency or "UZS").upper() == "USD":
        return to_uzs(value), "UZS"
    return value, (currency or "UZS")


# ─── Room Availability ────────────────────────────────────────────────────────

def _check_room_availability(
    room_id: int,
    check_in: date,
    check_out: date,
) -> bool:
    row = fetch_one(
        """
        SELECT 1 FROM pms_room r
        WHERE r.id = %s
          AND r.is_active = TRUE
          AND NOT EXISTS (
              SELECT 1 FROM pms_booking b
              WHERE b.room_id = r.id
                AND b.status NOT IN ('cancelled', 'checked_out', 'no_show')
                AND b.check_in < %s
                AND b.check_out > %s
          )
          AND NOT EXISTS (
              SELECT 1 FROM pms_calendar_slot cs
              WHERE cs.room_id = r.id
                AND cs.status IN ('blocked', 'occupied', 'held')
                AND cs.date >= %s
                AND cs.date < %s
          )
        FOR UPDATE
        """,
        [room_id, check_out, check_in, check_in, check_out],
    )
    return row is not None


def _split_filter_values(values: list[str] | tuple[str, ...] | str | None) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        raw_items = values.split(",")
    else:
        raw_items = list(values)
    return [str(item).strip() for item in raw_items if str(item).strip()]


def _has_rate_plan_tables() -> bool:
    row = fetch_one(
        """
        SELECT (
            to_regclass('pms_rate_plan') IS NOT NULL
            AND to_regclass('pms_room_type_rate_plan') IS NOT NULL
        ) AS exists_flag
        """
    )
    return bool(row and row["exists_flag"])


def _room_filter_clause(
    *,
    room_types: list[str] | None = None,
    room_type_presets: list[str] | None = None,
    rate_plans: list[str] | None = None,
    meal_plans: list[str] | None = None,
    min_capacity: int | None = None,
    max_capacity: int | None = None,
    min_price: Decimal | None = None,
    max_price: Decimal | None = None,
) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []

    room_types = room_types or []
    room_type_presets = room_type_presets or []
    rate_plans = rate_plans or []
    meal_plans = meal_plans or []

    if room_types:
        clauses.append("r.room_type_name = ANY(%s::text[])")
        params.append(room_types)
    if room_type_presets:
        clauses.append("r.room_type_preset = ANY(%s::text[])")
        params.append(room_type_presets)
    if meal_plans:
        clauses.append("r.meal_plan = ANY(%s::text[])")
        params.append(meal_plans)
    if min_capacity is not None:
        clauses.append("r.capacity >= %s")
        params.append(min_capacity)
    if max_capacity is not None:
        clauses.append("r.capacity <= %s")
        params.append(max_capacity)
    if min_price is not None:
        clauses.append("COALESCE(r.base_price, 0) >= %s")
        params.append(min_price)
    if max_price is not None:
        clauses.append("COALESCE(r.base_price, 0) <= %s")
        params.append(max_price)
    if rate_plans:
        if _has_rate_plan_tables():
            clauses.append(
                """
                (
                    r.room_type_name = ANY(%s::text[])
                    OR r.room_type_preset = ANY(%s::text[])
                    OR EXISTS (
                        SELECT 1
                        FROM pms_room_type_rate_plan rtrp
                        JOIN pms_rate_plan rp ON rp.id = rtrp.rate_plan_id
                        WHERE rtrp.room_type_id = r.room_type_id
                          AND rp.code = ANY(%s::text[])
                          AND rp.is_active = TRUE
                    )
                )
                """
            )
            params.extend([rate_plans, rate_plans, rate_plans])
        else:
            clauses.append(
                "(r.room_type_name = ANY(%s::text[]) OR r.room_type_preset = ANY(%s::text[]) OR r.meal_plan = ANY(%s::text[]))"
            )
            params.extend([rate_plans, rate_plans, rate_plans])

    return clauses, params


def _room_sellability_reason(row: dict[str, Any]) -> str | None:
    availability = str(row.get("availability") or "").lower()
    condition = str(row.get("condition") or "").lower()
    if availability == "blocked":
        return "blocked"
    if availability == "occupied":
        return "occupied"
    if condition in {"maintenance", "inspection"}:
        return condition
    return None


def _calendar_status(row: dict[str, Any]) -> tuple[str, str | None]:
    booking_status = row.get("booking_status")
    slot_status = str(row.get("slot_status") or "").lower()
    availability = str(row.get("availability") or "").lower()
    condition = str(row.get("condition") or "").lower()

    if booking_status and booking_status not in {"cancelled", "checked_out", "no_show"}:
        return "occupied", "booking"
    if slot_status in {"held", "blocked", "occupied"}:
        return slot_status, slot_status
    if availability == "occupied":
        return "occupied", "room_occupied"
    if availability == "blocked":
        return "blocked", "room_blocked"
    if condition in {"maintenance", "inspection"}:
        return "blocked", condition
    return "available", None


def _best_room_allocation(
    rooms: list[dict[str, Any]],
    guests: int,
    *,
    nights: int = 1,
    budget_max: Decimal | None = None,
) -> tuple[list[dict[str, Any]], Decimal | None]:
    """Pick the smallest-capacity room combination that can host ``guests``.

    Tie-break order: smallest total capacity, then fewest rooms, then lowest
    total price, then stable room id order.
    """
    if guests <= 0 or not rooms:
        return [], None

    nights = max(int(nights or 1), 1)

    normalized: list[tuple[int, Decimal, int, dict[str, Any]]] = []
    for room in rooms:
        try:
            capacity = int(room.get("capacity_adults") or room.get("capacity") or 0)
        except (TypeError, ValueError):
            capacity = 0
        if capacity <= 0:
            continue
        try:
            price = Decimal(str(room.get("price_per_night") or 0))
        except Exception:
            price = Decimal("0")
        normalized.append((capacity, price, int(room.get("id") or 0), dict(room)))

    if not normalized:
        return [], None

    normalized.sort(key=lambda item: (-item[0], item[1], item[2]))
    suffix_capacity = [0] * (len(normalized) + 1)
    for idx in range(len(normalized) - 1, -1, -1):
        suffix_capacity[idx] = suffix_capacity[idx + 1] + normalized[idx][0]

    best_combo: list[dict[str, Any]] | None = None
    best_key: tuple[int, int, Decimal, tuple[int, ...]] | None = None

    def _maybe_store(selected: list[tuple[int, Decimal, int, dict[str, Any]]], total_capacity: int, total_price: Decimal) -> None:
        nonlocal best_combo, best_key
        if budget_max is not None and total_price > budget_max:
            return
        key = (
            total_capacity,
            len(selected),
            total_price,
            tuple(item[2] for item in selected),
        )
        if best_key is None or key < best_key:
            best_key = key
            best_combo = [dict(item[3]) for item in selected]

    def _dfs(start: int, selected: list[tuple[int, Decimal, int, dict[str, Any]]], total_capacity: int, total_price: Decimal) -> None:
        if budget_max is not None and total_price > budget_max:
            return
        if total_capacity >= guests:
            _maybe_store(selected, total_capacity, total_price)
            return
        if start >= len(normalized):
            return
        if total_capacity + suffix_capacity[start] < guests:
            return

        for idx in range(start, len(normalized)):
            capacity, price, room_id, room = normalized[idx]
            room_total_price = price * Decimal(nights)
            selected_room = dict(room)
            selected_room["nights"] = nights
            selected_room["total_price"] = room_total_price
            selected.append((capacity, room_total_price, room_id, selected_room))
            _dfs(idx + 1, selected, total_capacity + capacity, total_price + room_total_price)
            selected.pop()

    _dfs(0, [], 0, Decimal("0"))
    if not best_combo:
        return [], None
    best_combo.sort(key=lambda row: (str(row.get("room_number") or ""), int(row.get("id") or 0)))
    total_price = sum((Decimal(str(room.get("total_price") or 0)) for room in best_combo), Decimal("0"))
    return best_combo, total_price


def get_bookings_status(booking_ids: list[int]) -> list[dict[str, Any]]:
    """Fetch just id+status for a set of ``pms_booking`` rows.

    Must be called inside ``_run_in_schema(tenant_schema, ...)`` — used by
    the B2B group-booking status sync to check whether a hotel has
    accepted/rejected the sibling room bookings of a request.
    """
    if not booking_ids:
        return []
    return fetch_all(
        "SELECT id, status FROM pms_booking WHERE id = ANY(%s)",
        [booking_ids],
    )


def get_available_rooms(
    property_id: int,
    *,
    check_in: date,
    check_out: date,
    guests: int = 1,
    room_types: list[str] | None = None,
    room_type_presets: list[str] | None = None,
    rate_plans: list[str] | None = None,
    meal_plans: list[str] | None = None,
    min_capacity: int | None = None,
    max_capacity: int | None = None,
    min_price: Decimal | None = None,
    max_price: Decimal | None = None,
) -> list[dict[str, Any]]:
    filter_clauses, filter_params = _room_filter_clause(
        room_types=room_types,
        room_type_presets=room_type_presets,
        rate_plans=rate_plans,
        meal_plans=meal_plans,
        min_capacity=min_capacity if min_capacity is not None else guests,
        max_capacity=max_capacity,
        min_price=min_price,
        max_price=max_price,
    )
    extra_where = ""
    if filter_clauses:
        extra_where = " AND " + " AND ".join(filter_clauses)
    rows = fetch_all(
        f"""
        SELECT
            r.id, r.room_number, r.floor, r.display_name, r.room_type_id,
            r.bedroom_count, r.beds, r.amenities,
            r.capacity AS capacity_adults,
            r.room_type_name, r.room_type_preset AS preset,
            r.meal_plan,
            r.area AS area_sqm,
            r.base_price AS price_per_night,
            COALESCE(r.currency, 'UZS') AS currency,
            COALESCE(r.photos, '{{}}'::text[]) AS images,
            r.availability,
            r.condition
        FROM pms_room r
        WHERE r.property_id = %s
          AND r.is_active = TRUE
          AND r.base_price IS NOT NULL
          AND r.base_price > 0
          AND r.capacity >= %s
          AND NOT EXISTS (
              SELECT 1 FROM pms_booking b
              WHERE b.room_id = r.id
                AND b.status NOT IN ('cancelled', 'checked_out', 'no_show')
                AND b.check_in < %s
                AND b.check_out > %s
          )
          AND NOT EXISTS (
              SELECT 1 FROM pms_calendar_slot cs
              WHERE cs.room_id = r.id
                AND cs.date >= %s
                AND cs.date < %s
                AND cs.status != 'available'
          )
          {extra_where}
        ORDER BY r.room_number ASC
        """,
        [property_id, guests, check_out, check_in, check_in, check_out, *filter_params],
    )
    for row in rows:
        row["price_per_night"], row["currency"] = _to_uzs_amount(
            row.get("price_per_night"), row.get("currency")
        )
    return rows


def lock_hotel_rooms(property_id: int, room_ids: list[int]) -> list[dict[str, Any]]:
    """Lock selected rooms so availability can be checked and booked atomically."""
    if not room_ids:
        return []
    return fetch_all(
        """
        SELECT id, property_id
        FROM pms_room
        WHERE property_id = %s AND id = ANY(%s) AND is_active = TRUE
        ORDER BY id
        FOR UPDATE
        """,
        [property_id, sorted(room_ids)],
    )


def get_room_with_details(room_id: int) -> dict[str, Any] | None:
    return fetch_one(
        """
        SELECT r.*, r.room_type_name, r.room_type_preset AS preset,
               r.capacity AS room_type_capacity
        FROM pms_room r
        WHERE r.id = %s AND r.is_active = TRUE
        """,
        [room_id],
    )


# ─── Pricing ──────────────────────────────────────────────────────────────────

def calculate_stay_price(
    room_id: int,
    check_in: date,
    check_out: date,
) -> dict[str, Any] | None:
    nights = (check_out - check_in).days
    if nights <= 0:
        return None

    room = fetch_one(
        "SELECT base_price, currency FROM pms_room WHERE id = %s AND is_active = TRUE",
        [room_id],
    )
    if not room:
        return None

    raw_rate = room.get("base_price")
    if raw_rate is None:
        return None

    ppn, currency = _to_uzs_amount(raw_rate, room.get("currency"))
    if ppn is None or ppn <= 0:
        return None
    base_price = ppn * nights
    hold_amount = (base_price * Decimal("0.30")).quantize(Decimal("0.01"))
    return {
        "nights": nights,
        "price_per_night": ppn,
        "total_price": base_price,
        "hold_amount": hold_amount,
        "remaining_on_arrival": base_price - hold_amount,
        "currency": currency,
    }


# ─── Booking CRUD ─────────────────────────────────────────────────────────────

def _insert_hotel_booking_with_retry(
    *,
    property_id: int,
    room_id: int,
    client_user_id: int,
    check_in: date,
    check_out: date,
    adults: int,
    children: int,
    pricing: dict,
    b2b_company_id: int | None,
    max_retries: int = 5,
) -> dict[str, Any] | None:
    for _ in range(max_retries):
        row = fetch_one(
            """
            INSERT INTO pms_booking (
                property_id, room_id, created_by,
                check_in, check_out, adult_count, child_count,
                rate, currency, total_cost, hold_amount,
                status, b2b_company_id, created_at, booking_number
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s,
                %s, 'UZS', %s, %s,
                'new', %s, NOW(),
                'H' || TO_CHAR(NOW(), 'YYMMDD') || LPAD(FLOOR(RANDOM() * 99999)::int::text, 5, '0')
            )
            RETURNING id, property_id, room_id, check_in, check_out, adult_count, child_count,
                      total_cost, hold_amount, status, booking_number, created_at, created_by
            """,
            [
                property_id, room_id, client_user_id,
                check_in, check_out, adults, children,
                pricing["price_per_night"], pricing["total_price"], pricing["hold_amount"],
                b2b_company_id,
            ],
        )
        if row is not None:
            return row
    return None


def create_hotel_booking(
    *,
    property_id: int,
    room_id: int,
    client_user_id: int,
    check_in: date,
    check_out: date,
    adults: int = 1,
    children: int = 0,
    card_id: str | None = None,
    b2b_company_id: int | None = None,
) -> dict[str, Any] | None:
    nights = (check_out - check_in).days
    if nights <= 0:
        return None
    if adults < 1 or children < 0:
        return None

    if not _check_room_availability(room_id, check_in, check_out):
        return None

    pricing = calculate_stay_price(room_id, check_in, check_out)
    if not pricing:
        return None

    return _insert_hotel_booking_with_retry(
        property_id=property_id,
        room_id=room_id,
        client_user_id=client_user_id,
        check_in=check_in,
        check_out=check_out,
        adults=adults,
        children=children,
        pricing=pricing,
        b2b_company_id=b2b_company_id,
    )


def create_hotel_booking_calendar_slots(
    booking_id: int,
    room_id: int,
    check_in: date,
    check_out: date,
) -> None:
    execute(
        """
        INSERT INTO pms_calendar_slot (room_id, date, status, created_at, updated_at)
        SELECT %s, d.date, 'occupied', NOW(), NOW()
        FROM generate_series(%s::date, %s::date - 1, '1 day'::interval) AS d(date)
        ON CONFLICT (room_id, date) DO UPDATE SET status = 'occupied', updated_at = NOW()
        """,
        [room_id, check_in, check_out],
    )


def release_hotel_booking_calendar_slots(
    room_id: int,
    check_in: date,
    check_out: date,
) -> None:
    execute(
        """
        UPDATE pms_calendar_slot
        SET status = 'available', updated_at = NOW()
        WHERE room_id = %s
          AND date >= %s AND date < %s
          AND status = 'occupied'
        """,
        [room_id, check_in, check_out],
    )


def list_client_hotel_bookings(
    client_user_id: int,
    statuses: list[str] | None = None,
) -> list[dict[str, Any]]:
    room_type_name = (
        "r.room_type_name"
        if _pms_room_has_column("room_type_name")
        else "rt.name"
    )
    room_type_preset = (
        "r.room_type_preset"
        if _pms_room_has_column("room_type_preset")
        else "rt.preset"
    )
    conditions = ["b.created_by = %s"]
    params: list[Any] = [client_user_id]
    if statuses:
        conditions.append("b.status = ANY(%s)")
        params.append(statuses)
    where = " AND ".join(conditions)
    return fetch_all(
        f"""
        SELECT
            b.id, b.booking_number, b.status, b.check_in, b.check_out,
            b.adult_count, b.child_count, b.total_cost, b.hold_amount,
            b.created_at,
            b.property_id, b.room_id,
            p.name AS hotel_name, p.city AS hotel_city, p.star_rating AS hotel_star_rating,
            r.room_number, r.display_name AS room_name, r.floor,
            {room_type_name} AS room_type_name,
            {room_type_preset} AS room_type_preset,
            r.room_type_id,
            COALESCE(r.base_price, 0) AS room_price_per_night
        FROM pms_booking b
        JOIN pms_property p ON p.id = b.property_id
        JOIN pms_room r ON r.id = b.room_id
        LEFT JOIN pms_room_type rt ON rt.id = r.room_type_id
        WHERE {where}
        ORDER BY b.created_at DESC, b.id DESC
        """,
        params,
    )


def get_client_hotel_booking(
    booking_id: int,
    client_user_id: int,
) -> dict[str, Any] | None:
    room_type_name = (
        "r.room_type_name"
        if _pms_room_has_column("room_type_name")
        else "rt.name"
    )
    room_type_preset = (
        "r.room_type_preset"
        if _pms_room_has_column("room_type_preset")
        else "rt.preset"
    )
    return fetch_one(
        f"""
        SELECT
            b.id, b.booking_number, b.status, b.check_in, b.check_out,
            b.adult_count, b.child_count, b.total_cost, b.hold_amount,
            b.created_at,
            b.property_id, b.room_id,
            p.name AS hotel_name, p.city AS hotel_city, p.full_address AS hotel_address,
            p.star_rating AS hotel_star_rating, p.latitude, p.longitude,
            p.check_in_time AS hotel_check_in_time, p.check_out_time AS hotel_check_out_time,
            COALESCE(p.photos, ARRAY[]::text[]) AS hotel_images,
            r.room_number, r.display_name AS room_name, r.floor,
            COALESCE(r.base_price, 0) AS room_price_per_night,
            r.bedroom_count, r.beds, r.amenities,
            r.capacity,
            {room_type_name} AS room_type_name,
            {room_type_preset} AS room_type_preset,
            r.room_type_id,
            r.meal_plan,
            COALESCE(r.photos, '{{}}'::text[]) AS photos
        FROM pms_booking b
        JOIN pms_property p ON p.id = b.property_id
        JOIN pms_room r ON r.id = b.room_id
        LEFT JOIN pms_room_type rt ON rt.id = r.room_type_id
        WHERE b.id = %s AND b.created_by = %s
        """,
        [booking_id, client_user_id],
    )


def _pms_room_has_column(column_name: str) -> bool:
    row = fetch_one(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = 'pms_room'
              AND column_name = %s
        ) AS exists_flag
        """,
        [column_name],
    )
    return bool(row and row["exists_flag"])


def get_hotel_booking_by_id(booking_id: int) -> dict[str, Any] | None:
    return fetch_one(
        """
        SELECT
            b.id, b.booking_number, b.status, b.check_in, b.check_out,
            b.adult_count, b.child_count, b.total_cost, b.hold_amount,
            b.created_at, b.created_by,
            b.property_id, b.room_id
        FROM pms_booking b
        WHERE b.id = %s
        """,
        [booking_id],
    )


def list_client_hotel_bookings_across_schemas(
    client_user_id: int,
    statuses: list[str] | None = None,
) -> list[dict[str, Any]]:
    from apps.property.hotel_repository import _run_in_schema, list_hotel_organizations

    bookings: list[dict[str, Any]] = []
    for organization in list_hotel_organizations():
        schema_name = organization["schema_name"]
        try:
            rows = _run_in_schema(
                schema_name,
                lambda: list_client_hotel_bookings(client_user_id, statuses),
            )
        except Exception:
            logger.exception(
                "Failed to list client hotel bookings in tenant schema",
                extra={"tenant_schema": schema_name, "client_user_id": client_user_id},
            )
            continue
        for row in rows:
            row["tenant_schema"] = schema_name
        bookings.extend(rows)

    return sorted(
        bookings,
        key=lambda row: (row.get("created_at"), row.get("id")),
        reverse=True,
    )


def find_client_hotel_booking_across_schemas(
    booking_id: int,
    client_user_id: int,
) -> tuple[str, dict[str, Any]] | None:
    from apps.property.hotel_repository import _run_in_schema, list_hotel_organizations

    for organization in list_hotel_organizations():
        schema_name = organization["schema_name"]
        try:
            booking = _run_in_schema(
                schema_name,
                lambda: get_client_hotel_booking(booking_id, client_user_id),
            )
        except Exception:
            logger.exception(
                "Failed to find client hotel booking in tenant schema",
                extra={"tenant_schema": schema_name, "booking_id": booking_id},
            )
            continue
        if booking:
            booking["tenant_schema"] = schema_name
            return schema_name, booking
    return None


def find_hotel_booking_across_schemas(
    booking_id: int,
    *,
    schema_name: str | None = None,
) -> tuple[str, dict[str, Any]] | None:
    from apps.property.hotel_repository import _run_in_schema, list_hotel_organizations

    schema_names = (
        [schema_name]
        if schema_name
        else [organization["schema_name"] for organization in list_hotel_organizations()]
    )
    for candidate_schema in schema_names:
        try:
            booking = _run_in_schema(
                candidate_schema,
                lambda: get_hotel_booking_by_id(booking_id),
            )
        except Exception:
            logger.exception(
                "Failed to find hotel booking in tenant schema",
                extra={"tenant_schema": candidate_schema, "booking_id": booking_id},
            )
            continue
        if booking:
            booking["tenant_schema"] = candidate_schema
            return candidate_schema, booking
    return None


def update_hotel_booking_status(
    booking_id: int,
    status: str,
    cancellation_reason: str | None = None,
) -> dict[str, Any] | None:
    row = fetch_one(
        """
        UPDATE pms_booking
        SET status = %s, updated_at = NOW()
        WHERE id = %s
        RETURNING id, booking_number, status, check_in, check_out,
                  adult_count, child_count, total_cost, hold_amount,
                  property_id, room_id, created_by, created_at
        """,
        [status, booking_id],
    )
    return row


# ─── Hotel Search & Details ───────────────────────────────────────────────────
#
# Hotels live one-per-organization, each in its own Postgres schema (see
# apps/property/hotel_repository.py). Search/list must loop every hotel
# organization's schema (via ``_run_in_schema``) and merge the results —
# there is no single global ``pms_property`` table to query directly.
def _search_hotels_in_schema(
    schema_name: str,
    organization: dict[str, Any],
    *,
    city: str | None,
    check_in: date | None,
    check_out: date | None,
    guests: int,
    star_rating: int | None,
    weel_classification: str | None,
    is_recommended: bool | None,
    themes: list[str] | None,
    price_min: Decimal | None,
    price_max: Decimal | None,
    budget_max: Decimal | None,
    room_types: list[str] | None,
    room_type_presets: list[str] | None,
    rate_plans: list[str] | None,
    meal_plans: list[str] | None,
    min_capacity: int | None,
    max_capacity: int | None,
    allow_multi_room: bool = False,
) -> list[dict[str, Any]]:

    conditions = ["p.is_active = TRUE"]
    params: list[Any] = []

    if city:
        conditions.append("(p.city ILIKE %s OR p.full_address ILIKE %s)")
        params.extend([f"%{city}%", f"%{city}%"])
    if star_rating is not None:
        conditions.append("p.star_rating = %s")
        params.append(star_rating)
    if weel_classification:
        conditions.append("p.weel_classification = %s")
        params.append(weel_classification)
    if is_recommended:
        conditions.append("COALESCE(p.is_recommended, FALSE) = TRUE")
    if themes:
        conditions.append("p.themes && %s::text[]")
        params.append("{" + ",".join(themes) + "}")

    # Availability: guest-count (room capacity) is always enforced. When a
    # check_in/check_out range is given, rooms that overlap an existing
    # (non-cancelled/completed) booking are excluded, and hotels left with
    # zero matching rooms are dropped entirely — this is the "kalendar +
    # necha kishi" (calendar + guest count) → only-available-hotels filter.
    capacity_requirement = 1 if allow_multi_room else (min_capacity or guests)

    if check_in and check_out:
        avail_join = """
            LEFT JOIN LATERAL (
                SELECT
                    (
                        SELECT r_min.base_price
                        FROM pms_room r_min
                        WHERE r_min.property_id = p.id
                          AND r_min.is_active = TRUE
                          AND r_min.capacity >= %s
                          AND r_min.base_price IS NOT NULL
                          AND NOT EXISTS (
                              SELECT 1 FROM pms_booking b
                              WHERE b.room_id = r_min.id
                                AND b.status NOT IN ('cancelled', 'checked_out', 'no_show')
                                AND b.check_in < %s
                                AND b.check_out > %s
                          )
                          {room_filter_sql_for_min}
                        ORDER BY r_min.base_price ASC, r_min.id ASC
                        LIMIT 1
                    ) AS min_price,
                    (
                        SELECT r_min.currency
                        FROM pms_room r_min
                        WHERE r_min.property_id = p.id
                          AND r_min.is_active = TRUE
                          AND r_min.capacity >= %s
                          AND r_min.base_price IS NOT NULL
                          AND NOT EXISTS (
                              SELECT 1 FROM pms_booking b
                              WHERE b.room_id = r_min.id
                                AND b.status NOT IN ('cancelled', 'checked_out', 'no_show')
                                AND b.check_in < %s
                                AND b.check_out > %s
                          )
                          {room_filter_sql_for_min}
                        ORDER BY r_min.base_price ASC, r_min.id ASC
                        LIMIT 1
                    ) AS min_price_currency,
                    COUNT(*) AS available_rooms
                FROM pms_room r
                WHERE r.property_id = p.id
                  AND r.is_active = TRUE
                  AND r.capacity >= %s
                  AND NOT EXISTS (
                      SELECT 1 FROM pms_booking b
                      WHERE b.room_id = r.id
                        AND b.status NOT IN ('cancelled', 'checked_out', 'no_show')
                        AND b.check_in < %s
                        AND b.check_out > %s
                  )
                  {room_filter_sql}
            ) pricing ON TRUE
        """
        room_filter_sql = ""
        room_filter_params: list[Any] = []
        filter_clauses, filter_params = _room_filter_clause(
            room_types=room_types,
            room_type_presets=room_type_presets,
            rate_plans=rate_plans,
            meal_plans=meal_plans,
            min_capacity=capacity_requirement,
            max_capacity=max_capacity,
            min_price=None,
            max_price=None,
        )
        if filter_clauses:
            room_filter_sql = " AND " + " AND ".join(filter_clauses)
            room_filter_params = filter_params
        room_filter_sql_for_min = room_filter_sql.replace("r.", "r_min.")
        avail_params = [
            capacity_requirement, check_out, check_in, *room_filter_params,
            capacity_requirement, check_out, check_in, *room_filter_params,
            capacity_requirement, check_out, check_in, *room_filter_params,
        ]
        conditions.append("COALESCE(pricing.available_rooms, 0) > 0")
    else:
        avail_join = """
            LEFT JOIN LATERAL (
                SELECT
                    (
                        SELECT r_min.base_price
                        FROM pms_room r_min
                        WHERE r_min.property_id = p.id
                          AND r_min.is_active = TRUE
                          AND r_min.capacity >= %s
                          AND r_min.base_price IS NOT NULL
                          {room_filter_sql_for_min}
                        ORDER BY r_min.base_price ASC, r_min.id ASC
                        LIMIT 1
                    ) AS min_price,
                    (
                        SELECT r_min.currency
                        FROM pms_room r_min
                        WHERE r_min.property_id = p.id
                          AND r_min.is_active = TRUE
                          AND r_min.capacity >= %s
                          AND r_min.base_price IS NOT NULL
                          {room_filter_sql_for_min}
                        ORDER BY r_min.base_price ASC, r_min.id ASC
                        LIMIT 1
                    ) AS min_price_currency,
                    COUNT(*) AS available_rooms
                FROM pms_room r
                WHERE r.property_id = p.id AND r.is_active = TRUE AND r.capacity >= %s
                  {room_filter_sql}
            ) pricing ON TRUE
        """
        room_filter_sql = ""
        room_filter_params = []
        filter_clauses, filter_params = _room_filter_clause(
            room_types=room_types,
            room_type_presets=room_type_presets,
            rate_plans=rate_plans,
            meal_plans=meal_plans,
            min_capacity=capacity_requirement,
            max_capacity=max_capacity,
            min_price=None,
            max_price=None,
        )
        if filter_clauses:
            room_filter_sql = " AND " + " AND ".join(filter_clauses)
            room_filter_params = filter_params
        room_filter_sql_for_min = room_filter_sql.replace("r.", "r_min.")
        avail_params = [
            capacity_requirement, *room_filter_params,
            capacity_requirement, *room_filter_params,
            capacity_requirement, *room_filter_params,
        ]
        # Without a date range the lateral join still counts only rooms large
        # enough for the party, but nothing dropped the hotels left with zero
        # of them — so a guest-count filter on its own silently matched
        # everything. Only enforced once a capacity is actually requested, so
        # plain listings (guests=1) keep showing hotels that have no rooms
        # configured yet.
        if capacity_requirement > 1:
            conditions.append("COALESCE(pricing.available_rooms, 0) > 0")

    if price_min is not None:
        conditions.append("pricing.min_price >= %s")
        params.append(price_min)
    if price_max is not None:
        conditions.append("pricing.min_price <= %s")
        params.append(price_max)

    where = "WHERE " + " AND ".join(conditions)

    sql = f"""
        SELECT
            p.id,
            p.guid,
            p.organization_id,
            p.partner_user_id,
            p.name,
            p.description_uz,
            p.description_ru,
            p.description_en,
            p.address,
            p.full_address,
            p.city,
            p.country,
            p.latitude::text AS latitude,
            p.longitude::text AS longitude,
            p.star_rating,
            p.weel_classification,
            p.themes,
            COALESCE(p.amenities, ARRAY[]::text[]) AS amenities,
            COALESCE(p.legal_info, '{{}}'::jsonb) AS legal_info,
            p.check_in_time,
            p.check_out_time,
            p.cancellation_policy,
            COALESCE(p.quiet_hours, TRUE) AS quiet_hours,
            COALESCE(p.alcohol_allowed, FALSE) AS alcohol_allowed,
            COALESCE(p.pets_allowed, FALSE) AS pets_allowed,
            p.timezone,
            COALESCE(p.photos, ARRAY[]::text[]) AS photos,
            COALESCE(p.is_active, TRUE) AS is_active,
            COALESCE(p.is_testing, FALSE) AS is_testing,
            COALESCE(p.is_verified, FALSE) AS is_verified,
            COALESCE(p.is_archived, FALSE) AS is_archived,
            COALESCE(p.is_recommended, FALSE) AS is_recommended,
            COALESCE(p.verification_status, 'waiting') AS verification_status,
            p.created_at,
            p.updated_at,
            pricing.min_price,
            pricing.min_price_currency,
            COALESCE(pricing.available_rooms, 0) AS available_rooms,
            (
                SELECT COUNT(*) FROM pms_booking pb
                WHERE pb.property_id = p.id AND pb.status NOT IN ('cancelled')
            ) AS booking_count,
            (
                SELECT AVG(rv.rating) FROM pms_review rv
                WHERE rv.property_id = p.id AND rv.is_complained = FALSE
            ) AS rating,
            (
                SELECT COUNT(*) FROM pms_review rv
                WHERE rv.property_id = p.id AND rv.is_complained = FALSE
            ) AS review_count
        FROM pms_property p
        {avail_join.format(
            room_filter_sql=room_filter_sql,
            room_filter_sql_for_min=room_filter_sql_for_min,
        )}
        {where}
    """
    all_params = avail_params + params
    rows = fetch_all(sql, all_params)
    import json as _json

    for row in rows:
        row["organization_id"] = organization.get("id")
        row["organization_name"] = organization.get("name")
        row["organization_slug"] = organization.get("slug")
        row["tenant_schema"] = schema_name
        row["guid"] = str(row["guid"]) if row.get("guid") else None
        row["min_price"], row["currency"] = _to_uzs_amount(
            row.get("min_price"), row.get("min_price_currency")
        )
        raw_legal = row.get("legal_info")
        if isinstance(raw_legal, str):
            try:
                row["legal_info"] = _json.loads(raw_legal)
            except Exception:
                row["legal_info"] = {}
        elif not isinstance(raw_legal, dict):
            row["legal_info"] = {}

    if not allow_multi_room:
        return rows

    filtered_rows: list[dict[str, Any]] = []
    for row in rows:
        try:
            candidate_rooms = get_available_rooms(
                int(row["id"]),
                check_in=check_in or date.today(),
                check_out=check_out or (check_in or date.today()) + timedelta(days=30),
                guests=1,
                room_types=room_types,
                room_type_presets=room_type_presets,
                rate_plans=rate_plans,
                meal_plans=meal_plans,
                min_capacity=1,
                max_capacity=max_capacity,
                min_price=price_min,
                max_price=price_max,
            )
        except Exception:
            continue
        search_check_in = check_in or date.today()
        search_check_out = check_out or (search_check_in + timedelta(days=30))
        nights = max((search_check_out - search_check_in).days, 1)
        matching_rooms, total_price = _best_room_allocation(
            candidate_rooms,
            guests,
            nights=nights,
            budget_max=budget_max,
        )
        if not matching_rooms:
            continue
        row["matching_rooms"] = matching_rooms
        row["total_estimated_price"] = total_price
        row["available_rooms"] = len(candidate_rooms)
        filtered_rows.append(row)
    return filtered_rows


def _collect_hotel_rows(
    *,
    city: str | None = None,
    check_in: date | None = None,
    check_out: date | None = None,
    guests: int = 1,
    star_rating: int | None = None,
    weel_classification: str | None = None,
    is_recommended: bool | None = None,
    themes: list[str] | None = None,
    price_min: Decimal | None = None,
    price_max: Decimal | None = None,
    budget_max: Decimal | None = None,
    room_types: list[str] | None = None,
    room_type_presets: list[str] | None = None,
    rate_plans: list[str] | None = None,
    meal_plans: list[str] | None = None,
    min_capacity: int | None = None,
    max_capacity: int | None = None,
    lat: float | None = None,
    lon: float | None = None,
    radius_km: float = 10.0,
    allow_multi_room: bool = False,
) -> list[dict[str, Any]]:
    from apps.property.hotel_repository import _run_in_schema, list_hotel_organizations, _haversine_km

    all_rows: list[dict[str, Any]] = []
    for organization in list_hotel_organizations():
        schema_name = organization["schema_name"]
        try:
            rows = _run_in_schema(
                schema_name,
                lambda sn=schema_name, org=organization: _search_hotels_in_schema(
                    sn, org,
                    city=city, check_in=check_in, check_out=check_out, guests=guests,
                    star_rating=star_rating, weel_classification=weel_classification,
                    is_recommended=is_recommended, themes=themes,
                    price_min=price_min, price_max=price_max, budget_max=budget_max,
                    room_types=room_types, room_type_presets=room_type_presets,
                    rate_plans=rate_plans, meal_plans=meal_plans,
                    min_capacity=min_capacity, max_capacity=max_capacity,
                    allow_multi_room=allow_multi_room,
                ),
            )
        except Exception:
            continue
        all_rows.extend(rows)

    if lat is not None and lon is not None:
        def _within_radius(row: dict[str, Any]) -> bool:
            try:
                row_lat = float(row.get("latitude") or 0)
                row_lon = float(row.get("longitude") or 0)
            except (TypeError, ValueError):
                return False
            if row_lat == 0.0 and row_lon == 0.0:
                return False
            return _haversine_km(lat, lon, row_lat, row_lon) <= radius_km

        all_rows = [r for r in all_rows if _within_radius(r)]

    return all_rows


_SORT_KEYS: dict[str, Any] = {
    # "mashhur" — most-booked hotels first.
    "popular": lambda r: -(r.get("booking_count") or 0),
    "rating": lambda r: -(float(r["star_rating"]) if r.get("star_rating") is not None else 0),
    "reviews": lambda r: -(r.get("booking_count") or 0),
    # "eng arzon"
    "cheap": lambda r: r["min_price"] if r.get("min_price") is not None else Decimal("Infinity"),
    # "eng qimmat"
    "expensive": lambda r: -(r["min_price"] if r.get("min_price") is not None else Decimal("-Infinity")),
    # "weel-tavsiya" — recommended hotels first, then by rating.
    "weel_recommended": lambda r: (
        0 if r.get("is_recommended") else 1,
        -(float(r["star_rating"]) if r.get("star_rating") is not None else 0),
    ),
}


def search_hotels(
    *,
    city: str | None = None,
    check_in: date | None = None,
    check_out: date | None = None,
    guests: int = 1,
    star_rating: int | None = None,
    weel_classification: str | None = None,
    is_recommended: bool | None = None,
    themes: list[str] | None = None,
    price_min: Decimal | None = None,
    price_max: Decimal | None = None,
    budget_max: Decimal | None = None,
    room_types: list[str] | None = None,
    room_type_presets: list[str] | None = None,
    rate_plans: list[str] | None = None,
    meal_plans: list[str] | None = None,
    min_capacity: int | None = None,
    max_capacity: int | None = None,
    lat: float | None = None,
    lon: float | None = None,
    radius_km: float = 10.0,
    sort_by: str = "popular",
    limit: int = 20,
    offset: int = 0,
    allow_multi_room: bool = False,
) -> list[dict[str, Any]]:
    rows = _collect_hotel_rows(
        city=city, check_in=check_in, check_out=check_out, guests=guests,
        star_rating=star_rating, weel_classification=weel_classification,
        is_recommended=is_recommended, themes=themes,
        price_min=price_min, price_max=price_max, budget_max=budget_max,
        room_types=room_types, room_type_presets=room_type_presets,
        rate_plans=rate_plans, meal_plans=meal_plans,
        min_capacity=min_capacity, max_capacity=max_capacity,
        lat=lat, lon=lon, radius_km=radius_km,
        allow_multi_room=allow_multi_room,
    )
    rows.sort(key=_SORT_KEYS.get(sort_by, _SORT_KEYS["popular"]))
    return rows[offset : offset + limit]


def count_hotels(
    *,
    city: str | None = None,
    check_in: date | None = None,
    check_out: date | None = None,
    guests: int = 1,
    star_rating: int | None = None,
    weel_classification: str | None = None,
    is_recommended: bool | None = None,
    themes: list[str] | None = None,
    price_min: Decimal | None = None,
    price_max: Decimal | None = None,
    budget_max: Decimal | None = None,
    room_types: list[str] | None = None,
    room_type_presets: list[str] | None = None,
    rate_plans: list[str] | None = None,
    meal_plans: list[str] | None = None,
    min_capacity: int | None = None,
    max_capacity: int | None = None,
    lat: float | None = None,
    lon: float | None = None,
    radius_km: float = 10.0,
    allow_multi_room: bool = False,
) -> int:
    return len(_collect_hotel_rows(
        city=city, check_in=check_in, check_out=check_out, guests=guests,
        star_rating=star_rating, weel_classification=weel_classification,
        is_recommended=is_recommended, themes=themes,
        price_min=price_min, price_max=price_max, budget_max=budget_max,
        room_types=room_types, room_type_presets=room_type_presets,
        rate_plans=rate_plans, meal_plans=meal_plans,
        min_capacity=min_capacity, max_capacity=max_capacity,
        lat=lat, lon=lon, radius_km=radius_km,
        allow_multi_room=allow_multi_room,
    ))


def get_hotel_card(property_id: int) -> dict[str, Any] | None:
    return fetch_one(
        """
        SELECT
            p.id, p.name, p.city, p.full_address, p.star_rating,
            p.weel_classification, p.themes, p.description_uz AS description,
            p.amenities, p.legal_info,
            p.pets_allowed, p.alcohol_allowed, p.quiet_hours,
            p.check_in_time, p.check_out_time,
            p.latitude, p.longitude, COALESCE(p.photos, ARRAY[]::text[]) AS photos
        FROM pms_property p
        WHERE p.id = %s AND p.is_active = TRUE
        """,
        [property_id],
    )


def get_hotel_card_by_guid(hotel_guid: str) -> dict[str, Any] | None:
    """Fetch a single hotel's search-result card (same shape as
    ``search_hotels`` rows) by its cross-schema GUID. Used to reopen the
    booking flow for one specific, already-known hotel (e.g. from booking
    history / analytics) without a fuzzy city-text search."""
    from apps.property.hotel_repository import (
        _run_in_schema,
        list_hotel_organizations,
        resolve_hotel_guid,
    )

    resolved = resolve_hotel_guid(hotel_guid)
    if not resolved:
        return None
    schema_name, hotel_id = resolved

    organization = next(
        (
            org
            for org in list_hotel_organizations()
            if org["schema_name"] == schema_name
        ),
        None,
    )
    if not organization:
        return None

    try:
        rows = _run_in_schema(
            schema_name,
            lambda: _search_hotels_in_schema(
                schema_name, organization,
                city=None, check_in=None, check_out=None, guests=1,
                star_rating=None, weel_classification=None, is_recommended=None,
                themes=None, price_min=None, price_max=None, budget_max=None,
                room_types=None, room_type_presets=None, rate_plans=None, meal_plans=None,
                min_capacity=None, max_capacity=None,
            ),
        )
    except Exception:
        return None

    return next((row for row in rows if int(row.get("id") or 0) == hotel_id), None)


def get_hotel_reviews(
    property_id: int,
    *,
    limit: int = 10,
    offset: int = 0,
) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT id, guest_name, rating, text, hotel_response, created_at
        FROM pms_review
        WHERE property_id = %s AND is_complained = FALSE
        ORDER BY created_at DESC
        LIMIT %s OFFSET %s
        """,
        [property_id, limit, offset],
    )


def has_eligible_hotel_booking_for_review(
    *,
    client_user_id: int,
    property_id: int,
) -> bool:
    row = fetch_one(
        """
        SELECT 1 FROM pms_booking b
        WHERE b.property_id = %s
          AND b.created_by = %s
          AND b.status IN ('confirmed', 'checked_out')
        LIMIT 1
        """,
        [property_id, client_user_id],
    )
    return row is not None


def create_hotel_review(
    *,
    property_id: int,
    guest_name: str,
    rating: int,
    text: str = "",
) -> dict[str, Any] | None:
    row = fetch_one(
        """
        INSERT INTO pms_review (property_id, guest_name, rating, text, created_at)
        VALUES (%s, %s, %s, %s, NOW())
        RETURNING id, guest_name, rating, text, hotel_response, created_at
        """,
        [property_id, guest_name, rating, text],
    )
    return row


def get_hotel_calendar(
    property_id: int,
    *,
    from_date: date,
    to_date: date,
    room_types: list[str] | None = None,
    room_type_presets: list[str] | None = None,
    include_summary: bool = False,
) -> list[dict[str, Any]]:
    filter_clauses, filter_params = _room_filter_clause(
        room_types=room_types,
        room_type_presets=room_type_presets,
    )
    room_where = ""
    if filter_clauses:
        room_where = " AND " + " AND ".join(filter_clauses)
    rows = fetch_all(
        f"""
        SELECT r.id AS room_id, r.display_name AS room_name,
               r.room_type_id, r.room_type_name, r.room_type_preset,
               r.capacity, COALESCE(r.base_price, 0) AS price_per_night,
               d.date::date AS date,
               CASE
                   WHEN b.id IS NOT NULL THEN 'occupied'
                   WHEN cs.status IS NOT NULL THEN cs.status
                   WHEN r.availability = 'blocked' THEN 'blocked'
                   WHEN r.availability = 'occupied' THEN 'occupied'
                   WHEN r.condition IN ('maintenance', 'inspection') THEN 'blocked'
                   ELSE 'available'
               END AS status,
               CASE
                   WHEN b.id IS NOT NULL THEN 'booking'
                   WHEN cs.status IS NOT NULL THEN cs.status
                   WHEN r.availability = 'blocked' THEN 'room_blocked'
                   WHEN r.availability = 'occupied' THEN 'room_occupied'
                   WHEN r.condition IN ('maintenance', 'inspection') THEN r.condition
                   ELSE NULL
               END AS status_reason
        FROM pms_room r
        CROSS JOIN LATERAL (
            SELECT generate_series(%s::date, %s::date, '1 day'::interval)::date AS date
        ) d
        LEFT JOIN pms_calendar_slot cs
            ON cs.room_id = r.id
           AND cs.date = d.date
        LEFT JOIN pms_booking b
            ON b.room_id = r.id
            AND b.status NOT IN ('cancelled', 'checked_out', 'no_show')
            AND b.check_in < d.date + 1
            AND b.check_out > d.date
        WHERE r.property_id = %s AND r.is_active = TRUE
          {room_where}
        ORDER BY r.id, d.date
        """,
        [from_date, to_date, property_id, *filter_params],
    )
    if not include_summary:
        return rows

    summary: dict[str, dict[str, Any]] = {}
    for row in rows:
        dt = row["date"].isoformat() if hasattr(row["date"], "isoformat") else str(row["date"])
        bucket = summary.setdefault(
            dt,
            {"date": dt, "available": 0, "occupied": 0, "blocked": 0, "held": 0, "total_rooms": 0},
        )
        bucket["total_rooms"] += 1
        status = row.get("status")
        if status in bucket:
            bucket[status] += 1
        elif status == "blocked":
            bucket["blocked"] += 1
        elif status == "occupied":
            bucket["occupied"] += 1
        elif status == "held":
            bucket["held"] += 1
        else:
            bucket["available"] += 1
    return {"rows": rows, "summary": list(summary.values())}
