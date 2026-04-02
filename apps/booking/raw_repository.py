from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from django.utils import timezone

from shared.raw.db import execute, fetch_all, fetch_one


def get_verified_property_by_guid(property_guid: str) -> dict[str, Any] | None:
    return fetch_one(
        """
        SELECT *
        FROM (
            SELECT
                'apartment'::text AS property_kind,
                id AS property_id,
                guid,
                partner_user_id,
                is_verified
            FROM public.apartment
            WHERE guid = %s

            UNION ALL

            SELECT
                'cottage'::text AS property_kind,
                id AS property_id,
                guid,
                partner_user_id,
                is_verified
            FROM public.cottage
            WHERE guid = %s
        ) p
        WHERE COALESCE(is_verified, FALSE) = TRUE
        LIMIT 1
        """,
        [property_guid, property_guid],
    )


def _property_columns(property_kind: str) -> tuple[str, str]:
    if property_kind == "apartment":
        return "property_apartment_id", "property_cottage_id"
    if property_kind == "cottage":
        return "property_cottage_id", "property_apartment_id"
    raise ValueError("Invalid property kind")


def fetch_calendar_rows(
    *,
    property_kind: str,
    property_id: int,
    from_date: date,
    to_date: date,
) -> list[dict[str, Any]]:
    main_col, _ = _property_columns(property_kind)
    return fetch_all(
        f"""
        SELECT date, status
        FROM public.calendar
        WHERE {main_col} = %s
          AND date BETWEEN %s AND %s
        ORDER BY date
        """,
        [property_id, from_date, to_date],
    )


def fetch_calendar_dates_by_status(
    *,
    property_kind: str,
    property_id: int,
    from_date: date,
    to_date: date,
    statuses: list[str],
) -> list[date]:
    main_col, _ = _property_columns(property_kind)
    rows = fetch_all(
        f"""
        SELECT date
        FROM public.calendar
        WHERE {main_col} = %s
          AND date BETWEEN %s AND %s
          AND status = ANY(%s)
        ORDER BY date
        """,
        [property_id, from_date, to_date, statuses],
    )
    return [row["date"] for row in rows]


def upsert_calendar_days(
    *,
    property_kind: str,
    property_id: int,
    days: list[date],
    status: str,
) -> None:
    if not days:
        return

    main_col, other_col = _property_columns(property_kind)
    now = timezone.now()
    for day in days:
        execute(
            f"""
            INSERT INTO public.calendar (
                guid,
                created_at,
                updated_at,
                status,
                date,
                {main_col},
                {other_col}
            ) VALUES (%s, %s, %s, %s, %s, %s, NULL)
            ON CONFLICT ({main_col}, date) DO UPDATE
            SET status = EXCLUDED.status,
                updated_at = EXCLUDED.updated_at
            """,
            [uuid.uuid4(), now, now, status, day, property_id],
        )


def delete_calendar_days_by_status(
    *,
    property_kind: str,
    property_id: int,
    from_date: date,
    to_date: date,
    status: str,
) -> int:
    main_col, _ = _property_columns(property_kind)
    return execute(
        f"""
        DELETE FROM public.calendar
        WHERE {main_col} = %s
          AND date BETWEEN %s AND %s
          AND status = %s
        """,
        [property_id, from_date, to_date, status],
    )
