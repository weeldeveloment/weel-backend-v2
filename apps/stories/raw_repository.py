from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from django.utils import timezone

from shared.raw.compat import get_table_name, is_postgresql, return_star, case_insensitive_like_sql
from shared.raw.db import execute, fetch_all, fetch_one


PROPERTY_GUID_EXPR = "COALESCE(a.guid, c.guid)"
PROPERTY_TITLE_EXPR = "COALESCE(a.title, c.title)"
PROPERTY_IMAGE_EXPR = "COALESCE(NULLIF(a.img[1], ''), NULLIF(c.img[1], ''))"
PROPERTY_PARTNER_EXPR = "COALESCE(a.partner_user_id, c.partner_user_id)"
PROPERTY_ARCHIVED_EXPR = "COALESCE(a.is_archived, c.is_archived, FALSE)"
PROPERTY_VERIFIED_EXPR = "COALESCE(a.is_verified, c.is_verified, FALSE)"
PROPERTY_KIND_EXPR = (
    "CASE WHEN s.is_platform_news THEN 'platform_news' "
    "WHEN s.property_apartment_id IS NOT NULL THEN 'apartment' "
    "ELSE 'cottage' END"
)
PROPERTY_TYPE_LABEL_EXPR = (
    "CASE WHEN s.is_platform_news THEN 'Platform News' "
    "WHEN s.property_apartment_id IS NOT NULL THEN 'Apartment' "
    "ELSE 'Cottages' END"
)

STORY_SELECT = f"""
    SELECT
        s.*,
        {PROPERTY_GUID_EXPR} AS property_guid,
        {PROPERTY_TITLE_EXPR} AS property_title,
        {PROPERTY_IMAGE_EXPR} AS property_img,
        {PROPERTY_PARTNER_EXPR} AS partner_user_id,
        {PROPERTY_ARCHIVED_EXPR} AS property_is_archived,
        {PROPERTY_VERIFIED_EXPR} AS property_is_verified,
        {PROPERTY_KIND_EXPR} AS property_kind,
        {PROPERTY_TYPE_LABEL_EXPR} AS property_type_label,
        COALESCE(s.is_platform_news, FALSE) AS is_platform_news,
        s.title AS news_title,
        s.body AS news_body
    FROM {get_table_name("stories")} s
    LEFT JOIN {get_table_name("apartment")} a ON a.id = s.property_apartment_id
    LEFT JOIN {get_table_name("cottage")} c ON c.id = s.property_cottage_id
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
    if is_postgresql():
        media_rows = fetch_all(
            f"""
            SELECT *
            FROM {get_table_name("story_media")}
            WHERE story_id = ANY(%s)
            ORDER BY id ASC
            """,
            [story_ids],
        )
    else:
        placeholders = ','.join(['%s'] * len(story_ids))
        media_rows = fetch_all(
            f"""
            SELECT *
            FROM {get_table_name("story_media")}
            WHERE story_id IN ({placeholders})
            ORDER BY id ASC
            """,
            story_ids,
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
    include_news: bool = True,
) -> list[dict[str, Any]]:
    where = ["(s.expires_at > %s OR s.expires_at IS NULL)"]
    params: list[Any] = [timezone.now()]
    if exclude_archived:
        where.append(f"{PROPERTY_ARCHIVED_EXPR} = FALSE")

    if public_only:
        where.append("s.is_verified = TRUE")
    if partner_user_id is not None:
        where.append(f"{PROPERTY_PARTNER_EXPR} = %s")
        params.append(partner_user_id)

    if property_kind == "apartment":
        if include_news:
            where.append("(s.property_apartment_id IS NOT NULL OR s.is_platform_news = TRUE)")
        else:
            where.append("s.property_apartment_id IS NOT NULL")
    elif property_kind == "cottage":
        if include_news:
            where.append("(s.property_cottage_id IS NOT NULL OR s.is_platform_news = TRUE)")
        else:
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
        f"""
        SELECT *
        FROM {get_table_name("story_media")}
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
        f"""
        SELECT *
        FROM (
            SELECT
                'apartment' AS property_kind,
                id,
                guid,
                title,
                img
            FROM {get_table_name("apartment")}
            WHERE guid = %s
              AND partner_user_id = %s

            UNION ALL

            SELECT
                'cottage' AS property_kind,
                id,
                guid,
                title,
                img
            FROM {get_table_name("cottage")}
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
        FROM {get_table_name("stories")} s
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
        f"""
        INSERT INTO {get_table_name("stories")} (
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
        {"RETURNING *" if return_star() else ""}
        """,
        [story_guid, now, now, expires_at, now, apartment_id, cottage_id],
    )
    if row is None and not return_star():
        row = fetch_one(
            f"SELECT * FROM {get_table_name('stories')} WHERE guid = %s ORDER BY id DESC LIMIT 1",
            [story_guid],
        )
    if row is None:
        raise RuntimeError("Failed to create story")
    return row


def create_story_media(*, story_id: int, media_path: str, media_type: str) -> dict[str, Any]:
    now = timezone.now()
    media_guid = uuid.uuid4()
    row = fetch_one(
        f"""
        INSERT INTO {get_table_name("story_media")} (
            guid,
            created_at,
            updated_at,
            media,
            media_type,
            story_id
        ) VALUES (%s, %s, %s, %s, %s, %s)
        {"RETURNING *" if return_star() else ""}
        """,
        [media_guid, now, now, media_path, media_type, story_id],
    )
    if row is None and not return_star():
        row = fetch_one(
            f"SELECT * FROM {get_table_name('story_media')} WHERE guid = %s ORDER BY id DESC LIMIT 1",
            [media_guid],
        )
    if row is None:
        raise RuntimeError("Failed to create story media")
    return row


def delete_story_for_partner(story_guid: uuid.UUID | str, partner_user_id: int) -> int:
    return execute(
        f"""
        DELETE FROM {get_table_name("stories")} s
        USING {get_table_name("apartment")} a
        WHERE s.guid = %s
          AND s.expires_at > %s
          AND s.property_apartment_id = a.id
          AND a.partner_user_id = %s
        """,
        [story_guid, timezone.now(), partner_user_id],
    ) + execute(
        f"""
        DELETE FROM {get_table_name("stories")} s
        USING {get_table_name("cottage")} c
        WHERE s.guid = %s
          AND s.expires_at > %s
          AND s.property_cottage_id = c.id
          AND c.partner_user_id = %s
        """,
        [story_guid, timezone.now(), partner_user_id],
    )


def delete_story_media(story_id: int, media_guid: uuid.UUID | str) -> int:
    return execute(
        f"""
        DELETE FROM {get_table_name("story_media")}
        WHERE story_id = %s
          AND guid = %s
        """,
        [story_id, media_guid],
    )


def increment_story_views(story_guid: uuid.UUID | str, increment_by: int) -> int:
    return execute(
        f"""
        UPDATE {get_table_name("stories")}
        SET views = COALESCE(views, 0) + %s,
            updated_at = %s
        WHERE guid = %s
        """,
        [increment_by, timezone.now(), story_guid],
    )


# ── Platform News helpers ────────────────────────────────────────────


def create_platform_news(*, title: str, body: str, admin_user_id: int) -> dict[str, Any]:
    now = timezone.now()
    story_guid = uuid.uuid4()
    row = fetch_one(
        f"""
        INSERT INTO {get_table_name("stories")} (
            guid,
            created_at,
            updated_at,
            is_verified,
            verified_by_user_id,
            verified_at,
            is_platform_news,
            title,
            body,
            views,
            uploaded_at
        ) VALUES (%s, %s, %s, TRUE, %s, %s, TRUE, %s, %s, 0, %s)
        {"RETURNING *" if return_star() else ""}
        """,
        [story_guid, now, now, admin_user_id, now, title, body, now],
    )
    if row is None and not return_star():
        row = fetch_one(
            f"SELECT * FROM {get_table_name('stories')} WHERE guid = %s ORDER BY id DESC LIMIT 1",
            [story_guid],
        )
    if row is None:
        raise RuntimeError("Failed to create platform news")
    return row


def get_platform_news_by_guid(news_guid: uuid.UUID | str) -> dict[str, Any] | None:
    row = fetch_one(
        f"""
        {STORY_SELECT}
        WHERE s.guid = %s AND s.is_platform_news = TRUE
        LIMIT 1
        """,
        [news_guid],
    )
    if row is None:
        return None
    _attach_media([row])
    return row


def count_platform_news_for_admin(
    *,
    search: str | None = None,
) -> int:
    params: list[Any] = []
    search_sql = ""
    if search:
        term = f"%{search.strip()}%"
        search_sql = (
            " AND ("
            f"{case_insensitive_like_sql('s.title', '%s')} "
            f"OR {case_insensitive_like_sql('s.body', '%s')} "
            f"OR {case_insensitive_like_sql('s.guid::text', '%s')}"
            ")"
        )
        params.extend([term, term, term])

    row = fetch_one(
        f"""
        SELECT COUNT(*) AS total
        FROM {get_table_name("stories")} s
        WHERE s.is_platform_news = TRUE{search_sql}
        """,
        params,
    )
    return int(row["total"]) if row else 0


def list_platform_news_for_admin(
    *,
    search: str | None = None,
    ordering: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    params: list[Any] = []
    search_sql = ""
    if search:
        term = f"%{search.strip()}%"
        search_sql = (
            " AND ("
            f"{case_insensitive_like_sql('s.title', '%s')} "
            f"OR {case_insensitive_like_sql('s.body', '%s')} "
            f"OR {case_insensitive_like_sql('s.guid::text', '%s')}"
            ")"
        )
        params.extend([term, term, term])

    order_field = "s.created_at"
    order_direction = "DESC"
    if ordering:
        direction = "DESC" if ordering.startswith("-") else "ASC"
        field_raw = ordering.lstrip("-+").strip()
        allowed = {"created_at", "updated_at", "views"}
        if field_raw in allowed:
            order_field = f"s.{field_raw}"
            order_direction = direction

    params.extend([limit, offset])

    rows = fetch_all(
        f"""
        {STORY_SELECT}
        WHERE s.is_platform_news = TRUE{search_sql}
        ORDER BY {order_field} {order_direction}, s.id {order_direction}
        LIMIT %s OFFSET %s
        """,
        params,
    )
    return _attach_media(rows)


def update_platform_news(
    news_guid: uuid.UUID | str,
    *,
    title: str | None = None,
    body: str | None = None,
) -> dict[str, Any] | None:
    set_parts: list[str] = ["updated_at = %s"]
    params: list[Any] = [timezone.now()]
    if title is not None:
        set_parts.append("title = %s")
        params.append(title)
    if body is not None:
        set_parts.append("body = %s")
        params.append(body)
    params.append(news_guid)

    row = fetch_one(
        f"""
        UPDATE {get_table_name("stories")}
        SET {', '.join(set_parts)}
        WHERE guid = %s AND is_platform_news = TRUE
        {"RETURNING *" if return_star() else ""}
        """,
        params,
    )
    if row is None and not return_star():
        row = fetch_one(
            f"SELECT * FROM {get_table_name('stories')} WHERE guid = %s AND is_platform_news = TRUE LIMIT 1",
            [news_guid],
        )
    return row


def delete_platform_news_by_guid(news_guid: uuid.UUID | str) -> int:
    return execute(
        f"""
        DELETE FROM {get_table_name("stories")}
        WHERE guid = %s AND is_platform_news = TRUE
        """,
        [news_guid],
    )


def add_news_media(*, story_id: int, media_path: str, media_type: str) -> dict[str, Any]:
    return create_story_media(story_id=story_id, media_path=media_path, media_type=media_type)


# ── Banner helpers ──────────────────────────────────────────────────


def create_banner(*, html_source: str, image: str) -> dict[str, Any]:
    now = timezone.now()
    banner_guid = uuid.uuid4()
    row = fetch_one(
        f"""
        INSERT INTO {get_table_name("banners")} (
            guid, html_source, image, created_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s)
        {"RETURNING *" if return_star() else ""}
        """,
        [banner_guid, html_source, image, now, now],
    )
    if row is None and not return_star():
        row = fetch_one(
            f"SELECT * FROM {get_table_name('banners')} WHERE guid = %s ORDER BY id DESC LIMIT 1",
            [banner_guid],
        )
    if row is None:
        raise RuntimeError("Failed to create banner")
    return row


def get_banner_by_guid(banner_guid: uuid.UUID | str) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        SELECT * FROM {get_table_name("banners")}
        WHERE guid = %s
        LIMIT 1
        """,
        [banner_guid],
    )


def count_banners(*, search: str | None = None) -> int:
    params: list[Any] = []
    search_sql = ""
    if search:
        term = f"%{search.strip()}%"
        search_sql = (
            " AND ("
            f"{case_insensitive_like_sql('html_source', '%s')} "
            f"OR {case_insensitive_like_sql('guid::text', '%s')}"
            ")"
        )
        params.extend([term, term])

    row = fetch_one(
        f"""
        SELECT COUNT(*) AS total
        FROM {get_table_name("banners")}
        WHERE TRUE{search_sql}
        """,
        params,
    )
    return int(row["total"]) if row else 0


def list_banners(
    *,
    search: str | None = None,
    ordering: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    params: list[Any] = []
    search_sql = ""
    if search:
        term = f"%{search.strip()}%"
        search_sql = (
            " AND ("
            f"{case_insensitive_like_sql('html_source', '%s')} "
            f"OR {case_insensitive_like_sql('guid::text', '%s')}"
            ")"
        )
        params.extend([term, term])

    order_field = "created_at"
    order_direction = "DESC"
    if ordering:
        direction = "DESC" if ordering.startswith("-") else "ASC"
        field_raw = ordering.lstrip("-+").strip()
        allowed = {"created_at", "updated_at"}
        if field_raw in allowed:
            order_field = field_raw
            order_direction = direction

    params.extend([limit, offset])

    return fetch_all(
        f"""
        SELECT * FROM {get_table_name("banners")}
        WHERE TRUE{search_sql}
        ORDER BY {order_field} {order_direction}, id {order_direction}
        LIMIT %s OFFSET %s
        """,
        params,
    )


def update_banner(
    banner_guid: uuid.UUID | str,
    *,
    html_source: str | None = None,
    image: str | None = None,
) -> dict[str, Any] | None:
    set_parts: list[str] = ["updated_at = %s"]
    params: list[Any] = [timezone.now()]
    if html_source is not None:
        set_parts.append("html_source = %s")
        params.append(html_source)
    if image is not None:
        set_parts.append("image = %s")
        params.append(image)
    params.append(banner_guid)

    row = fetch_one(
        f"""
        UPDATE {get_table_name("banners")}
        SET {', '.join(set_parts)}
        WHERE guid = %s
        {"RETURNING *" if return_star() else ""}
        """,
        params,
    )
    if row is None and not return_star():
        row = fetch_one(
            f"SELECT * FROM {get_table_name('banners')} WHERE guid = %s LIMIT 1",
            [banner_guid],
        )
    return row


def delete_banner_by_guid(banner_guid: uuid.UUID | str) -> int:
    return execute(
        f"""
        DELETE FROM {get_table_name("banners")}
        WHERE guid = %s
        """,
        [banner_guid],
    )


# ── Admin moderation helpers ──────────────────────────────────────────


def count_stories_for_admin(
    *,
    is_verified: bool | None = None,
    search: str | None = None,
) -> int:
    where_clauses: list[str] = []
    params: list[Any] = []

    if is_verified is not None:
        where_clauses.append("s.is_verified = %s")
        params.append(is_verified)

    search_sql = ""
    if search:
        term = f"%{search.strip()}%"
        search_sql = (
            " AND ("
            f"{case_insensitive_like_sql('COALESCE(a.title, c.title)', '%s')} "
            f"OR {case_insensitive_like_sql('COALESCE(a.partner_user_id::text, c.partner_user_id::text)', '%s')} "
            f"OR {case_insensitive_like_sql('s.guid::text', '%s')}"
            ")"
        )
        params.extend([term, term, term])

    where = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    row = fetch_one(
        f"""
        SELECT COUNT(*) AS total
        FROM {get_table_name("stories")} s
        LEFT JOIN {get_table_name("apartment")} a ON a.id = s.property_apartment_id
        LEFT JOIN {get_table_name("cottage")} c ON c.id = s.property_cottage_id
        {where}{search_sql}
        """,
        params,
    )
    return int(row["total"]) if row else 0


def list_stories_for_admin(
    *,
    is_verified: bool | None = None,
    search: str | None = None,
    ordering: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    where_clauses: list[str] = []
    params: list[Any] = []

    if is_verified is not None:
        where_clauses.append("s.is_verified = %s")
        params.append(is_verified)

    search_sql = ""
    if search:
        term = f"%{search.strip()}%"
        search_sql = (
            " AND ("
            f"{case_insensitive_like_sql('COALESCE(a.title, c.title)', '%s')} "
            f"OR {case_insensitive_like_sql('COALESCE(a.partner_user_id::text, c.partner_user_id::text)', '%s')} "
            f"OR {case_insensitive_like_sql('s.guid::text', '%s')}"
            ")"
        )
        params.extend([term, term, term])

    where = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    order_field = "s.created_at"
    order_direction = "DESC"
    if ordering:
        direction = "DESC" if ordering.startswith("-") else "ASC"
        field_raw = ordering.lstrip("-+").strip()
        allowed = {"created_at", "uploaded_at", "expires_at", "views"}
        if field_raw in allowed:
            order_field = f"s.{field_raw}"
            order_direction = direction

    params.extend([limit, offset])

    rows = fetch_all(
        f"""
        {STORY_SELECT}
        {where}{search_sql}
        ORDER BY {order_field} {order_direction}, s.id {order_direction}
        LIMIT %s OFFSET %s
        """,
        params,
    )
    return _attach_media(rows)


def update_story_verification(
    story_guid: uuid.UUID | str,
    *,
    is_verified: bool,
    verified_by_user_id: int | None = None,
) -> dict[str, Any] | None:
    verified_at = timezone.now() if is_verified else None
    row = fetch_one(
        f"""
        UPDATE {get_table_name("stories")}
        SET is_verified = %s,
            verified_by_user_id = %s,
            verified_at = %s,
            updated_at = %s
        WHERE guid = %s
        {"RETURNING *" if return_star() else ""}
        """,
        [is_verified, verified_by_user_id, verified_at, timezone.now(), story_guid],
    )
    if row is None and not return_star():
        row = fetch_one(
            f"SELECT * FROM {get_table_name('stories')} WHERE guid = %s LIMIT 1",
            [story_guid],
        )
    return row


def delete_story_by_guid(story_guid: uuid.UUID | str) -> int:
    return execute(
        f"""
        DELETE FROM {get_table_name("stories")}
        WHERE guid = %s
        """,
        [story_guid],
    )


def reset_stories_verification_for_property(property_id: int, property_kind: str) -> int:
    """Reset is_verified to FALSE for all active stories linked to a property."""
    if property_kind == "apartment":
        where = "property_apartment_id = %s"
    elif property_kind == "cottage":
        where = "property_cottage_id = %s"
    else:
        return 0
    return execute(
        f"""
        UPDATE {get_table_name("stories")}
        SET is_verified = FALSE,
            verified_by_user_id = NULL,
            verified_at = NULL,
            updated_at = %s
        WHERE {where}
          AND expires_at > %s
        """,
        [timezone.now(), property_id, timezone.now()],
    )
