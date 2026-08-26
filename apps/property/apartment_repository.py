from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any
from urllib.parse import urlparse
from uuid import UUID, uuid4

from django.core.cache import cache
from django.utils import timezone
from django.db.utils import ProgrammingError

from payment.exchange_rate import exchange_rate
from shared.raw.compat import get_table_name, is_postgresql
from shared.raw.db import execute, fetch_all, fetch_one, table_exists
from property.models import VerificationStatus
from property.search_filters import (
    SERVICES_MATCH_ALL,
    append_bbox_filter,
    append_capacity_filters,
    append_flag_filters,
    append_services_filter,
)
from shared.raw.tables import BOOKING_TABLE

APARTMENT_TYPE_GUID = UUID("11111111-1111-1111-1111-111111111111")
COTTAGE_TYPE_GUID = UUID("22222222-2222-2222-2222-222222222222")
HOTEL_TYPE_GUID = UUID("33333333-3333-3333-3333-333333333333")

PROPERTY_KIND_APARTMENT = "apartment"
PROPERTY_KIND_COTTAGE = "cottage"
PROPERTY_KIND_HOTEL = "hotel"
logger = logging.getLogger(__name__)
TYPE_GUID_TO_KIND = {
    str(APARTMENT_TYPE_GUID): PROPERTY_KIND_APARTMENT,
    str(COTTAGE_TYPE_GUID): PROPERTY_KIND_COTTAGE,
    str(HOTEL_TYPE_GUID): PROPERTY_KIND_HOTEL,
}


def _table(*candidates: str) -> str:
    for candidate in candidates:
        try:
            if table_exists(candidate):
                return get_table_name(candidate)
        except Exception:
            # Keep module import safe for Swagger/schema generation when DB is
            # temporarily unavailable. Runtime queries will still fail loudly.
            logger.warning(
                "table resolution skipped for candidate '%s'; falling back",
                candidate,
                exc_info=True,
            )
    # Prefer Django-style prefixed table names when schema isn't ready yet.
    return get_table_name(candidates[-1])


APARTMENT_TABLE = _table("apartment", "property_apartment")
USERS_TABLE = _table("users", "users_user")
REVIEW_TABLE = _table("review", "property_review")
PROPERTY_SERVICE_TABLE = _table("property_service", "property_propertyservice")
REGION_TABLE = _table("region", "property_region")
DISTRICT_TABLE = _table("district", "property_district")
PREFECTURE_TABLE = _table("prefecture", "property_prefecture")
DISTRICT_PREFECTURE_TABLE = _table("district_prefecture")


def parse_property_kind(value: str | UUID | None) -> str | None:
    if value is None:
        return None
    raw = str(value).strip().lower()
    if not raw:
        return None
    if raw in {"apartment", "apartments", "kvartira", "квартира"}:
        return PROPERTY_KIND_APARTMENT
    if raw in {
        "cottage",
        "cottages",
        "dacha",
        "dom",
        "house",
        "guesthouse",
        "guest_house",
        "дача",
        "дачи",
        "дом",
        "гостевой дом",
    }:
        return PROPERTY_KIND_COTTAGE
    if raw in {"hotel", "hotels", "mehmonxona", "отель", "гостиница"}:
        return PROPERTY_KIND_HOTEL
    return TYPE_GUID_TO_KIND.get(raw)


_DEFAULT_TYPE_DATA: list[dict[str, Any]] = [
    {
        "guid": str(COTTAGE_TYPE_GUID),
        "title_en": "Cottage",
        "title_ru": "Дача",
        "title_uz": "Dacha",
        "icon": "property/icons/home-03.svg",
    },
    {
        "guid": str(APARTMENT_TYPE_GUID),
        "title_en": "Apartment",
        "title_ru": "Квартира",
        "title_uz": "Kvartira",
        "icon": "property/icons/building-skyscraper2.svg",
    },
    {
        "guid": str(HOTEL_TYPE_GUID),
        "title_en": "Hotel",
        "title_ru": "Отель",
        "title_uz": "Mehmonxona",
        "icon": "property/icons/building-skyscraper2.svg",
    },
]


def list_property_types(language: str = "uz") -> list[dict[str, Any]]:
    db_icons = _load_db_icons()
    result = []
    for row in _DEFAULT_TYPE_DATA:
        if language == "ru":
            title = row["title_ru"]
        elif language == "en":
            title = row["title_en"]
        else:
            title = row["title_uz"]
        kind = TYPE_GUID_TO_KIND.get(row["guid"], "")
        result.append({
            "guid": row["guid"],
            "title": title,
            "icon_url": db_icons.get(kind) or row["icon"],
            "kind": kind,
        })
    return result


def _load_db_icons() -> dict[str, str]:
    property_type_table = "property_type"
    if not table_exists(property_type_table):
        return {}
    try:
        rows = fetch_all(
            f"""
            SELECT slug, icon_url
            FROM {get_table_name(property_type_table)}
            WHERE slug IN (%s, %s, %s)
              AND icon_url IS NOT NULL
              AND icon_url != ''
            """,
            [PROPERTY_KIND_APARTMENT, PROPERTY_KIND_COTTAGE, PROPERTY_KIND_HOTEL],
        )
    except Exception:
        logger.warning("Failed to load type icons from DB", exc_info=True)
        return {}
    result: dict[str, str] = {}
    for row in rows:
        url = row["icon_url"]
        if url.startswith(("http://", "https://")):
            path = urlparse(url).path
            if path:
                url = path.lstrip("/")
        result[row["slug"]] = url
    return result


def list_property_services(
    language: str = "uz",
    property_type: str | None = None,
) -> list[dict[str, Any]]:
    if table_exists("services"):
        try:
            if language == "ru":
                title_select = "COALESCE(NULLIF(s.title_ru, ''), NULLIF(s.title, ''))"
                order_field = "COALESCE(NULLIF(s.title_ru, ''), NULLIF(s.title, ''))"
            elif language == "en":
                title_select = "s.title"
                order_field = "s.title"
            else:
                title_select = "COALESCE(NULLIF(s.title, ''), NULLIF(s.title_ru, ''))"
                order_field = "COALESCE(NULLIF(s.title, ''), NULLIF(s.title_ru, ''))"

            type_clause = ""
            params: list[Any] = []
            if property_type:
                type_clause = "WHERE %s = ANY(s.type)"
                params.append(property_type)

            query = f"""
                SELECT
                    s.id AS guid,
                    {title_select} AS title,
                    s.icon_url AS icon_url,
                    s.category_key AS category_key
                FROM services s
                {type_clause}
                ORDER BY s.category_key NULLS LAST, {order_field}, s.id
                """
            return fetch_all(query, params if params else None)
        except ProgrammingError:
            pass

    legacy_candidates = [PROPERTY_SERVICE_TABLE, "property_service", "property_propertyservice"]
    seen: set[str] = set()
    for table_name in legacy_candidates:
        table_name = str(table_name).strip()
        if not table_name or table_name in seen:
            continue
        seen.add(table_name)
        if not table_exists(table_name):
            continue
        try:
            if language == "ru":
                legacy_title_select = "COALESCE(NULLIF(ps.title_ru, ''), NULLIF(ps.title_uz, ''), NULLIF(ps.title_en, ''))"
                legacy_order = legacy_title_select
            elif language == "en":
                legacy_title_select = "COALESCE(NULLIF(ps.title_en, ''), NULLIF(ps.title_uz, ''), NULLIF(ps.title_ru, ''))"
                legacy_order = legacy_title_select
            else:
                legacy_title_select = "COALESCE(NULLIF(ps.title_uz, ''), NULLIF(ps.title_ru, ''), NULLIF(ps.title_en, ''))"
                legacy_order = legacy_title_select

            return fetch_all(
                f"""
                SELECT
                    ps.guid,
                    {legacy_title_select} AS title,
                    ps.icon AS icon_url,
                    NULL AS category_key
                FROM {table_name} ps
                ORDER BY {legacy_order}, ps.id
                """
            )
        except ProgrammingError:
            pass

    return []


_AMENITY_SERVICE_CACHE_KEY = "property:amenity-services:{language}"


def amenity_service_lookup(language: str = "uz") -> dict[str, dict[str, str]]:
    """GUID -> service row (`guid`, `title`, `icon_url`) from the `services`
    reference table.

    Amenity columns (`pms_property.amenities`, `pms_room.amenities`,
    apartment/cottage services) store whatever the writing form sent: the admin
    edit forms write raw `services.id` GUIDs, older/demo rows hold titles. Read
    paths therefore have to resolve before handing anything to a client.
    ``icon_url`` is the stored path — callers turn it into an absolute URL.
    """
    cache_key = _AMENITY_SERVICE_CACHE_KEY.format(language=language)
    services = cache.get(cache_key)
    if services is None:
        services = {}
        try:
            for row in list_property_services(language=language):
                guid = str(row.get("guid") or "").strip()
                title = str(row.get("title") or "").strip()
                if guid and title:
                    services[guid] = {
                        "guid": guid,
                        "title": title,
                        "icon_url": str(row.get("icon_url") or "").strip(),
                    }
        except Exception:
            logger.warning("amenity service lookup failed", exc_info=True)
            services = {}
        cache.set(cache_key, services, timeout=600)
    return services


def amenity_title_lookup(language: str = "uz") -> dict[str, str]:
    """GUID -> display title map. See [amenity_service_lookup]."""
    return {
        guid: service["title"]
        for guid, service in amenity_service_lookup(language).items()
    }


def resolve_amenity_services(
    values: list[Any] | None, language: str = "uz"
) -> list[dict[str, str]]:
    """Expand stored amenity values into `{guid, title, icon_url}` rows — the
    shape clients need to render an amenity with its icon rather than bare
    text. Values that aren't known GUIDs (rows that already store a title)
    come back as title-only entries with no guid to look an icon up by."""
    if not values:
        return []
    lookup = amenity_service_lookup(language)
    services: list[dict[str, str]] = []
    for value in values:
        item = str(value).strip()
        if not item:
            continue
        service = lookup.get(item)
        if service is None:
            services.append({"guid": "", "title": item, "icon_url": ""})
        else:
            services.append(dict(service))
    return services


def resolve_amenity_titles(
    values: list[Any] | None, language: str = "uz"
) -> list[str]:
    """Translate stored amenity values into display titles, passing through
    anything that isn't a known GUID (already-a-title rows). Idempotent, so it
    is safe to call on a list another layer may have resolved already."""
    if not values:
        return []
    lookup = amenity_title_lookup(language)
    resolved: list[str] = []
    for value in values:
        item = str(value).strip()
        if not item:
            continue
        resolved.append(lookup.get(item, item))
    return resolved


def list_regions() -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT
            r.id,
            r.guid,
            COALESCE(NULLIF(r.title_uz, ''), NULLIF(r.title_ru, ''), NULLIF(r.title_en, '')) AS title,
            r.img
        FROM {REGION_TABLE} r
        ORDER BY COALESCE(NULLIF(r.title_uz, ''), NULLIF(r.title_ru, ''), NULLIF(r.title_en, '')), r.id
        """
    )


def list_districts(*, region_id: int | None = None) -> list[dict[str, Any]]:
    where = ["1 = 1"]
    params: list[Any] = []
    if region_id is not None:
        where.append("d.region_id = %s")
        params.append(region_id)
    return fetch_all(
        f"""
        SELECT
            d.id,
            d.guid,
            COALESCE(NULLIF(d.title_uz, ''), NULLIF(d.title_ru, ''), NULLIF(d.title_en, '')) AS title,
            d.region_id,
            r.guid AS region_guid,
            COALESCE(NULLIF(r.title_uz, ''), NULLIF(r.title_ru, ''), NULLIF(r.title_en, '')) AS region_title,
            r.img AS region_img
        FROM {DISTRICT_TABLE} d
        LEFT JOIN {REGION_TABLE} r ON r.id = d.region_id
        WHERE {' AND '.join(where)}
        ORDER BY COALESCE(NULLIF(d.title_uz, ''), NULLIF(d.title_ru, ''), NULLIF(d.title_en, '')), d.id
        """,
        params,
    )


def list_prefectures(*, district_id: int | None = None, district_guid: str | None = None) -> list[dict[str, Any]]:
    """List prefectures; optional district_id or district_guid filter."""
    where = ["1 = 1"]
    params: list[Any] = []
    if district_id is not None:
        where.append("dp.district_id = %s")
        params.append(district_id)
    elif district_guid:
        where.append("CAST(d.guid AS TEXT) = %s")
        params.append(str(district_guid))
    return fetch_all(
        f"""
        SELECT
            p.id AS guid,
            COALESCE(NULLIF(p.name, ''), NULLIF(p.ru_name, '')) AS title,
            d.guid AS district_guid,
            COALESCE(NULLIF(d.title_uz, ''), NULLIF(d.title_ru, ''), NULLIF(d.title_en, '')) AS district_title
        FROM {PREFECTURE_TABLE} p
        LEFT JOIN {DISTRICT_PREFECTURE_TABLE} dp ON dp.prefecture_id = p.id
        LEFT JOIN {DISTRICT_TABLE} d ON d.id = dp.district_id
        WHERE {' AND '.join(where)}
        ORDER BY COALESCE(NULLIF(p.name, ''), NULLIF(p.ru_name, '')), p.id
        """,
        params,
    )


def list_prefectures_for_district(district_id: int) -> list[dict[str, Any]]:
    """Backward-compatible; same rows as list_prefectures(district_id=...) for one district."""
    return list_prefectures(district_id=district_id)


def is_prefecture_linked_to_district(*, district_id: int, prefecture_guid: str) -> bool:
    row = fetch_one(
        f"""
        SELECT EXISTS (
            SELECT 1
            FROM {DISTRICT_PREFECTURE_TABLE} dp
            WHERE dp.district_id = %s
              AND CAST(dp.prefecture_id AS TEXT) = %s
        ) AS exists_flag
        """,
        [district_id, str(prefecture_guid)],
    )
    return bool(row and row.get("exists_flag"))


def resolve_region_id_by_guid(region_guid: str | None) -> int | None:
    raw = str(region_guid or "").strip()
    if not raw:
        return None
    row = fetch_one(
        f"SELECT r.id FROM {REGION_TABLE} r WHERE CAST(r.guid AS TEXT) = %s LIMIT 1",
        [raw],
    )
    value = row.get("id") if row else None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def resolve_district_id_by_guid(district_guid: str | None) -> int | None:
    raw = str(district_guid or "").strip()
    if not raw:
        return None
    row = fetch_one(
        f"SELECT d.id FROM {DISTRICT_TABLE} d WHERE CAST(d.guid AS TEXT) = %s LIMIT 1",
        [raw],
    )
    value = row.get("id") if row else None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def resolve_prefecture_id_by_guid(prefecture_guid: str | None) -> str | None:
    raw = str(prefecture_guid or "").strip()
    if not raw:
        return None
    row = fetch_one(
        f"SELECT p.id FROM {PREFECTURE_TABLE} p WHERE CAST(p.id AS TEXT) = %s LIMIT 1",
        [raw],
    )
    return str(row["id"]) if row else None


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
        COALESCE(a.is_testing, FALSE) AS is_testing,
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
        a.region_id,
        rgn.guid AS region_guid,
        COALESCE(NULLIF(rgn.title_uz, ''), NULLIF(rgn.title_ru, ''), NULLIF(rgn.title_en, '')) AS region_name,
        a.district_id,
        dst.guid AS district_guid,
        COALESCE(NULLIF(dst.title_uz, ''), NULLIF(dst.title_ru, ''), NULLIF(dst.title_en, '')) AS district_name,
        a.prefecture_id,
        pref.id AS prefecture_guid,
        COALESCE(NULLIF(pref.name, ''), NULLIF(pref.ru_name, '')) AS prefecture_name,
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
        a.guests AS guests,
        a.rooms AS rooms,
        a.beds AS beds,
        a.bathrooms AS bathrooms,
        u.username AS partner_username,
        u.first_name AS partner_first_name,
        u.last_name AS partner_last_name,
        u.phone_number AS partner_phone_number,
        COALESCE(stats.average_rating, 5.0) AS average_rating,
        COALESCE(stats.review_count, 0) AS review_count
    FROM {APARTMENT_TABLE} a
    LEFT JOIN {REGION_TABLE} rgn ON rgn.id = a.region_id
    LEFT JOIN {DISTRICT_TABLE} dst ON dst.id = a.district_id
    LEFT JOIN {PREFECTURE_TABLE} pref ON CAST(pref.id AS TEXT) = CAST(a.prefecture_id AS TEXT)
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
    testing_only: bool | None = None,
    lat: float | None = None,
    lon: float | None = None,
    radius_km: float = 10.0,
    bbox: tuple[float, float, float, float] | None = None,
    services: list[str] | None = None,
    services_match: str = SERVICES_MATCH_ALL,
    min_guests: int | None = None,
    min_rooms: int | None = None,
    min_beds: int | None = None,
    min_bathrooms: int | None = None,
    allowed_pets: bool | None = None,
    allowed_alcohol: bool | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> list[dict[str, Any]]:
    where = ["1 = 1"]
    params: list[Any] = []

    if not include_all_records:
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
        raw = search.strip()
        like_param = f"%{raw}%"
        where.append(
            "(a.title ILIKE %s"
            " OR COALESCE(a.city, '') ILIKE %s"
            " OR word_similarity(%s, a.title) > 0.3"
            " OR word_similarity(%s, COALESCE(a.city, '')) > 0.3)"
        )
        params.extend([like_param, like_param, raw, raw])
    if lat is not None and lon is not None:
        where.append(
            "(a.latitude IS NOT NULL AND a.longitude IS NOT NULL"
            " AND 6371.0 * 2.0 * asin(sqrt("
            "   power(sin((radians(%s) - radians(a.latitude::float))  / 2.0), 2.0)"
            "   + cos(radians(a.latitude::float)) * cos(radians(%s))"
            "   * power(sin((radians(%s) - radians(a.longitude::float)) / 2.0), 2.0)"
            " )) <= %s)"
        )
        params.extend([lat, lat, lon, radius_km])
    append_bbox_filter(where, params, alias="a", bbox=bbox)
    append_services_filter(
        where, params, alias="a", services=services, match=services_match
    )
    append_capacity_filters(
        where,
        params,
        alias="a",
        min_guests=min_guests,
        min_rooms=min_rooms,
        min_beds=min_beds,
        min_bathrooms=min_bathrooms,
    )
    append_flag_filters(
        where,
        params,
        alias="a",
        allowed_pets=allowed_pets,
        allowed_alcohol=allowed_alcohol,
    )
    if region_id is not None:
        where.append("a.region_id = %s")
        params.append(region_id)
    if district_id is not None:
        where.append("a.district_id = %s")
        params.append(district_id)
    if prefecture_id is not None:
        where.append("CAST(a.prefecture_id AS TEXT) = %s")
        params.append(str(prefecture_id))
    if corporate is not None:
        where.append("COALESCE(a.is_allowed_corporate, FALSE) = %s")
        params.append(bool(corporate))
    if min_price is not None:
        where.append("COALESCE(a.price, 0) >= %s")
        params.append(min_price)
    if max_price is not None:
        where.append("COALESCE(a.price, 0) <= %s")
        params.append(max_price)
    if testing_only is not None:
        where.append("COALESCE(a.is_testing, FALSE) = %s")
        params.append(bool(testing_only))

    return fetch_all(
        f"""
        {APARTMENT_SELECT}
        WHERE {' AND '.join(where)}
        ORDER BY a.created_at DESC, a.id DESC
        {'LIMIT %s' if limit else ''}
        {'OFFSET %s' if offset else ''}
        """,
        params + ([limit] if limit else []) + ([offset] if offset else []),
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
            is_testing,
            comment_count,
            price, currency, img, partner_user_id,
            services,
            latitude, longitude, city, country,
            region_id, district_id, prefecture_id,
            description_en, description_ru, description_uz,
            check_in, check_out,
            is_allowed_alcohol, is_allowed_corporate, is_allowed_pets, is_quiet_hours,
            apartment_number, home_number, entrance_number, floor_number, pass_code,
            guests, rooms, beds, bathrooms
        ) VALUES (
            %s, %s, %s,
            %s, %s,
            FALSE, %s, FALSE, FALSE,
            %s,
            %s,
            %s, %s, %s, %s,
            %s::uuid[],
            %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s
        )
        RETURNING guid
        """,
        [
            uuid4(), now, now,
            values.get("title"), values.get("title_sort"),
            VerificationStatus.WAITING.value,
            bool(values.get("is_testing", False)),
            int(values.get("comment_count", 0)),
            values.get("price"), values.get("currency"), values.get("img"),
            partner_user_id,
            values.get("services") or [],
            values.get("latitude"), values.get("longitude"),
            values.get("city"), values.get("country"),
            values.get("region_id"), values.get("district_id"), values.get("prefecture_id"),
            values.get("description_en"), values.get("description_ru"), values.get("description_uz"),
            values.get("check_in"), values.get("check_out"),
            bool(values.get("is_allowed_alcohol", False)),
            bool(values.get("is_allowed_corporate", False)),
            bool(values.get("is_allowed_pets", False)),
            bool(values.get("is_quiet_hours", False)),
            values.get("apartment_number"), values.get("home_number"),
            values.get("entrance_number"), values.get("floor_number"), values.get("pass_code"),
            values.get("guests"), values.get("rooms"), values.get("beds"), values.get("bathrooms"),
        ],
    )
    if not row:
        return None
    return get_apartment_for_partner(str(row["guid"]), partner_user_id)


_APARTMENT_UPDATE_ALLOWED = {
    "title", "title_sort",
    "price", "currency", "img",
    "latitude", "longitude", "city", "country",
    "region_id", "district_id", "prefecture_id",
    "description_en", "description_ru", "description_uz",
    "check_in", "check_out",
    "is_allowed_alcohol", "is_allowed_corporate", "is_allowed_pets", "is_quiet_hours",
    "apartment_number", "home_number", "entrance_number", "floor_number", "pass_code",
    "guests", "rooms", "beds", "bathrooms",
    "services",
}

_APARTMENT_PRICE_ONLY_FIELDS = {"price"}


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
    if "services" in updates:
        raw_services = updates.get("services") or []
        updates["services"] = [str(service) for service in raw_services if service is not None]
    dropped_keys = sorted(k for k in values.keys() if k not in _APARTMENT_UPDATE_ALLOWED)
    logger.info(
        "apartment_update_normalized apartment_id=%s partner_user_id=%s allowed_keys=%s dropped_keys=%s services_count=%s desc_ru_len=%s desc_uz_len=%s",
        apartment_id,
        partner_user_id,
        sorted(updates.keys()),
        dropped_keys,
        len(updates.get("services") or []),
        len(str(updates.get("description_ru") or "")),
        len(str(updates.get("description_uz") or "")),
    )
    updates["updated_at"] = timezone.now()
    changed_fields = set(updates.keys())
    changed_non_price_fields = changed_fields - _APARTMENT_PRICE_ONLY_FIELDS
    should_send_to_moderation = bool(changed_non_price_fields)
    if should_send_to_moderation:
        updates["is_verified"] = False
        updates["verification_status"] = VerificationStatus.WAITING.value
        from stories.raw_repository import reset_stories_verification_for_property
        reset_stories_verification_for_property(apartment_id, "apartment")

    assignments_parts: list[str] = []
    for col in updates:
        if col == "services" and is_postgresql():
            assignments_parts.append("services = %s::uuid[]")
        else:
            assignments_parts.append(f"{col} = %s")
    assignments = ", ".join(assignments_parts)
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


# ---------------------------------------------------------------------------
# Admin-scoped operations (no partner_user_id restriction)
# ---------------------------------------------------------------------------

_APARTMENT_ADMIN_UPDATE_ALLOWED: set[str] = set(_APARTMENT_UPDATE_ALLOWED) | {
    "is_verified",
    "verified_at",
    "verification_status",
    "is_archived",
    "is_recommended",
    "is_testing",
    "partner_user_id",
    "verified_by_user_id",
    "comment_count",
    "legacy_property_id",
}


def admin_get_apartment(apartment_guid: str) -> dict[str, Any] | None:
    """Fetch an apartment by guid with no ownership/visibility filters (admin use)."""
    return fetch_one(
        f"""
        {APARTMENT_SELECT}
        WHERE a.guid = %s
        LIMIT 1
        """,
        [apartment_guid],
    )


def admin_create_apartment(
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
    create_values.setdefault("price", Decimal("0"))
    create_values.setdefault("currency", "UZS")
    create_values.setdefault("img", [])
    create_values.setdefault("services", [])
    create_values.setdefault("comment_count", 0)
    create_values.setdefault("check_in", "14:00:00")
    create_values.setdefault("check_out", "12:00:00")
    create_values.setdefault("is_allowed_alcohol", False)
    create_values.setdefault("is_allowed_corporate", False)
    create_values.setdefault("is_allowed_pets", False)
    create_values.setdefault("is_quiet_hours", False)
    create_values.setdefault("apartment_number", 0)
    create_values.setdefault("home_number", 0)
    create_values.setdefault("entrance_number", 0)
    create_values.setdefault("floor_number", 0)
    create_values.setdefault("pass_code", 0)
    create_values.setdefault("guests", 1)
    create_values.setdefault("rooms", 1)
    create_values.setdefault("beds", 1)
    create_values.setdefault("bathrooms", 1)

    created = create_apartment(partner_user_id=owner_id, values=create_values)
    if not created:
        return None

    apartment_guid = str(created["guid"])
    admin_values = {
        k: v
        for k, v in (values or {}).items()
        if k in _APARTMENT_ADMIN_UPDATE_ALLOWED
    }
    if partner_user_id is not None:
        admin_values["partner_user_id"] = int(partner_user_id)
    if admin_values:
        updated = admin_update_apartment(
            apartment_guid=apartment_guid,
            values=admin_values,
            admin_user_id=admin_user_id,
        )
        if updated:
            return updated
    return admin_get_apartment(apartment_guid)


def admin_update_apartment(
    *,
    apartment_guid: str,
    values: dict[str, Any],
    admin_user_id: int | None = None,
) -> dict[str, Any] | None:
    """Update an apartment as admin. No partner ownership check.

    Supports every writable column, including verification/archival flags.
    Does NOT auto-reset verification (unlike partner-side `update_apartment`).
    """
    existing = fetch_one(
        f"SELECT id FROM {APARTMENT_TABLE} WHERE guid = %s LIMIT 1",
        [apartment_guid],
    )
    if not existing:
        return None
    apartment_id = int(existing["id"])

    updates: dict[str, Any] = {
        k: v for k, v in values.items() if k in _APARTMENT_ADMIN_UPDATE_ALLOWED
    }
    if "services" in updates:
        raw_services = updates.get("services") or []
        updates["services"] = [str(service) for service in raw_services if service is not None]

    if "is_verified" in updates:
        if bool(updates["is_verified"]):
            updates["verified_at"] = updates.get("verified_at") or timezone.now()
            if admin_user_id is not None:
                updates["verified_by_user_id"] = updates.get("verified_by_user_id") or int(
                    admin_user_id
                )
            updates["verification_status"] = VerificationStatus.ACCEPTED.value
        else:
            updates["verified_at"] = None
            updates["verified_by_user_id"] = None
            if updates.get("verification_status") != VerificationStatus.CANCELLED.value:
                updates["verification_status"] = VerificationStatus.WAITING.value

    updates["updated_at"] = timezone.now()

    if not updates:
        return admin_get_apartment(apartment_guid)

    assignments_parts: list[str] = []
    for col in updates:
        if col == "services" and is_postgresql():
            assignments_parts.append("services = %s::uuid[]")
        else:
            assignments_parts.append(f"{col} = %s")
    assignments = ", ".join(assignments_parts)
    params = list(updates.values()) + [apartment_id]
    fetch_one(
        f"UPDATE {APARTMENT_TABLE} SET {assignments} WHERE id = %s RETURNING guid",
        params,
    )
    return admin_get_apartment(apartment_guid)


def admin_delete_apartment(*, apartment_guid: str) -> int:
    return execute(
        f"DELETE FROM {APARTMENT_TABLE} WHERE guid = %s",
        [apartment_guid],
    )


def admin_append_apartment_images(
    *,
    apartment_guid: str,
    image_paths: list[str],
) -> dict[str, Any] | None:
    existing = fetch_one(
        f"SELECT id, img FROM {APARTMENT_TABLE} WHERE guid = %s LIMIT 1",
        [apartment_guid],
    )
    if not existing:
        return None
    current = existing.get("img") or []
    if not isinstance(current, list):
        current = [current] if current else []
    new_img = list(current) + image_paths
    updated = fetch_one(
        f"UPDATE {APARTMENT_TABLE} SET img = %s, updated_at = %s WHERE id = %s RETURNING guid",
        [new_img, timezone.now(), int(existing["id"])],
    )
    if not updated:
        return None
    return admin_get_apartment(apartment_guid)


def admin_remove_apartment_image(
    *,
    apartment_guid: str,
    image_path: str,
) -> dict[str, Any] | None:
    existing = fetch_one(
        f"SELECT id, img FROM {APARTMENT_TABLE} WHERE guid = %s LIMIT 1",
        [apartment_guid],
    )
    if not existing:
        return None
    current = existing.get("img") or []
    if not isinstance(current, list):
        current = [current] if current else []
    new_img = [p for p in current if p != image_path]
    updated = fetch_one(
        f"UPDATE {APARTMENT_TABLE} SET img = %s, updated_at = %s WHERE id = %s RETURNING guid",
        [new_img, timezone.now(), int(existing["id"])],
    )
    if not updated:
        return None
    return admin_get_apartment(apartment_guid)


def set_apartment_primary_image(
    *,
    apartment_id: int,
    partner_user_id: int,
    image_path: str | None,
) -> dict[str, Any] | None:
    image_payload = [image_path] if image_path else []
    row = fetch_one(
        f"UPDATE {APARTMENT_TABLE} SET img = %s, updated_at = %s, is_verified = %s, verification_status = %s WHERE id = %s AND partner_user_id = %s RETURNING guid",
        [image_payload, timezone.now(), False, VerificationStatus.WAITING.value, apartment_id, partner_user_id],
    )
    if not row:
        return None
    return get_apartment_for_partner(str(row["guid"]), partner_user_id)


def append_apartment_images(
    *,
    apartment_id: int,
    partner_user_id: int,
    image_paths: list[str],
) -> dict[str, Any] | None:
    row = fetch_one(
        f"SELECT img FROM {APARTMENT_TABLE} WHERE id = %s AND partner_user_id = %s",
        [apartment_id, partner_user_id],
    )
    if not row:
        return None
    current = row.get("img") or []
    if not isinstance(current, list):
        current = [current] if current else []
    new_img = list(current) + image_paths
    updated = fetch_one(
        f"UPDATE {APARTMENT_TABLE} SET img = %s, updated_at = %s, is_verified = %s, verification_status = %s WHERE id = %s AND partner_user_id = %s RETURNING guid",
        [new_img, timezone.now(), False, VerificationStatus.WAITING.value, apartment_id, partner_user_id],
    )
    if not updated:
        return None
    return get_apartment_for_partner(str(updated["guid"]), partner_user_id)


def remove_apartment_image(
    *,
    apartment_id: int,
    partner_user_id: int,
    image_path: str,
) -> dict[str, Any] | None:
    row = fetch_one(
        f"SELECT img FROM {APARTMENT_TABLE} WHERE id = %s AND partner_user_id = %s",
        [apartment_id, partner_user_id],
    )
    if not row:
        return None
    current = row.get("img") or []
    if not isinstance(current, list):
        current = [current] if current else []
    new_img = [p for p in current if p != image_path]
    updated = fetch_one(
        f"UPDATE {APARTMENT_TABLE} SET img = %s, updated_at = %s, is_verified = %s, verification_status = %s WHERE id = %s AND partner_user_id = %s RETURNING guid",
        [new_img, timezone.now(), False, VerificationStatus.WAITING.value, apartment_id, partner_user_id],
    )
    if not updated:
        return None
    return get_apartment_for_partner(str(updated["guid"]), partner_user_id)


def replace_apartment_image(
    *,
    apartment_id: int,
    partner_user_id: int,
    old_image_path: str,
    new_image_path: str,
) -> dict[str, Any] | None:
    row = fetch_one(
        f"SELECT img FROM {APARTMENT_TABLE} WHERE id = %s AND partner_user_id = %s",
        [apartment_id, partner_user_id],
    )
    if not row:
        return None
    current = row.get("img") or []
    if not isinstance(current, list):
        current = [current] if current else []
    new_img = [new_image_path if p == old_image_path else p for p in current]
    updated = fetch_one(
        f"UPDATE {APARTMENT_TABLE} SET img = %s, updated_at = %s, is_verified = %s, verification_status = %s WHERE id = %s AND partner_user_id = %s RETURNING guid",
        [new_img, timezone.now(), False, VerificationStatus.WAITING.value, apartment_id, partner_user_id],
    )
    if not updated:
        return None
    return get_apartment_for_partner(str(updated["guid"]), partner_user_id)


def effective_apartment_price(row: dict[str, Any]) -> Decimal:
    val = row.get("price")
    if val is not None:
        try:
            return Decimal(str(val))
        except Exception:
            pass
    return Decimal("0")


def _exchange_rate_safe() -> Decimal:
    try:
        return Decimal(str(exchange_rate()))
    except Exception:
        return Decimal("1")


def _convert_amount(amount: Decimal, from_currency: str, to_currency: str, rate: Decimal) -> Decimal:
    source = (from_currency or "UZS").upper()
    target = (to_currency or "UZS").upper()
    if source == target:
        return amount
    if source == "USD" and target == "UZS":
        return amount * rate
    if source == "UZS" and target == "USD":
        if rate == 0:
            return amount
        return amount / rate
    return amount


def _effective_price(row: dict[str, Any], reference_date) -> Decimal:
    if str(row.get("property_kind") or "") == PROPERTY_KIND_COTTAGE:
        # Weekend pricing is applied only on Saturday/Sunday.
        field = "price_on_weekends" if reference_date.weekday() in {5, 6} else "price_on_working_days"
        value = row.get(field)
        if value is not None:
            try:
                return Decimal(str(value))
            except Exception:
                pass
    return effective_apartment_price(row)


def _sort_rows(rows: list[dict[str, Any]], *, ordering: str | None = None, sort: str | None = None) -> list[dict[str, Any]]:
    sort_value = (sort or "").strip().lower()
    # If sort parameter is provided, use it; otherwise fall back to ordering
    if sort_value:
        key_spec = {
            "price_high": "-order_price_uzs",
            "price_low": "order_price_uzs",
            "rating_high": "-average_rating",
            "rating_low": "average_rating",
            "reviews_high": "-review_count",
            "reviews_low": "review_count",
            "title_asc": "title",
            "title_desc": "-title",
            "corporate_yes": "-is_allowed_corporate",
            "corporate_no": "is_allowed_corporate",
        }.get(sort_value)
    else:
        key_spec = None
    # If no valid sort mapping, fall back to ordering parameter
    if not key_spec:
        key_spec = (ordering or "").strip() if ordering else "-created_at"
    desc = key_spec.startswith("-")
    key_name = key_spec[1:] if desc else key_spec
    if key_name == "title":
        return sorted(rows, key=lambda r: (str(r.get("title") or "").lower(), r.get("id") or 0), reverse=desc)
    if key_name in {"order_price_uzs", "average_rating"}:
        return sorted(rows, key=lambda r: (Decimal(str(r.get(key_name) or 0)), r.get("id") or 0), reverse=desc)
    if key_name == "review_count":
        return sorted(rows, key=lambda r: (int(r.get("review_count") or 0), r.get("id") or 0), reverse=desc)
    if key_name == "is_allowed_corporate":
        return sorted(rows, key=lambda r: (bool(r.get("is_allowed_corporate")), r.get("id") or 0), reverse=desc)
    return sorted(rows, key=lambda r: (r.get("created_at"), r.get("id") or 0), reverse=desc)


def prepare_property_rows(
    rows: list[dict[str, Any]],
    *,
    reference_date,
    min_price=None,
    max_price=None,
    currency: str | None = None,
    sort: str | None = None,
    ordering: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    rate = _exchange_rate_safe()
    prepared: list[dict[str, Any]] = []
    target_currency = (currency or "").upper().strip() if currency else None
    if target_currency not in {None, "", "USD", "UZS"}:
        target_currency = None
    for raw_row in rows:
        row = dict(raw_row)
        effective = _effective_price(row, reference_date)
        row["effective_price"] = effective
        row["order_price_uzs"] = _convert_amount(effective, str(row.get("currency") or "UZS"), "UZS", rate)
        value = effective
        if target_currency:
            value = _convert_amount(value, str(row.get("currency") or "UZS"), target_currency, rate)
        if min_price is not None and value < min_price:
            continue
        if max_price is not None and value > max_price:
            continue
        prepared.append(row)
    prepared = _sort_rows(prepared, ordering=ordering, sort=sort)
    if limit is not None and limit >= 0:
        prepared = prepared[:limit]
    return prepared


def list_reviews(*, property_kind: str, property_id: int, include_hidden: bool = False) -> list[dict[str, Any]]:
    where_property = "r.apartment_id = %s" if property_kind == PROPERTY_KIND_APARTMENT else "r.cottage_id = %s"
    where = [where_property]
    params: list[Any] = [property_id]
    if not include_hidden:
        where.append("COALESCE(r.is_hidden, FALSE) = FALSE")
    return fetch_all(
        f"""
        SELECT r.guid, r.created_at, r.rating, r.comment, r.user_id AS client_id,
               u.first_name AS client_first_name, u.last_name AS client_last_name
        FROM {REVIEW_TABLE} r
        LEFT JOIN {USERS_TABLE} u ON u.id = r.user_id
        WHERE {' AND '.join(where)}
        ORDER BY r.created_at DESC, r.id DESC
        """,
        params,
    )


def has_eligible_booking_for_review(*, client_user_id: int, property_kind: str, property_id: int) -> bool:
    property_column = "property_apartment_id" if property_kind == PROPERTY_KIND_APARTMENT else "property_cottage_id"
    statuses = ["confirmed", "completed", "cancelled"]
    if is_postgresql():
        row = fetch_one(
            f"""SELECT EXISTS (SELECT 1 FROM {BOOKING_TABLE}
                 WHERE client_user_id = %s AND {property_column} = %s AND status = ANY(%s)) AS exists_flag""",
            [client_user_id, property_id, statuses],
        )
    else:
        placeholders = ",".join(["%s"] * len(statuses))
        row = fetch_one(
            f"""SELECT EXISTS (SELECT 1 FROM {BOOKING_TABLE}
                 WHERE client_user_id = %s AND {property_column} = %s AND status IN ({placeholders})) AS exists_flag""",
            [client_user_id, property_id] + statuses,
        )
    return bool(row and row["exists_flag"])


def create_review(*, client_user_id: int, property_kind: str, property_id: int, rating: Decimal, comment: str | None):
    now = timezone.now()
    apartment_id = property_id if property_kind == PROPERTY_KIND_APARTMENT else None
    cottage_id = property_id if property_kind == PROPERTY_KIND_COTTAGE else None
    row = fetch_one(
        f"""INSERT INTO {REVIEW_TABLE} (guid, created_at, updated_at, rating, comment, is_hidden, user_id, apartment_id, cottage_id)
            VALUES (%s, %s, %s, %s, %s, FALSE, %s, %s, %s) RETURNING guid""",
        [uuid4(), now, now, rating, comment, client_user_id, apartment_id, cottage_id],
    )
    if not row:
        return None
    reviews = list_reviews(property_kind=property_kind, property_id=property_id, include_hidden=True)
    return next((review for review in reviews if str(review.get("guid")) == str(row["guid"])), None)
