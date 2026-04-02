from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from django.utils import timezone

from shared.raw.db import execute, fetch_all, fetch_one


PROPERTY_GUID_EXPR = "COALESCE(a.guid, c.guid)"
PROPERTY_TITLE_EXPR = "COALESCE(a.title, c.title)"
PROPERTY_IMAGE_EXPR = "COALESCE(a.img, c.img)"
PROPERTY_PARTNER_EXPR = "COALESCE(a.partner_user_id, c.partner_user_id)"
PROPERTY_ARCHIVED_EXPR = "COALESCE(a.is_archived, c.is_archived, FALSE)"
PROPERTY_KIND_EXPR = "CASE WHEN s.property_apartment_id IS NOT NULL THEN 'apartment' ELSE 'cottage' END"
PROPERTY_TYPE_LABEL_EXPR = (
    "CASE WHEN s.property_apartment_id IS NOT NULL THEN 'Apartment' ELSE 'Cottages' END"
)

STORY_SELECT = f"""
    SELECT
        s.*,
        {PROPERTY_GUID_EXPR} AS property_guid,
        {PROPERTY_TITLE_EXPR} AS property_title,
        {PROPERTY_IMAGE_EXPR} AS property_img,
        {PROPERTY_PARTNER_EXPR} AS partner_user_id,
        {PROPERTY_ARCHIVED_EXPR} AS property_is_archived,
        {PROPERTY_KIND_EXPR} AS property_kind,
        {PROPERTY_TYPE_LABEL_EXPR} AS property_type_label
    FROM public.stories s
    LEFT JOIN public.apartment a ON a.id = s.property_apartment_id
    LEFT JOIN public.cottage c ON c.id = s.property_cottage_id
"""


def parse_property_kind(raw_value: str | None) -> str | None:
    if not raw_value:
        return None

    normalized = str(raw_value).strip().lower()
    if normalized in {"apartment", "apartments"}:
        return "apartment"
    if normalized in {"cottage", "cottages", "dacha"}:
        return "cottage"
    return None


def _attach_media(stories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not stories:
        return stories

    story_ids = [int(story["id"]) for story in stories]
    media_rows = fetch_all(
        """
        SELECT *
        FROM public.story_media
        WHERE story_id = ANY(%s)
        ORDER BY id ASC
        """,
        [story_ids],
    )

    media_by_story: dict[int, list[dict[str, Any]]] = {story_id: [] for story_id in story_ids}
    for row in media_rows:
        story_id = int(row["story_id"])
        media_by_story.setdefault(story_id, []).append(row)

    for story in stories:
        story["media"] = media_by_story.get(int(story["id"]), [])
    return stories


def list_active_stories(
    *,
    partner_user_id: int | None = None,
    public_only: bool = False,
    property_kind: str | None = None,
    exclude_archived: bool = True,
) -> list[dict[str, Any]]:
    where = ["s.expires_at > %s"]
    params: list[Any] = [timezone.now()]
    if exclude_archived:
        where.append(f"{PROPERTY_ARCHIVED_EXPR} = FALSE")

    if public_only:
        where.append("s.is_verified = TRUE")
    if partner_user_id is not None:
        where.append(f"{PROPERTY_PARTNER_EXPR} = %s")
        params.append(partner_user_id)
    if property_kind == "apartment":
        where.append("s.property_apartment_id IS NOT NULL")
    elif property_kind == "cottage":
        where.append("s.property_cottage_id IS NOT NULL")

    rows = fetch_all(
        f"""
        {STORY_SELECT}
        WHERE {' AND '.join(where)}
        ORDER BY s.uploaded_at DESC, s.id DESC
        """,
        params,
    )
    return _attach_media(rows)


def get_story_by_guid(story_guid: uuid.UUID | str, *, active_only: bool = True) -> dict[str, Any] | None:
    where = ["s.guid = %s"]
    params: list[Any] = [story_guid]
    if active_only:
        where.append("s.expires_at > %s")
        params.append(timezone.now())

    row = fetch_one(
        f"""
        {STORY_SELECT}
        WHERE {' AND '.join(where)}
        ORDER BY s.uploaded_at DESC, s.id DESC
        LIMIT 1
        """,
        params,
    )
    if row is None:
        return None
    _attach_media([row])
    return row


def get_story_media_by_guid(story_id: int, media_guid: uuid.UUID | str) -> dict[str, Any] | None:
    return fetch_one(
        """
        SELECT *
        FROM public.story_media
        WHERE story_id = %s
          AND guid = %s
        LIMIT 1
        """,
        [story_id, media_guid],
    )


def get_owned_property_by_guid(
    *,
    partner_user_id: int,
    property_guid: uuid.UUID | str,
) -> dict[str, Any] | None:
    return fetch_one(
        """
        SELECT *
        FROM (
            SELECT
                'apartment'::text AS property_kind,
                id,
                guid,
                title,
                img
            FROM public.apartment
            WHERE guid = %s
              AND partner_user_id = %s

            UNION ALL

            SELECT
                'cottage'::text AS property_kind,
                id,
                guid,
                title,
                img
            FROM public.cottage
            WHERE guid = %s
              AND partner_user_id = %s
        ) p
        LIMIT 1
        """,
        [property_guid, partner_user_id, property_guid, partner_user_id],
    )


def get_active_story_for_property(property_kind: str, property_id: int) -> dict[str, Any] | None:
    if property_kind == "apartment":
        where = "s.property_apartment_id = %s"
    elif property_kind == "cottage":
        where = "s.property_cottage_id = %s"
    else:
        return None

    return fetch_one(
        f"""
        SELECT *
        FROM public.stories s
        WHERE {where}
          AND s.expires_at > %s
        ORDER BY s.uploaded_at DESC, s.id DESC
        LIMIT 1
        """,
        [property_id, timezone.now()],
    )


def create_story_for_property(property_kind: str, property_id: int) -> dict[str, Any]:
    now = timezone.now()
    expires_at = now + timedelta(hours=48)
    story_guid = uuid.uuid4()

    apartment_id = property_id if property_kind == "apartment" else None
    cottage_id = property_id if property_kind == "cottage" else None

    row = fetch_one(
        """
        INSERT INTO public.stories (
            guid,
            created_at,
            updated_at,
            is_verified,
            expires_at,
            views,
            uploaded_at,
            property_apartment_id,
            property_cottage_id
        ) VALUES (%s, %s, %s, FALSE, %s, 0, %s, %s, %s)
        RETURNING *
        """,
        [story_guid, now, now, expires_at, now, apartment_id, cottage_id],
    )
    if row is None:
        raise RuntimeError("Failed to create story")
    return row


def create_story_media(*, story_id: int, media_path: str, media_type: str) -> dict[str, Any]:
    now = timezone.now()
    row = fetch_one(
        """
        INSERT INTO public.story_media (
            guid,
            created_at,
            updated_at,
            media,
            media_type,
            story_id
        ) VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        [uuid.uuid4(), now, now, media_path, media_type, story_id],
    )
    if row is None:
        raise RuntimeError("Failed to create story media")
    return row


def delete_story_for_partner(story_guid: uuid.UUID | str, partner_user_id: int) -> int:
    return execute(
        f"""
        DELETE FROM public.stories s
        USING public.apartment a
        WHERE s.guid = %s
          AND s.expires_at > %s
          AND s.property_apartment_id = a.id
          AND a.partner_user_id = %s
        """,
        [story_guid, timezone.now(), partner_user_id],
    ) + execute(
        f"""
        DELETE FROM public.stories s
        USING public.cottage c
        WHERE s.guid = %s
          AND s.expires_at > %s
          AND s.property_cottage_id = c.id
          AND c.partner_user_id = %s
        """,
        [story_guid, timezone.now(), partner_user_id],
    )


def delete_story_media(story_id: int, media_guid: uuid.UUID | str) -> int:
    return execute(
        """
        DELETE FROM public.story_media
        WHERE story_id = %s
          AND guid = %s
        """,
        [story_id, media_guid],
    )


def increment_story_views(story_guid: uuid.UUID | str, increment_by: int) -> int:
    return execute(
        """
        UPDATE public.stories
        SET views = COALESCE(views, 0) + %s,
            updated_at = %s
        WHERE guid = %s
        """,
        [increment_by, timezone.now(), story_guid],
    )
