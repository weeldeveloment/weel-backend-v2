from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from shared.raw.db import execute, fetch_all, fetch_one


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
                AND b.status NOT IN ('cancelled', 'completed')
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
        """,
        [room_id, check_out, check_in, check_in, check_out],
    )
    return row is not None


def get_available_rooms(
    property_id: int,
    *,
    check_in: date,
    check_out: date,
    guests: int = 1,
) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT
            r.id, r.room_number, r.floor, r.display_name,
            r.bedroom_count, rt.base_rate AS price_per_night, r.beds, r.amenities,
            r.capacity,
            rt.name AS room_type_name, rt.preset,
            r.meal_plan,
            COALESCE(
                (SELECT json_agg(json_build_object('url', pi.image_url, 'order', pi.order))
                 FROM pms_room_image pi WHERE pi.room_id = r.id),
                '[]'::json
            ) AS images
        FROM pms_room r
        JOIN pms_room_type rt ON rt.id = r.room_type_id
        WHERE r.property_id = %s
          AND r.is_active = TRUE
          AND r.capacity >= %s
          AND NOT EXISTS (
              SELECT 1 FROM pms_booking b
              WHERE b.room_id = r.id
                AND b.status NOT IN ('cancelled', 'completed')
                AND b.check_in < %s
                AND b.check_out > %s
          )
        ORDER BY COALESCE(rt.base_rate, 0) ASC
        """,
        [property_id, guests, check_out, check_in],
    )


def get_room_with_details(room_id: int) -> dict[str, Any] | None:
    return fetch_one(
        """
        SELECT r.*, rt.name AS room_type_name, rt.preset, rt.base_rate,
               rt.capacity AS room_type_capacity, rt.base_rate AS price_per_night
        FROM pms_room r
        JOIN pms_room_type rt ON rt.id = r.room_type_id
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
        """
        SELECT rt.base_rate AS price_per_night
        FROM pms_room r
        JOIN pms_room_type rt ON rt.id = r.room_type_id
        WHERE r.id = %s
        """,
        [room_id],
    )
    if not room:
        return None

    raw_rate = room.get("price_per_night")
    if raw_rate is None:
        return None

    ppn = Decimal(str(raw_rate))
    base_price = ppn * nights
    hold_amount = (base_price * Decimal("0.30")).quantize(Decimal("0.01"))
    return {
        "nights": nights,
        "price_per_night": ppn,
        "total_price": base_price,
        "hold_amount": hold_amount,
        "remaining_on_arrival": base_price - hold_amount,
    }


# ─── Booking CRUD ─────────────────────────────────────────────────────────────

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
) -> dict[str, Any] | None:
    nights = (check_out - check_in).days
    if nights <= 0:
        return None

    if not _check_room_availability(room_id, check_in, check_out):
        return None

    pricing = calculate_stay_price(room_id, check_in, check_out)
    if not pricing:
        # fallback: use 100000 UZS/night default
        ppn = Decimal("100000")
        base_price = ppn * nights
        hold_amount = (base_price * Decimal("0.30")).quantize(Decimal("0.01"))
        pricing = {
            "nights": nights,
            "price_per_night": ppn,
            "total_price": base_price,
            "hold_amount": hold_amount,
            "remaining_on_arrival": base_price - hold_amount,
        }

    total_price = pricing["total_price"]
    hold_amount = pricing["hold_amount"]

    row = fetch_one(
        """
        INSERT INTO pms_booking (
            property_id, room_id, created_by,
            check_in, check_out, adult_count, child_count,
            rate, currency, total_cost, hold_amount,
            status, created_at, booking_number
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s,
            %s, 'UZS', %s, %s,
            'pending', NOW(),
            'H' || TO_CHAR(NOW(), 'YYMMDD') || LPAD(FLOOR(RANDOM() * 99999)::int::text, 5, '0')
        )
        RETURNING id, property_id, room_id, check_in, check_out, adult_count, child_count,
                  total_cost, hold_amount, status, booking_number, created_at, created_by
        """,
        [
            property_id, room_id, client_user_id,
            check_in, check_out, adults, children,
            pricing["price_per_night"], total_price, hold_amount,
        ],
    )
    return row


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
            rt.name AS room_type_name, rt.preset AS room_type_preset,
            rt.base_rate AS room_price_per_night
        FROM pms_booking b
        JOIN pms_property p ON p.id = b.property_id
        JOIN pms_room r ON r.id = b.room_id
        JOIN pms_room_type rt ON rt.id = r.room_type_id
        WHERE {where}
        ORDER BY b.created_at DESC, b.id DESC
        """,
        params,
    )


def get_client_hotel_booking(
    booking_id: int,
    client_user_id: int,
) -> dict[str, Any] | None:
    return fetch_one(
        """
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
            rt.base_rate AS room_price_per_night,
            r.bedroom_count, r.beds, r.amenities,
            r.capacity,
            rt.name AS room_type_name, rt.preset AS room_type_preset,
            r.meal_plan,
            COALESCE(
                (SELECT json_agg(json_build_object('url', pi.image_url, 'order', pi.order))
                 FROM pms_room_image pi WHERE pi.room_id = r.id),
                '[]'::json
            ) AS room_images
        FROM pms_booking b
        JOIN pms_property p ON p.id = b.property_id
        JOIN pms_room r ON r.id = b.room_id
        JOIN pms_room_type rt ON rt.id = r.room_type_id
        WHERE b.id = %s AND b.created_by = %s
        """,
        [booking_id, client_user_id],
    )


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

def search_hotels(
    *,
    city: str | None = None,
    check_in: date | None = None,
    check_out: date | None = None,
    guests: int = 1,
    star_rating: int | None = None,
    weel_classification: str | None = None,
    themes: list[str] | None = None,
    price_min: Decimal | None = None,
    price_max: Decimal | None = None,
    sort_by: str = "popular",
    limit: int = 20,
    offset: int = 0,
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
    if themes:
        conditions.append("p.themes && %s::text[]")
        params.append("{" + ",".join(themes) + "}")

    where = "WHERE " + " AND ".join(conditions)

    sort_map = {
        "popular":     "p.star_rating DESC NULLS LAST",
        "rating":      "p.star_rating DESC NULLS LAST",
        "reviews":     "p.star_rating DESC NULLS LAST",
        "cheap":       "min_price ASC NULLS LAST",
        "expensive":   "min_price DESC NULLS LAST",
    }
    order_clause = sort_map.get(sort_by, "p.star_rating DESC NULLS LAST")

    if check_in and check_out:
        price_join = """
            LEFT JOIN LATERAL (
                SELECT MIN(rt.base_rate) AS min_price
                FROM pms_room r
                JOIN pms_room_type rt ON rt.id = r.room_type_id
                WHERE r.property_id = p.id
                  AND r.is_active = TRUE
                  AND NOT EXISTS (
                      SELECT 1 FROM pms_booking b
                      WHERE b.room_id = r.id
                        AND b.status NOT IN ('cancelled', 'completed')
                        AND b.check_in < %s
                        AND b.check_out > %s
                  )
            ) pricing ON TRUE
        """
        params_price = [check_out, check_in]
        if price_min is not None:
            conditions.append("pricing.min_price >= %s")
            params.append(price_min)
        if price_max is not None:
            conditions.append("pricing.min_price <= %s")
            params.append(price_max)
    else:
        price_join = """
            LEFT JOIN LATERAL (
                SELECT MIN(rt.base_rate) AS min_price
                FROM pms_room r
                JOIN pms_room_type rt ON rt.id = r.room_type_id
                WHERE r.property_id = p.id AND r.is_active = TRUE
            ) pricing ON TRUE
        """
        params_price = []

    sql = f"""
        SELECT
            p.id, p.name, p.city, p.full_address, p.star_rating,
            p.weel_classification, p.themes, p.description_uz,
            p.photos,
            p.check_in_time, p.check_out_time,
            p.latitude, p.longitude,
            pricing.min_price
        FROM pms_property p
        {price_join}
        {where}
        ORDER BY {order_clause}
        LIMIT %s OFFSET %s
    """
    all_params = params_price + params + [limit, offset]
    return fetch_all(sql, all_params)


def count_hotels(
    *,
    city: str | None = None,
    star_rating: int | None = None,
    weel_classification: str | None = None,
    themes: list[str] | None = None,
) -> int:
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
    if themes:
        conditions.append("p.themes && %s::text[]")
        params.append("{" + ",".join(themes) + "}")

    where = "WHERE " + " AND ".join(conditions)
    row = fetch_one(f"SELECT COUNT(*) AS cnt FROM pms_property p {where}", params)
    return int(row["cnt"]) if row else 0


def get_hotel_card(property_id: int) -> dict[str, Any] | None:
    return fetch_one(
        """
        SELECT
            p.id, p.name, p.city, p.full_address, p.star_rating,
            p.weel_classification, p.themes, p.description_uz,
            p.amenities, p.legal_info,
            p.pets_allowed, p.alcohol_allowed, p.quiet_hours,
            p.check_in_time, p.check_out_time,
            p.latitude, p.longitude, COALESCE(p.photos, ARRAY[]::text[]) AS photos
        FROM pms_property p
        WHERE p.id = %s AND p.is_active = TRUE
        """,
        [property_id],
    )


def get_hotel_reviews(
    property_id: int,
    *,
    limit: int = 10,
    offset: int = 0,
) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT id, guest_name, rating, text, response, created_at
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
          AND b.status IN ('confirmed', 'completed')
          AND b.completed_at IS NOT NULL
        LIMIT 1
        """,
        [property_id, client_user_id],
    )
    return row is not None


def create_hotel_review(
    *,
    property_id: int,
    client_user_id: int,
    guest_name: str,
    rating: int,
    text: str = "",
) -> dict[str, Any] | None:
    row = fetch_one(
        """
        INSERT INTO pms_review (property_id, client_user_id, guest_name, rating, text, created_at)
        VALUES (%s, %s, %s, %s, %s, NOW())
        RETURNING id, guest_name, rating, text, response, created_at
        """,
        [property_id, client_user_id, guest_name, rating, text],
    )
    return row


def get_hotel_calendar(
    property_id: int,
    *,
    from_date: date,
    to_date: date,
) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT r.id AS room_id, r.display_name AS room_name,
               d.date::date AS date,
               CASE WHEN b.id IS NOT NULL THEN 'booked' ELSE 'available' END AS status
        FROM pms_room r
        CROSS JOIN LATERAL (
            SELECT generate_series(%s::date, %s::date, '1 day'::interval)::date AS date
        ) d
        LEFT JOIN pms_booking b
            ON b.room_id = r.id
            AND b.status NOT IN ('cancelled', 'completed')
            AND b.check_in < d.date + 1
            AND b.check_out > d.date
        WHERE r.property_id = %s AND r.is_active = TRUE
        ORDER BY r.id, d.date
        """,
        [from_date, to_date, property_id],
    )
