from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
import logging
from typing import Any
from uuid import uuid4

from django.utils import timezone

from property.models import VerificationStatus
from shared.raw.compat import get_table_name, is_postgresql
from shared.raw.db import execute, fetch_all, fetch_one, table_exists

logger = logging.getLogger(__name__)


def _table(*candidates: str) -> str:
    for candidate in candidates:
        try:
            if table_exists(candidate):
                return get_table_name(candidate)
        except Exception:
            logger.warning(
                "table resolution skipped for candidate '%s'; falling back",
                candidate,
                exc_info=True,
            )
    # Prefer Django-style prefixed table names when schema isn't ready yet.
    return get_table_name(candidates[-1])


COTTAGE_TABLE = _table("cottage", "property_cottage")
COTTAGE_PRICE_TABLE = _table("cottage_price", "property_cottageprice")
CALENDAR_TABLE = _table("calendar")
BOOKING_TABLE = _table("booking", "booking_booking")
BOOKING_PRICE_TABLE = _table("booking_price", "booking_bookingprice")
BOOKING_TRANSACTION_TABLE = _table("booking_transaction", "booking_bookingtransaction")
USERS_TABLE = _table("users", "users_user")
REVIEW_TABLE = _table("review", "property_review")
DISTRICT_TABLE = _table("district", "property_district")
REGION_TABLE = _table("region", "property_region")
PREFECTURE_TABLE = _table("prefecture", "property_prefecture")
DISTRICT_PREFECTURE_TABLE = _table("district_prefecture")


def _column_exists(table_name: str, column_name: str, schema: str = "public") -> bool:
    try:
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
    except Exception:
        logger.warning(
            "column detection failed for %s.%s; using safe fallback",
            table_name,
            column_name,
            exc_info=True,
        )
        return False


HAS_COTTAGE_REGION_ID = _column_exists(COTTAGE_TABLE, "region_id")
HAS_COTTAGE_DISTRICT_ID = _column_exists(COTTAGE_TABLE, "district_id")
HAS_COTTAGE_GUESTS = _column_exists(COTTAGE_TABLE, "guests")
HAS_COTTAGE_ROOMS = _column_exists(COTTAGE_TABLE, "rooms")
HAS_COTTAGE_BEDS = _column_exists(COTTAGE_TABLE, "beds")
HAS_COTTAGE_BATHROOMS = _column_exists(COTTAGE_TABLE, "bathrooms")

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
        c.weekend_only_sunday_inclusive,
        c.comment_count,
        COALESCE(current_price.price_per_person, c.price_per_person) AS price_per_person,
        COALESCE(current_price.price_on_working_days, c.price_on_working_days) AS price_on_working_days,
        COALESCE(current_price.price_on_weekends, c.price_on_weekends) AS price_on_weekends,
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
        reg.guid AS region_guid,
        COALESCE(NULLIF(reg.title_uz, ''), NULLIF(reg.title_ru, ''), NULLIF(reg.title_en, '')) AS region_name,
        {DISTRICT_SELECT_SQL} AS district_id,
        d.guid AS district_guid,
        COALESCE(NULLIF(d.title_uz, ''), NULLIF(d.title_ru, ''), NULLIF(d.title_en, '')) AS district_name,
        c.prefecture_id,
        pref.id AS prefecture_guid,
        COALESCE(NULLIF(pref.name, ''), NULLIF(pref.ru_name, '')) AS prefecture_name,
        c.description_en,
        c.description_ru,
        c.description_uz,
        c.check_in,
        c.check_out,
        c.is_allowed_alcohol,
        c.is_allowed_corporate,
        c.is_allowed_pets,
        c.is_quiet_hours,
        {"c.guests" if HAS_COTTAGE_GUESTS else "NULL"} AS guests,
        {"c.rooms" if HAS_COTTAGE_ROOMS else "NULL"} AS rooms,
        {"c.beds" if HAS_COTTAGE_BEDS else "NULL"} AS beds,
        {"c.bathrooms" if HAS_COTTAGE_BATHROOMS else "NULL"} AS bathrooms,
        u.username AS partner_username,
        u.first_name AS partner_first_name,
        u.last_name AS partner_last_name,
        u.phone_number AS partner_phone_number,
        COALESCE(stats.average_rating, 5.0) AS average_rating,
        COALESCE(stats.review_count, 0) AS review_count,
        COALESCE(monthly_prices.price_data, '[]'::jsonb) AS price
    FROM {COTTAGE_TABLE} c
    LEFT JOIN (
        SELECT prefecture_id, MIN(district_id) AS district_id
        FROM {DISTRICT_PREFECTURE_TABLE}
        GROUP BY prefecture_id
    ) dp ON dp.prefecture_id = c.prefecture_id
    LEFT JOIN {DISTRICT_TABLE} d ON d.id = dp.district_id
    LEFT JOIN {REGION_TABLE} reg ON reg.id = d.region_id
    LEFT JOIN {PREFECTURE_TABLE} pref ON CAST(pref.id AS TEXT) = CAST(c.prefecture_id AS TEXT)
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
        FROM {COTTAGE_PRICE_TABLE} cp
        WHERE cp.cottage_id = c.id
    ) monthly_prices ON TRUE
    LEFT JOIN LATERAL (
        SELECT
            cp.price_per_person,
            cp.price_on_working_days,
            cp.price_on_weekends
        FROM {COTTAGE_PRICE_TABLE} cp
        WHERE cp.cottage_id = c.id
          AND CURRENT_DATE BETWEEN cp.month_from AND cp.month_to
        LIMIT 1
    ) current_price ON TRUE
"""


def list_cottages(
    *,
    public_only: bool = True,
    include_all_records: bool = False,
    partner_user_id: int | None = None,
    recommended_only: bool = False,
    search: str | None = None,
    region_id: int | None = None,
    district_id: int | None = None,
    prefecture_id: str | None = None,
    corporate: bool | None = None,
    min_price: Decimal | None = None,
    max_price: Decimal | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> list[dict[str, Any]]:
    where = ["1 = 1"]
    params: list[Any] = []

    if not include_all_records:
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
        where.append("LOWER(COALESCE(c.title, '')) LIKE LOWER(%s)")
        params.append(f"%{search.strip()}%")
    if region_id is not None:
        where.append(f"{REGION_SELECT_SQL} = %s")
        params.append(region_id)
    if district_id is not None:
        where.append(f"{DISTRICT_SELECT_SQL} = %s")
        params.append(district_id)
    if prefecture_id is not None:
        where.append("CAST(c.prefecture_id AS TEXT) = %s")
        params.append(str(prefecture_id))
    if corporate is not None:
        where.append("COALESCE(c.is_allowed_corporate, FALSE) = %s")
        params.append(bool(corporate))
    # For cottages, check if at least one price falls within the range
    # Prefer cottage_price (current effective row), fall back to legacy columns on cottage table.
    if min_price is not None:
        where.append(
            "(COALESCE(current_price.price_on_working_days, c.price_on_working_days, 0) >= %s OR COALESCE(current_price.price_on_weekends, c.price_on_weekends, 0) >= %s)"
        )
        params.extend([min_price, min_price])
    if max_price is not None:
        where.append(
            "(COALESCE(current_price.price_on_working_days, c.price_on_working_days, 0) <= %s OR COALESCE(current_price.price_on_weekends, c.price_on_weekends, 0) <= %s)"
        )
        params.extend([max_price, max_price])

    return fetch_all(
        f"""
        {COTTAGE_SELECT}
        WHERE {' AND '.join(where)}
        ORDER BY c.created_at DESC, c.id DESC
        {'LIMIT %s' if limit else ''}
        {'OFFSET %s' if offset else ''}
        """,
        params + ([limit] if limit else []) + ([offset] if offset else []),
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
    if HAS_COTTAGE_GUESTS:
        optional_columns.append("guests")
        optional_placeholders.append("%s")
        optional_params.append(values.get("guests"))
    if HAS_COTTAGE_ROOMS:
        optional_columns.append("rooms")
        optional_placeholders.append("%s")
        optional_params.append(values.get("rooms"))
    if HAS_COTTAGE_BEDS:
        optional_columns.append("beds")
        optional_placeholders.append("%s")
        optional_params.append(values.get("beds"))
    if HAS_COTTAGE_BATHROOMS:
        optional_columns.append("bathrooms")
        optional_placeholders.append("%s")
        optional_params.append(values.get("bathrooms"))

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
            weekend_only_sunday_inclusive, comment_count,
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
            FALSE, %s, FALSE, FALSE,
            %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s,
            {location_placeholders_sql},
            %s,
            %s, %s, %s,
            %s, %s,
            %s, %s, %s, %s
        )
        RETURNING id, guid
        """,
        [
            uuid4(), now, now,
            values["title"], values["title_sort"],
            VerificationStatus.WAITING.value,
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
    monthly_prices = values.get("price") if isinstance(values.get("price"), list) else None
    if monthly_prices:
        replace_cottage_prices(cottage_id=int(row["id"]), price_rows=monthly_prices)
    return get_cottage_for_partner(str(row["guid"]), partner_user_id)


def replace_cottage_prices(*, cottage_id: int, price_rows: list[dict[str, Any]]) -> None:
    if not table_exists(COTTAGE_PRICE_TABLE):
        return

    execute(
        f"DELETE FROM {COTTAGE_PRICE_TABLE} WHERE cottage_id = %s",
        [cottage_id],
    )

    now = timezone.now()
    for item in price_rows:
        if not isinstance(item, dict):
            continue
        fetch_one(
            f"""
            INSERT INTO {COTTAGE_PRICE_TABLE} (
                guid,
                created_at,
                updated_at,
                cottage_id,
                month_from,
                month_to,
                price_per_person,
                price_on_working_days,
                price_on_weekends
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            [
                uuid4(),
                now,
                now,
                cottage_id,
                item.get("month_from"),
                item.get("month_to"),
                item.get("price_per_person"),
                item.get("price_on_working_days"),
                item.get("price_on_weekends"),
            ],
        )


_COTTAGE_UPDATE_ALLOWED = {
    "title", "title_sort",
    "weekend_only_sunday_inclusive",
    "price_per_person", "price_on_working_days", "price_on_weekends",
    "currency", "img",
    "services",
    "latitude", "longitude", "city", "country",
    "prefecture_id",
    "description_en", "description_ru", "description_uz",
    "check_in", "check_out",
    "is_allowed_alcohol", "is_allowed_corporate", "is_allowed_pets", "is_quiet_hours",
}

_COTTAGE_PRICE_ONLY_FIELDS = {"price_per_person", "price_on_working_days", "price_on_weekends"}

if HAS_COTTAGE_REGION_ID:
    _COTTAGE_UPDATE_ALLOWED.add("region_id")
if HAS_COTTAGE_DISTRICT_ID:
    _COTTAGE_UPDATE_ALLOWED.add("district_id")
if HAS_COTTAGE_GUESTS:
    _COTTAGE_UPDATE_ALLOWED.add("guests")
if HAS_COTTAGE_ROOMS:
    _COTTAGE_UPDATE_ALLOWED.add("rooms")
if HAS_COTTAGE_BEDS:
    _COTTAGE_UPDATE_ALLOWED.add("beds")
if HAS_COTTAGE_BATHROOMS:
    _COTTAGE_UPDATE_ALLOWED.add("bathrooms")


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

    monthly_prices = values.get("price") if isinstance(values.get("price"), list) else None
    updates: dict[str, Any] = {k: v for k, v in values.items() if k in _COTTAGE_UPDATE_ALLOWED}
    if "services" in updates:
        raw_services = updates.get("services") or []
        updates["services"] = [str(service) for service in raw_services if service is not None]
    updates["updated_at"] = timezone.now()
    changed_fields = set(updates.keys())
    if monthly_prices is not None:
        changed_fields.add("price")
    changed_non_price_fields = changed_fields - _COTTAGE_PRICE_ONLY_FIELDS - {"price"}
    should_send_to_moderation = bool(changed_non_price_fields)
    if should_send_to_moderation:
        updates["is_verified"] = False
        updates["verification_status"] = VerificationStatus.WAITING.value
        from stories.raw_repository import reset_stories_verification_for_property
        reset_stories_verification_for_property(cottage_id, "cottage")

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
    if monthly_prices is not None:
        replace_cottage_prices(cottage_id=cottage_id, price_rows=monthly_prices)
    return get_cottage_for_partner(str(row["guid"]), partner_user_id)


def delete_cottage(*, cottage_id: int, partner_user_id: int) -> int:
    return execute(
        f"DELETE FROM {COTTAGE_TABLE} WHERE id = %s AND partner_user_id = %s",
        [cottage_id, partner_user_id],
    )


# ---------------------------------------------------------------------------
# Admin-scoped operations (no partner_user_id restriction)
# ---------------------------------------------------------------------------

_COTTAGE_ADMIN_UPDATE_ALLOWED: set[str] = set(_COTTAGE_UPDATE_ALLOWED) | {
    "is_verified",
    "verified_at",
    "verification_status",
    "is_archived",
    "is_recommended",
    "partner_user_id",
    "verified_by_user_id",
    "comment_count",
    "legacy_property_id",
}


def admin_get_cottage(cottage_guid: str) -> dict[str, Any] | None:
    """Fetch a cottage by guid with no ownership/visibility filters (admin use)."""
    return fetch_one(
        f"""
        {COTTAGE_SELECT}
        WHERE c.guid = %s
        LIMIT 1
        """,
        [cottage_guid],
    )


def admin_create_cottage(
    *,
    values: dict[str, Any],
    admin_user_id: int | None = None,
    partner_user_id: int | None = None,
) -> dict[str, Any] | None:
    owner_id = (
        int(partner_user_id)
        if partner_user_id is not None
        else int(admin_user_id)
        if admin_user_id is not None
        else None
    )
    if owner_id is None:
        return None

    create_values = dict(values or {})
    create_values.setdefault("currency", "UZS")
    create_values.setdefault("img", [])
    create_values.setdefault("services", [])
    create_values.setdefault("comment_count", 0)

    created = create_cottage(partner_user_id=owner_id, values=create_values)
    if not created:
        return None

    cottage_guid = str(created["guid"])
    admin_values = {
        k: v
        for k, v in (values or {}).items()
        if k in _COTTAGE_ADMIN_UPDATE_ALLOWED or k == "price"
    }
    if partner_user_id is not None:
        admin_values["partner_user_id"] = int(partner_user_id)
    if admin_values:
        updated = admin_update_cottage(
            cottage_guid=cottage_guid,
            values=admin_values,
            admin_user_id=admin_user_id,
        )
        if updated:
            return updated
    return admin_get_cottage(cottage_guid)


def admin_update_cottage(
    *,
    cottage_guid: str,
    values: dict[str, Any],
    admin_user_id: int | None = None,
) -> dict[str, Any] | None:
    """Update a cottage as admin. No partner ownership check.

    Supports every writable column, including verification/archival flags.
    Does NOT auto-reset verification (unlike partner-side `update_cottage`).
    """
    existing = fetch_one(
        f"SELECT id FROM {COTTAGE_TABLE} WHERE guid = %s LIMIT 1",
        [cottage_guid],
    )
    if not existing:
        return None
    cottage_id = int(existing["id"])

    monthly_prices = values.get("price") if isinstance(values.get("price"), list) else None
    updates: dict[str, Any] = {
        k: v for k, v in values.items() if k in _COTTAGE_ADMIN_UPDATE_ALLOWED
    }
    if "services" in updates:
        raw_services = updates.get("services") or []
        updates["services"] = [str(service) for service in raw_services if service is not None]

    if "is_verified" in updates:
        if bool(updates["is_verified"]):
            updates.setdefault("verified_at", timezone.now())
            if admin_user_id is not None:
                updates.setdefault("verified_by_user_id", int(admin_user_id))
            updates.setdefault("verification_status", VerificationStatus.ACCEPTED.value)
        else:
            updates.setdefault("verified_at", None)

    updates["updated_at"] = timezone.now()

    if updates:
        assignments_parts: list[str] = []
        for col in updates:
            if col == "services" and is_postgresql():
                assignments_parts.append("services = %s::uuid[]")
            else:
                assignments_parts.append(f"{col} = %s")
        assignments = ", ".join(assignments_parts)
        params = list(updates.values()) + [cottage_id]
        fetch_one(
            f"UPDATE {COTTAGE_TABLE} SET {assignments} WHERE id = %s RETURNING guid",
            params,
        )

    if monthly_prices is not None:
        replace_cottage_prices(cottage_id=cottage_id, price_rows=monthly_prices)

    return admin_get_cottage(cottage_guid)


def admin_delete_cottage(*, cottage_guid: str) -> int:
    existing = fetch_one(
        f"SELECT id FROM {COTTAGE_TABLE} WHERE guid = %s LIMIT 1",
        [cottage_guid],
    )
    if not existing:
        return 0

    cottage_id = int(existing["id"])

    booking_rows = fetch_all(
        f"SELECT id FROM {BOOKING_TABLE} WHERE property_cottage_id = %s",
        [cottage_id],
    )
    booking_ids = [int(row["id"]) for row in booking_rows if row.get("id") is not None]
    if booking_ids:
        if is_postgresql():
            execute(
                f"DELETE FROM {BOOKING_TRANSACTION_TABLE} WHERE booking_id = ANY(%s)",
                [booking_ids],
            )
            execute(
                f"DELETE FROM {BOOKING_PRICE_TABLE} WHERE booking_id = ANY(%s)",
                [booking_ids],
            )
        else:
            placeholders = ",".join(["%s"] * len(booking_ids))
            execute(
                f"DELETE FROM {BOOKING_TRANSACTION_TABLE} WHERE booking_id IN ({placeholders})",
                booking_ids,
            )
            execute(
                f"DELETE FROM {BOOKING_PRICE_TABLE} WHERE booking_id IN ({placeholders})",
                booking_ids,
            )
        execute(
            f"DELETE FROM {BOOKING_TABLE} WHERE property_cottage_id = %s",
            [cottage_id],
        )

    execute(
        f"DELETE FROM {CALENDAR_TABLE} WHERE property_cottage_id = %s",
        [cottage_id],
    )
    return execute(
        f"DELETE FROM {COTTAGE_TABLE} WHERE id = %s",
        [cottage_id],
    )


def admin_append_cottage_images(
    *,
    cottage_guid: str,
    image_paths: list[str],
) -> dict[str, Any] | None:
    existing = fetch_one(
        f"SELECT id, img FROM {COTTAGE_TABLE} WHERE guid = %s LIMIT 1",
        [cottage_guid],
    )
    if not existing:
        return None
    current = existing.get("img") or []
    if not isinstance(current, list):
        current = [current] if current else []
    new_img = list(current) + image_paths
    updated = fetch_one(
        f"UPDATE {COTTAGE_TABLE} SET img = %s, updated_at = %s WHERE id = %s RETURNING guid",
        [new_img, timezone.now(), int(existing["id"])],
    )
    if not updated:
        return None
    return admin_get_cottage(cottage_guid)


def admin_remove_cottage_image(
    *,
    cottage_guid: str,
    image_path: str,
) -> dict[str, Any] | None:
    existing = fetch_one(
        f"SELECT id, img FROM {COTTAGE_TABLE} WHERE guid = %s LIMIT 1",
        [cottage_guid],
    )
    if not existing:
        return None
    current = existing.get("img") or []
    if not isinstance(current, list):
        current = [current] if current else []
    new_img = [p for p in current if p != image_path]
    updated = fetch_one(
        f"UPDATE {COTTAGE_TABLE} SET img = %s, updated_at = %s WHERE id = %s RETURNING guid",
        [new_img, timezone.now(), int(existing["id"])],
    )
    if not updated:
        return None
    return admin_get_cottage(cottage_guid)


def set_cottage_primary_image(
    *,
    cottage_id: int,
    partner_user_id: int,
    image_path: str | None,
) -> dict[str, Any] | None:
    image_payload = [image_path] if image_path else []
    row = fetch_one(
        f"UPDATE {COTTAGE_TABLE} SET img = %s, updated_at = %s, is_verified = %s, verification_status = %s WHERE id = %s AND partner_user_id = %s RETURNING guid",
        [image_payload, timezone.now(), False, VerificationStatus.WAITING.value, cottage_id, partner_user_id],
    )
    if not row:
        return None
    return get_cottage_for_partner(str(row["guid"]), partner_user_id)


def append_cottage_images(
    *,
    cottage_id: int,
    partner_user_id: int,
    image_paths: list[str],
) -> dict[str, Any] | None:
    row = fetch_one(
        f"SELECT img FROM {COTTAGE_TABLE} WHERE id = %s AND partner_user_id = %s",
        [cottage_id, partner_user_id],
    )
    logger.info("[append_cottage_images] SELECT img for cottage_id=%s: row=%s", cottage_id, row)
    if not row:
        return None
    current = row.get("img") or []
    logger.info("[append_cottage_images] raw current img=%r type=%s", current, type(current).__name__)
    if not isinstance(current, list):
        current = [current] if current else []
    new_img = list(current) + image_paths
    logger.info("[append_cottage_images] new_img=%r", new_img)
    updated = fetch_one(
        f"UPDATE {COTTAGE_TABLE} SET img = %s, updated_at = %s, is_verified = %s, verification_status = %s WHERE id = %s AND partner_user_id = %s RETURNING guid",
        [new_img, timezone.now(), False, VerificationStatus.WAITING.value, cottage_id, partner_user_id],
    )
    logger.info("[append_cottage_images] UPDATE result=%s", updated)
    if not updated:
        return None
    return get_cottage_for_partner(str(updated["guid"]), partner_user_id)


def remove_cottage_image(
    *,
    cottage_id: int,
    partner_user_id: int,
    image_path: str,
) -> dict[str, Any] | None:
    row = fetch_one(
        f"SELECT img FROM {COTTAGE_TABLE} WHERE id = %s AND partner_user_id = %s",
        [cottage_id, partner_user_id],
    )
    if not row:
        return None
    current = row.get("img") or []
    if not isinstance(current, list):
        current = [current] if current else []
    new_img = [p for p in current if p != image_path]
    updated = fetch_one(
        f"UPDATE {COTTAGE_TABLE} SET img = %s, updated_at = %s, is_verified = %s, verification_status = %s WHERE id = %s AND partner_user_id = %s RETURNING guid",
        [new_img, timezone.now(), False, VerificationStatus.WAITING.value, cottage_id, partner_user_id],
    )
    if not updated:
        return None
    return get_cottage_for_partner(str(updated["guid"]), partner_user_id)


def replace_cottage_image(
    *,
    cottage_id: int,
    partner_user_id: int,
    old_image_path: str,
    new_image_path: str,
) -> dict[str, Any] | None:
    row = fetch_one(
        f"SELECT img FROM {COTTAGE_TABLE} WHERE id = %s AND partner_user_id = %s",
        [cottage_id, partner_user_id],
    )
    if not row:
        return None
    current = row.get("img") or []
    if not isinstance(current, list):
        current = [current] if current else []
    new_img = [new_image_path if p == old_image_path else p for p in current]
    updated = fetch_one(
        f"UPDATE {COTTAGE_TABLE} SET img = %s, updated_at = %s, is_verified = %s, verification_status = %s WHERE id = %s AND partner_user_id = %s RETURNING guid",
        [new_img, timezone.now(), False, VerificationStatus.WAITING.value, cottage_id, partner_user_id],
    )
    if not updated:
        return None
    return get_cottage_for_partner(str(updated["guid"]), partner_user_id)


def _parse_date(value) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except (ValueError, TypeError):
            pass
    return None


def effective_cottage_price(row: dict[str, Any], reference_date: date) -> Decimal:
    field = "price_on_weekends" if reference_date.weekday() >= 4 else "price_on_working_days"

    # Prefer cottage_price rows (SSOT), then fall back to legacy top-level columns.
    price_rows = row.get("price")
    if price_rows:
        for item in price_rows:
            if not isinstance(item, dict):
                continue
            month_from = _parse_date(item.get("month_from"))
            month_to = _parse_date(item.get("month_to"))
            if month_from and month_to and month_from <= reference_date <= month_to:
                val = item.get(field)
                if val is not None:
                    try:
                        return Decimal(str(val))
                    except (InvalidOperation, TypeError, ValueError):
                        pass
                break

    val = row.get(field)
    if val is not None:
        try:
            return Decimal(str(val))
        except (InvalidOperation, TypeError, ValueError):
            pass
    return Decimal("0")


def list_partners_with_expired_cottage_prices() -> list[dict[str, Any]]:
    """Return partners who own verified, non-archived cottages missing current-month prices."""
    return fetch_all(
        f"""
        SELECT DISTINCT c.partner_user_id AS partner_id, u.phone_number
        FROM {COTTAGE_TABLE} c
        JOIN {USERS_TABLE} u ON u.id = c.partner_user_id
        WHERE COALESCE(c.is_archived, FALSE) = FALSE
          AND COALESCE(c.is_verified, FALSE) = TRUE
          AND (
              NOT EXISTS (
                  SELECT 1 FROM {COTTAGE_PRICE_TABLE} cp
                  WHERE cp.cottage_id = c.id
              )
              OR NOT EXISTS (
                  SELECT 1 FROM {COTTAGE_PRICE_TABLE} cp
                  WHERE cp.cottage_id = c.id
                    AND cp.month_from >= DATE_TRUNC('month', CURRENT_DATE)
                    AND cp.month_from <  DATE_TRUNC('month', CURRENT_DATE) + INTERVAL '1 month'
              )
          )
        ORDER BY c.partner_user_id
        """
    )
