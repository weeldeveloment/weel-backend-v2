from __future__ import annotations

from typing import Any

from django.db import connection

from apps.platform.raw_repository import list_organizations


def _safe_schema_name(schema_name: str | None) -> str | None:
    raw = str(schema_name or "").strip()
    if not raw:
        return None
    if not raw.replace("_", "").isalnum():
        return None
    return raw


def _fetch_hotels_for_schema(schema_name: str) -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute("SET search_path TO %s, public", [schema_name])
        try:
            cursor.execute(
                """
                SELECT
                    p.id,
                    (p.id::text || ':' || %s) AS guid,
                    p.name AS title,
                    COALESCE(p.photos, ARRAY[]::text[]) AS img,
                    NULL::numeric AS price,
                    p.currency,
                    p.latitude::text AS latitude,
                    p.longitude::text AS longitude,
                    p.country,
                    p.city,
                    NULL::integer AS guests,
                    NULL::integer AS rooms,
                    NULL::integer AS beds,
                    NULL::integer AS bathrooms,
                    COALESCE(p.alcohol_allowed, FALSE) AS is_allowed_alcohol,
                    FALSE AS is_allowed_corporate,
                    COALESCE(p.pets_allowed, FALSE) AS is_allowed_pets,
                    COALESCE(p.quiet_hours, TRUE) AS is_quiet_hours,
                    p.created_at,
                    p.updated_at,
                    p.star_rating::float AS average_rating,
                    0 AS comment_count,
                    %s AS tenant_schema
                FROM pms_property p
                WHERE COALESCE(p.is_active, TRUE) = TRUE
                ORDER BY p.created_at DESC, p.id DESC
                """,
                [schema_name, schema_name],
            )
            columns = [col[0] for col in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
        finally:
            cursor.execute("SET search_path TO public")


def list_hotels(*, limit: int | None = None) -> list[dict[str, Any]]:
    organizations = list_organizations()
    rows: list[dict[str, Any]] = []

    for organization in organizations:
        schema_name = _safe_schema_name(organization.get("schema_name"))
        if not schema_name:
            continue
        try:
            tenant_rows = _fetch_hotels_for_schema(schema_name)
        except Exception:
            continue
        rows.extend(tenant_rows)

    rows.sort(
        key=lambda row: (
            row.get("created_at") is None,
            row.get("created_at"),
            row.get("id"),
        ),
        reverse=True,
    )
    if limit is not None:
        rows = rows[:limit]
    return rows
