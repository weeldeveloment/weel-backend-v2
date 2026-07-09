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
        """,
        [room_id, check_out, check_in, check_in, check_out],
    )
    return row is not None


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
) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT
            r.id, r.room_number, r.floor, r.display_name,
            r.bedroom_count, r.beds, r.amenities,
            r.capacity,
            r.room_type_name, r.room_type_preset AS preset,
            r.meal_plan,
            COALESCE(r.photos, '{}'::text[]) AS photos
        FROM pms_room r
        WHERE r.property_id = %s
          AND r.is_active = TRUE
          AND r.capacity >= %s
          AND NOT EXISTS (
              SELECT 1 FROM pms_booking b
              WHERE b.room_id = r.id
                AND b.status NOT IN ('cancelled', 'checked_out', 'no_show')
                AND b.check_in < %s
                AND b.check_out > %s
          )
        ORDER BY r.room_number ASC
        """,
        [property_id, guests, check_out, check_in],
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
        "SELECT id FROM pms_room WHERE id = %s",
        [room_id],
    )
    if not room:
        return None

    raw_rate = None
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
    b2b_company_id: int | None = None,
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
            pricing["price_per_night"], total_price, hold_amount,
            b2b_company_id,
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
            r.room_type_name, r.room_type_preset,
            0 AS room_price_per_night
        FROM pms_booking b
        JOIN pms_property p ON p.id = b.property_id
        JOIN pms_room r ON r.id = b.room_id
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
            0 AS room_price_per_night,
            r.bedroom_count, r.beds, r.amenities,
            r.capacity,
            r.room_type_name, r.room_type_preset,
            r.meal_plan,
            COALESCE(r.photos, '{}'::text[]) AS photos
        FROM pms_booking b
        JOIN pms_property p ON p.id = b.property_id
        JOIN pms_room r ON r.id = b.room_id
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
#
# Hotels live one-per-organization, each in its own Postgres schema (see
# apps/property/hotel_repository.py). Search/list must loop every hotel
# organization's schema (via ``_run_in_schema``) and merge the results —
# there is no single global ``pms_property`` table to query directly.
# Each result is tagged with a ``hotel_guid`` (``"<schema>:<id>"``, see
# ``encode_hotel_guid``) since raw numeric ids are only unique per-schema.

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
) -> list[dict[str, Any]]:
    from apps.property.hotel_repository import encode_hotel_guid

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
    if check_in and check_out:
        avail_join = """
            LEFT JOIN LATERAL (
                SELECT 0 AS min_price, COUNT(*) AS available_rooms
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
            ) pricing ON TRUE
        """
        avail_params = [guests, check_out, check_in]
        conditions.append("COALESCE(pricing.available_rooms, 0) > 0")
    else:
        avail_join = """
            LEFT JOIN LATERAL (
                SELECT 0 AS min_price, COUNT(*) AS available_rooms
                FROM pms_room r
                WHERE r.property_id = p.id AND r.is_active = TRUE AND r.capacity >= %s
            ) pricing ON TRUE
        """
        avail_params = [guests]

    if price_min is not None:
        conditions.append("pricing.min_price >= %s")
        params.append(price_min)
    if price_max is not None:
        conditions.append("pricing.min_price <= %s")
        params.append(price_max)

    where = "WHERE " + " AND ".join(conditions)

    sql = f"""
        SELECT
            p.id, p.name, p.city, p.full_address, p.star_rating,
            p.weel_classification, p.themes, p.description_uz AS description,
            p.photos, p.check_in_time, p.check_out_time,
            p.latitude, p.longitude,
            COALESCE(p.is_recommended, FALSE) AS is_recommended,
            pricing.min_price,
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
        {avail_join}
        {where}
    """
    all_params = avail_params + params
    rows = fetch_all(sql, all_params)
    for row in rows:
        row["organization_id"] = organization.get("id")
        row["organization_name"] = organization.get("name")
        row["tenant_schema"] = schema_name
        row["hotel_guid"] = encode_hotel_guid(schema_name, row["id"])
    return rows


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
    lat: float | None = None,
    lon: float | None = None,
    radius_km: float = 10.0,
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
                    price_min=price_min, price_max=price_max,
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
    lat: float | None = None,
    lon: float | None = None,
    radius_km: float = 10.0,
    sort_by: str = "popular",
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    rows = _collect_hotel_rows(
        city=city, check_in=check_in, check_out=check_out, guests=guests,
        star_rating=star_rating, weel_classification=weel_classification,
        is_recommended=is_recommended, themes=themes,
        price_min=price_min, price_max=price_max,
        lat=lat, lon=lon, radius_km=radius_km,
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
    lat: float | None = None,
    lon: float | None = None,
    radius_km: float = 10.0,
) -> int:
    return len(_collect_hotel_rows(
        city=city, check_in=check_in, check_out=check_out, guests=guests,
        star_rating=star_rating, weel_classification=weel_classification,
        is_recommended=is_recommended, themes=themes,
        price_min=price_min, price_max=price_max,
        lat=lat, lon=lon, radius_km=radius_km,
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
            AND b.status NOT IN ('cancelled', 'checked_out', 'no_show')
            AND b.check_in < d.date + 1
            AND b.check_out > d.date
        WHERE r.property_id = %s AND r.is_active = TRUE
        ORDER BY r.id, d.date
        """,
        [from_date, to_date, property_id],
    )
