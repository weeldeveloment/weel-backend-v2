from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any
from uuid import UUID, uuid4

from django.utils import timezone

from payment.exchange_rate import exchange_rate
from shared.raw.db import execute, fetch_all, fetch_one
from shared.raw.compat import get_table_name, is_postgresql, return_star


APARTMENT_TYPE_GUID = UUID("11111111-1111-1111-1111-111111111111")
COTTAGE_TYPE_GUID = UUID("22222222-2222-2222-2222-222222222222")

PROPERTY_KIND_APARTMENT = "apartment"
PROPERTY_KIND_COTTAGE = "cottage"
PROPERTY_KINDS = {PROPERTY_KIND_APARTMENT, PROPERTY_KIND_COTTAGE}

KIND_TO_TABLE = {
    PROPERTY_KIND_APARTMENT: "apartment",
    PROPERTY_KIND_COTTAGE: "cottage",
}
KIND_TO_TYPE_GUID = {
    PROPERTY_KIND_APARTMENT: APARTMENT_TYPE_GUID,
    PROPERTY_KIND_COTTAGE: COTTAGE_TYPE_GUID,
}
KIND_TO_TYPE_TITLE = {
    PROPERTY_KIND_APARTMENT: "Apartment",
    PROPERTY_KIND_COTTAGE: "Cottages",
}

TYPE_GUID_TO_KIND = {
    str(APARTMENT_TYPE_GUID): PROPERTY_KIND_APARTMENT,
    str(COTTAGE_TYPE_GUID): PROPERTY_KIND_COTTAGE,
}

PROPERTY_UNION_SELECT = f"""
    SELECT *
    FROM (
        SELECT
            'apartment' AS property_kind,
            a.id,
            a.legacy_property_id,
            a.guid,
            a.created_at,
            a.updated_at,
            a.title,
            a.title_sort,
            a.is_verified,
            a.verified_at,
            a.verification_status,
            a.is_archived,
            a.is_recommended,
            a.minimum_weekend_day_stay,
            a.weekend_only_sunday_inclusive,
            a.comment_count,
            a.price,
            NULL AS price_per_person,
            NULL AS price_on_working_days,
            NULL AS price_on_weekends,
            a.currency,
            a.img,
            a.partner_user_id,
            a.verified_by_user_id,
            a.latitude,
            a.longitude,
            a.city,
            a.country,
            a.services,
            NULL AS region_id,
            NULL AS district_id,
            NULL AS shaharcha_id,
            NULL AS mahalla_id,
            a.description_en,
            a.description_ru,
            a.description_uz,
            a.check_in,
            a.check_out,
            a.is_allowed_alcohol,
            a.is_allowed_corporate,
            a.is_allowed_pets,
            a.is_quiet_hours,
            NULL AS apartment_number,
            NULL AS home_number,
            NULL AS entrance_number,
            NULL AS floor_number,
            NULL AS pass_code,
            u.username AS partner_username,
            u.first_name AS partner_first_name,
            u.last_name AS partner_last_name,
            u.phone_number AS partner_phone_number,
            COALESCE(stats.average_rating, 5.0) AS average_rating,
            COALESCE(stats.review_count, 0) AS review_count
        FROM {get_table_name("apartment")} a
        LEFT JOIN {get_table_name("users")} u ON u.id = a.partner_user_id
        LEFT JOIN LATERAL (
            SELECT
                ROUND(COALESCE(AVG(r.rating), 5.0), 2) AS average_rating,
                COUNT(*) AS review_count
            FROM {get_table_name("review")} r
            WHERE r.apartment_id = a.id
              AND (COALESCE(r.is_hidden, FALSE) = FALSE)
              AND r.rating IS NOT NULL
        ) stats ON TRUE

        UNION ALL

        SELECT
            'cottage' AS property_kind,
            c.id,
            c.legacy_property_id,
            c.guid,
            c.created_at,
            c.updated_at,
            c.title,
            c.title_sort,
            c.is_verified,
            c.verified_at,
            c.verification_status,
            c.is_archived,
            c.is_recommended,
            c.minimum_weekend_day_stay,
            c.weekend_only_sunday_inclusive,
            c.comment_count,
            NULL AS price,
            c.price_per_person,
            c.price_on_working_days,
            c.price_on_weekends,
            c.currency,
            c.img,
            c.partner_user_id,
            c.verified_by_user_id,
            c.latitude,
            c.longitude,
            c.city,
            c.country,
            c.services,
            NULL AS region_id,
            NULL AS district_id,
            NULL AS shaharcha_id,
            NULL AS mahalla_id,
            c.description_en,
            c.description_ru,
            c.description_uz,
            c.check_in,
            c.check_out,
            c.is_allowed_alcohol,
            c.is_allowed_corporate,
            c.is_allowed_pets,
            c.is_quiet_hours,
            NULL AS apartment_number,
            NULL AS home_number,
            NULL AS entrance_number,
            NULL AS floor_number,
            NULL AS pass_code,
            u.username AS partner_username,
            u.first_name AS partner_first_name,
            u.last_name AS partner_last_name,
            u.phone_number AS partner_phone_number,
            COALESCE(stats.average_rating, 5.0) AS average_rating,
            COALESCE(stats.review_count, 0) AS review_count
        FROM {get_table_name("cottage")} c
        LEFT JOIN {get_table_name("users")} u ON u.id = c.partner_user_id
        LEFT JOIN LATERAL (
            SELECT
                ROUND(COALESCE(AVG(r.rating), 5.0), 2) AS average_rating,
                COUNT(*) AS review_count
            FROM {get_table_name("review")} r
            WHERE r.cottage_id = c.id
              AND (COALESCE(r.is_hidden, FALSE) = FALSE)
              AND r.rating IS NOT NULL
        ) stats ON TRUE
    ) p
"""


def parse_property_kind(value: str | UUID | None) -> str | None:
    if value is None:
        return None
    raw = str(value).strip().lower()
    if not raw:
        return None
    if raw in {"apartment", "apartments"}:
        return PROPERTY_KIND_APARTMENT
    if raw in {"cottage", "cottages"}:
        return PROPERTY_KIND_COTTAGE
    return TYPE_GUID_TO_KIND.get(raw)


def list_property_types() -> list[dict[str, Any]]:
    return [
        {
            "guid": APARTMENT_TYPE_GUID,
            "title": "Apartment",
            "icon_url": None,
        },
        {
            "guid": COTTAGE_TYPE_GUID,
            "title": "Cottages",
            "icon_url": None,
        },
    ]


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _exchange_rate_safe() -> Decimal:
    try:
        return Decimal(str(exchange_rate()))
    except Exception:
        return Decimal("1")


def _effective_price(row: dict[str, Any], reference_date: date) -> Decimal:
    if str(row.get("property_kind") or "") == PROPERTY_KIND_COTTAGE:
        field = "price_on_weekends" if reference_date.weekday() >= 4 else "price_on_working_days"
        value = _to_decimal(row.get(field))
        if value is not None:
            return value
    fallback = _to_decimal(row.get("price"))
    if fallback is not None:
        return fallback
    return Decimal("0")


def _convert_amount(
    amount: Decimal,
    from_currency: str,
    to_currency: str,
    rate: Decimal,
) -> Decimal:
    source = (from_currency or "UZS").upper()
    target = (to_currency or "UZS").upper()
    if source == target:
        return amount
    if source == "USD" and target == "UZS":
        return amount * rate
    if source == "UZS" and target == "USD":
        if rate == 0:
            return amount
        return (amount / rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return amount


def _sort_rows(
    rows: list[dict[str, Any]],
    *,
    ordering: str | None = None,
    sort: str | None = None,
) -> list[dict[str, Any]]:
    order_value = (ordering or "").strip()
    sort_value = (sort or "").strip().lower()

    mapped = ""
    if sort_value == "price_high":
        mapped = "-order_price_uzs"
    elif sort_value == "price_low":
        mapped = "order_price_uzs"
    elif sort_value == "rating_high":
        mapped = "-average_rating"
    elif sort_value == "rating_low":
        mapped = "average_rating"
    elif sort_value == "reviews_high":
        mapped = "-review_count"
    elif sort_value == "reviews_low":
        mapped = "review_count"
    elif sort_value == "title_asc":
        mapped = "title"
    elif sort_value == "title_desc":
        mapped = "-title"

    key_spec = order_value or mapped or "-created_at"
    descending = key_spec.startswith("-")
    key_name = key_spec[1:] if key_spec.startswith("-") else key_spec
    if key_name not in {"title", "order_price_uzs", "review_count", "average_rating", "created_at"}:
        key_name = "created_at"
        descending = True

    if key_name == "title":
        return sorted(rows, key=lambda row: (str(row.get("title") or "").lower(), row.get("id") or 0), reverse=descending)
    if key_name in {"order_price_uzs", "average_rating"}:
        return sorted(
            rows,
            key=lambda row: (_to_decimal(row.get(key_name)) or Decimal("0"), row.get("id") or 0),
            reverse=descending,
        )
    if key_name == "review_count":
        return sorted(rows, key=lambda row: (int(row.get("review_count") or 0), row.get("id") or 0), reverse=descending)
    return sorted(rows, key=lambda row: (row.get("created_at"), row.get("id") or 0), reverse=descending)


def _apply_price_filters(
    rows: list[dict[str, Any]],
    *,
    min_price: Decimal | None,
    max_price: Decimal | None,
    currency: str | None,
    rate: Decimal,
) -> list[dict[str, Any]]:
    target_currency = (currency or "").upper().strip() if currency else None
    if target_currency not in {None, "", "USD", "UZS"}:
        target_currency = None

    filtered: list[dict[str, Any]] = []
    for row in rows:
        value = _to_decimal(row.get("effective_price")) or Decimal("0")
        row_currency = str(row.get("currency") or "UZS").upper()
        if target_currency:
            value = _convert_amount(value, row_currency, target_currency, rate)
        if min_price is not None and value < min_price:
            continue
        if max_price is not None and value > max_price:
            continue
        filtered.append(row)
    return filtered


def prepare_property_rows(
    rows: list[dict[str, Any]],
    *,
    reference_date: date,
    min_price: Decimal | None = None,
    max_price: Decimal | None = None,
    currency: str | None = None,
    sort: str | None = None,
    ordering: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    rate = _exchange_rate_safe()
    prepared: list[dict[str, Any]] = []
    for raw_row in rows:
        row = dict(raw_row)
        effective = _effective_price(row, reference_date)
        row["effective_price"] = effective
        row["order_price_uzs"] = _convert_amount(
            effective,
            str(row.get("currency") or "UZS"),
            "UZS",
            rate,
        )
        prepared.append(row)

    prepared = _apply_price_filters(
        prepared,
        min_price=min_price,
        max_price=max_price,
        currency=currency,
        rate=rate,
    )
    prepared = _sort_rows(prepared, ordering=ordering, sort=sort)
    if limit is not None and limit >= 0:
        prepared = prepared[:limit]
    return prepared


def list_properties(
    *,
    public_only: bool = True,
    partner_user_id: int | None = None,
    property_kind: str | None = None,
    recommended_only: bool = False,
    search: str | None = None,
    region_id: int | None = None,
    district_id: int | None = None,
    corporate: bool | None = None,
) -> list[dict[str, Any]]:
    where = ["1 = 1"]
    params: list[Any] = []

    if public_only:
        where.append("COALESCE(p.is_verified, FALSE) = TRUE")
    if public_only or partner_user_id is None:
        where.append("COALESCE(p.is_archived, FALSE) = FALSE")
    if partner_user_id is not None:
        where.append("p.partner_user_id = %s")
        params.append(partner_user_id)
    if property_kind in PROPERTY_KINDS:
        where.append("p.property_kind = %s")
        params.append(property_kind)
    if recommended_only:
        where.append("COALESCE(p.is_recommended, FALSE) = TRUE")
    if search:
        where.append("COALESCE(p.title, '') LIKE %s")
        params.append(f"%{search.strip()}%")
    if region_id is not None:
        where.append("p.region_id = %s")
        params.append(region_id)
    if district_id is not None:
        where.append("p.district_id = %s")
        params.append(district_id)
    if corporate is not None:
        where.append("COALESCE(p.is_allowed_corporate, FALSE) = %s")
        params.append(bool(corporate))

    return fetch_all(
        f"""
        {PROPERTY_UNION_SELECT}
        WHERE {' AND '.join(where)}
        ORDER BY p.created_at DESC, p.id DESC
        """,
        params,
    )


def get_property_for_public(property_guid: str) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        {PROPERTY_UNION_SELECT}
        WHERE p.guid = %s
          AND COALESCE(p.is_verified, FALSE) = TRUE
          AND COALESCE(p.is_archived, FALSE) = FALSE
        LIMIT 1
        """,
        [property_guid],
    )


def get_property_for_partner(property_guid: str, partner_user_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        {PROPERTY_UNION_SELECT}
        WHERE p.guid = %s
          AND p.partner_user_id = %s
        LIMIT 1
        """,
        [property_guid, partner_user_id],
    )


def _table_for_kind(property_kind: str) -> str:
    table = KIND_TO_TABLE.get(property_kind)
    if not table:
        raise ValueError("Invalid property kind")
    return get_table_name(table)


def create_property(
    *,
    property_kind: str,
    partner_user_id: int,
    values: dict[str, Any],
) -> dict[str, Any] | None:
    table = _table_for_kind(property_kind)
    now = timezone.now()

    if property_kind == PROPERTY_KIND_APARTMENT:
        row = fetch_one(
            f"""
            INSERT INTO {table} (
                guid,
                created_at,
                updated_at,
                title,
                title_sort,
                is_verified,
                verification_status,
                is_archived,
                is_recommended,
                minimum_weekend_day_stay,
                weekend_only_sunday_inclusive,
                comment_count,
                price,
                currency,
                img,
                partner_user_id,
                latitude,
                longitude,
                city,
                country,
                region_id,
                district_id,
                description_en,
                description_ru,
                description_uz,
                check_in,
                check_out,
                is_allowed_alcohol,
                is_allowed_corporate,
                is_allowed_pets,
                is_quiet_hours,
                apartment_number,
                home_number,
                entrance_number,
                floor_number,
                pass_code
            ) VALUES (
                %s, %s, %s, %s, %s,
                FALSE, 'pending', FALSE, FALSE,
                %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s
            )
            RETURNING guid
            """,
            [
                uuid4(),
                now,
                now,
                values["title"],
                values["title_sort"],
                bool(values.get("minimum_weekend_day_stay", False)),
                bool(values.get("weekend_only_sunday_inclusive", False)),
                int(values.get("comment_count", 0)),
                values.get("price"),
                values.get("currency"),
                values.get("img"),
                partner_user_id,
                values.get("latitude"),
                values.get("longitude"),
                values.get("city"),
                values.get("country"),
                values.get("region_id"),
                values.get("district_id"),
                values.get("description_en"),
                values.get("description_ru"),
                values.get("description_uz"),
                values.get("check_in"),
                values.get("check_out"),
                bool(values.get("is_allowed_alcohol", False)),
                bool(values.get("is_allowed_corporate", False)),
                bool(values.get("is_allowed_pets", False)),
                bool(values.get("is_quiet_hours", False)),
                values.get("apartment_number"),
                values.get("home_number"),
                values.get("entrance_number"),
                values.get("floor_number"),
                values.get("pass_code"),
            ],
        )
    else:
        row = fetch_one(
            f"""
            INSERT INTO {table} (
                guid,
                created_at,
                updated_at,
                title,
                title_sort,
                is_verified,
                verification_status,
                is_archived,
                is_recommended,
                minimum_weekend_day_stay,
                weekend_only_sunday_inclusive,
                comment_count,
                price_per_person,
                price_on_working_days,
                price_on_weekends,
                currency,
                img,
                partner_user_id,
                latitude,
                longitude,
                city,
                country,
                region_id,
                district_id,
                description_en,
                description_ru,
                description_uz,
                check_in,
                check_out,
                is_allowed_alcohol,
                is_allowed_corporate,
                is_allowed_pets,
                is_quiet_hours,
                apartment_number,
                home_number,
                entrance_number,
                floor_number,
                pass_code
            ) VALUES (
                %s, %s, %s, %s, %s,
                FALSE, 'pending', FALSE, FALSE,
                %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s
            )
            RETURNING guid
            """,
            [
                uuid4(),
                now,
                now,
                values["title"],
                values["title_sort"],
                bool(values.get("minimum_weekend_day_stay", False)),
                bool(values.get("weekend_only_sunday_inclusive", False)),
                int(values.get("comment_count", 0)),
                values.get("price_per_person"),
                values.get("price_on_working_days"),
                values.get("price_on_weekends"),
                values.get("currency"),
                values.get("img"),
                partner_user_id,
                values.get("latitude"),
                values.get("longitude"),
                values.get("city"),
                values.get("country"),
                values.get("region_id"),
                values.get("district_id"),
                values.get("description_en"),
                values.get("description_ru"),
                values.get("description_uz"),
                values.get("check_in"),
                values.get("check_out"),
                bool(values.get("is_allowed_alcohol", False)),
                bool(values.get("is_allowed_corporate", False)),
                bool(values.get("is_allowed_pets", False)),
                bool(values.get("is_quiet_hours", False)),
                values.get("apartment_number"),
                values.get("home_number"),
                values.get("entrance_number"),
                values.get("floor_number"),
                values.get("pass_code"),
            ],
        )
    if not row:
        return None
    return get_property_for_partner(str(row["guid"]), partner_user_id)

def update_property(
    *,
    property_kind: str,
    property_id: int,
    partner_user_id: int,
    values: dict[str, Any],
) -> dict[str, Any] | None:
    table = _table_for_kind(property_kind)
    if not values:
        return fetch_one(
            f"""
            SELECT *
            FROM {table}
            WHERE id = %s
              AND partner_user_id = %s
            LIMIT 1
            """,
            [property_id, partner_user_id],
        )

    allowed = {
        "title",
        "title_sort",
        "minimum_weekend_day_stay",
        "weekend_only_sunday_inclusive",
        "currency",
        "img",
        "latitude",
        "longitude",
        "city",
        "country",
        "region_id",
        "district_id",
        "description_en",
        "description_ru",
        "description_uz",
        "check_in",
        "check_out",
        "is_allowed_alcohol",
        "is_allowed_corporate",
        "is_allowed_pets",
        "is_quiet_hours",
        "apartment_number",
        "home_number",
        "entrance_number",
        "floor_number",
        "pass_code",
    }
    if property_kind == PROPERTY_KIND_APARTMENT:
        allowed.add("price")
    else:
        allowed.update({"price_per_person", "price_on_working_days", "price_on_weekends"})
    updates: dict[str, Any] = {key: value for key, value in values.items() if key in allowed}
    updates["updated_at"] = timezone.now()
    updates["is_verified"] = False
    updates["verification_status"] = "pending"

    assignments = ", ".join(f"{column} = %s" for column in updates.keys())
    params = list(updates.values()) + [property_id, partner_user_id]
    row = fetch_one(
        f"""
        UPDATE {table}
        SET {assignments}
        WHERE id = %s
          AND partner_user_id = %s
        RETURNING guid
        """,
        params,
    )
    if not row:
        return None
    return get_property_for_partner(str(row["guid"]), partner_user_id)

def delete_property(
    *,
    property_kind: str,
    property_id: int,
    partner_user_id: int,
) -> int:
    table = _table_for_kind(property_kind)
    return execute(
        f"""
        DELETE FROM {table}
        WHERE id = %s
          AND partner_user_id = %s
        """,
        [property_id, partner_user_id],
    )


def set_property_primary_image(
    *,
    property_kind: str,
    property_id: int,
    partner_user_id: int,
    image_path: str | None,
) -> dict[str, Any] | None:
    table = _table_for_kind(property_kind)
    row = fetch_one(
        f"""
        UPDATE {table}
        SET img = %s,
            updated_at = %s
        WHERE id = %s
          AND partner_user_id = %s
        RETURNING guid
        """,
        [image_path, timezone.now(), property_id, partner_user_id],
    )
    if not row:
        return None
    return get_property_for_partner(str(row["guid"]), partner_user_id)


def list_reviews(
    *,
    property_kind: str,
    property_id: int,
    include_hidden: bool = False,
) -> list[dict[str, Any]]:
    if property_kind == PROPERTY_KIND_APARTMENT:
        where_property = "r.apartment_id = %s"
    else:
        where_property = "r.cottage_id = %s"

    where = [where_property]
    params: list[Any] = [property_id]
    if not include_hidden:
        where.append("COALESCE(r.is_hidden, FALSE) = FALSE")

    return fetch_all(
        f"""
        SELECT
            r.guid,
            r.created_at,
            r.rating,
            r.comment,
            r.user_id AS client_id,
            u.first_name AS client_first_name,
            u.last_name AS client_last_name
        FROM {get_table_name("review")} r
        LEFT JOIN {get_table_name("users")} u ON u.id = r.user_id
        WHERE """
        + " AND ".join(where)
        + """
        ORDER BY r.created_at DESC, r.id DESC
        """,
        params,
    )


def has_eligible_booking_for_review(
    *,
    client_user_id: int,
    property_kind: str,
    property_id: int,
) -> bool:
    property_column = "property_apartment_id" if property_kind == PROPERTY_KIND_APARTMENT else "property_cottage_id"
    statuses = ["confirmed", "completed", "cancelled"]
    if is_postgresql():
        row = fetch_one(
            f"""
            SELECT EXISTS (
                SELECT 1
                FROM {get_table_name("booking")}
                WHERE client_user_id = %s
                  AND {property_column} = %s
                  AND status = ANY(%s)
            ) AS exists_flag
            """,
            [client_user_id, property_id, statuses],
        )
    else:
        placeholders = ','.join(['%s'] * len(statuses))
        row = fetch_one(
            f"""
            SELECT EXISTS (
                SELECT 1
                FROM {get_table_name("booking")}
                WHERE client_user_id = %s
                  AND {property_column} = %s
                  AND status IN ({placeholders})
            ) AS exists_flag
            """,
            [client_user_id, property_id] + statuses,
        )
    return bool(row and row["exists_flag"])


def create_review(
    *,
    client_user_id: int,
    property_kind: str,
    property_id: int,
    rating: Decimal,
    comment: str | None,
) -> dict[str, Any] | None:
    now = timezone.now()
    apartment_id = property_id if property_kind == PROPERTY_KIND_APARTMENT else None
    cottage_id = property_id if property_kind == PROPERTY_KIND_COTTAGE else None

    row = fetch_one(
        f"""
        INSERT INTO {get_table_name("review")} (
            guid,
            created_at,
            updated_at,
            rating,
            comment,
            is_hidden,
            user_id,
            apartment_id,
            cottage_id
        ) VALUES (
            %s,
            %s,
            %s,
            %s,
            %s,
            FALSE,
            %s,
            %s,
            %s
        )
        RETURNING guid
        """,
        [
            uuid4(),
            now,
            now,
            rating,
            comment,
            client_user_id,
            apartment_id,
            cottage_id,
        ],
    )
    if not row:
        return None
    reviews = list_reviews(
        property_kind=property_kind,
        property_id=property_id,
        include_hidden=True,
    )
    return next((review for review in reviews if str(review.get("guid")) == str(row["guid"])), None)
