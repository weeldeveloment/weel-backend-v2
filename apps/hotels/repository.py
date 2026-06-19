from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from shared.raw.db import fetch_all, fetch_one


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
        "popular":  "p.review_count DESC NULLS LAST",
        "rating":   "p.rating DESC NULLS LAST",
        "reviews":  "p.review_count DESC NULLS LAST",
        "cheap":    "min_price ASC NULLS LAST",
        "expensive":"min_price DESC NULLS LAST",
    }
    order_clause = sort_map.get(sort_by, "p.review_count DESC NULLS LAST")

    # Subquery for minimum room price for the requested dates
    if check_in and check_out:
        price_join = """
            LEFT JOIN LATERAL (
                SELECT MIN(r.price_per_night) AS min_price
                FROM pms_room r
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
                SELECT MIN(r.price_per_night) AS min_price
                FROM pms_room r
                WHERE r.property_id = p.id AND r.is_active = TRUE
            ) pricing ON TRUE
        """
        params_price = []

    sql = f"""
        SELECT
            p.id, p.name, p.city, p.full_address, p.star_rating,
            p.weel_classification, p.themes, p.description,
            p.rating, p.review_count, p.check_in_time, p.check_out_time,
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
            p.weel_classification, p.themes, p.description,
            p.rating, p.review_count, p.check_in_time, p.check_out_time,
            p.latitude, p.longitude, p.amenities, p.legal_info,
            p.wifi, p.parking, p.pool, p.restaurant, p.gym,
            p.pets_allowed, p.alcohol_allowed, p.quiet_hours
        FROM pms_property p
        WHERE p.id = %s AND p.is_active = TRUE
        """,
        [property_id],
    )


def get_hotel_images(property_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        "SELECT id, image_url, display_order FROM pms_property_image WHERE property_id = %s ORDER BY display_order",
        [property_id],
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
            r.bedroom_count, r.price_per_night, r.beds, r.amenities,
            r.capacity_adults, r.capacity_children,
            rt.name AS room_type_name, rt.preset, rt.area_sqm,
            rt.meal_plan,
            (
                SELECT json_agg(json_build_object('url', ri.image_url, 'order', ri.display_order))
                FROM pms_room_image ri WHERE ri.room_id = r.id
            ) AS images
        FROM pms_room r
        JOIN pms_room_type rt ON rt.id = r.room_type_id
        WHERE r.property_id = %s
          AND r.is_active = TRUE
          AND r.capacity_adults >= %s
          AND NOT EXISTS (
              SELECT 1 FROM pms_booking b
              WHERE b.room_id = r.id
                AND b.status NOT IN ('cancelled', 'completed')
                AND b.check_in < %s
                AND b.check_out > %s
          )
        ORDER BY r.price_per_night ASC
        """,
        [property_id, guests, check_out, check_in],
    )


def calculate_stay_price(
    room_id: int,
    check_in: date,
    check_out: date,
) -> dict[str, Any] | None:
    nights = (check_out - check_in).days
    if nights <= 0:
        return None

    room = fetch_one(
        "SELECT price_per_night FROM pms_room WHERE id = %s",
        [room_id],
    )
    if not room:
        return None

    base_price = Decimal(str(room["price_per_night"])) * nights
    hold_amount = (base_price * Decimal("0.30")).quantize(Decimal("0.01"))
    return {
        "nights": nights,
        "price_per_night": room["price_per_night"],
        "total_price": base_price,
        "hold_amount": hold_amount,
        "remaining_on_arrival": base_price - hold_amount,
    }


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
