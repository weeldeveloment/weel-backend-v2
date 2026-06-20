from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time
from typing import Any, TypeVar

from django.db import connection
from django.utils import timezone

from apps.platform.raw_repository import get_organization_by_schema, list_organizations

T = TypeVar("T")


def _safe_schema_name(schema_name: str | None) -> str | None:
    raw = str(schema_name or "").strip()
    if not raw:
        return None
    if not raw.replace("_", "").isalnum():
        return None
    return raw


def encode_hotel_guid(schema_name: str, hotel_id: int | str) -> str:
    return f"{schema_name}:{hotel_id}"


def decode_hotel_guid(hotel_guid: str) -> tuple[str, int] | None:
    raw = str(hotel_guid or "").strip()
    if ":" not in raw:
        return None
    schema_name, raw_id = raw.split(":", 1)
    safe_schema = _safe_schema_name(schema_name)
    if not safe_schema:
        return None
    try:
        hotel_id = int(str(raw_id).strip())
    except (TypeError, ValueError):
        return None
    return safe_schema, hotel_id


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
    payload["guid"] = encode_hotel_guid(tenant_schema, hotel_id)
    payload["title"] = payload.get("name") or payload.get("title") or ""
    payload["img"] = payload.get("photos") or payload.get("img") or []
    payload["price"] = None
    payload["guests"] = None
    payload["rooms"] = None
    payload["beds"] = None
    payload["bathrooms"] = None
    payload["is_allowed_alcohol"] = bool(payload.get("alcohol_allowed", False))
    payload["is_allowed_corporate"] = False
    payload["is_allowed_pets"] = bool(payload.get("pets_allowed", False))
    payload["is_quiet_hours"] = bool(payload.get("quiet_hours", True))
    payload["average_rating"] = (
        float(payload["star_rating"]) if payload.get("star_rating") is not None else None
    )
    payload["comment_count"] = 0
    payload["check_in_time"] = _iso_time(payload.get("check_in_time"))
    payload["check_out_time"] = _iso_time(payload.get("check_out_time"))
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
    include_inactive: bool = False,
) -> list[dict[str, Any]]:
    def _query() -> list[dict[str, Any]]:
        with connection.cursor() as cursor:
            where = []
            params: list[Any] = []
            if hotel_id is not None:
                where.append("p.id = %s")
                params.append(hotel_id)
            if not include_inactive:
                where.append("COALESCE(p.is_active, TRUE) = TRUE")
            where_sql = f"WHERE {' AND '.join(where)}" if where else ""
            cursor.execute(
                f"""
                SELECT
                    p.id,
                    p.organization_id,
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
                    p.created_at,
                    p.updated_at,
                    %s AS tenant_schema
                FROM pms_property p
                {where_sql}
                ORDER BY p.created_at DESC, p.id DESC
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
    return any(raw in str(value or "").lower() for value in haystacks)


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


def list_hotel_organizations() -> list[dict[str, Any]]:
    organizations = []
    for organization in list_organizations():
        schema_name = _safe_schema_name(organization.get("schema_name"))
        if not schema_name:
            continue
        payload = {
            "id": organization.get("id"),
            "name": organization.get("name"),
            "slug": organization.get("slug"),
            "schema_name": schema_name,
        }
        organizations.append(payload)
    return organizations


def list_hotels(*, limit: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for organization in list_hotel_organizations():
        try:
            tenant_rows = _fetch_hotel_rows_for_schema(organization["schema_name"])
        except Exception:
            continue
        rows.extend(
            _serialize_hotel_row(row, organization)
            for row in tenant_rows
        )

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
    normalized_schema = _safe_schema_name(tenant_schema)

    for organization in list_hotel_organizations():
        if organization_id is not None and int(organization["id"]) != int(organization_id):
            continue
        if normalized_schema and organization["schema_name"] != normalized_schema:
            continue
        try:
            tenant_rows = _fetch_hotel_rows_for_schema(
                organization["schema_name"],
                include_inactive=True,
            )
        except Exception:
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
            if not _matches_search(payload, search):
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
    schema_name, hotel_id = decoded
    organization = get_organization_by_schema(schema_name)
    if not organization:
        return None
    rows = _fetch_hotel_rows_for_schema(
        schema_name,
        hotel_id=hotel_id,
        include_inactive=True,
    )
    if not rows:
        return None
    return _serialize_hotel_row(rows[0], organization)


def create_admin_hotel(*, schema_name: str, values: dict[str, Any]) -> dict[str, Any] | None:
    safe_schema = _safe_schema_name(schema_name)
    if not safe_schema:
        return None

    def _insert() -> dict[str, Any] | None:
        with connection.cursor() as cursor:
            now = timezone.now()
            cursor.execute(
                """
                INSERT INTO pms_property (
                    organization_id,
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
                    created_at,
                    updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                RETURNING id
                """,
                [
                    values.get("organization_id"),
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
                    now,
                    now,
                ],
            )
            row = cursor.fetchone()
            if not row:
                return None
            return {"id": int(row[0])}

    created = _run_in_schema(safe_schema, _insert)
    if not created:
        return None
    return get_admin_hotel(encode_hotel_guid(safe_schema, created["id"]))


def update_admin_hotel(*, hotel_guid: str, values: dict[str, Any]) -> dict[str, Any] | None:
    decoded = decode_hotel_guid(hotel_guid)
    if not decoded:
        return None
    schema_name, hotel_id = decoded
    if not values:
        return get_admin_hotel(hotel_guid)

    set_parts = []
    params: list[Any] = []
    for key, value in values.items():
        set_parts.append(f"{key} = %s")
        params.append(value)
    set_parts.append("updated_at = %s")
    params.append(timezone.now())
    params.append(hotel_id)

    def _update() -> bool:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                UPDATE pms_property
                SET {", ".join(set_parts)}
                WHERE id = %s
                """,
                params,
            )
            return cursor.rowcount > 0

    updated = _run_in_schema(schema_name, _update)
    if not updated:
        return None
    return get_admin_hotel(hotel_guid)


def delete_admin_hotel(*, hotel_guid: str) -> bool:
    decoded = decode_hotel_guid(hotel_guid)
    if not decoded:
        return False
    schema_name, hotel_id = decoded

    def _delete() -> bool:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE pms_property
                SET is_active = FALSE, updated_at = %s
                WHERE id = %s
                """,
                [timezone.now(), hotel_id],
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
