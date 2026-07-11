from __future__ import annotations

import json as _json
import logging
import math
from collections.abc import Callable
from datetime import date, datetime, time
from typing import Any, TypeVar

from django.db import connection
from django.utils import timezone
from pydantic import ValidationError

from apps.platform.raw_repository import get_organization_by_schema, list_organizations

logger = logging.getLogger(__name__)
T = TypeVar("T")


def _safe_schema_name(schema_name: str | None) -> str:
    raw = str(schema_name or "").strip()
    if not raw:
        raise ValidationError("Schema name is empty")
    if not raw.replace("_", "").isalnum():
        raise ValidationError("Schema name contains invalid characters")
    return raw


def encode_hotel_guid(schema_name: str, hotel_id: int | str) -> str:
    return f"{schema_name}:{hotel_id}"


def decode_hotel_guid(hotel_guid: str) -> tuple[str | None, str]:
    raw = str(hotel_guid or "").strip()
    if not raw:
        raise ValidationError("Hotel GUID is empty")
    if ":" in raw:
        schema_name, raw_id = raw.split(":", 1)
        safe_schema = _safe_schema_name(schema_name)
        if not safe_schema:
            raise ValidationError("Invalid schema name in hotel GUID")
        try:
            hotel_id = int(str(raw_id).strip())
        except (TypeError, ValueError):
            raise ValidationError("Invalid hotel ID in GUID")
        return safe_schema, str(hotel_id)
    return None, raw


def _resolve_hotel_from_guid(hotel_guid: str) -> tuple[str, int | str] | None:
    """Resolve a hotel GUID to (schema_name, hotel_identifier) for querying.
    Returns None if the GUID cannot be resolved.
    Accepts both legacy composite format (schema:id) and new UUID format."""
    decoded = decode_hotel_guid(hotel_guid)
    if decoded is None:
        return None
    schema_name, identifier = decoded
    if schema_name is not None:
        return schema_name, int(identifier)
    return None, identifier




def _iso_time(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, time):
        return value.isoformat()
    text = str(value).strip()
    return text or None


def _serialize_hotel_row(
    row: dict[str, Any],
    organization: dict[str, Any] | None,
) -> dict[str, Any]:
    payload = dict(row)
    tenant_schema = str(payload.get("tenant_schema") or "")
    hotel_id = int(payload.get("id") or 0)
    raw_guid = payload.get("guid")
    if raw_guid is not None:
        payload["guid"] = str(raw_guid)
    else:
        payload["guid"] = encode_hotel_guid(tenant_schema, hotel_id)
    payload["title"] = payload.get("name") or payload.get("title") or ""
    payload["img"] = payload.get("photos") or payload.get("img") or []
    raw_legal = payload.get("legal_info")
    if isinstance(raw_legal, str):
        try:
            raw_legal = _json.loads(raw_legal)
        except Exception:
            raw_legal = {}
    payload["legal_info"] = raw_legal if isinstance(raw_legal, dict) else {}
    payload["is_allowed_alcohol"] = bool(payload.get("alcohol_allowed", False))
    payload["is_allowed_pets"] = bool(payload.get("pets_allowed", False))
    payload["is_quiet_hours"] = bool(payload.get("quiet_hours", True))
    payload["is_verified"] = bool(payload.get("is_verified", False))
    payload["is_archived"] = bool(payload.get("is_archived", False))
    payload["is_recommended"] = bool(payload.get("is_recommended", False))
    payload["verification_status"] = payload.get("verification_status") or "waiting"
    payload["property_kind"] = "hotel"
    payload["check_in_time"] = _iso_time(payload.get("check_in_time"))
    payload["check_out_time"] = _iso_time(payload.get("check_out_time"))
    payload["partner_user_id"] = payload.get("partner_user_id")
    payload["organization_id"] = organization.get("id") if organization else None
    payload["organization_name"] = organization.get("name") if organization else None
    payload["organization_slug"] = organization.get("slug") if organization else None
    return payload


def _run_in_schema(schema_name: str, fn: Callable[[], T]) -> T:
    with connection.cursor() as cursor:
        cursor.execute("SET search_path TO %s, public", [schema_name])
        try:
            return fn()
        finally:
            cursor.execute("SET search_path TO public")


def _fetch_rows(cursor) -> list[dict[str, Any]]:
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _fetch_hotel_rows_for_schema(
    schema_name: str,
    *,
    hotel_id: int | None = None,
    hotel_guid: str | None = None,
    include_inactive: bool = False,
    include_unverified: bool = False,
    search: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    def _query() -> list[dict[str, Any]]:
        with connection.cursor() as cursor:
            where = []
            params: list[Any] = []
            if hotel_id is not None:
                where.append("p.id = %s")
                params.append(hotel_id)
            elif hotel_guid is not None:
                where.append("p.guid::text = %s")
                params.append(hotel_guid)
            if not include_inactive:
                where.append("COALESCE(p.is_active, TRUE) = TRUE")
            if not include_unverified:
                where.append("COALESCE(p.is_verified, FALSE) = TRUE")
            if search:
                raw_search = str(search).strip()
                where.append(
                    "(p.name ILIKE %s OR COALESCE(p.city, '') ILIKE %s OR COALESCE(p.address, '') ILIKE %s)"
                )
                like = f"%{raw_search}%"
                params.extend([like, like, like])
            where_sql = f"WHERE {' AND '.join(where)}" if where else ""
            limit_sql = f"LIMIT {int(limit)}" if limit else ""
            cursor.execute(
                f"""
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
                    p.city,
                    p.country,
                    p.latitude::text AS latitude,
                    p.longitude::text AS longitude,
                    p.star_rating,
                    COALESCE(p.amenities, ARRAY[]::text[]) AS amenities,
                    COALESCE(p.legal_info, '{{}}'::jsonb) AS legal_info,
                    p.check_in_time,
                    p.check_out_time,
                    p.cancellation_policy,
                    COALESCE(p.quiet_hours, TRUE) AS quiet_hours,
                    COALESCE(p.alcohol_allowed, FALSE) AS alcohol_allowed,
                    COALESCE(p.pets_allowed, FALSE) AS pets_allowed,
                    p.currency,
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
                    %s AS tenant_schema,
                    (SELECT MIN(rate.rate) FROM pms_rate rate WHERE rate.property_id = p.id AND rate.date_to >= CURRENT_DATE) AS price_from,
                    (SELECT AVG(rv.rating) FROM pms_review rv WHERE rv.property_id = p.id AND rv.is_complained = FALSE) AS review_score,
                    (SELECT COUNT(*) FROM pms_review rv WHERE rv.property_id = p.id AND rv.is_complained = FALSE) AS review_count
                FROM pms_property p
                {where_sql}
                ORDER BY p.created_at DESC, p.id DESC
                {limit_sql}
                """,
                [schema_name, *params],
            )
            return _fetch_rows(cursor)

    return _run_in_schema(schema_name, _query)


def _sort_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows.sort(
        key=lambda row: (
            row.get("created_at") is None,
            row.get("created_at"),
            row.get("id"),
        ),
        reverse=True,
    )
    return rows


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return 6371.0 * 2.0 * math.asin(math.sqrt(a))


def _matches_search(row: dict[str, Any], search: str | None) -> bool:
    raw = str(search or "").strip().lower()
    if not raw:
        return True
    haystacks = (
        row.get("title"),
        row.get("organization_name"),
        row.get("organization_slug"),
        row.get("city"),
        row.get("address"),
    )
    haystack_text = " ".join(str(v or "").lower() for v in haystacks)
    if raw in haystack_text:
        return True
    # Word-level partial match: every search word appears somewhere
    words = raw.split()
    return all(any(word in str(v or "").lower() for v in haystacks) for word in words)


def _matches_location(
    row: dict[str, Any],
    lat: float | None,
    lon: float | None,
    radius_km: float,
) -> bool:
    if lat is None or lon is None:
        return True
    try:
        row_lat = float(row.get("latitude") or 0)
        row_lon = float(row.get("longitude") or 0)
    except (TypeError, ValueError):
        return False
    if row_lat == 0.0 and row_lon == 0.0:
        return False
    return _haversine_km(lat, lon, row_lat, row_lon) <= radius_km


def _matches_created_range(
    row: dict[str, Any],
    *,
    created_from: date | None,
    created_to: date | None,
) -> bool:
    created_at = row.get("created_at")
    if created_at is None:
        return created_from is None and created_to is None
    if isinstance(created_at, datetime):
        created_date = timezone.localtime(created_at).date() if timezone.is_aware(created_at) else created_at.date()
    else:
        try:
            created_date = date.fromisoformat(str(created_at)[:10])
        except ValueError:
            return True
    if created_from and created_date < created_from:
        return False
    if created_to and created_date > created_to:
        return False
    return True


def _find_hotel_by_guid_across_schemas(
    guid_value: str,
    *,
    include_inactive: bool = False,
    include_unverified: bool = False,
) -> list[dict[str, Any]]:
    """Search for a hotel by UUID guid across all tenant schemas."""
    for organization in list_hotel_organizations():
        try:
            rows = _fetch_hotel_rows_for_schema(
                organization["schema_name"],
                hotel_guid=guid_value,
                include_inactive=include_inactive,
                include_unverified=include_unverified,
                limit=1,
            )
        except Exception:
            logger.warning(
                "Failed to find hotel by guid=%s across schemas (schema=%s)",
                guid_value, organization["schema_name"], exc_info=True,
            )
            continue
        if rows:
            return rows
    return []


def list_hotel_organizations() -> list[dict[str, Any]]:
    organizations = []
    for organization in list_organizations():
        raw_schema = organization.get("schema_name")
        if not raw_schema:
            continue
        try:
            schema_name = _safe_schema_name(raw_schema)
        except ValidationError:
            logger.warning(
                "Invalid schema_name=%s for organization id=%s",
                raw_schema, organization.get("id"), exc_info=True,
            )
            continue
        payload = {
            "id": organization.get("id"),
            "name": organization.get("name"),
            "slug": organization.get("slug"),
            "schema_name": schema_name,
        }
        organizations.append(payload)
    return organizations


def list_hotels(
    *,
    search: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    radius_km: float = 10.0,
    limit: int | None = None,
    testing_only: bool | None = None,
    include_unverified: bool = False,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    remaining = limit

    for organization in list_hotel_organizations():
        try:
            tenant_rows = _fetch_hotel_rows_for_schema(
                organization["schema_name"],
                include_unverified=include_unverified,
                search=search,
                limit=remaining,
            )
        except Exception:
            logger.warning(
                "Failed to list hotels from schema=%s (public)",
                organization["schema_name"], exc_info=True,
            )
            continue
        serialized = [
            _serialize_hotel_row(row, organization)
            for row in tenant_rows
            if testing_only is None or bool(row.get("is_testing", False)) is bool(testing_only)
        ]
        rows.extend(serialized)
        if limit:
            remaining = limit - len(rows)
            if remaining <= 0:
                break

    if lat is not None and lon is not None:
        rows = [r for r in rows if _matches_location(r, lat, lon, radius_km)]

    _sort_rows(rows)
    if limit is not None:
        rows = rows[:limit]
    return rows


def list_admin_hotels(
    *,
    search: str | None = None,
    organization_id: int | None = None,
    tenant_schema: str | None = None,
    is_active: bool | None = None,
    created_from: date | None = None,
    created_to: date | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    normalized_schema = _safe_schema_name(tenant_schema) if tenant_schema else None

    for organization in list_hotel_organizations():
        if organization_id is not None and int(organization["id"]) != int(organization_id):
            continue
        if normalized_schema and organization["schema_name"] != normalized_schema:
            continue
        try:
            tenant_rows = _fetch_hotel_rows_for_schema(
                organization["schema_name"],
                include_inactive=True,
                include_unverified=True,
                search=search,
            )
        except Exception:
            logger.warning(
                "Failed to list admin hotels from schema=%s",
                organization["schema_name"], exc_info=True,
            )
            continue
        for row in tenant_rows:
            payload = _serialize_hotel_row(row, organization)
            if is_active is not None and bool(payload.get("is_active")) != is_active:
                continue
            if not _matches_created_range(
                payload,
                created_from=created_from,
                created_to=created_to,
            ):
                continue
            rows.append(payload)

    _sort_rows(rows)
    if limit is not None:
        rows = rows[:limit]
    return rows


def get_admin_hotel(hotel_guid: str) -> dict[str, Any] | None:
    decoded = decode_hotel_guid(hotel_guid)
    if not decoded:
        return None
    schema_name, identifier = decoded
    if schema_name is not None:
        organization = get_organization_by_schema(schema_name)
        if not organization:
            return None
        rows = _fetch_hotel_rows_for_schema(
            schema_name,
            hotel_id=int(identifier),
            include_inactive=True,
            include_unverified=True,
        )
    else:
        rows = _find_hotel_by_guid_across_schemas(
            identifier,
            include_inactive=True,
            include_unverified=True,
        )
        if not rows:
            return None
        organization = get_organization_by_schema(rows[0].get("tenant_schema") or "")
    if not rows:
        return None
    return _serialize_hotel_row(rows[0], organization)


def fetch_room_summaries(schema_name: str, property_id: int) -> list[dict[str, Any]]:
    def _query():
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    r.id,
                    r.display_name,
                    r.room_type_preset,
                    r.capacity,
                    r.bedroom_count,
                    COALESCE(r.beds, '[]'::jsonb) AS beds,
                    COALESCE(r.photos, ARRAY[]::text[]) AS photos,
                    COALESCE(r.amenities, ARRAY[]::text[]) AS amenities,
                    (SELECT MIN(rate.rate) FROM pms_rate rate
                     WHERE rate.room_id = r.id AND rate.date_to >= CURRENT_DATE) AS price_from
                FROM pms_room r
                WHERE r.property_id = %s AND r.is_active = TRUE
                ORDER BY r.room_number ASC
                """,
                [property_id],
            )
            return _fetch_rows(cursor)

    return _run_in_schema(schema_name, _query)


def get_hotel_for_public(hotel_guid: str) -> dict[str, Any] | None:
    """Fetch a single active AND verified hotel by encoded GUID."""
    decoded = decode_hotel_guid(hotel_guid)
    if not decoded:
        return None
    schema_name, identifier = decoded
    if schema_name is not None:
        organization = get_organization_by_schema(schema_name)
        if not organization:
            return None
        rows = _fetch_hotel_rows_for_schema(schema_name, hotel_id=int(identifier))
    else:
        rows = _find_hotel_by_guid_across_schemas(identifier)
        if not rows:
            return None
        organization = get_organization_by_schema(rows[0].get("tenant_schema") or "")
    if not rows:
        return None
    return _serialize_hotel_row(rows[0], organization)


def create_admin_hotel(*, schema_name: str, values: dict[str, Any]) -> dict[str, Any] | None:
    safe_schema = _safe_schema_name(schema_name)
    if not safe_schema:
        return None
    organization = get_organization_by_schema(safe_schema)
    if not organization:
        return None
    organization_id = organization.get("id")
    if not organization_id:
        return None

    def _insert() -> dict[str, Any] | None:
        with connection.cursor() as cursor:
            now = timezone.now()
            cursor.execute(
                """
                INSERT INTO pms_property (
                    guid,
                    organization_id,
                    partner_user_id,
                    name,
                    description_uz,
                    description_ru,
                    description_en,
                    address,
                    city,
                    country,
                    latitude,
                    longitude,
                    star_rating,
                    amenities,
                    legal_info,
                    check_in_time,
                    check_out_time,
                    cancellation_policy,
                    quiet_hours,
                    alcohol_allowed,
                    pets_allowed,
                    currency,
                    timezone,
                    photos,
                    is_active,
                    is_testing,
                    is_verified,
                    verification_status,
                    created_at,
                    updated_at
                ) VALUES (
                    gen_random_uuid(),
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                RETURNING id, guid
                """,
                [
                    int(organization_id),
                    values.get("partner_user_id"),
                    values.get("name"),
                    values.get("description_uz"),
                    values.get("description_ru"),
                    values.get("description_en"),
                    values.get("address"),
                    values.get("city"),
                    values.get("country"),
                    values.get("latitude"),
                    values.get("longitude"),
                    values.get("star_rating"),
                    values.get("amenities") or [],
                    _json.dumps(values.get("legal_info") or {}),
                    values.get("check_in_time"),
                    values.get("check_out_time"),
                    values.get("cancellation_policy"),
                    values.get("quiet_hours", True),
                    values.get("alcohol_allowed", False),
                    values.get("pets_allowed", False),
                    values.get("currency") or "USD",
                    values.get("timezone") or "Asia/Tashkent",
                    values.get("photos") or [],
                    values.get("is_active", True),
                    values.get("is_testing", False),
                    values.get("is_verified", False),
                    values.get("verification_status", "waiting"),
                    now,
                    now,
                ],
            )
            row = cursor.fetchone()
            if not row:
                return None
            return {"id": int(row[0]), "guid": str(row[1])}

    created = _run_in_schema(safe_schema, _insert)
    if not created:
        return None
    return get_admin_hotel(str(created["guid"]))


def update_admin_hotel(*, hotel_guid: str, values: dict[str, Any]) -> dict[str, Any] | None:
    resolved = _resolve_hotel_from_guid(hotel_guid)
    if not resolved:
        return None
    schema_name, identifier = resolved

    if isinstance(identifier, int):
        organization = get_organization_by_schema(schema_name)
    else:
        rows = _find_hotel_by_guid_across_schemas(
            identifier, include_inactive=True, include_unverified=True
        )
        if not rows:
            return None
        schema_name = rows[0].get("tenant_schema") or ""
        organization = get_organization_by_schema(schema_name)
    if not organization:
        return None
    allowed_columns = {
        "name",
        "description_uz",
        "description_ru",
        "description_en",
        "address",
        "city",
        "country",
        "latitude",
        "longitude",
        "star_rating",
        "amenities",
        "legal_info",
        "check_in_time",
        "check_out_time",
        "cancellation_policy",
        "quiet_hours",
        "alcohol_allowed",
        "pets_allowed",
        "currency",
        "timezone",
        "photos",
        "is_active",
        "is_testing",
        "is_verified",
        "is_archived",
        "is_recommended",
        "verification_status",
        "partner_user_id",
    }
    filtered_values = {}
    for key, value in values.items():
        if key not in allowed_columns:
            continue
        if key == "legal_info" and isinstance(value, dict):
            filtered_values[key] = _json.dumps(value)
        else:
            filtered_values[key] = value
    filtered_values["organization_id"] = organization["id"]
    if not filtered_values:
        return get_admin_hotel(hotel_guid)

    set_parts = []
    params: list[Any] = []
    for key, value in filtered_values.items():
        set_parts.append(f"{key} = %s")
        params.append(value)
    set_parts.append("updated_at = %s")
    params.append(timezone.now())
    params.append(identifier)
    where_clause = "id = %s" if isinstance(identifier, int) else "guid::text = %s"

    def _update() -> bool:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE pms_property
                SET {", ".join(set_parts)}
                WHERE {where_clause}
                """,
                params,
            )
            return cursor.rowcount > 0

    updated = _run_in_schema(schema_name, _update)
    if not updated:
        return None
    return get_admin_hotel(hotel_guid)


def delete_admin_hotel(*, hotel_guid: str) -> bool:
    resolved = _resolve_hotel_from_guid(hotel_guid)
    if not resolved:
        return False
    schema_name, identifier = resolved

    if isinstance(identifier, str):
        rows = _find_hotel_by_guid_across_schemas(
            identifier, include_inactive=True, include_unverified=True
        )
        if not rows:
            return False
        schema_name = rows[0].get("tenant_schema") or ""
    where_clause = "id = %s" if isinstance(identifier, int) else "guid::text = %s"

    def _delete() -> bool:
        with connection.cursor() as cursor:
            cursor.execute(
                f"DELETE FROM pms_property WHERE {where_clause}",
                [identifier],
            )
            return cursor.rowcount > 0

    return _run_in_schema(schema_name, _delete)


def admin_append_hotel_images(*, hotel_guid: str, image_paths: list[str]) -> dict[str, Any] | None:
    current = get_admin_hotel(hotel_guid)
    if not current:
        return None
    existing = current.get("photos") or current.get("img") or []
    next_images = [str(value) for value in existing if value] + [str(value) for value in image_paths if value]
    return update_admin_hotel(hotel_guid=hotel_guid, values={"photos": next_images})


def admin_remove_hotel_image(*, hotel_guid: str, image_path: str) -> dict[str, Any] | None:
    current = get_admin_hotel(hotel_guid)
    if not current:
        return None
    existing = current.get("photos") or current.get("img") or []
    normalized = str(image_path or "").strip()
    next_images = [str(value) for value in existing if str(value) != normalized]
    if len(next_images) == len(existing):
        return None
    return update_admin_hotel(hotel_guid=hotel_guid, values={"photos": next_images})


def list_hotel_favorites(
    favorite_guids: set[str],
) -> list[dict[str, Any]]:
    """Fetch full hotel rows for a set of encoded hotel GUIDs."""
    by_schema: dict[str, list[int]] = {}
    uuid_guids: list[str] = []

    for guid in favorite_guids:
        decoded = decode_hotel_guid(guid)
        if not decoded:
            continue
        schema_name, identifier = decoded
        if schema_name is not None:
            by_schema.setdefault(schema_name, []).append(int(identifier))
        else:
            uuid_guids.append(identifier)
    results: list[dict[str, Any]] = []
    org_cache: dict[str, dict[str, Any] | None] = {}

    for schema_name, hotel_ids in by_schema.items():
        org = org_cache.get(schema_name)
        if org is None:
            org = get_organization_by_schema(schema_name)
            org_cache[schema_name] = org
        if not org:
            continue
        for hid in hotel_ids:
            try:
                rows = _fetch_hotel_rows_for_schema(schema_name, hotel_id=hid)
                if rows:
                    results.append(_serialize_hotel_row(rows[0], org))
            except Exception:
                continue

    for uuid_guid in uuid_guids:
        try:
            rows = _find_hotel_by_guid_across_schemas(
                uuid_guid, include_inactive=True, include_unverified=True
            )
            if rows:
                row = rows[0]
                schema_name = row.get("tenant_schema") or ""
                org = get_organization_by_schema(schema_name)
                results.append(_serialize_hotel_row(row, org))
        except Exception:
            continue
    return results
