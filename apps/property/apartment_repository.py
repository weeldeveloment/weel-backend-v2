from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

from django.utils import timezone

from shared.raw.db import execute, fetch_all, fetch_one
from shared.raw.compat import is_postgresql, get_table_name

from .raw_repository import APARTMENT_TABLE, USERS_TABLE, REVIEW_TABLE


APARTMENT_SELECT = f"""
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
        a.description_en,
        a.description_ru,
        a.description_uz,
        a.check_in,
        a.check_out,
        a.is_allowed_alcohol,
        a.is_allowed_corporate,
        a.is_allowed_pets,
        a.is_quiet_hours,
        a.apartment_number,
        a.home_number,
        a.entrance_number,
        a.floor_number,
        a.pass_code,
        u.username AS partner_username,
        u.first_name AS partner_first_name,
        u.last_name AS partner_last_name,
        u.phone_number AS partner_phone_number,
        COALESCE(stats.average_rating, 5.0) AS average_rating,
        COALESCE(stats.review_count, 0) AS review_count
    FROM {APARTMENT_TABLE} a
    LEFT JOIN {USERS_TABLE} u ON u.id = a.partner_user_id
    LEFT JOIN LATERAL (
        SELECT
            ROUND(COALESCE(AVG(r.rating), 5.0), 2) AS average_rating,
            COUNT(*) AS review_count
        FROM {REVIEW_TABLE} r
        WHERE r.apartment_id = a.id
          AND (COALESCE(r.is_hidden, FALSE) = FALSE)
          AND r.rating IS NOT NULL
    ) stats ON TRUE
"""


def list_apartments(
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
        where.append("COALESCE(a.is_verified, FALSE) = TRUE")
    if public_only or partner_user_id is None:
        where.append("COALESCE(a.is_archived, FALSE) = FALSE")
    if partner_user_id is not None:
        where.append("a.partner_user_id = %s")
        params.append(partner_user_id)
    if recommended_only:
        where.append("COALESCE(a.is_recommended, FALSE) = TRUE")
    if search:
        where.append("COALESCE(a.title, '') LIKE %s")
        params.append(f"%{search.strip()}%")
    if region_id is not None:
        where.append("a.region_id = %s")
        params.append(region_id)
    if district_id is not None:
        where.append("a.district_id = %s")
        params.append(district_id)
    if corporate is not None:
        where.append("COALESCE(a.is_allowed_corporate, FALSE) = %s")
        params.append(bool(corporate))

    return fetch_all(
        f"""
        {APARTMENT_SELECT}
        WHERE {' AND '.join(where)}
        ORDER BY a.created_at DESC, a.id DESC
        """,
        params,
    )


def get_apartment_for_public(apartment_guid: str) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        {APARTMENT_SELECT}
        WHERE a.guid = %s
          AND COALESCE(a.is_verified, FALSE) = TRUE
          AND COALESCE(a.is_archived, FALSE) = FALSE
        LIMIT 1
        """,
        [apartment_guid],
    )


def get_apartment_for_partner(apartment_guid: str, partner_user_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        {APARTMENT_SELECT}
        WHERE a.guid = %s
          AND a.partner_user_id = %s
        LIMIT 1
        """,
        [apartment_guid, partner_user_id],
    )


def create_apartment(
    *,
    partner_user_id: int,
    values: dict[str, Any],
) -> dict[str, Any] | None:
    now = timezone.now()
    row = fetch_one(
        f"""
        INSERT INTO {APARTMENT_TABLE} (
            guid, created_at, updated_at,
            title, title_sort,
            is_verified, verification_status, is_archived, is_recommended,
            minimum_weekend_day_stay, weekend_only_sunday_inclusive, comment_count,
            price, currency, img, partner_user_id,
            latitude, longitude, city, country,
            region_id, district_id,
            description_en, description_ru, description_uz,
            check_in, check_out,
            is_allowed_alcohol, is_allowed_corporate, is_allowed_pets, is_quiet_hours,
            apartment_number, home_number, entrance_number, floor_number, pass_code
        ) VALUES (
            %s, %s, %s,
            %s, %s,
            FALSE, 'pending', FALSE, FALSE,
            %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s,
            %s, %s, %s,
            %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s, %s
        )
        RETURNING guid
        """,
        [
            uuid4(), now, now,
            values["title"], values["title_sort"],
            bool(values.get("minimum_weekend_day_stay", False)),
            bool(values.get("weekend_only_sunday_inclusive", False)),
            int(values.get("comment_count", 0)),
            values.get("price"), values.get("currency"), values.get("img"),
            partner_user_id,
            values.get("latitude"), values.get("longitude"),
            values.get("city"), values.get("country"),
            values.get("region_id"), values.get("district_id"),
            values.get("description_en"), values.get("description_ru"), values.get("description_uz"),
            values.get("check_in"), values.get("check_out"),
            bool(values.get("is_allowed_alcohol", False)),
            bool(values.get("is_allowed_corporate", False)),
            bool(values.get("is_allowed_pets", False)),
            bool(values.get("is_quiet_hours", False)),
            values.get("apartment_number"), values.get("home_number"),
            values.get("entrance_number"), values.get("floor_number"), values.get("pass_code"),
        ],
    )
    if not row:
        return None
    return get_apartment_for_partner(str(row["guid"]), partner_user_id)


_APARTMENT_UPDATE_ALLOWED = {
    "title", "title_sort",
    "minimum_weekend_day_stay", "weekend_only_sunday_inclusive",
    "price", "currency", "img",
    "latitude", "longitude", "city", "country",
    "region_id", "district_id",
    "description_en", "description_ru", "description_uz",
    "check_in", "check_out",
    "is_allowed_alcohol", "is_allowed_corporate", "is_allowed_pets", "is_quiet_hours",
    "apartment_number", "home_number", "entrance_number", "floor_number", "pass_code",
}


def update_apartment(
    *,
    apartment_id: int,
    partner_user_id: int,
    values: dict[str, Any],
) -> dict[str, Any] | None:
    if not values:
        return fetch_one(
            f"SELECT * FROM {APARTMENT_TABLE} WHERE id = %s AND partner_user_id = %s LIMIT 1",
            [apartment_id, partner_user_id],
        )

    updates: dict[str, Any] = {k: v for k, v in values.items() if k in _APARTMENT_UPDATE_ALLOWED}
    updates["updated_at"] = timezone.now()
    updates["is_verified"] = False
    updates["verification_status"] = "pending"

    assignments = ", ".join(f"{col} = %s" for col in updates)
    params = list(updates.values()) + [apartment_id, partner_user_id]
    row = fetch_one(
        f"UPDATE {APARTMENT_TABLE} SET {assignments} WHERE id = %s AND partner_user_id = %s RETURNING guid",
        params,
    )
    if not row:
        return None
    return get_apartment_for_partner(str(row["guid"]), partner_user_id)


def delete_apartment(*, apartment_id: int, partner_user_id: int) -> int:
    return execute(
        f"DELETE FROM {APARTMENT_TABLE} WHERE id = %s AND partner_user_id = %s",
        [apartment_id, partner_user_id],
    )


def set_apartment_primary_image(
    *,
    apartment_id: int,
    partner_user_id: int,
    image_path: str | None,
) -> dict[str, Any] | None:
    row = fetch_one(
        f"UPDATE {APARTMENT_TABLE} SET img = %s, updated_at = %s WHERE id = %s AND partner_user_id = %s RETURNING guid",
        [image_path, timezone.now(), apartment_id, partner_user_id],
    )
    if not row:
        return None
    return get_apartment_for_partner(str(row["guid"]), partner_user_id)


def effective_apartment_price(row: dict[str, Any]) -> Decimal:
    val = row.get("price")
    if val is not None:
        try:
            return Decimal(str(val))
        except Exception:
            pass
    return Decimal("0")
