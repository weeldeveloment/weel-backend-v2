from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import uuid4

from django.utils import timezone

from shared.raw.compat import get_table_name, is_postgresql
from shared.raw.db import execute, fetch_all, fetch_one, table_exists


def _table(*candidates: str) -> str:
    for candidate in candidates:
        if table_exists(candidate):
            return get_table_name(candidate)
    # Prefer Django-style prefixed table names when schema isn't ready yet.
    return get_table_name(candidates[-1])


COTTAGE_TABLE = _table("cottage", "property_cottage")
USERS_TABLE = _table("users", "users_user")
REVIEW_TABLE = _table("review", "property_review")
DISTRICT_TABLE = _table("district", "property_district")
REGION_TABLE = _table("region", "property_region")
DISTRICT_PREFECTURE_TABLE = _table("district_prefecture")


def _column_exists(table_name: str, column_name: str, schema: str = "public") -> bool:
    raw_table = str(table_name).split(".")[-1]
    row = fetch_one(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = %s
              AND table_name = %s
              AND column_name = %s
        ) AS exists
        """,
        [schema, raw_table, column_name],
    )
    return bool(row and row.get("exists"))


HAS_COTTAGE_REGION_ID = _column_exists(COTTAGE_TABLE, "region_id")
HAS_COTTAGE_DISTRICT_ID = _column_exists(COTTAGE_TABLE, "district_id")

REGION_SELECT_SQL = "COALESCE(c.region_id, d.region_id)" if HAS_COTTAGE_REGION_ID else "d.region_id"
DISTRICT_SELECT_SQL = "COALESCE(c.district_id, dp.district_id)" if HAS_COTTAGE_DISTRICT_ID else "dp.district_id"


COTTAGE_SELECT = f"""
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
        {REGION_SELECT_SQL} AS region_id,
        {DISTRICT_SELECT_SQL} AS district_id,
        c.prefecture_id,
        c.description_en,
        c.description_ru,
        c.description_uz,
        c.check_in,
        c.check_out,
        c.is_allowed_alcohol,
        c.is_allowed_corporate,
        c.is_allowed_pets,
        c.is_quiet_hours,
        u.username AS partner_username,
        u.first_name AS partner_first_name,
        u.last_name AS partner_last_name,
        u.phone_number AS partner_phone_number,
        COALESCE(stats.average_rating, 5.0) AS average_rating,
        COALESCE(stats.review_count, 0) AS review_count
    FROM {COTTAGE_TABLE} c
    LEFT JOIN {DISTRICT_PREFECTURE_TABLE} dp ON dp.prefecture_id = c.prefecture_id
    LEFT JOIN {DISTRICT_TABLE} d ON d.id = dp.district_id
    LEFT JOIN {REGION_TABLE} reg ON reg.id = d.region_id
    LEFT JOIN {USERS_TABLE} u ON u.id = c.partner_user_id
    LEFT JOIN LATERAL (
        SELECT
            ROUND(COALESCE(AVG(r.rating), 5.0), 2) AS average_rating,
            COUNT(*) AS review_count
        FROM {REVIEW_TABLE} r
        WHERE r.cottage_id = c.id
          AND (COALESCE(r.is_hidden, FALSE) = FALSE)
          AND r.rating IS NOT NULL
    ) stats ON TRUE
"""


def list_cottages(
    *,
    public_only: bool = True,
    partner_user_id: int | None = None,
    recommended_only: bool = False,
    search: str | None = None,
    region_id: int | None = None,
    district_id: int | None = None,
    corporate: bool | None = None,
) -> list[dict[str, Any]]:
    where = ["1 = 1"]
    params: list[Any] = []

    if public_only:
        where.append("COALESCE(c.is_verified, FALSE) = TRUE")
    if public_only or partner_user_id is None:
        where.append("COALESCE(c.is_archived, FALSE) = FALSE")
    if partner_user_id is not None:
        where.append("c.partner_user_id = %s")
        params.append(partner_user_id)
    if recommended_only:
        where.append("COALESCE(c.is_recommended, FALSE) = TRUE")
    if search:
        where.append("COALESCE(c.title, '') LIKE %s")
        params.append(f"%{search.strip()}%")
    if region_id is not None:
        where.append(f"{REGION_SELECT_SQL} = %s")
        params.append(region_id)
    if district_id is not None:
        where.append(f"{DISTRICT_SELECT_SQL} = %s")
        params.append(district_id)
    if corporate is not None:
        where.append("COALESCE(c.is_allowed_corporate, FALSE) = %s")
        params.append(bool(corporate))

    return fetch_all(
        f"""
        {COTTAGE_SELECT}
        WHERE {' AND '.join(where)}
        ORDER BY c.created_at DESC, c.id DESC
        """,
        params,
    )


def get_cottage_for_public(cottage_guid: str) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        {COTTAGE_SELECT}
        WHERE c.guid = %s
          AND COALESCE(c.is_verified, FALSE) = TRUE
          AND COALESCE(c.is_archived, FALSE) = FALSE
        LIMIT 1
        """,
        [cottage_guid],
    )


def get_cottage_for_partner(cottage_guid: str, partner_user_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        {COTTAGE_SELECT}
        WHERE c.guid = %s
          AND c.partner_user_id = %s
        LIMIT 1
        """,
        [cottage_guid, partner_user_id],
    )


def create_cottage(
    *,
    partner_user_id: int,
    values: dict[str, Any],
) -> dict[str, Any] | None:
    now = timezone.now()
    optional_columns: list[str] = []
    optional_placeholders: list[str] = []
    optional_params: list[Any] = []
    if HAS_COTTAGE_REGION_ID:
        optional_columns.append("region_id")
        optional_placeholders.append("%s")
        optional_params.append(values.get("region_id"))
    if HAS_COTTAGE_DISTRICT_ID:
        optional_columns.append("district_id")
        optional_placeholders.append("%s")
        optional_params.append(values.get("district_id"))

    location_columns_sql = "latitude, longitude, city, country"
    if optional_columns:
        location_columns_sql = f"{location_columns_sql}, {', '.join(optional_columns)}"

    location_placeholders_sql = "%s, %s, %s, %s"
    if optional_placeholders:
        location_placeholders_sql = f"{location_placeholders_sql}, {', '.join(optional_placeholders)}"

    row = fetch_one(
        f"""
        INSERT INTO {COTTAGE_TABLE} (
            guid, created_at, updated_at,
            title, title_sort,
            is_verified, verification_status, is_archived, is_recommended,
            minimum_weekend_day_stay, weekend_only_sunday_inclusive, comment_count,
            price_per_person, price_on_working_days, price_on_weekends,
            currency, img, partner_user_id,
            services,
            {location_columns_sql},
            prefecture_id,
            description_en, description_ru, description_uz,
            check_in, check_out,
            is_allowed_alcohol, is_allowed_corporate, is_allowed_pets, is_quiet_hours
        ) VALUES (
            %s, %s, %s,
            %s, %s,
            FALSE, 'pending', FALSE, FALSE,
            %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s,
            {location_placeholders_sql},
            %s,
            %s, %s, %s,
            %s, %s,
            %s, %s, %s, %s
        )
        RETURNING guid
        """,
        [
            uuid4(), now, now,
            values["title"], values["title_sort"],
            bool(values.get("minimum_weekend_day_stay", False)),
            bool(values.get("weekend_only_sunday_inclusive", False)),
            int(values.get("comment_count", 0)),
            values.get("price_per_person"), values.get("price_on_working_days"),
            values.get("price_on_weekends"),
            values.get("currency"), values.get("img"),
            partner_user_id,
            values.get("services") or [],
            values.get("latitude"), values.get("longitude"),
            values.get("city"), values.get("country"),
            *optional_params,
            values.get("prefecture_id"),
            values.get("description_en"), values.get("description_ru"), values.get("description_uz"),
            values.get("check_in"), values.get("check_out"),
            bool(values.get("is_allowed_alcohol", False)),
            bool(values.get("is_allowed_corporate", False)),
            bool(values.get("is_allowed_pets", False)),
            bool(values.get("is_quiet_hours", False)),
        ],
    )
    if not row:
        return None
    return get_cottage_for_partner(str(row["guid"]), partner_user_id)


_COTTAGE_UPDATE_ALLOWED = {
    "title", "title_sort",
    "minimum_weekend_day_stay", "weekend_only_sunday_inclusive",
    "price_per_person", "price_on_working_days", "price_on_weekends",
    "currency", "img",
    "services",
    "latitude", "longitude", "city", "country",
    "prefecture_id",
    "description_en", "description_ru", "description_uz",
    "check_in", "check_out",
    "is_allowed_alcohol", "is_allowed_corporate", "is_allowed_pets", "is_quiet_hours",
}

if HAS_COTTAGE_REGION_ID:
    _COTTAGE_UPDATE_ALLOWED.add("region_id")
if HAS_COTTAGE_DISTRICT_ID:
    _COTTAGE_UPDATE_ALLOWED.add("district_id")


def update_cottage(
    *,
    cottage_id: int,
    partner_user_id: int,
    values: dict[str, Any],
) -> dict[str, Any] | None:
    if not values:
        return fetch_one(
            f"SELECT * FROM {COTTAGE_TABLE} WHERE id = %s AND partner_user_id = %s LIMIT 1",
            [cottage_id, partner_user_id],
        )

    updates: dict[str, Any] = {k: v for k, v in values.items() if k in _COTTAGE_UPDATE_ALLOWED}
    if "services" in updates:
        raw_services = updates.get("services") or []
        updates["services"] = [str(service) for service in raw_services if service is not None]
    updates["updated_at"] = timezone.now()
    updates["is_verified"] = False
    updates["verification_status"] = "pending"

    assignments_parts: list[str] = []
    for col in updates:
        if col == "services" and is_postgresql():
            assignments_parts.append("services = %s::uuid[]")
        else:
            assignments_parts.append(f"{col} = %s")
    assignments = ", ".join(assignments_parts)
    params = list(updates.values()) + [cottage_id, partner_user_id]
    row = fetch_one(
        f"UPDATE {COTTAGE_TABLE} SET {assignments} WHERE id = %s AND partner_user_id = %s RETURNING guid",
        params,
    )
    if not row:
        return None
    return get_cottage_for_partner(str(row["guid"]), partner_user_id)


def delete_cottage(*, cottage_id: int, partner_user_id: int) -> int:
    return execute(
        f"DELETE FROM {COTTAGE_TABLE} WHERE id = %s AND partner_user_id = %s",
        [cottage_id, partner_user_id],
    )


def set_cottage_primary_image(
    *,
    cottage_id: int,
    partner_user_id: int,
    image_path: str | None,
) -> dict[str, Any] | None:
    image_payload = [image_path] if image_path else []
    row = fetch_one(
        f"UPDATE {COTTAGE_TABLE} SET img = %s, updated_at = %s WHERE id = %s AND partner_user_id = %s RETURNING guid",
        [image_payload, timezone.now(), cottage_id, partner_user_id],
    )
    if not row:
        return None
    return get_cottage_for_partner(str(row["guid"]), partner_user_id)


def effective_cottage_price(row: dict[str, Any], reference_date: date) -> Decimal:
    field = "price_on_weekends" if reference_date.weekday() >= 4 else "price_on_working_days"
    val = row.get(field)
    if val is not None:
        try:
            return Decimal(str(val))
        except (InvalidOperation, TypeError, ValueError):
            pass
    return Decimal("0")
