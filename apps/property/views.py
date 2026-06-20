from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta, datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlencode, urlparse
from uuid import uuid4

from admin_auth.authentication import AdminJWTAuthentication
from admin_auth.permissions import IsAdminUser
from django.conf import settings
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import parsers, serializers, status
from rest_framework.exceptions import NotFound, ValidationError, APIException
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from shared.permissions import IsClient, IsPartner, IsPartnerOrAdmin
from shared.raw.compat import get_table_name
from shared.raw.db import fetch_all
from users.authentication import (
    ClientJWTAuthentication,
    OptionalClientOrPartnerJWTAuthentication,
    PartnerJWTAuthentication,
)
from users.raw_repository import fetch_users_by_ids, get_user_by_id

from .apartment_repository import (
    APARTMENT_TYPE_GUID,
    COTTAGE_TYPE_GUID,
    PROPERTY_KIND_HOTEL,
    PROPERTY_KIND_APARTMENT,
    PROPERTY_KIND_COTTAGE,
    admin_append_apartment_images,
    admin_create_apartment,
    admin_get_apartment,
    admin_remove_apartment_image,
    admin_update_apartment,
    append_apartment_images,
    create_apartment,
    create_review,
    delete_apartment,
    effective_apartment_price,
    get_apartment_for_partner,
    get_apartment_for_public,
    has_eligible_booking_for_review,
    list_apartments,
    list_districts,
    list_prefectures,
    list_property_services,
    list_property_types,
    list_regions,
    list_reviews,
    parse_property_kind,
    prepare_property_rows,
    remove_apartment_image,
    replace_apartment_image,
    resolve_district_id_by_guid,
    resolve_prefecture_id_by_guid,
    resolve_region_id_by_guid,
    update_apartment,
)
from .apartment_serializers import (
    ApartmentAdminListSerializer,
    ApartmentAdminUpdateSerializer,
    ApartmentCreateSerializer,
    ApartmentDetailSerializer,
    ApartmentListSerializer,
    ApartmentPartnerListSerializer,
    ApartmentUpdateSerializer,
)
from .cottage_repository import (
    COTTAGE_SELECT,
    admin_append_cottage_images,
    admin_create_cottage,
    admin_delete_cottage,
    admin_get_cottage,
    admin_remove_cottage_image,
    admin_update_cottage,
    append_cottage_images,
    create_cottage,
    delete_cottage,
    effective_cottage_price,
    get_cottage_for_partner,
    get_cottage_for_public,
    list_cottages,
    remove_cottage_image,
    replace_cottage_image,
    update_cottage,
)
from .cottage_serializers import (
    CottageAdminListSerializer,
    CottageAdminUpdateSerializer,
    CottageCreateSerializer,
    CottageDetailSerializer,
    CottageListSerializer,
    CottagePartnerListSerializer,
    CottageUpdateSerializer,
)
from apps.platform.raw_repository import get_organization_by_id, list_organizations
from .hotel_repository import (
    admin_append_hotel_images,
    admin_remove_hotel_image,
    create_admin_hotel,
    delete_admin_hotel,
    get_admin_hotel,
    list_admin_hotels,
    list_hotel_organizations,
    list_hotels,
    update_admin_hotel,
)
from .hotel_serializers import (
    HotelAdminListSerializer,
    HotelAdminUpdateSerializer,
    HotelListSerializer,
)
from .serializers import (
    DistrictListSerializer,
    LocationDistrictListSerializer,
    LocationPrefectureSerializer,
    LocationRegionListSerializer,
    PrefectureListSerializer,
    PropertyServiceListSerializer,
    RegionListSerializer,
    RegionsResponseSerializer,
)

logger = logging.getLogger(__name__)


def _track_client_search(request) -> None:
    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return
    if getattr(user, "role", None) != "client":
        return

    try:
        from recommendation.graph import record_search

        params = getattr(request, "query_params", None) or getattr(request, "GET", {})
        flat = {k: v[0] if isinstance(v, (list, tuple)) else v for k, v in params.items()}
        record_search(int(user.id), flat)
    except Exception:
        pass


_FAVORITES_CACHE_TTL_SECONDS = 60 * 60 * 24 * 30
_PROPERTY_META_CACHE_TTL_SECONDS = 60 * 10
_PROPERTY_LIST_CACHE_TTL_SECONDS = 60
_DEFAULT_PUBLIC_LIST_LIMIT = 50
_DEFAULT_PARTNER_LIST_LIMIT = 100


class _OptionalLimitPagePagination(PageNumberPagination):
    """If neither `limit` nor `page` is sent, skip pagination and return the full list."""

    page_size = 20
    page_size_query_param = "limit"
    max_page_size = 100

    def paginate_queryset(self, queryset, request, view=None):
        qp = request.query_params
        if self.page_query_param not in qp and self.page_size_query_param not in qp:
            return None
        return super().paginate_queryset(queryset, request, view)


class CottagePagination(_OptionalLimitPagePagination):
    pass


class ApartmentPagination(_OptionalLimitPagePagination):
    pass


PROPERTY_LIST_QUERY_PARAMS = [
    openapi.Parameter("search", openapi.IN_QUERY, type=openapi.TYPE_STRING),
    openapi.Parameter(
        "location_id",
        openapi.IN_QUERY,
        type=openapi.TYPE_STRING,
        description="Location UUID or integer ID. Tried as region GUID → district GUID → prefecture GUID.",
    ),
    openapi.Parameter("region_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
    openapi.Parameter("district_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
    openapi.Parameter("prefecture_id", openapi.IN_QUERY, type=openapi.TYPE_STRING, format="uuid"),
    openapi.Parameter(
        "property_type",
        openapi.IN_QUERY,
        type=openapi.TYPE_STRING,
        enum=["apartment", "cottage", "hotel"],
        description="Filter by property kind. Omit in the generic /properties/ endpoint to return all supported kinds.",
    ),
    openapi.Parameter("corporate", openapi.IN_QUERY, type=openapi.TYPE_BOOLEAN),
    openapi.Parameter("min_price", openapi.IN_QUERY, type=openapi.TYPE_NUMBER),
    openapi.Parameter("max_price", openapi.IN_QUERY, type=openapi.TYPE_NUMBER),
    openapi.Parameter("currency", openapi.IN_QUERY, type=openapi.TYPE_STRING),
    openapi.Parameter(
        "sort",
        openapi.IN_QUERY,
        type=openapi.TYPE_STRING,
        enum=[
            "price_high",
            "price_low",
            "rating_high",
            "rating_low",
            "reviews_high",
            "reviews_low",
            "title_asc",
            "title_desc",
            "corporate_yes",
            "corporate_no",
        ],
    ),
    openapi.Parameter("ordering", openapi.IN_QUERY, type=openapi.TYPE_STRING),
    openapi.Parameter(
        "from_date", openapi.IN_QUERY, type=openapi.TYPE_STRING, format="date"
    ),
    openapi.Parameter("limit", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
    openapi.Parameter("page", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
]

TESTING_MODE_HEADER_PARAM = openapi.Parameter(
    "X-Testing-Mode",
    openapi.IN_HEADER,
    type=openapi.TYPE_BOOLEAN,
    description="When `true`, return only testing properties. When omitted or false, testing properties are excluded.",
)

LOCATION_QUERY_PARAMS = [
    openapi.Parameter("region_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
    openapi.Parameter("district_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
]

RECOMMENDATIONS_QUERY_PARAMS = PROPERTY_LIST_QUERY_PARAMS + [
    openapi.Parameter(
        "kind",
        openapi.IN_QUERY,
        type=openapi.TYPE_STRING,
        enum=["property", "apartment", "cottage"],
    ),
    openapi.Parameter(
        "type",
        openapi.IN_QUERY,
        type=openapi.TYPE_STRING,
        enum=["featured", "best-by-reviews", "most-booked"],
    ),
]

PROPERTY_FILTER_BY_LINK_SCHEMA = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    properties={
        "url": openapi.Schema(type=openapi.TYPE_STRING),
        "link": openapi.Schema(type=openapi.TYPE_STRING),
    },
)

PROPERTY_LOCATION_REGION_SCHEMA = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    nullable=True,
    properties={
        "id": openapi.Schema(type=openapi.TYPE_INTEGER, nullable=True),
        "guid": openapi.Schema(type=openapi.TYPE_STRING, format="uuid", nullable=True),
        "name": openapi.Schema(type=openapi.TYPE_STRING, nullable=True),
    },
)

PROPERTY_LOCATION_DISTRICT_SCHEMA = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    nullable=True,
    properties={
        "id": openapi.Schema(type=openapi.TYPE_INTEGER, nullable=True),
        "guid": openapi.Schema(type=openapi.TYPE_STRING, format="uuid", nullable=True),
        "name": openapi.Schema(type=openapi.TYPE_STRING, nullable=True),
    },
)

PROPERTY_LOCATION_PREFECTURE_SCHEMA = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    nullable=True,
    properties={
        "id": openapi.Schema(type=openapi.TYPE_STRING, nullable=True),
        "name": openapi.Schema(type=openapi.TYPE_STRING, nullable=True),
    },
)

PROPERTY_LOCATION_SCHEMA = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    properties={
        "latitude": openapi.Schema(type=openapi.TYPE_STRING, nullable=True),
        "longitude": openapi.Schema(type=openapi.TYPE_STRING, nullable=True),
        "country": openapi.Schema(type=openapi.TYPE_STRING, nullable=True),
        "city": openapi.Schema(type=openapi.TYPE_STRING, nullable=True),
        "region": PROPERTY_LOCATION_REGION_SCHEMA,
        "district": PROPERTY_LOCATION_DISTRICT_SCHEMA,
        "prefecture": PROPERTY_LOCATION_PREFECTURE_SCHEMA,
    },
)

PROPERTY_ROOM_SCHEMA = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    nullable=True,
    properties={
        "guid": openapi.Schema(type=openapi.TYPE_STRING, format="uuid", nullable=True),
        "guests": openapi.Schema(type=openapi.TYPE_INTEGER, nullable=True),
        "rooms": openapi.Schema(type=openapi.TYPE_INTEGER, nullable=True),
        "beds": openapi.Schema(type=openapi.TYPE_INTEGER, nullable=True),
        "bathrooms": openapi.Schema(type=openapi.TYPE_INTEGER, nullable=True),
    },
)

COTTAGE_PRICE_ITEM_SCHEMA = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    description=(
        "One calendar month of prices. `month_from` is the first day of the month; "
        "`month_to` is the last day (full month). Price amounts may be omitted if the "
        "same top-level `price_per_person`, `price_on_working_days`, `price_on_weekends` apply."
    ),
    required=["month_from", "month_to"],
    properties={
        "month_from": openapi.Schema(
            type=openapi.TYPE_STRING,
            format="date",
            description="First day of the month (YYYY-MM-DD).",
        ),
        "month_to": openapi.Schema(
            type=openapi.TYPE_STRING,
            format="date",
            description="Last day of the month (YYYY-MM-DD).",
        ),
        "price_per_person": openapi.Schema(
            type=openapi.TYPE_NUMBER,
            format="double",
            nullable=True,
        ),
        "price_on_working_days": openapi.Schema(
            type=openapi.TYPE_NUMBER,
            format="double",
            nullable=True,
        ),
        "price_on_weekends": openapi.Schema(
            type=openapi.TYPE_NUMBER,
            format="double",
            nullable=True,
        ),
    },
)

_COTTAGE_PARTNER_FLAT_REQUEST_PROPERTIES: dict[str, openapi.Schema] = {
    "title": openapi.Schema(type=openapi.TYPE_STRING),
    "currency": openapi.Schema(
        type=openapi.TYPE_STRING,
        enum=["USD", "UZS"],
        default="UZS",
    ),
    "weekend_only_sunday_inclusive": openapi.Schema(
        type=openapi.TYPE_BOOLEAN,
        default=False,
    ),
    "price_per_person": openapi.Schema(
        type=openapi.TYPE_NUMBER,
        format="double",
        nullable=True,
        description="Per-person price (both months unless you vary via legacy `price` list only).",
    ),
    "price_on_working_days": openapi.Schema(
        type=openapi.TYPE_NUMBER,
        format="double",
        nullable=True,
        description="Working-day rate.",
    ),
    "price_on_weekends": openapi.Schema(
        type=openapi.TYPE_NUMBER,
        format="double",
        nullable=True,
        description="Weekend rate.",
    ),
    "month_from": openapi.Schema(
        type=openapi.TYPE_STRING,
        format="date",
        nullable=True,
        description="First pricing month: interval start (YYYY-MM-DD). Use with month_to, next_month_from, next_month_to.",
    ),
    "month_to": openapi.Schema(
        type=openapi.TYPE_STRING,
        format="date",
        nullable=True,
        description="First pricing month: interval end (YYYY-MM-DD). Should be the last day of that month.",
    ),
    "next_month_from": openapi.Schema(
        type=openapi.TYPE_STRING,
        format="date",
        nullable=True,
        description="Second pricing month: interval start.",
    ),
    "next_month_to": openapi.Schema(
        type=openapi.TYPE_STRING,
        format="date",
        nullable=True,
        description="Second pricing month: interval end.",
    ),
    "latitude": openapi.Schema(type=openapi.TYPE_STRING, nullable=True),
    "longitude": openapi.Schema(type=openapi.TYPE_STRING, nullable=True),
    "country": openapi.Schema(type=openapi.TYPE_STRING, nullable=True),
    "city": openapi.Schema(type=openapi.TYPE_STRING, nullable=True),
    "region_id": openapi.Schema(type=openapi.TYPE_STRING, nullable=True),
    "district_id": openapi.Schema(type=openapi.TYPE_STRING, nullable=True),
    "prefecture_id": openapi.Schema(
        type=openapi.TYPE_STRING,
        format="uuid",
        nullable=True,
    ),
    "description_en": openapi.Schema(type=openapi.TYPE_STRING, nullable=True),
    "description_ru": openapi.Schema(type=openapi.TYPE_STRING, nullable=True),
    "description_uz": openapi.Schema(type=openapi.TYPE_STRING, nullable=True),
    "check_in": openapi.Schema(type=openapi.TYPE_STRING, format="time", nullable=True),
    "check_out": openapi.Schema(type=openapi.TYPE_STRING, format="time", nullable=True),
    "is_allowed_alcohol": openapi.Schema(type=openapi.TYPE_BOOLEAN, default=False),
    "is_allowed_corporate": openapi.Schema(type=openapi.TYPE_BOOLEAN, default=False),
    "is_allowed_pets": openapi.Schema(type=openapi.TYPE_BOOLEAN, default=False),
    "is_quiet_hours": openapi.Schema(type=openapi.TYPE_BOOLEAN, default=False),
    "services": openapi.Schema(
        type=openapi.TYPE_ARRAY,
        items=openapi.Schema(type=openapi.TYPE_STRING, format="uuid"),
        description="Service UUIDs (same as legacy `property_services`).",
    ),
    "guests": openapi.Schema(type=openapi.TYPE_INTEGER, nullable=True),
    "rooms": openapi.Schema(type=openapi.TYPE_INTEGER, nullable=True),
    "beds": openapi.Schema(type=openapi.TYPE_INTEGER, nullable=True),
    "bathrooms": openapi.Schema(type=openapi.TYPE_INTEGER, nullable=True),
    "img": openapi.Schema(
        type=openapi.TYPE_ARRAY,
        items=openapi.Schema(type=openapi.TYPE_STRING),
        nullable=True,
        description="Image paths or URLs; a single string is also accepted by the API.",
    ),
}

COTTAGE_PARTNER_CREATE_REQUEST_SCHEMA = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    description=(
        "Partner cottage create. Send flat `price_*` amounts plus either "
        "(A) omit all month fields — server uses current and next calendar month, or "
        "(B) set `month_from`, `month_to`, `next_month_from`, `next_month_to` (full month ranges, YYYY-MM-DD). "
        "Do not use a nested `price` array (legacy; still accepted by the API if month fields are not used)."
    ),
    required=["title"],
    properties=_COTTAGE_PARTNER_FLAT_REQUEST_PROPERTIES,
)

COTTAGE_PARTNER_PATCH_REQUEST_SCHEMA = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    description=(
        "Partner cottage partial update (PATCH). Same flat fields as POST /api/property/cottages/; "
        "all optional — send only what changes. Pricing: use `price_*` with optional "
        "`month_from` / `month_to` / `next_month_from` / `next_month_to` (all four together), "
        "or legacy `price` list (not documented here). Changing non-price fields may reset verification."
    ),
    properties=_COTTAGE_PARTNER_FLAT_REQUEST_PROPERTIES,
)

PROPERTY_DETAIL_RESPONSE_SCHEMA = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    properties={
        "guid": openapi.Schema(type=openapi.TYPE_STRING, format="uuid"),
        "title": openapi.Schema(type=openapi.TYPE_STRING),
        "img": openapi.Schema(
            type=openapi.TYPE_ARRAY, items=openapi.Schema(type=openapi.TYPE_STRING)
        ),
        "created_at": openapi.Schema(type=openapi.TYPE_STRING, format="date-time"),
        "currency": openapi.Schema(type=openapi.TYPE_STRING, nullable=True),
        "price": openapi.Schema(
            type=openapi.TYPE_NUMBER,
            format="decimal",
            nullable=True,
            description="Apartment price in UZS (converted from USD if needed). Null for cottages.",
        ),
        "price_per_person": openapi.Schema(
            type=openapi.TYPE_NUMBER,
            format="decimal",
            nullable=True,
            description="Cottage price per person in UZS. Null for apartments.",
        ),
        "price_on_working_days": openapi.Schema(
            type=openapi.TYPE_NUMBER,
            format="decimal",
            nullable=True,
            description="Cottage working-day price in UZS. Null for apartments.",
        ),
        "price_on_weekends": openapi.Schema(
            type=openapi.TYPE_NUMBER,
            format="decimal",
            nullable=True,
            description="Cottage weekend price in UZS. Null for apartments.",
        ),
        "monthly_prices": openapi.Schema(
            type=openapi.TYPE_ARRAY,
            items=COTTAGE_PRICE_ITEM_SCHEMA,
            nullable=True,
            description="Cottage monthly price breakdown. Empty/null for apartments.",
        ),
        "weekend_only_sunday_inclusive": openapi.Schema(
            type=openapi.TYPE_BOOLEAN, nullable=True
        ),
        "description": openapi.Schema(
            type=openapi.TYPE_STRING,
            nullable=True,
            description="Localized description for cottages.",
        ),
        "description_en": openapi.Schema(
            type=openapi.TYPE_STRING,
            nullable=True,
            description="English description for apartments.",
        ),
        "description_ru": openapi.Schema(
            type=openapi.TYPE_STRING,
            nullable=True,
            description="Russian description for apartments.",
        ),
        "description_uz": openapi.Schema(
            type=openapi.TYPE_STRING,
            nullable=True,
            description="Uzbek description for apartments (falls back to en/ru if empty).",
        ),
        "comment_count": openapi.Schema(type=openapi.TYPE_INTEGER),
        "average_rating": openapi.Schema(
            type=openapi.TYPE_NUMBER, format="float", nullable=True
        ),
        "is_favorite": openapi.Schema(type=openapi.TYPE_BOOLEAN),
        "services": openapi.Schema(
            type=openapi.TYPE_ARRAY,
            items=openapi.Schema(type=openapi.TYPE_STRING, format="uuid"),
            nullable=True,
            description="List of service UUIDs (apartments).",
        ),
        "property_services": openapi.Schema(
            type=openapi.TYPE_ARRAY,
            items=openapi.Schema(type=openapi.TYPE_STRING, format="uuid"),
            nullable=True,
            description="List of service UUIDs (cottages).",
        ),
        "region_id": openapi.Schema(type=openapi.TYPE_INTEGER, nullable=True),
        "district_id": openapi.Schema(type=openapi.TYPE_INTEGER, nullable=True),
        "prefecture_id": openapi.Schema(type=openapi.TYPE_STRING, nullable=True),
        "latitude": openapi.Schema(type=openapi.TYPE_STRING, nullable=True),
        "longitude": openapi.Schema(type=openapi.TYPE_STRING, nullable=True),
        "country": openapi.Schema(type=openapi.TYPE_STRING, nullable=True),
        "city": openapi.Schema(type=openapi.TYPE_STRING, nullable=True),
        "property_location": PROPERTY_LOCATION_SCHEMA,
        "apartment_number": openapi.Schema(type=openapi.TYPE_STRING, nullable=True),
        "home_number": openapi.Schema(type=openapi.TYPE_STRING, nullable=True),
        "entrance_number": openapi.Schema(type=openapi.TYPE_STRING, nullable=True),
        "floor_number": openapi.Schema(type=openapi.TYPE_STRING, nullable=True),
        "pass_code": openapi.Schema(type=openapi.TYPE_STRING, nullable=True),
        "check_in": openapi.Schema(
            type=openapi.TYPE_STRING, format="time", nullable=True
        ),
        "check_out": openapi.Schema(
            type=openapi.TYPE_STRING, format="time", nullable=True
        ),
        "is_allowed_alcohol": openapi.Schema(type=openapi.TYPE_BOOLEAN),
        "is_allowed_corporate": openapi.Schema(type=openapi.TYPE_BOOLEAN),
        "is_allowed_pets": openapi.Schema(type=openapi.TYPE_BOOLEAN),
        "is_quiet_hours": openapi.Schema(type=openapi.TYPE_BOOLEAN),
        "guests": openapi.Schema(type=openapi.TYPE_INTEGER, nullable=True),
        "rooms": openapi.Schema(type=openapi.TYPE_INTEGER, nullable=True),
        "beds": openapi.Schema(type=openapi.TYPE_INTEGER, nullable=True),
        "bathrooms": openapi.Schema(type=openapi.TYPE_INTEGER, nullable=True),
        "property_room": PROPERTY_ROOM_SCHEMA,
    },
)

MIXED_PROPERTY_LIST_RESPONSE_SCHEMA = openapi.Schema(
    type=openapi.TYPE_ARRAY,
    items=openapi.Schema(
        type=openapi.TYPE_OBJECT,
        properties={
            "guid": openapi.Schema(type=openapi.TYPE_STRING, format="uuid"),
            "title": openapi.Schema(type=openapi.TYPE_STRING),
            "property_type": openapi.Schema(type=openapi.TYPE_OBJECT),
            "property_location": PROPERTY_LOCATION_SCHEMA,
        },
    ),
)

# ---------------------------------------------------------------------------
# Reusable error schemas
# ---------------------------------------------------------------------------

_ERROR_DETAIL_SCHEMA = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    properties={
        "detail": openapi.Schema(type=openapi.TYPE_STRING),
    },
)

_ERROR_VALIDATION_SCHEMA = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    properties={
        "detail": openapi.Schema(type=openapi.TYPE_STRING, nullable=True),
    },
)


class RawPropertyTypeSerializer(serializers.Serializer):
    guid = serializers.UUIDField()
    title = serializers.CharField()
    icon_url = serializers.CharField(allow_null=True)
    kind = serializers.CharField()


class RawPropertyReviewClientSerializer(serializers.Serializer):
    guid = serializers.UUIDField(allow_null=True)
    first_name = serializers.CharField(allow_blank=True, allow_null=True)
    last_name = serializers.CharField(allow_blank=True, allow_null=True)


class RawPropertyReviewSerializer(serializers.Serializer):
    guid = serializers.UUIDField()
    client = RawPropertyReviewClientSerializer()
    rating = serializers.DecimalField(max_digits=2, decimal_places=1, allow_null=True)
    comment = serializers.CharField(allow_blank=True, allow_null=True)
    created_at = serializers.DateTimeField()

    def to_representation(self, instance):
        row = dict(instance)
        row["client"] = {
            "guid": None,
            "first_name": row.get("client_first_name"),
            "last_name": row.get("client_last_name"),
        }
        return super().to_representation(row)


class RawPropertyReviewCreateSerializer(serializers.Serializer):
    rating = serializers.DecimalField(max_digits=2, decimal_places=1, required=True)
    comment = serializers.CharField(required=False, allow_blank=True, allow_null=True)


def _source_get(source, key: str, default=None):
    value = source.get(key, default)
    if isinstance(value, list):
        return value[0] if value else default
    return value


def _parse_bool(value) -> bool | None:
    if value is None:
        return None
    raw = str(value).strip().lower()
    if raw in {"true", "1", "yes", "on"}:
        return True
    if raw in {"false", "0", "no", "off"}:
        return False
    return None


def _parse_int(value) -> int | None:
    if value in (None, "", "null", "None", "undefined"):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _parse_region_id_or_guid(value) -> int | None:
    parsed = _parse_int(value)
    if parsed is not None:
        return parsed
    raw = str(value or "").strip()
    if not raw:
        return None
    return resolve_region_id_by_guid(raw)


def _parse_decimal(value) -> Decimal | None:
    if value in (None, "", "null", "None", "undefined"):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _preferred_language(request) -> str:
    raw = (request.headers.get("Accept-Language") or "").lower()
    if raw.startswith("ru"):
        return "ru"
    if raw.startswith("en"):
        return "en"
    return "uz"


def _select_title(row: dict, language: str, *, title_key: str = "title") -> str:
    if language == "ru":
        value = row.get("title_ru") or row.get("ru_name") or row.get("title")
    elif language == "en":
        value = row.get("title_en") or row.get("name") or row.get("title")
    else:
        value = row.get("title_uz") or row.get("name") or row.get("title")
    return str(value or row.get(title_key) or "")


def _fetch_regions(language: str) -> list[dict]:
    rows = fetch_all(
        """
        SELECT
            id,
            guid,
            title_uz,
            title_ru,
            title_en,
            img
        FROM public.region
        ORDER BY title_uz ASC
        """
    )
    normalized = []
    for row in rows:
        normalized.append(
            {
                "guid": row["guid"],
                "title": _select_title(row, language),
                "img": row.get("img"),
                "id": row.get("id"),
            }
        )
    return normalized


def _fetch_districts(language: str, region_id: int | None = None) -> list[dict]:
    params: list[object] = []
    where_sql = ""
    if region_id is not None:
        where_sql = "WHERE d.region_id = %s"
        params.append(region_id)
    rows = fetch_all(
        f"""
        SELECT
            d.id,
            d.guid,
            d.title_uz,
            d.title_ru,
            d.title_en,
            d.region_id
        FROM public.district d
        {where_sql}
        ORDER BY d.title_uz ASC
        """,
        params,
    )
    normalized = []
    for row in rows:
        normalized.append(
            {
                "guid": row["guid"],
                "title": _select_title(row, language),
                "region_id": row.get("region_id"),
                "id": row.get("id"),
            }
        )
    return normalized


def _fetch_prefectures(language: str, district_id: int | None = None) -> list[dict]:
    params: list[object] = []
    where_sql = ""
    if district_id is not None:
        where_sql = "WHERE dp.district_id = %s"
        params.append(district_id)
    rows = fetch_all(
        f"""
        SELECT
            p.id,
            p.name,
            p.ru_name,
            dp.district_id
        FROM public.prefecture p
        LEFT JOIN public.district_prefecture dp ON dp.prefecture_id = p.id
        {where_sql}
        ORDER BY p.name ASC
        """,
        params,
    )
    normalized = []
    for row in rows:
        normalized.append(
            {
                "guid": row["id"],
                "title": _select_title(
                    {"name": row.get("name"), "ru_name": row.get("ru_name")}, language
                ),
                "district_id": row.get("district_id"),
            }
        )
    return normalized


def _build_location_tree(language: str) -> list[dict]:
    regions = _fetch_regions(language)
    districts = _fetch_districts(language)
    prefectures = _fetch_prefectures(language)

    prefectures_by_district: dict[int, list[dict]] = {}
    for prefecture in prefectures:
        district_id = prefecture.get("district_id")
        if district_id is None:
            continue
        prefectures_by_district.setdefault(int(district_id), []).append(
            {"guid": prefecture["guid"], "title": prefecture["title"]}
        )

    districts_by_region: dict[int, list[dict]] = {}
    for district in districts:
        district_id = int(district["id"])
        region_id = district.get("region_id")
        if region_id is None:
            continue
        districts_by_region.setdefault(int(region_id), []).append(
            {
                "id": district_id,
                "guid": district["guid"],
                "title": district["title"],
                "prefectures": prefectures_by_district.get(district_id, []),
            }
        )

    return [
        {
            "id": region.get("id"),
            "guid": region["guid"],
            "title": region["title"],
            "districts": districts_by_region.get(int(region["id"]), []),
        }
        for region in regions
    ]


def _resolve_reference_date(value) -> date:
    raw = str(value).strip() if value is not None else ""
    if not raw:
        return date.today()
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return date.today()


def _build_media_url(request, media_path: str | None) -> str | None:
    if not media_path:
        return None
    try:
        url = default_storage.url(media_path)
    except Exception:
        return None
    if not request:
        return url
    return request.build_absolute_uri(url)


def _favorites_cache_key(client_user_id: int) -> str:
    return f"property:favorites:{int(client_user_id)}"


def _load_favorite_guids(client_user_id: int) -> set[str]:
    payload = cache.get(_favorites_cache_key(client_user_id), [])
    if not isinstance(payload, (list, tuple, set)):
        return set()
    return {str(value) for value in payload if value}


def _store_favorite_guids(client_user_id: int, values: set[str]) -> None:
    cache.set(
        _favorites_cache_key(client_user_id),
        sorted({str(value) for value in values if value}),
        timeout=_FAVORITES_CACHE_TTL_SECONDS,
    )


def _favorite_guids_from_request(request) -> set[str]:
    user = getattr(request, "user", None)
    if not user or not getattr(user, "is_authenticated", False):
        return set()
    if getattr(user, "role", None) != "client":
        return set()
    return _load_favorite_guids(int(user.id))


def _public_cache_key(request, prefix: str) -> str:
    params = getattr(request, "query_params", None) or getattr(request, "GET", {})
    query = urlencode(sorted(params.items()), doseq=True)
    lang = _preferred_language(request)
    return f"{prefix}:lang={lang}:testing={int(_is_testing_mode_request(request))}:query={query}"


def _is_testing_mode_request(request) -> bool:
    return bool(_parse_bool(request.headers.get("X-Testing-Mode")))


def _get_or_set_cached_payload(request, cache_key: str, timeout: int, loader):
    if getattr(request.user, "is_authenticated", False):
        return loader()
    payload = cache.get(cache_key)
    if payload is not None:
        return payload
    payload = loader()
    cache.set(cache_key, payload, timeout=timeout)
    return payload


def _validate_image_upload(uploaded):
    if uploaded is None:
        raise ValidationError({"image": [_("This field is required.")]})
    max_size = getattr(settings, "MAX_IMAGE_SIZE", 20 * 1024 * 1024)
    allowed_ext = {
        ext.lower() for ext in getattr(settings, "ALLOWED_PHOTO_EXTENSION", [])
    }
    name = (uploaded.name or "").lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    content_type = (uploaded.content_type or "").lower()

    # If no extension in filename but valid image content-type, derive ext from content_type
    if not ext and content_type.startswith("image/"):
        ext = content_type.split("/")[-1]
        # Map common content-type suffixes to file extensions
        ext_map = {
            "jpeg": "jpg",
            "png": "png",
            "gif": "gif",
            "webp": "webp",
            "heic": "heic",
            "heif": "heif",
        }
        ext = ext_map.get(ext, ext)

    if allowed_ext and ext not in allowed_ext:
        raise ValidationError({"image": [_("Unsupported file extension.")]})
    if content_type and not content_type.startswith("image/"):
        raise ValidationError({"image": [_("Only image uploads are allowed.")]})
    if uploaded.size and uploaded.size > max_size:
        raise ValidationError({"image": [_("File too large.")]})
    return True


def _extract_list_params(source):
    # Handle corporate sort as filter
    sort_value = (_source_get(source, "sort") or "").strip().lower()
    corporate_filter = _parse_bool(_source_get(source, "corporate"))
    if sort_value == "corporate_yes":
        corporate_filter = True
    elif sort_value == "corporate_no":
        corporate_filter = False

    limit = _parse_int(_source_get(source, "limit"))
    if limit is not None:
        limit = max(1, min(limit, 100))

    # Explicit params take precedence
    region_id = _parse_region_id_or_guid(_source_get(source, "region_id"))
    district_id = _parse_int(_source_get(source, "district_id"))
    prefecture_id = None

    # location_id is a generic fallback: try region GUID → district GUID → prefecture GUID
    location_id = _source_get(source, "location_id")
    if location_id is not None and region_id is None and district_id is None:
        parsed_int = _parse_int(location_id)
        if parsed_int is not None:
            region_id = parsed_int
        else:
            raw = str(location_id).strip()
            if raw:
                region_id = resolve_region_id_by_guid(raw)
                if region_id is None:
                    district_id = resolve_district_id_by_guid(raw)
                    if district_id is None:
                        prefecture_id = resolve_prefecture_id_by_guid(raw)

    return {
        "search": _source_get(source, "search"),
        "region_id": region_id,
        "district_id": district_id,
        "prefecture_id": prefecture_id,
        "corporate": corporate_filter,
        "min_price": _parse_decimal(_source_get(source, "min_price")),
        "max_price": _parse_decimal(_source_get(source, "max_price")),
        "limit": limit,
    }


def _parse_date(value) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _extract_prepare_params(
    source, *, default_ordering="-created_at", default_limit=None
):
    limit = _parse_int(_source_get(source, "limit"))
    if limit is None:
        limit = default_limit
    if limit is not None:
        limit = max(0, min(limit, 200))
    return {
        "min_price": _parse_decimal(_source_get(source, "min_price")),
        "max_price": _parse_decimal(_source_get(source, "max_price")),
        "currency": _source_get(source, "currency"),
        "sort": _source_get(source, "sort"),
        "ordering": _source_get(source, "ordering") or default_ordering,
        "reference_date": _resolve_reference_date(_source_get(source, "from_date")),
        "limit": limit,
    }


def _list_apartment_rows(
    source,
    *,
    public_only,
    partner_user_id=None,
    recommended_only=False,
    default_ordering="-created_at",
    default_limit=None,
    include_all_records=False,
    testing_only: bool | None = None,
):
    lp = _extract_list_params(source)
    rows = list_apartments(
        public_only=public_only,
        include_all_records=include_all_records,
        partner_user_id=partner_user_id,
        recommended_only=recommended_only,
        testing_only=testing_only,
        **lp,
    )
    pp = _extract_prepare_params(
        source, default_ordering=default_ordering, default_limit=default_limit
    )
    return prepare_property_rows(rows, **pp)


def _list_cottage_rows(
    source,
    *,
    public_only,
    partner_user_id=None,
    recommended_only=False,
    default_ordering="-created_at",
    default_limit=None,
    include_all_records=False,
    testing_only: bool | None = None,
):
    lp = _extract_list_params(source)
    rows = list_cottages(
        public_only=public_only,
        include_all_records=include_all_records,
        partner_user_id=partner_user_id,
        recommended_only=recommended_only,
        testing_only=testing_only,
        **lp,
    )
    pp = _extract_prepare_params(
        source, default_ordering=default_ordering, default_limit=default_limit
    )
    return prepare_property_rows(rows, **pp)


def _list_hotel_rows(
    source,
    *,
    default_limit=None,
    testing_only: bool | None = None,
):
    pp = _extract_prepare_params(
        source, default_ordering="-created_at", default_limit=default_limit
    )
    limit = pp.pop("limit", None)
    rows = list_hotels(limit=limit, testing_only=testing_only)
    return rows


def _serialize_partner_user(user) -> dict | None:
    if user is None:
        return None
    return {
        "id": int(user.id),
        "role": user.role,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "phone_number": user.phone_number,
        "email": user.email,
        "username": user.username,
        "avatar": user.avatar,
        "is_active": bool(user.is_active),
        "is_verified": bool(user.is_verified),
    }


def _attach_partner_users(rows: list[dict]) -> list[dict]:
    partner_ids = {
        int(row["partner_user_id"])
        for row in rows
        if row.get("partner_user_id") not in (None, "", "null")
    }
    users_by_id = fetch_users_by_ids(partner_ids)
    enriched: list[dict] = []
    for row in rows:
        payload = dict(row)
        raw_partner_id = row.get("partner_user_id")
        partner_id = _parse_int(raw_partner_id)
        payload["partner_user"] = (
            _serialize_partner_user(users_by_id.get(partner_id))
            if partner_id is not None
            else None
        )
        enriched.append(payload)
    return enriched


# ---------------------------------------------------------------------------
# Static / simple views
# ---------------------------------------------------------------------------


class PropertyTypeListView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_id="listPropertyTypes",
        operation_summary="List property types",
        operation_description="Returns the public property types with localized titles, icon URLs, and `kind` field. Results are cached for 10 minutes.",
        tags=["Property / Meta"],
        manual_parameters=[
            openapi.Parameter(
                "Accept-Language",
                openapi.IN_HEADER,
                type=openapi.TYPE_STRING,
                enum=["en", "ru", "uz"],
                default="uz",
                description="Preferred language for localized titles. Defaults to Uzbek.",
            ),
        ],
        responses={
            200: RawPropertyTypeSerializer(many=True),
            500: _ERROR_DETAIL_SCHEMA,
        },
    )
    def get(self, request, *args, **kwargs):
        language = _preferred_language(request)

        def _load():
            rows = list_property_types(language)
            # Keep cottage first regardless of source ordering.
            rows = sorted(
                rows,
                key=lambda row: (
                    0 if str(row.get("guid")) == str(COTTAGE_TYPE_GUID) else 1
                ),
            )
            for row in rows:
                row["icon_url"] = _build_media_url(request, row.get("icon_url"))
            return RawPropertyTypeSerializer(rows, many=True).data

        data = _get_or_set_cached_payload(
            request,
            _public_cache_key(request, f"property:type-list:v2:{language}"),
            _PROPERTY_META_CACHE_TTL_SECONDS,
            _load,
        )
        return Response(data, status=status.HTTP_200_OK)


class PropertyServiceListView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_id="listPropertyServices",
        operation_summary="List property services",
        operation_description="Returns all available property services (amenities) with localized titles and icon URLs. Results are cached for 10 minutes.",
        tags=["Property / Meta"],
        manual_parameters=[
            openapi.Parameter(
                "Accept-Language",
                openapi.IN_HEADER,
                type=openapi.TYPE_STRING,
                enum=["en", "ru", "uz"],
                default="uz",
                description="Preferred language for localized titles. Defaults to Uzbek.",
            ),
        ],
        responses={
            200: PropertyServiceListSerializer(many=True),
            500: _ERROR_DETAIL_SCHEMA,
        },
    )
    def get(self, request, *args, **kwargs):
        language = _preferred_language(request)

        def _load():
            rows = list_property_services(language)
            data = []
            for row in rows:
                payload = dict(row)
                payload["icon_url"] = _build_media_url(request, payload.get("icon_url"))
                data.append(payload)
            serializer = PropertyServiceListSerializer(data, many=True)
            return serializer.data

        data = _get_or_set_cached_payload(
            request,
            _public_cache_key(request, f"property:service-list:{language}"),
            _PROPERTY_META_CACHE_TTL_SECONDS,
            _load,
        )
        return Response(data, status=status.HTTP_200_OK)


class RegionListView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_id="listRegions",
        operation_summary="List regions",
        operation_description="Returns all regions with titles and image URLs. Results are cached for 10 minutes.",
        tags=["Property / Meta"],
        responses={
            200: RegionListSerializer(many=True),
            500: _ERROR_DETAIL_SCHEMA,
        },
    )
    def get(self, request, *args, **kwargs):
        def _load():
            rows = list_regions()
            data = []
            for row in rows:
                payload = dict(row)
                payload["img"] = _build_media_url(request, payload.get("img"))
                data.append(payload)
            serializer = RegionListSerializer(data, many=True)
            return serializer.data

        data = _get_or_set_cached_payload(
            request,
            _public_cache_key(request, "property:region-list"),
            _PROPERTY_META_CACHE_TTL_SECONDS,
            _load,
        )
        return Response(data, status=status.HTTP_200_OK)


class DistrictListView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_id="listDistricts",
        operation_summary="List districts",
        operation_description="Returns all districts, optionally filtered by region_id or region GUID. Results are cached for 10 minutes.",
        tags=["Property / Meta"],
        manual_parameters=[
            openapi.Parameter(
                "region_id",
                openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                required=False,
                description="Filter by region database id or region GUID.",
            ),
        ],
        responses={
            200: DistrictListSerializer(many=True),
            500: _ERROR_DETAIL_SCHEMA,
        },
    )
    def get(self, request, *args, **kwargs):
        def _load():
            region_id = _parse_region_id_or_guid(request.query_params.get("region_id"))
            rows = list_districts(region_id=region_id)
            data = []
            for row in rows:
                data.append(
                    {
                        "id": row.get("id"),
                        "guid": row.get("guid"),
                        "title": row.get("title"),
                        "region_id": row.get("region_id"),
                        "region": {
                            "id": row.get("region_id"),
                            "guid": row.get("region_guid"),
                            "title": row.get("region_title"),
                            "img": _build_media_url(request, row.get("region_img")),
                        }
                        if row.get("region_guid")
                        else None,
                    }
                )
            serializer = DistrictListSerializer(data, many=True)
            return serializer.data

        data = _get_or_set_cached_payload(
            request,
            _public_cache_key(request, "property:district-list"),
            _PROPERTY_META_CACHE_TTL_SECONDS,
            _load,
        )
        return Response(data, status=status.HTTP_200_OK)


class PrefectureListView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_id="listPrefectures",
        operation_summary="List prefectures",
        operation_description="Returns all prefectures, optionally filtered by district_id or district_guid. Results are cached for 10 minutes.",
        tags=["Property / Meta"],
        manual_parameters=[
            openapi.Parameter(
                "district_id",
                openapi.IN_QUERY,
                type=openapi.TYPE_INTEGER,
                required=False,
                description="Filter by district database id.",
            ),
            openapi.Parameter(
                "district_guid",
                openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                format="uuid",
                required=False,
                description="Filter by district GUID.",
            ),
        ],
        responses={
            200: PrefectureListSerializer(many=True),
            500: _ERROR_DETAIL_SCHEMA,
        },
    )
    def get(self, request, *args, **kwargs):
        def _load():
            district_id = _parse_int(request.query_params.get("district_id"))
            district_guid = (
                request.query_params.get("district_guid") or ""
            ).strip() or None
            rows = list_prefectures(
                district_id=district_id, district_guid=district_guid
            )
            data = []
            for row in rows:
                data.append(
                    {
                        "guid": row.get("guid"),
                        "title": row.get("title"),
                        "district": {
                            "guid": row.get("district_guid"),
                            "title": row.get("district_title"),
                        }
                        if row.get("district_guid")
                        else None,
                    }
                )
            serializer = PrefectureListSerializer(data, many=True)
            return serializer.data

        data = _get_or_set_cached_payload(
            request,
            _public_cache_key(request, "property:prefecture-list"),
            _PROPERTY_META_CACHE_TTL_SECONDS,
            _load,
        )
        return Response(data, status=status.HTTP_200_OK)


class LocationListView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_id="listLocations",
        operation_summary="List location tree",
        operation_description="Returns the full hierarchical location tree: regions → districts → prefectures. Results are cached for 10 minutes.",
        tags=["Property / Meta"],
        manual_parameters=[
            openapi.Parameter(
                "Accept-Language",
                openapi.IN_HEADER,
                type=openapi.TYPE_STRING,
                enum=["en", "ru", "uz"],
                default="uz",
                description="Preferred language for localized titles. Defaults to Uzbek.",
            ),
        ],
        responses={
            200: RegionsResponseSerializer(),
            500: _ERROR_DETAIL_SCHEMA,
        },
    )
    def get(self, request, *args, **kwargs):
        def _load():
            language = _preferred_language(request)
            regions = _build_location_tree(language)
            serializer = LocationRegionListSerializer(regions, many=True)
            return {"regions": serializer.data}

        payload = _get_or_set_cached_payload(
            request,
            _public_cache_key(request, "property:location-tree"),
            _PROPERTY_META_CACHE_TTL_SECONDS,
            _load,
        )
        return Response(payload, status=status.HTTP_200_OK)


class CategoryListView(APIView):
    authentication_classes = [OptionalClientOrPartnerJWTAuthentication]
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_id="listCategories",
        operation_summary="List categories",
        operation_description="Returns an empty list. Categories are not yet implemented.",
        tags=["Property / Meta"],
        responses={
            200: openapi.Schema(
                type=openapi.TYPE_ARRAY, items=openapi.Schema(type=openapi.TYPE_OBJECT)
            ),
        },
    )
    def get(self, request, *args, **kwargs):
        return Response([], status=status.HTTP_200_OK)


class CategoryLatestPropertyListView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_id="listCategoryLatestProperties",
        operation_summary="List latest properties by category",
        operation_description="Returns an empty list. Category-based latest properties are not yet implemented.",
        tags=["Property / Meta"],
        responses={
            200: openapi.Schema(
                type=openapi.TYPE_ARRAY, items=openapi.Schema(type=openapi.TYPE_OBJECT)
            ),
        },
    )
    def get(self, request, *args, **kwargs):
        return Response([], status=status.HTTP_200_OK)


class CategoryPropertyRecommendationView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_id="listCategoryPropertyRecommendations",
        operation_summary="List property recommendations by category",
        operation_description="Returns an empty list. Category-based recommendations are not yet implemented.",
        tags=["Property / Meta"],
        responses={
            200: openapi.Schema(
                type=openapi.TYPE_ARRAY, items=openapi.Schema(type=openapi.TYPE_OBJECT)
            ),
        },
    )
    def get(self, request, *args, **kwargs):
        return Response([], status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Recommendations (unified, returns both kinds)
# ---------------------------------------------------------------------------


class UnifiedRecommendationsListView(APIView):
    authentication_classes = [OptionalClientOrPartnerJWTAuthentication]
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_id="listRecommendations",
        operation_summary="List recommended properties",
        operation_description="Returns featured, best-reviewed, or most-booked properties. Supports filtering by kind (apartment, cottage, or both). Results are cached for 60 seconds.",
        tags=["Property / Public"],
        manual_parameters=RECOMMENDATIONS_QUERY_PARAMS + [TESTING_MODE_HEADER_PARAM],
        responses={
            200: MIXED_PROPERTY_LIST_RESPONSE_SCHEMA,
            500: _ERROR_DETAIL_SCHEMA,
        },
    )
    def get(self, request, *args, **kwargs):
        testing_only = _is_testing_mode_request(request)
        def _load():
            property_type = (
                str(request.query_params.get("kind") or "property").strip().lower()
            )
            if property_type not in {
                "property",
                "apartment",
                "cottage",
                "apartments",
                "cottages",
            }:
                return []
            source_params = request.query_params.copy()
            source_params.pop("kind", None)
            source_params.pop("type", None)

            rec_type = (
                str(request.query_params.get("type") or "featured").strip().lower()
            )
            if rec_type == "best-by-reviews":
                ordering = "-average_rating"
            elif rec_type == "most-booked":
                ordering = "order_price_uzs"
            else:
                ordering = "-created_at"

            ctx = {
                "request": request,
                "favorite_guids": _favorite_guids_from_request(request),
            }

            # Guaranteed cottages — always included in recommendations
            _GUARANTEED_COTTAGE_GUIDS = (
                "336d214d-8065-4b84-a3b1-a7a6e03f4ed1",  # Milliy Dacha
                "278e8df8-55a8-48b0-8636-ec7c55118b29",  # Steklo House Dacha
                "addb2627-c506-418b-ade9-a7bc82005027",  # Nano Park
                "a4929c41-7a61-4112-80d0-d5189aff2ac6",  # Manzara Dacha
                "b5eb8c02-33f7-44fd-a6fa-763b3c54699a",  # Salih House Dacha
            )

            def _fetch_guaranteed():
                if testing_only:
                    return []
                gcot_rows = fetch_all(
                    f"""
                    {COTTAGE_SELECT}
                    WHERE CAST(c.guid AS TEXT) IN %s
                      AND COALESCE(c.is_verified, FALSE) = TRUE
                      AND COALESCE(c.is_archived, FALSE) = FALSE
                    """,
                    [_GUARANTEED_COTTAGE_GUIDS],
                )
                return CottageListSerializer(
                    gcot_rows, many=True, context=ctx
                ).data

            if property_type in {"apartment", "apartments"}:
                rows = _list_apartment_rows(
                    source_params,
                    public_only=True,
                    recommended_only=False,
                    default_ordering=ordering,
                    default_limit=15,
                    testing_only=testing_only,
                )
                result = ApartmentListSerializer(rows, many=True, context=ctx).data
                guaranteed = _fetch_guaranteed()
                existing = {str(item.get("guid") or "") for item in result}
                result = [g for g in guaranteed if str(g.get("guid", "")) not in existing] + result
                return result

            if property_type in {"cottage", "cottages"}:
                rows = _list_cottage_rows(
                    source_params,
                    public_only=True,
                    recommended_only=False,
                    default_ordering=ordering,
                    default_limit=15,
                    testing_only=testing_only,
                )
                result = CottageListSerializer(rows, many=True, context=ctx).data
                guaranteed = _fetch_guaranteed()
                existing = {str(item.get("guid") or "") for item in result}
                result = [g for g in guaranteed if str(g.get("guid", "")) not in existing] + result
                return result

            apt_rows = _list_apartment_rows(
                source_params,
                public_only=True,
                recommended_only=False,
                default_ordering=ordering,
                default_limit=15,
                testing_only=testing_only,
            )
            cot_rows = _list_cottage_rows(
                source_params,
                public_only=True,
                recommended_only=False,
                default_ordering=ordering,
                default_limit=15,
                testing_only=testing_only,
            )
            combined = (
                ApartmentListSerializer(apt_rows, many=True, context=ctx).data
                + CottageListSerializer(cot_rows, many=True, context=ctx).data
            )
            result = combined[:15]
            guaranteed = _fetch_guaranteed()
            existing = {str(item.get("guid") or "") for item in result}
            result = [g for g in guaranteed if str(g.get("guid", "")) not in existing] + result
            return result

        data = _get_or_set_cached_payload(
            request,
            _public_cache_key(request, "property:recommendations"),
            _PROPERTY_LIST_CACHE_TTL_SECONDS,
            _load,
        )
        return Response(data, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Apartment views
# ---------------------------------------------------------------------------


class ApartmentPropertyListCreateView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    pagination_class = ApartmentPagination

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsPartner()]
        return [AllowAny()]

    @swagger_auto_schema(
        operation_id="listApartments",
        operation_summary="List apartments",
        operation_description="Returns verified public apartments. Without `limit` and `page`, all matching rows are returned; with either query param, results are paginated (default page size 20, max `limit` 100). Supports search, filtering, and sorting. `X-Testing-Mode: true` returns only testing apartments; otherwise testing apartments are excluded.",
        tags=["Property / Public"],
        manual_parameters=PROPERTY_LIST_QUERY_PARAMS + [TESTING_MODE_HEADER_PARAM],
        responses={
            200: ApartmentListSerializer(many=True),
            500: _ERROR_DETAIL_SCHEMA,
        },
    )
    def get(self, request, *args, **kwargs):
        _track_client_search(request)
        testing_only = _is_testing_mode_request(request)
        query_params = request.query_params.copy()
        query_params.pop("limit", None)

        rows = _list_apartment_rows(
            query_params,
            public_only=True,
            default_limit=None,
            testing_only=testing_only,
        )
        ctx = {
            "request": request,
            "favorite_guids": _favorite_guids_from_request(request),
        }

        paginator = self.pagination_class()
        paginated_data = paginator.paginate_queryset(rows, request)
        if paginated_data is not None:
            serializer = ApartmentListSerializer(paginated_data, many=True, context=ctx)
            return paginator.get_paginated_response(serializer.data)

        serializer = ApartmentListSerializer(rows, many=True, context=ctx)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @swagger_auto_schema(
        operation_id="createApartment",
        operation_summary="Create an apartment",
        operation_description="Partner-only. Creates a new apartment listing. The property is created with verification_status=waiting.",
        tags=["Property / Partner"],
        request_body=ApartmentCreateSerializer,
        responses={
            201: openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    "detail": openapi.Schema(type=openapi.TYPE_STRING),
                    "property_id": openapi.Schema(
                        type=openapi.TYPE_STRING, format="uuid"
                    ),
                    "status_code": openapi.Schema(type=openapi.TYPE_INTEGER),
                },
            ),
            400: _ERROR_VALIDATION_SCHEMA,
            401: _ERROR_DETAIL_SCHEMA,
            403: _ERROR_DETAIL_SCHEMA,
        },
    )
    def post(self, request, *args, **kwargs):
        serializer = ApartmentCreateSerializer(
            data=request.data, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        created = create_apartment(
            partner_user_id=int(request.user.id),
            values=serializer.validated_data["values"],
        )
        if not created:
            raise ValidationError(_("Apartment could not be created"))
        return Response(
            {
                "detail": _(
                    "Property has been created successfully, please wait while we verify it"
                ),
                "property_id": str(created["guid"]),
                "status_code": 201,
            },
            status=status.HTTP_201_CREATED,
        )


# ---------------------------------------------------------------------------
# Cottage views
# ---------------------------------------------------------------------------


class CottagePropertyListCreateView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    pagination_class = CottagePagination

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsPartner()]
        return [AllowAny()]

    @swagger_auto_schema(
        operation_id="listCottages",
        operation_summary="List cottages",
        operation_description="Returns verified public cottages. Without `limit` and `page`, all matching rows are returned; with either query param, results are paginated (default page size 20, max `limit` 100). Supports search, filtering, and sorting. `X-Testing-Mode: true` returns only testing cottages; otherwise testing cottages are excluded.",
        tags=["Property / Public"],
        manual_parameters=PROPERTY_LIST_QUERY_PARAMS + [TESTING_MODE_HEADER_PARAM],
        responses={
            200: CottageListSerializer(many=True),
            500: _ERROR_DETAIL_SCHEMA,
        },
    )
    def get(self, request, *args, **kwargs):
        _track_client_search(request)
        testing_only = _is_testing_mode_request(request)
        query_params = request.query_params.copy()
        query_params.pop("limit", None)

        rows = _list_cottage_rows(
            query_params,
            public_only=True,
            default_limit=None,
            testing_only=testing_only,
        )
        ctx = {
            "request": request,
            "favorite_guids": _favorite_guids_from_request(request),
        }

        paginator = self.pagination_class()
        paginated_data = paginator.paginate_queryset(rows, request)
        if paginated_data is not None:
            serializer = CottageListSerializer(paginated_data, many=True, context=ctx)
            return paginator.get_paginated_response(serializer.data)

        serializer = CottageListSerializer(rows, many=True, context=ctx)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @swagger_auto_schema(
        operation_id="createCottage",
        operation_summary="Create a cottage",
        operation_description="Partner-only. Creates a new cottage listing. The property is created with verification_status=waiting.",
        tags=["Property / Partner"],
        request_body=COTTAGE_PARTNER_CREATE_REQUEST_SCHEMA,
        responses={
            201: openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    "detail": openapi.Schema(type=openapi.TYPE_STRING),
                    "property_id": openapi.Schema(
                        type=openapi.TYPE_STRING, format="uuid"
                    ),
                    "status_code": openapi.Schema(type=openapi.TYPE_INTEGER),
                },
            ),
            400: _ERROR_VALIDATION_SCHEMA,
            401: _ERROR_DETAIL_SCHEMA,
            403: _ERROR_DETAIL_SCHEMA,
        },
    )
    def post(self, request, *args, **kwargs):
        serializer = CottageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        created = create_cottage(
            partner_user_id=int(request.user.id),
            values=serializer.validated_data["normalized_values"],
        )
        if not created:
            raise ValidationError(_("Cottage could not be created"))
        return Response(
            {
                "detail": _(
                    "Property has been created successfully, please wait while we verify it"
                ),
                "property_id": str(created["guid"]),
                "status_code": 201,
            },
            status=status.HTTP_201_CREATED,
        )


class HotelPropertyListView(APIView):
    pagination_class = CottagePagination
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_id="listHotels",
        operation_summary="List hotels",
        operation_description="Returns active public hotels from PMS. Without `limit` and `page`, all matching rows are returned; with either query param, results are paginated. `X-Testing-Mode: true` returns only testing hotels; otherwise testing hotels are excluded.",
        tags=["Property / Public"],
        manual_parameters=PROPERTY_LIST_QUERY_PARAMS + [TESTING_MODE_HEADER_PARAM],
        responses={
            200: HotelListSerializer(many=True),
            500: _ERROR_DETAIL_SCHEMA,
        },
    )
    def get(self, request, *args, **kwargs):
        _track_client_search(request)
        testing_only = _is_testing_mode_request(request)
        query_params = request.query_params.copy()
        query_params.pop("limit", None)
        rows = _list_hotel_rows(
            query_params,
            default_limit=None,
            testing_only=testing_only,
        )
        ctx = {
            "request": request,
            "favorite_guids": _favorite_guids_from_request(request),
        }
        paginator = self.pagination_class()
        paginated_data = paginator.paginate_queryset(rows, request)
        if paginated_data is not None:
            serializer = HotelListSerializer(paginated_data, many=True, context=ctx)
            return paginator.get_paginated_response(serializer.data)

        serializer = HotelListSerializer(rows, many=True, context=ctx)
        return Response(serializer.data, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Kept for URL compat: generic PropertyListCreateView delegates to kind-specific
# ---------------------------------------------------------------------------


class PropertyListCreateView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    forced_property_type: str | None = None
    pagination_class = ApartmentPagination

    def get_authenticators(self):
        if self.request.method == "GET":
            return []
        return super().get_authenticators()

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsPartner()]
        return [AllowAny()]

    @swagger_auto_schema(
        operation_id="listProperties",
        operation_summary="List properties",
        operation_description="Returns verified public apartments, cottages, and hotels. Use `property_type` or `kind` to filter to one property kind. `X-Testing-Mode: true` returns only testing properties; otherwise testing properties are excluded.",
        tags=["Property / Public"],
        manual_parameters=PROPERTY_LIST_QUERY_PARAMS + [TESTING_MODE_HEADER_PARAM],
        responses={200: MIXED_PROPERTY_LIST_RESPONSE_SCHEMA},
    )
    def get(self, request, *args, **kwargs):
        testing_only = _is_testing_mode_request(request)
        ctx = {
            "request": request,
            "favorite_guids": _favorite_guids_from_request(request),
        }
        kind_value = (
            request.query_params.get("property_type")
            or request.query_params.get("kind")
        )
        requested_kind = self.forced_property_type or parse_property_kind(kind_value)

        if requested_kind == PROPERTY_KIND_COTTAGE:
            rows = _list_cottage_rows(
                request.query_params,
                public_only=True,
                default_limit=None,
                testing_only=testing_only,
            )
            paginator = self.pagination_class()
            paginated_data = paginator.paginate_queryset(rows, request)
            if paginated_data is not None:
                serializer = CottageListSerializer(paginated_data, many=True, context=ctx)
                return paginator.get_paginated_response(serializer.data)
            return Response(CottageListSerializer(rows, many=True, context=ctx).data)

        if requested_kind == PROPERTY_KIND_APARTMENT:
            rows = _list_apartment_rows(
                request.query_params,
                public_only=True,
                default_limit=None,
                testing_only=testing_only,
            )
            paginator = self.pagination_class()
            paginated_data = paginator.paginate_queryset(rows, request)
            if paginated_data is not None:
                serializer = ApartmentListSerializer(paginated_data, many=True, context=ctx)
                return paginator.get_paginated_response(serializer.data)
            return Response(ApartmentListSerializer(rows, many=True, context=ctx).data)

        if requested_kind == PROPERTY_KIND_HOTEL:
            rows = _list_hotel_rows(
                request.query_params,
                default_limit=None,
                testing_only=testing_only,
            )
            paginator = self.pagination_class()
            paginated_data = paginator.paginate_queryset(rows, request)
            if paginated_data is not None:
                serializer = HotelListSerializer(paginated_data, many=True, context=ctx)
                return paginator.get_paginated_response(serializer.data)
            return Response(HotelListSerializer(rows, many=True, context=ctx).data)

        # Default: return apartments, cottages, and hotels (mixed list)
        apt_rows = _list_apartment_rows(
            request.query_params,
            public_only=True,
            default_limit=_DEFAULT_PUBLIC_LIST_LIMIT,
            testing_only=testing_only,
        )
        cot_rows = _list_cottage_rows(
            request.query_params,
            public_only=True,
            default_limit=_DEFAULT_PUBLIC_LIST_LIMIT,
            testing_only=testing_only,
        )
        hotel_rows = _list_hotel_rows(
            request.query_params,
            default_limit=_DEFAULT_PUBLIC_LIST_LIMIT,
            testing_only=testing_only,
        )
        data = (
            ApartmentListSerializer(apt_rows, many=True, context=ctx).data
            + CottageListSerializer(cot_rows, many=True, context=ctx).data
            + HotelListSerializer(hotel_rows, many=True, context=ctx).data
        )
        return Response(data, status=status.HTTP_200_OK)

    @swagger_auto_schema(
        operation_id="createProperty",
        operation_summary="Create a property",
        operation_description="Partner-only compatibility endpoint. Creates an apartment by default, or a cottage when the URL forces cottage mode.",
        tags=["Property / Partner"],
        request_body=ApartmentCreateSerializer,
        responses={
            201: openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    "detail": openapi.Schema(type=openapi.TYPE_STRING),
                    "property_id": openapi.Schema(
                        type=openapi.TYPE_STRING, format="uuid"
                    ),
                    "status_code": openapi.Schema(type=openapi.TYPE_INTEGER),
                },
            ),
            400: _ERROR_VALIDATION_SCHEMA,
            401: _ERROR_DETAIL_SCHEMA,
            403: _ERROR_DETAIL_SCHEMA,
        },
    )
    def post(self, request, *args, **kwargs):
        if self.forced_property_type == PROPERTY_KIND_COTTAGE:
            serializer = CottageCreateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            created = create_cottage(
                partner_user_id=int(request.user.id),
                values=serializer.validated_data["normalized_values"],
            )
        else:
            serializer = ApartmentCreateSerializer(
                data=request.data,
                partial=True,
                context={"request": request},
            )
            serializer.is_valid(raise_exception=True)
            created = create_apartment(
                partner_user_id=int(request.user.id),
                values=serializer.validated_data["values"],
            )
        if not created:
            raise ValidationError(_("Property could not be created"))
        return Response(
            {
                "detail": _(
                    "Property has been created successfully, please wait while we verify it"
                ),
                "property_id": str(created["guid"]),
                "status_code": 201,
            },
            status=status.HTTP_201_CREATED,
        )


class PropertyFilterByLinkView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_id="filterPropertyByLink",
        operation_summary="Filter property by link",
        operation_description="Accepts a property URL or link and returns the matching property GUID if found.",
        tags=["Property / Public"],
        request_body=PROPERTY_FILTER_BY_LINK_SCHEMA,
        responses={
            200: openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    "guid": openapi.Schema(
                        type=openapi.TYPE_STRING, format="uuid", nullable=True
                    ),
                },
            ),
            400: _ERROR_VALIDATION_SCHEMA,
        },
    )
    def post(self, request, *args, **kwargs):
        payload = request.data or {}
        url = str(payload.get("url") or payload.get("link") or "").strip()
        if not url:
            return Response(
                {"url": [_("This field is required.")]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        parsed = parse_qs(urlparse(url).query)
        ctx = {
            "request": request,
            "favorite_guids": _favorite_guids_from_request(request),
        }
        apt_rows = _list_apartment_rows(
            parsed, public_only=True, default_limit=_DEFAULT_PUBLIC_LIST_LIMIT
        )
        cot_rows = _list_cottage_rows(
            parsed, public_only=True, default_limit=_DEFAULT_PUBLIC_LIST_LIMIT
        )
        data = (
            ApartmentListSerializer(apt_rows, many=True, context=ctx).data
            + CottageListSerializer(cot_rows, many=True, context=ctx).data
        )
        return Response(data, status=status.HTTP_200_OK)


class RegionPropertyListView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_id="listPropertiesByRegion",
        operation_summary="List properties by region",
        operation_description="Returns apartments and cottages filtered by a specific region. Supports the same query filters as the public list. `X-Testing-Mode: true` returns only testing properties; otherwise testing properties are excluded.",
        tags=["Property / Public"],
        manual_parameters=PROPERTY_LIST_QUERY_PARAMS + [TESTING_MODE_HEADER_PARAM],
        responses={
            200: MIXED_PROPERTY_LIST_RESPONSE_SCHEMA,
            500: _ERROR_DETAIL_SCHEMA,
        },
    )
    def get(self, request, *args, **kwargs):
        region_id = _parse_int(self.kwargs.get("region_id"))
        if region_id is None:
            return Response([], status=status.HTTP_200_OK)
        testing_only = _is_testing_mode_request(request)
        mutable = request.query_params.copy()
        mutable["region_id"] = str(region_id)
        ctx = {
            "request": request,
            "favorite_guids": _favorite_guids_from_request(request),
        }
        apt_rows = _list_apartment_rows(
            mutable,
            public_only=True,
            default_limit=_DEFAULT_PUBLIC_LIST_LIMIT,
            testing_only=testing_only,
        )
        cot_rows = _list_cottage_rows(
            mutable,
            public_only=True,
            default_limit=_DEFAULT_PUBLIC_LIST_LIMIT,
            testing_only=testing_only,
        )
        data = (
            ApartmentListSerializer(apt_rows, many=True, context=ctx).data
            + CottageListSerializer(cot_rows, many=True, context=ctx).data
        )
        return Response(data, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Retrieve / Update / Delete  (kind-aware, looks up in both tables)
# ---------------------------------------------------------------------------


def _get_property_for_public(guid: str):
    row = get_apartment_for_public(guid)
    if row:
        return row
    return get_cottage_for_public(guid)


def _get_property_for_partner(guid: str, partner_user_id: int):
    row = get_apartment_for_partner(guid, partner_user_id)
    if row:
        return row
    return get_cottage_for_partner(guid, partner_user_id)


def _property_kind_hint_from_path(path: str) -> str | None:
    raw = str(path or "").lower()
    if "/cottages/" in raw:
        return PROPERTY_KIND_COTTAGE
    if "/apartments/" in raw:
        return PROPERTY_KIND_APARTMENT
    return None


class PropertyRetrieveUpdateDestroyView(APIView):
    authentication_classes = [PartnerJWTAuthentication]

    def get_permissions(self):
        if self.request.method in {"PUT", "PATCH", "DELETE"}:
            return [IsPartner()]
        return [AllowAny()]

    def _partner_property_or_404(self, property_id: str):
        hinted_kind = _property_kind_hint_from_path(getattr(self.request, "path", ""))
        if hinted_kind == PROPERTY_KIND_COTTAGE:
            row = get_cottage_for_partner(str(property_id), int(self.request.user.id))
        elif hinted_kind == PROPERTY_KIND_APARTMENT:
            row = get_apartment_for_partner(str(property_id), int(self.request.user.id))
        else:
            row = _get_property_for_partner(str(property_id), int(self.request.user.id))
        if not row:
            raise NotFound(_("Property not found"))
        return row

    def _read_property_or_404(self, property_id: str):
        user = getattr(self.request, "user", None)
        if user is not None and getattr(user, "role", None) == "partner":
            return self._partner_property_or_404(property_id)
        hinted_kind = _property_kind_hint_from_path(getattr(self.request, "path", ""))
        if hinted_kind == PROPERTY_KIND_COTTAGE:
            row = get_cottage_for_public(str(property_id))
        elif hinted_kind == PROPERTY_KIND_APARTMENT:
            row = get_apartment_for_public(str(property_id))
        else:
            row = _get_property_for_public(str(property_id))
        if not row:
            raise NotFound(_("Property not found"))
        return row

    @swagger_auto_schema(
        responses={
            200: PROPERTY_DETAIL_RESPONSE_SCHEMA,
        }
    )
    def get(self, request, property_id, *args, **kwargs):
        row = self._read_property_or_404(str(property_id))
        ctx = {
            "request": request,
            "favorite_guids": _favorite_guids_from_request(request),
        }
        property_type = str(row.get("property_kind") or "")
        if property_type == PROPERTY_KIND_COTTAGE:
            serializer = CottageDetailSerializer(row, context=ctx)
        else:
            serializer = ApartmentDetailSerializer(row, context=ctx)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @swagger_auto_schema(
        operation_id="fullUpdateProperty",
        operation_summary="Fully update a property",
        operation_description="Partner-only full update for an apartment or cottage. Mutating fields resets verification status to waiting.",
        tags=["Property / Partner"],
        request_body=ApartmentUpdateSerializer,
        responses={
            200: openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    "detail": openapi.Schema(type=openapi.TYPE_STRING),
                    "status_code": openapi.Schema(type=openapi.TYPE_INTEGER),
                    "warning": openapi.Schema(type=openapi.TYPE_STRING, nullable=True),
                },
            ),
            400: _ERROR_VALIDATION_SCHEMA,
            401: _ERROR_DETAIL_SCHEMA,
            403: _ERROR_DETAIL_SCHEMA,
            404: _ERROR_DETAIL_SCHEMA,
        },
    )
    def patch(self, request, property_id, *args, **kwargs):
        current = self._partner_property_or_404(str(property_id))
        property_type = str(current["property_kind"])
        logger.info(
            "property_update_request property_id=%s property_type=%s partner_user_id=%s payload_keys=%s",
            property_id,
            property_type,
            getattr(request.user, "id", None),
            sorted((request.data or {}).keys())
            if hasattr(request.data, "keys")
            else [],
        )
        if property_type == PROPERTY_KIND_COTTAGE:
            serializer = CottageUpdateSerializer(
                data=request.data, partial=True, context={"request": request, "is_update": True}
            )
            serializer.is_valid(raise_exception=True)
            logger.info(
                "property_update_normalized property_id=%s property_type=cottage normalized_keys=%s",
                property_id,
                sorted(serializer.validated_data.get("normalized_values", {}).keys()),
            )
            updated = update_cottage(
                cottage_id=int(current["id"]),
                partner_user_id=int(request.user.id),
                values=serializer.validated_data["normalized_values"],
            )
        else:
            serializer = ApartmentUpdateSerializer(
                data=request.data, partial=True, context={"request": request}
            )
            serializer.is_valid(raise_exception=True)
            values = serializer.validated_data.get("values") or {}
            logger.info(
                "property_update_normalized property_id=%s property_type=apartment prepared_keys=%s services_count=%s desc_ru_len=%s desc_uz_len=%s",
                property_id,
                sorted(values.keys()),
                len(values.get("services") or []),
                len(str(values.get("description_ru") or "")),
                len(str(values.get("description_uz") or "")),
            )
            updated = update_apartment(
                apartment_id=int(current["id"]),
                partner_user_id=int(request.user.id),
                values=values,
            )
        if not updated:
            raise NotFound(_("Property not found"))
        is_verified = bool(updated.get("is_verified"))
        payload = {
            "detail": "Your changes have been saved successfully",
            "status_code": 200,
        }
        if not is_verified:
            payload["warning"] = (
                "Property has been sent for re-verification, please wait while we verify it"
            )
        return Response(payload, status=status.HTTP_200_OK)

    @swagger_auto_schema(
        operation_id="deleteProperty",
        operation_summary="Delete a property",
        operation_description="Partner-only hard delete of an apartment or cottage.",
        tags=["Property / Partner"],
        responses={
            204: None,
            401: _ERROR_DETAIL_SCHEMA,
            403: _ERROR_DETAIL_SCHEMA,
            404: _ERROR_DETAIL_SCHEMA,
        },
    )
    def delete(self, request, property_id, *args, **kwargs):
        current = self._partner_property_or_404(str(property_id))
        property_type = str(current["property_kind"])
        if property_type == PROPERTY_KIND_COTTAGE:
            deleted = delete_cottage(
                cottage_id=int(current["id"]), partner_user_id=int(request.user.id)
            )
        else:
            deleted = delete_apartment(
                apartment_id=int(current["id"]), partner_user_id=int(request.user.id)
            )
        if not deleted:
            raise NotFound(_("Property not found"))
        return Response(status=status.HTTP_204_NO_CONTENT)


class CottagePropertyRetrieveUpdateDestroyView(PropertyRetrieveUpdateDestroyView):
    """Same as `PropertyRetrieveUpdateDestroyView` but Swagger documents cottage PATCH body (flat, like create)."""

    @swagger_auto_schema(
        operation_id="partialUpdateCottage",
        operation_summary="Partially update a cottage",
        operation_description=(
            "Partner-only partial update for a cottage. Request body matches POST /api/property/cottages/ "
            "(flat `price_*`, month range fields, location, descriptions, services, rooms); all fields optional."
        ),
        tags=["Property / Partner"],
        request_body=COTTAGE_PARTNER_PATCH_REQUEST_SCHEMA,
        responses={
            200: openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    "detail": openapi.Schema(type=openapi.TYPE_STRING),
                    "status_code": openapi.Schema(type=openapi.TYPE_INTEGER),
                    "warning": openapi.Schema(type=openapi.TYPE_STRING, nullable=True),
                },
            ),
            400: _ERROR_VALIDATION_SCHEMA,
            401: _ERROR_DETAIL_SCHEMA,
            403: _ERROR_DETAIL_SCHEMA,
            404: _ERROR_DETAIL_SCHEMA,
        },
    )
    def patch(self, request, property_id, *args, **kwargs):
        return super().patch(request, property_id, *args, **kwargs)


# ---------------------------------------------------------------------------
# Image views
# ---------------------------------------------------------------------------


def _resolve_image_path(request, property_row, image_url):
    """Map a public image URL (or raw path) back to the stored path in DB."""
    # Normalize incoming image_url: fix single-slash protocols and strip trailing slash
    normalized = str(image_url or "").rstrip("/")
    if normalized.startswith("https:/") and not normalized.startswith("https://"):
        normalized = "https://" + normalized[7:]
    if normalized.startswith("http:/") and not normalized.startswith("http://"):
        normalized = "http://" + normalized[6:]

    # Build candidate representations that might appear in the URL/path field.
    candidates: set[str] = {normalized}
    candidates.add(normalized.lstrip("/"))
    # If client sent a full URL, also compare against its path component.
    try:
        from urllib.parse import urlparse

        parsed = urlparse(normalized)
        if parsed.scheme and parsed.netloc:
            candidates.add((parsed.path or "").rstrip("/"))
            candidates.add((parsed.path or "").rstrip("/").lstrip("/"))
    except Exception:
        pass
    # Accept common media prefixes that may be present in public URLs.
    for value in list(candidates):
        if value.startswith("weel-media/"):
            candidates.add(value.removeprefix("weel-media/"))
        if value.startswith("media/"):
            candidates.add(value.removeprefix("media/"))

    current_paths = property_row.get("img") or []
    if not isinstance(current_paths, list):
        current_paths = [current_paths] if current_paths else []
    for path in current_paths:
        if path in candidates:
            return path
        url = _build_media_url(request, path)
        if url:
            url_normalized = url.rstrip("/")
            if url_normalized in candidates:
                return path
            try:
                from urllib.parse import urlparse

                parsed_url = urlparse(url_normalized)
                built_path = (parsed_url.path or "").rstrip("/")
                if built_path in candidates or built_path.lstrip("/") in candidates:
                    return path
            except Exception:
                pass
    return None


class PropertyImageCreateView(APIView):
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    @swagger_auto_schema(
        operation_id="createPropertyImage",
        operation_summary="Upload property image(s)",
        operation_description="Partner-only. Uploads image file(s) and appends them to the property's gallery. If the property is not yet verified, the images are marked as pending approval.",
        tags=["Property / Partner"],
        manual_parameters=[
            openapi.Parameter(
                "property_id",
                openapi.IN_PATH,
                type=openapi.TYPE_STRING,
                format="uuid",
                description="Property GUID.",
            ),
            openapi.Parameter(
                "image",
                openapi.IN_FORM,
                type=openapi.TYPE_FILE,
                required=True,
                description="Image file to upload (JPEG/PNG/WebP).",
            ),
        ],
        responses={
            201: openapi.Schema(
                type=openapi.TYPE_ARRAY,
                items=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "guid": openapi.Schema(type=openapi.TYPE_STRING, format="uuid"),
                        "order": openapi.Schema(type=openapi.TYPE_INTEGER),
                        "is_pending": openapi.Schema(type=openapi.TYPE_BOOLEAN),
                        "image_url": openapi.Schema(type=openapi.TYPE_STRING),
                    },
                ),
            ),
            200: openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    "detail": openapi.Schema(type=openapi.TYPE_STRING),
                    "status": openapi.Schema(type=openapi.TYPE_STRING),
                },
            ),
            400: _ERROR_VALIDATION_SCHEMA,
            401: _ERROR_DETAIL_SCHEMA,
            403: _ERROR_DETAIL_SCHEMA,
            404: _ERROR_DETAIL_SCHEMA,
        },
    )
    def post(self, request, property_id, *args, **kwargs):
        import logging
        log = logging.getLogger(__name__)
        log.info("[PropertyImageCreateView.post] property_id=%s user=%s", property_id, request.user.id)

        property_row = _get_property_for_partner(str(property_id), int(request.user.id))
        if not property_row:
            raise NotFound(_("Property not found"))

        uploaded_files = request.FILES.getlist("images")
        if not uploaded_files:
            single = request.FILES.get("image")
            if single is not None:
                uploaded_files = [single]
        if not uploaded_files:
            raise ValidationError({"images": [_("This field is required.")]})

        for file in uploaded_files:
            _validate_image_upload(file)

        saved_paths = [
            default_storage.save(f"property/images/{uuid4()}_{file.name}", file)
            for file in uploaded_files
        ]
        log.info("[PropertyImageCreateView.post] saved_paths=%s property_kind=%s", saved_paths, property_row.get("property_kind"))

        property_type = str(property_row["property_kind"])
        if property_type == PROPERTY_KIND_COTTAGE:
            updated = append_cottage_images(
                cottage_id=int(property_row["id"]),
                partner_user_id=int(request.user.id),
                image_paths=saved_paths,
            )
        else:
            updated = append_apartment_images(
                apartment_id=int(property_row["id"]),
                partner_user_id=int(request.user.id),
                image_paths=saved_paths,
            )
        log.info("[PropertyImageCreateView.post] updated img=%s", updated.get("img") if updated else None)

        if not updated:
            raise NotFound(_("Property not found"))

        if not bool(updated.get("is_verified")):
            return Response(
                {"detail": "Your image(s) are pending approval", "status": "pending"},
                status=status.HTTP_200_OK,
            )

        updated_img = updated.get("img") or []
        if not isinstance(updated_img, list):
            updated_img = [updated_img] if updated_img else []
        return Response(
            [
                {
                    "guid": uuid4(),
                    "order": idx + 1,
                    "is_pending": False,
                    "image_url": _build_media_url(request, path),
                }
                for idx, path in enumerate(updated_img)
            ],
            status=status.HTTP_201_CREATED,
        )


class PropertyImageUpdateDeleteView(APIView):
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    @swagger_auto_schema(
        operation_id="updatePropertyImage",
        operation_summary="Update a specific property image",
        operation_description="Partner-only. Replaces a specific image in the property's gallery. If the property is not yet verified, the image is marked as pending approval.",
        tags=["Property / Partner"],
        manual_parameters=[
            openapi.Parameter(
                "property_id",
                openapi.IN_PATH,
                type=openapi.TYPE_STRING,
                format="uuid",
                description="Property GUID.",
            ),
            openapi.Parameter(
                "image_url",
                openapi.IN_PATH,
                type=openapi.TYPE_STRING,
                description="Image URL or stored path of the image to replace.",
            ),
            openapi.Parameter(
                "image",
                openapi.IN_FORM,
                type=openapi.TYPE_FILE,
                required=True,
                description="New image file to upload (JPEG/PNG/WebP).",
            ),
        ],
        responses={
            200: openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    "detail": openapi.Schema(type=openapi.TYPE_STRING),
                    "status": openapi.Schema(type=openapi.TYPE_STRING),
                },
            ),
            400: _ERROR_VALIDATION_SCHEMA,
            401: _ERROR_DETAIL_SCHEMA,
            403: _ERROR_DETAIL_SCHEMA,
            404: _ERROR_DETAIL_SCHEMA,
        },
    )
    def patch(self, request, property_id, image_url, *args, **kwargs):
        property_row = _get_property_for_partner(str(property_id), int(request.user.id))
        if not property_row:
            raise NotFound(_("Property not found"))

        old_path = _resolve_image_path(request, property_row, image_url)
        if not old_path:
            raise NotFound(_("Property image not found"))

        uploaded = request.FILES.get("image")
        if uploaded is None:
            images = request.FILES.getlist("images")
            if images:
                uploaded = images[0]
        if uploaded is None:
            raise ValidationError({"image": [_("This field is required.")]})

        _validate_image_upload(uploaded)
        new_path = default_storage.save(
            f"property/images/{uuid4()}_{uploaded.name}", uploaded
        )

        property_type = str(property_row["property_kind"])
        if property_type == PROPERTY_KIND_COTTAGE:
            updated = replace_cottage_image(
                cottage_id=int(property_row["id"]),
                partner_user_id=int(request.user.id),
                old_image_path=old_path,
                new_image_path=new_path,
            )
        else:
            updated = replace_apartment_image(
                apartment_id=int(property_row["id"]),
                partner_user_id=int(request.user.id),
                old_image_path=old_path,
                new_image_path=new_path,
            )
        if not updated:
            raise NotFound(_("Property not found"))

        try:
            default_storage.delete(old_path)
        except Exception:
            pass

        return Response(
            {
                "detail": _("Your image has been updated and is pending approval"),
                "status": "pending",
            },
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        operation_id="deletePropertyImage",
        operation_summary="Delete a specific property image",
        operation_description="Partner-only. Removes a specific image from the property's gallery.",
        tags=["Property / Partner"],
        manual_parameters=[
            openapi.Parameter(
                "property_id",
                openapi.IN_PATH,
                type=openapi.TYPE_STRING,
                format="uuid",
                description="Property GUID.",
            ),
            openapi.Parameter(
                "image_url",
                openapi.IN_PATH,
                type=openapi.TYPE_STRING,
                description="Image URL or stored path of the image to delete.",
            ),
        ],
        responses={
            204: None,
            401: _ERROR_DETAIL_SCHEMA,
            403: _ERROR_DETAIL_SCHEMA,
            404: _ERROR_DETAIL_SCHEMA,
        },
    )
    def delete(self, request, property_id, image_url, *args, **kwargs):
        property_row = _get_property_for_partner(str(property_id), int(request.user.id))
        if not property_row:
            raise NotFound(_("Property not found"))

        old_path = _resolve_image_path(request, property_row, image_url)
        if not old_path:
            raise NotFound(_("Property image not found"))

        property_type = str(property_row["property_kind"])
        if property_type == PROPERTY_KIND_COTTAGE:
            updated = remove_cottage_image(
                cottage_id=int(property_row["id"]),
                partner_user_id=int(request.user.id),
                image_path=old_path,
            )
        else:
            updated = remove_apartment_image(
                apartment_id=int(property_row["id"]),
                partner_user_id=int(request.user.id),
                image_path=old_path,
            )
        if not updated:
            raise NotFound(_("Property not found"))

        try:
            default_storage.delete(old_path)
        except Exception:
            pass

        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Admin Image Management (no partner ownership check, no verification reset)
# ---------------------------------------------------------------------------


class AdminPropertyImageCreateView(APIView):
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]
    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_id="adminCreatePropertyImage",
        operation_summary="Upload property image(s) (admin)",
        operation_description="Admin-only. Uploads image file(s) and appends them to the property's gallery.",
        tags=["Admin / Property"],
        manual_parameters=[
            openapi.Parameter(
                "property_id",
                openapi.IN_PATH,
                type=openapi.TYPE_STRING,
                format="uuid",
                description="Property GUID.",
            ),
            openapi.Parameter(
                "image",
                openapi.IN_FORM,
                type=openapi.TYPE_FILE,
                required=True,
                description="Image file to upload (JPEG/PNG/WebP).",
            ),
        ],
        responses={
            201: openapi.Schema(
                type=openapi.TYPE_ARRAY,
                items=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "guid": openapi.Schema(type=openapi.TYPE_STRING, format="uuid"),
                        "order": openapi.Schema(type=openapi.TYPE_INTEGER),
                        "is_pending": openapi.Schema(type=openapi.TYPE_BOOLEAN),
                        "image_url": openapi.Schema(type=openapi.TYPE_STRING),
                    },
                ),
            ),
            400: _ERROR_VALIDATION_SCHEMA,
            401: _ERROR_DETAIL_SCHEMA,
            403: _ERROR_DETAIL_SCHEMA,
            404: _ERROR_DETAIL_SCHEMA,
        },
    )
    def post(self, request, property_id, *args, **kwargs):
        property_row = admin_get_apartment(str(property_id))
        if not property_row:
            property_row = admin_get_cottage(str(property_id))
        if not property_row:
            raise NotFound(_("Property not found"))

        uploaded_files = request.FILES.getlist("images")
        if not uploaded_files:
            single = request.FILES.get("image")
            if single is not None:
                uploaded_files = [single]
        if not uploaded_files:
            raise ValidationError({"images": [_("This field is required.")]})

        for file in uploaded_files:
            _validate_image_upload(file)

        saved_paths = [
            default_storage.save(f"property/images/{uuid4()}_{file.name}", file)
            for file in uploaded_files
        ]

        property_type = str(property_row["property_kind"])
        if property_type == PROPERTY_KIND_COTTAGE:
            updated = admin_append_cottage_images(
                cottage_guid=str(property_id),
                image_paths=saved_paths,
            )
        else:
            updated = admin_append_apartment_images(
                apartment_guid=str(property_id),
                image_paths=saved_paths,
            )

        if not updated:
            raise NotFound(_("Property not found"))

        updated_img = updated.get("img") or []
        if not isinstance(updated_img, list):
            updated_img = [updated_img] if updated_img else []
        return Response(
            [
                {
                    "guid": uuid4(),
                    "order": idx + 1,
                    "is_pending": False,
                    "image_url": _build_media_url(request, path),
                }
                for idx, path in enumerate(updated_img)
            ],
            status=status.HTTP_201_CREATED,
        )


class AdminPropertyImageDeleteView(APIView):
    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_id="adminDeletePropertyImage",
        operation_summary="Delete a specific property image (admin)",
        operation_description="Admin-only. Removes a specific image from the property's gallery.",
        tags=["Admin / Property"],
        manual_parameters=[
            openapi.Parameter(
                "property_id",
                openapi.IN_PATH,
                type=openapi.TYPE_STRING,
                format="uuid",
                description="Property GUID.",
            ),
            openapi.Parameter(
                "image_id",
                openapi.IN_PATH,
                type=openapi.TYPE_STRING,
                description="Image URL or stored path of the image to delete.",
            ),
        ],
        responses={
            204: None,
            401: _ERROR_DETAIL_SCHEMA,
            403: _ERROR_DETAIL_SCHEMA,
            404: _ERROR_DETAIL_SCHEMA,
        },
    )
    def delete(self, request, property_id, image_id, *args, **kwargs):
        property_row = admin_get_apartment(str(property_id))
        if not property_row:
            property_row = admin_get_cottage(str(property_id))
        if not property_row:
            raise NotFound(_("Property not found"))

        old_path = _resolve_image_path(request, property_row, image_id)
        if not old_path:
            raise NotFound(_("Property image not found"))

        property_type = str(property_row["property_kind"])
        if property_type == PROPERTY_KIND_COTTAGE:
            updated = admin_remove_cottage_image(
                cottage_guid=str(property_id),
                image_path=old_path,
            )
        else:
            updated = admin_remove_apartment_image(
                apartment_guid=str(property_id),
                image_path=old_path,
            )
        if not updated:
            raise NotFound(_("Property not found"))

        try:
            default_storage.delete(old_path)
        except Exception:
            pass

        return Response(status=status.HTTP_204_NO_CONTENT)


class AdminHotelImageCreateView(APIView):
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]
    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_id="adminCreateHotelImage",
        operation_summary="Upload hotel image(s) (admin)",
        operation_description="Admin-only. Uploads image file(s) and appends them to the hotel's gallery.",
        tags=["Admin / Property"],
        manual_parameters=[
            openapi.Parameter(
                "property_id",
                openapi.IN_PATH,
                type=openapi.TYPE_STRING,
                description="Tenant-aware hotel guid.",
            ),
            openapi.Parameter(
                "image",
                openapi.IN_FORM,
                type=openapi.TYPE_FILE,
                required=True,
                description="Image file to upload (JPEG/PNG/WebP).",
            ),
        ],
        responses={201: openapi.Schema(type=openapi.TYPE_ARRAY, items=openapi.Schema(type=openapi.TYPE_OBJECT))},
    )
    def post(self, request, property_id, *args, **kwargs):
        hotel_row = get_admin_hotel(str(property_id))
        if not hotel_row:
            raise NotFound(_("Hotel not found"))

        uploaded_files = request.FILES.getlist("images")
        if not uploaded_files:
            single = request.FILES.get("image")
            if single is not None:
                uploaded_files = [single]
        if not uploaded_files:
            raise ValidationError({"images": [_("This field is required.")]})

        for file in uploaded_files:
            _validate_image_upload(file)

        saved_paths = [
            default_storage.save(f"property/images/{uuid4()}_{file.name}", file)
            for file in uploaded_files
        ]
        updated = admin_append_hotel_images(
            hotel_guid=str(property_id),
            image_paths=saved_paths,
        )
        if not updated:
            raise NotFound(_("Hotel not found"))

        updated_img = updated.get("img") or []
        if not isinstance(updated_img, list):
            updated_img = [updated_img] if updated_img else []
        return Response(
            [
                {
                    "guid": uuid4(),
                    "order": idx + 1,
                    "is_pending": False,
                    "image_url": _build_media_url(request, path),
                }
                for idx, path in enumerate(updated_img)
            ],
            status=status.HTTP_201_CREATED,
        )


class AdminHotelImageDeleteView(APIView):
    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_id="adminDeleteHotelImage",
        operation_summary="Delete a specific hotel image (admin)",
        operation_description="Admin-only. Removes a specific image from the hotel's gallery.",
        tags=["Admin / Property"],
        manual_parameters=[
            openapi.Parameter(
                "property_id",
                openapi.IN_PATH,
                type=openapi.TYPE_STRING,
                description="Tenant-aware hotel guid.",
            ),
            openapi.Parameter(
                "image_id",
                openapi.IN_PATH,
                type=openapi.TYPE_STRING,
                description="Image URL or stored path of the image to delete.",
            ),
        ],
        responses={204: None},
    )
    def delete(self, request, property_id, image_id, *args, **kwargs):
        hotel_row = get_admin_hotel(str(property_id))
        if not hotel_row:
            raise NotFound(_("Hotel not found"))

        old_path = _resolve_image_path(request, hotel_row, image_id)
        if not old_path:
            raise NotFound(_("Hotel image not found"))

        updated = admin_remove_hotel_image(
            hotel_guid=str(property_id),
            image_path=old_path,
        )
        if not updated:
            raise NotFound(_("Hotel image not found"))

        try:
            default_storage.delete(old_path)
        except Exception:
            pass

        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Reviews  (shared — reviews reference both kinds)
# ---------------------------------------------------------------------------


class PropertyReviewListCreateView(APIView):
    authentication_classes = [ClientJWTAuthentication]

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsClient()]
        return [AllowAny()]

    def _get_property_or_404(self, property_id: str):
        row = _get_property_for_public(str(property_id))
        if not row:
            raise NotFound(_("Property not found"))
        return row

    @swagger_auto_schema(
        operation_id="listPropertyReviews",
        operation_summary="List property reviews",
        operation_description="Returns public reviews for a property. No authentication required.",
        tags=["Property / Reviews"],
        manual_parameters=[
            openapi.Parameter(
                "property_id",
                openapi.IN_PATH,
                type=openapi.TYPE_STRING,
                format="uuid",
                description="Property GUID.",
            ),
        ],
        responses={
            200: RawPropertyReviewSerializer(many=True),
            404: _ERROR_DETAIL_SCHEMA,
        },
    )
    def get(self, request, property_id, *args, **kwargs):
        property_row = self._get_property_or_404(str(property_id))
        rows = list_reviews(
            property_kind=str(property_row["property_kind"]),
            property_id=int(property_row["id"]),
            include_hidden=False,
        )
        return Response(
            RawPropertyReviewSerializer(rows, many=True).data, status=status.HTTP_200_OK
        )

    @swagger_auto_schema(
        operation_id="createPropertyReview",
        operation_summary="Create a property review",
        operation_description="Client-only. Creates a review for a property the client has an eligible completed or accepted booking for.",
        tags=["Property / Reviews"],
        manual_parameters=[
            openapi.Parameter(
                "property_id",
                openapi.IN_PATH,
                type=openapi.TYPE_STRING,
                format="uuid",
                description="Property GUID.",
            ),
        ],
        request_body=RawPropertyReviewCreateSerializer,
        responses={
            201: RawPropertyReviewSerializer,
            400: _ERROR_VALIDATION_SCHEMA,
            401: _ERROR_DETAIL_SCHEMA,
            403: _ERROR_DETAIL_SCHEMA,
            404: _ERROR_DETAIL_SCHEMA,
        },
    )
    def post(self, request, property_id, *args, **kwargs):
        property_row = self._get_property_or_404(str(property_id))
        serializer = RawPropertyReviewCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        can_review = has_eligible_booking_for_review(
            client_user_id=int(request.user.id),
            property_kind=str(property_row["property_kind"]),
            property_id=int(property_row["id"]),
        )
        if not can_review:
            raise ValidationError(
                _("You can leave a review only for accepted or completed bookings")
            )

        created = create_review(
            client_user_id=int(request.user.id),
            property_kind=str(property_row["property_kind"]),
            property_id=int(property_row["id"]),
            rating=serializer.validated_data["rating"],
            comment=serializer.validated_data.get("comment"),
        )
        if not created:
            raise ValidationError(_("Review could not be created"))
        return Response(
            RawPropertyReviewSerializer(created).data, status=status.HTTP_201_CREATED
        )


class PartnerPropertyReviewListView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    @swagger_auto_schema(
        operation_id="listPartnerPropertyReviews",
        operation_summary="List all reviews for a property (partner)",
        operation_description="Partner-only. Returns all reviews for a property, including hidden ones.",
        tags=["Property / Partner"],
        manual_parameters=[
            openapi.Parameter(
                "property_id",
                openapi.IN_PATH,
                type=openapi.TYPE_STRING,
                format="uuid",
                description="Property GUID.",
            ),
        ],
        responses={
            200: RawPropertyReviewSerializer(many=True),
            401: _ERROR_DETAIL_SCHEMA,
            403: _ERROR_DETAIL_SCHEMA,
            404: _ERROR_DETAIL_SCHEMA,
        },
    )
    def get(self, request, property_id, *args, **kwargs):
        property_row = _get_property_for_partner(str(property_id), int(request.user.id))
        if not property_row:
            raise NotFound(_("Property not found"))
        rows = list_reviews(
            property_kind=str(property_row["property_kind"]),
            property_id=int(property_row["id"]),
            include_hidden=True,
        )
        return Response(
            RawPropertyReviewSerializer(rows, many=True).data, status=status.HTTP_200_OK
        )


ANALYTICS_RANGE_DAYS = {
    "week": 7,
    "month": 30,
    "quarter": 90,
    "year": 365,
}


def _analytics_range_bounds(range_name: str) -> tuple[date, date, date, date]:
    days = ANALYTICS_RANGE_DAYS.get(range_name, ANALYTICS_RANGE_DAYS["month"])
    today = timezone.localdate()
    start_date = today - timedelta(days=days - 1)
    previous_end_date = start_date - timedelta(days=1)
    previous_start_date = previous_end_date - timedelta(days=days - 1)
    return start_date, today, previous_start_date, previous_end_date


def _analytics_property_id_column(property_kind: str) -> str:
    if property_kind == PROPERTY_KIND_APARTMENT:
        return "property_apartment_id"
    return "property_cottage_id"


def _as_date(value):
    if hasattr(value, "date"):
        return value.date()
    return value


def _change_percent(current: int, previous: int) -> float:
    if previous <= 0:
        return 0.0 if current <= 0 else 100.0
    return round(((current - previous) / previous) * 100, 1)


def _format_amount(amount: Decimal) -> str:
    return str(Decimal(amount).quantize(Decimal("0.01")))


def _build_analytics_series(
    start_date: date, end_date: date, values_by_date: dict[str, float | int]
) -> list[dict[str, float | int | str]]:
    series: list[dict[str, float | int | str]] = []
    current = start_date
    while current <= end_date:
        label = current.isoformat()
        series.append({"label": label, "value": values_by_date.get(label, 0)})
        current += timedelta(days=1)
    return series


def _summarize_bookings(
    rows: list[dict], start_date: date, end_date: date
) -> dict[str, object]:
    booked_count = 0
    cancelled_count = 0
    no_show_count = 0
    cancelled_after_booking_count = 0
    activity: dict[str, int] = defaultdict(int)

    for row in rows:
        created_at = row.get("created_at")
        if created_at:
            created_date = _as_date(created_at)
            if start_date <= created_date <= end_date:
                activity[created_date.isoformat()] += 1

        confirmed_at = row.get("confirmed_at") or row.get("completed_at")
        if confirmed_at:
            confirmed_date = _as_date(confirmed_at)
            if start_date <= confirmed_date <= end_date and str(
                row.get("status") or ""
            ).lower() in {"confirmed", "completed"}:
                booked_count += 1

        cancelled_at = row.get("cancelled_at")
        if cancelled_at:
            cancelled_date = _as_date(cancelled_at)
            if (
                start_date <= cancelled_date <= end_date
                and str(row.get("status") or "").lower() == "cancelled"
            ):
                cancelled_count += 1
                reason = str(row.get("cancellation_reason") or "").strip().lower()
                if reason == "user_no_show":
                    no_show_count += 1
                else:
                    cancelled_after_booking_count += 1

    return {
        "booked_count": booked_count,
        "cancelled_count": cancelled_count,
        "no_show_count": no_show_count,
        "cancelled_after_booking_count": cancelled_after_booking_count,
        "activity": activity,
    }


def _summarize_income(
    rows: list[dict], start_date: date, end_date: date
) -> dict[str, object]:
    total = Decimal("0")
    bars: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    count = 0

    for row in rows:
        created_at = row.get("created_at")
        if not created_at:
            continue
        created_date = _as_date(created_at)
        if not (start_date <= created_date <= end_date):
            continue
        amount = Decimal(str(row.get("amount") or "0"))
        total += amount
        bars[created_date.isoformat()] += amount
        count += 1

    return {"total": total, "count": count, "bars": bars}


class PartnerPropertyAnalyticsView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    @swagger_auto_schema(
        operation_id="getPropertyAnalytics",
        operation_summary="Get property analytics",
        operation_description="Partner-only. Returns booking statistics, cancellation metrics, and income breakdown for a specific property over a given time range.",
        tags=["Property / Partner"],
        manual_parameters=[
            openapi.Parameter(
                "property_id",
                openapi.IN_PATH,
                type=openapi.TYPE_STRING,
                format="uuid",
                description="Property GUID.",
            ),
            openapi.Parameter(
                "range",
                openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                enum=["week", "month", "quarter", "year"],
                default="month",
                description="Time range for analytics.",
            ),
        ],
        responses={
            200: openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    "property": openapi.Schema(
                        type=openapi.TYPE_OBJECT,
                        properties={
                            "guid": openapi.Schema(
                                type=openapi.TYPE_STRING, format="uuid"
                            ),
                            "title": openapi.Schema(type=openapi.TYPE_STRING),
                            "image_url": openapi.Schema(
                                type=openapi.TYPE_STRING, nullable=True
                            ),
                            "city": openapi.Schema(
                                type=openapi.TYPE_STRING, nullable=True
                            ),
                        },
                    ),
                    "range": openapi.Schema(type=openapi.TYPE_STRING),
                    "bookings_overview": openapi.Schema(type=openapi.TYPE_OBJECT),
                    "bookings_activity": openapi.Schema(
                        type=openapi.TYPE_ARRAY,
                        items=openapi.Schema(type=openapi.TYPE_OBJECT),
                    ),
                    "income_overview": openapi.Schema(
                        type=openapi.TYPE_OBJECT,
                        properties={
                            "balance_amount": openapi.Schema(type=openapi.TYPE_STRING),
                            "currency": openapi.Schema(type=openapi.TYPE_STRING),
                            "bars": openapi.Schema(
                                type=openapi.TYPE_ARRAY,
                                items=openapi.Schema(type=openapi.TYPE_OBJECT),
                            ),
                        },
                    ),
                },
            ),
            401: _ERROR_DETAIL_SCHEMA,
            403: _ERROR_DETAIL_SCHEMA,
            404: _ERROR_DETAIL_SCHEMA,
        },
    )
    def get(self, request, property_id, *args, **kwargs):
        partner_id = int(request.user.id)
        property_row = _get_property_for_partner(str(property_id), partner_id)
        if not property_row:
            raise NotFound(_("Property not found"))

        property_kind = str(property_row["property_kind"])
        if property_kind == PROPERTY_KIND_APARTMENT:
            full_property = get_apartment_for_partner(str(property_id), partner_id)
        else:
            full_property = get_cottage_for_partner(str(property_id), partner_id)

        if not full_property:
            raise NotFound(_("Property not found"))

        range_name = str(request.query_params.get("range") or "month").lower()
        if range_name not in ANALYTICS_RANGE_DAYS:
            raise ValidationError({"range": _("Invalid range")})

        start_date, end_date, previous_start_date, previous_end_date = (
            _analytics_range_bounds(range_name)
        )
        property_column = _analytics_property_id_column(property_kind)
        property_table = get_table_name("booking")
        transaction_table = get_table_name("transaction_history")

        booking_rows = fetch_all(
            f"""
            SELECT created_at, confirmed_at, cancelled_at, completed_at, status, cancellation_reason
            FROM {property_table}
            WHERE {property_column} = %s
            ORDER BY created_at ASC, id ASC
            """,
            [int(property_row["id"])],
        )

        income_rows = fetch_all(
            f"""
            SELECT th.created_at, th.amount
            FROM {transaction_table} th
            INNER JOIN {property_table} b ON b.id = th.booking_id
            WHERE b.{property_column} = %s
              AND th.type = 'CHRG'
              AND th.status = 'CHARGED'
            ORDER BY th.created_at ASC, th.id ASC
            """,
            [int(property_row["id"])],
        )

        current_bookings = _summarize_bookings(booking_rows, start_date, end_date)
        previous_bookings = _summarize_bookings(
            booking_rows, previous_start_date, previous_end_date
        )
        current_income = _summarize_income(income_rows, start_date, end_date)

        booked_count = int(current_bookings["booked_count"])
        cancelled_count = int(current_bookings["cancelled_count"])
        no_show_count = int(current_bookings["no_show_count"])
        cancelled_after_booking_count = int(
            current_bookings["cancelled_after_booking_count"]
        )

        previous_booked_count = int(previous_bookings["booked_count"])
        previous_cancelled_count = int(previous_bookings["cancelled_count"])
        previous_no_show_count = int(previous_bookings["no_show_count"])
        previous_cancelled_after_booking_count = int(
            previous_bookings["cancelled_after_booking_count"]
        )

        booked_change_percent = _change_percent(booked_count, previous_booked_count)
        cancelled_change_percent = _change_percent(
            cancelled_count, previous_cancelled_count
        )
        no_show_change_percent = _change_percent(no_show_count, previous_no_show_count)
        cancelled_after_booking_change_percent = _change_percent(
            cancelled_after_booking_count,
            previous_cancelled_after_booking_count,
        )
        comparison_percent = _change_percent(
            booked_count + cancelled_count,
            previous_booked_count + previous_cancelled_count,
        )

        income_total = Decimal(current_income["total"])
        charged_count = int(current_income["count"])
        total_mix = booked_count + cancelled_count + charged_count
        if total_mix <= 0:
            distribution = {
                "income_percent": 0,
                "bookings_percent": 0,
                "cancellations_percent": 0,
            }
        else:
            distribution = {
                "income_percent": round((charged_count / total_mix) * 100, 1),
                "bookings_percent": round((booked_count / total_mix) * 100, 1),
                "cancellations_percent": round((cancelled_count / total_mix) * 100, 1),
            }

        property_image_url = None
        img_value = full_property.get("img")
        if img_value:
            try:
                property_image_url = default_storage.url(img_value)
            except Exception:
                property_image_url = str(img_value)

        return Response(
            {
                "property": {
                    "guid": str(full_property.get("guid")),
                    "title": full_property.get("title") or "",
                    "image_url": property_image_url,
                    "city": full_property.get("city"),
                },
                "range": range_name,
                "bookings_overview": {
                    "comparison_percent": comparison_percent,
                    "booked_count": booked_count,
                    "booked_change_percent": booked_change_percent,
                    "cancelled_count": cancelled_count,
                    "cancelled_change_percent": cancelled_change_percent,
                    "no_show_count": no_show_count,
                    "no_show_change_percent": no_show_change_percent,
                    "cancelled_after_booking_count": cancelled_after_booking_count,
                    "cancelled_after_booking_change_percent": cancelled_after_booking_change_percent,
                    "distribution": distribution,
                },
                "bookings_activity": _build_analytics_series(
                    start_date, end_date, dict(current_bookings["activity"])
                ),
                "income_overview": {
                    "balance_amount": _format_amount(income_total),
                    "currency": str(full_property.get("currency") or "UZS"),
                    "bars": _build_analytics_series(
                        start_date,
                        end_date,
                        {
                            key: float(value)
                            for key, value in dict(current_income["bars"]).items()
                        },
                    ),
                },
            },
            status=status.HTTP_200_OK,
        )


# ---------------------------------------------------------------------------
# Partner property list
# ---------------------------------------------------------------------------


class PartnerPropertyListView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    @swagger_auto_schema(
        operation_id="listPartnerProperties",
        operation_summary="List partner properties",
        operation_description="Partner-only. Returns the authenticated partner's own apartments and cottages, including unverified and archived. Supports the same filters as public list.",
        tags=["Property / Partner"],
        manual_parameters=(
            [p for p in PROPERTY_LIST_QUERY_PARAMS if p.name != "property_type"]
            + [
                openapi.Parameter(
                    "property_type",
                    openapi.IN_QUERY,
                    type=openapi.TYPE_STRING,
                    enum=["apartment", "cottage"],
                    required=False,
                    description="Filter by property kind. Omit to return both.",
                ),
            ]
        ),
        responses={
            200: MIXED_PROPERTY_LIST_RESPONSE_SCHEMA,
            401: _ERROR_DETAIL_SCHEMA,
            403: _ERROR_DETAIL_SCHEMA,
        },
    )
    def get(self, request, *args, **kwargs):
        property_type = parse_property_kind(request.query_params.get("property_type"))
        ctx = {"request": request}
        partner_id = int(request.user.id)

        if property_type == PROPERTY_KIND_APARTMENT:
            rows = _list_apartment_rows(
                request.query_params,
                public_only=False,
                partner_user_id=partner_id,
                default_limit=_DEFAULT_PARTNER_LIST_LIMIT,
            )
            return Response(
                ApartmentPartnerListSerializer(rows, many=True, context=ctx).data
            )
        if property_type == PROPERTY_KIND_COTTAGE:
            rows = _list_cottage_rows(
                request.query_params,
                public_only=False,
                partner_user_id=partner_id,
                default_limit=_DEFAULT_PARTNER_LIST_LIMIT,
            )
            return Response(
                CottagePartnerListSerializer(rows, many=True, context=ctx).data
            )

        apt_rows = _list_apartment_rows(
            request.query_params,
            public_only=False,
            partner_user_id=partner_id,
            default_limit=_DEFAULT_PARTNER_LIST_LIMIT,
        )
        cot_rows = _list_cottage_rows(
            request.query_params,
            public_only=False,
            partner_user_id=partner_id,
            default_limit=_DEFAULT_PARTNER_LIST_LIMIT,
        )
        data = (
            ApartmentPartnerListSerializer(apt_rows, many=True, context=ctx).data
            + CottagePartnerListSerializer(cot_rows, many=True, context=ctx).data
        )
        return Response(data, status=status.HTTP_200_OK)


class PartnerAllPropertyListView(APIView):
    authentication_classes = [AdminJWTAuthentication, PartnerJWTAuthentication]
    permission_classes = [IsPartnerOrAdmin]

    @swagger_auto_schema(
        operation_id="listAllPartnerProperties",
        operation_summary="List all properties for a partner",
        operation_description="Admin or Partner. Returns every property owned by a partner (or the authenticated partner). Admins can pass partner_id to query another partner's listings.",
        tags=["Property / Partner"],
        manual_parameters=PROPERTY_LIST_QUERY_PARAMS
        + [
            openapi.Parameter(
                "partner_id",
                openapi.IN_QUERY,
                type=openapi.TYPE_INTEGER,
                required=False,
                description="Admin only: target partner user id. Partners ignore this and always use the JWT subject.",
            ),
        ],
        responses={
            200: MIXED_PROPERTY_LIST_RESPONSE_SCHEMA,
            401: _ERROR_DETAIL_SCHEMA,
            403: _ERROR_DETAIL_SCHEMA,
        },
    )
    def get(self, request, *args, **kwargs):
        ctx = {"request": request}
        role = getattr(request.user, "role", None)
        if role == "admin":
            raw = request.query_params.get("partner_id")
            if raw is None or str(raw).strip() == "":
                raise ValidationError(
                    {
                        "partner_id": _(
                            "This field is required when using an admin token."
                        )
                    }
                )
            try:
                partner_id = int(str(raw).strip())
            except (TypeError, ValueError):
                raise ValidationError({"partner_id": _("Enter a valid integer.")})
            if get_user_by_id(partner_id, role="partner", active_only=True) is None:
                raise ValidationError(
                    {"partner_id": _("No active partner account found for this id.")}
                )
        else:
            partner_id = int(request.user.id)
        apt_rows = _list_apartment_rows(
            request.query_params,
            public_only=False,
            partner_user_id=partner_id,
            default_limit=None,
        )
        cot_rows = _list_cottage_rows(
            request.query_params,
            public_only=False,
            partner_user_id=partner_id,
            default_limit=None,
        )
        data = (
            ApartmentPartnerListSerializer(apt_rows, many=True, context=ctx).data
            + CottagePartnerListSerializer(cot_rows, many=True, context=ctx).data
        )
        return Response(data, status=status.HTTP_200_OK)


class AdminAllPropertiesListView(APIView):
    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        tags=["Admin / Property"],
        manual_parameters=(
            [p for p in PROPERTY_LIST_QUERY_PARAMS if p.name != "property_type"]
            + [
                openapi.Parameter(
                    "property_type",
                    openapi.IN_QUERY,
                    type=openapi.TYPE_STRING,
                    enum=["apartment", "cottage", "hotel", "apartments", "cottages", "hotels"],
                    required=False,
                    description="Optional. Omit to return apartments, cottages, and hotels together.",
                ),
            ]
        ),
        responses={200: MIXED_PROPERTY_LIST_RESPONSE_SCHEMA},
        operation_summary="List all properties (admin)",
        operation_description=(
            "Returns every apartment, cottage, and hotel in the database, including unverified and archived. "
            "Supports the same filters as public list (search, region, price, sort, limit, etc.)."
        ),
    )
    def get(self, request, *args, **kwargs):
        ctx = {"request": request}
        requested_kind = parse_property_kind(request.query_params.get("property_type"))
        list_kwargs = dict(
            public_only=False,
            include_all_records=True,
            default_limit=None,
        )
        if requested_kind == PROPERTY_KIND_APARTMENT:
            rows = _attach_partner_users(
                _list_apartment_rows(request.query_params, **list_kwargs)
            )
            return Response(
                ApartmentAdminListSerializer(rows, many=True, context=ctx).data
            )
        if requested_kind == PROPERTY_KIND_COTTAGE:
            rows = _attach_partner_users(
                _list_cottage_rows(request.query_params, **list_kwargs)
            )
            return Response(
                CottageAdminListSerializer(rows, many=True, context=ctx).data
            )
        if requested_kind == PROPERTY_KIND_HOTEL:
            rows = list_admin_hotels(
                search=request.query_params.get("search"),
                organization_id=_parse_int(request.query_params.get("organization_id")),
                tenant_schema=request.query_params.get("tenant_schema"),
                is_active=_parse_bool(request.query_params.get("is_active")),
                created_from=_parse_date(request.query_params.get("created_from")),
                created_to=_parse_date(request.query_params.get("created_to")),
            )
            return Response(
                HotelAdminListSerializer(rows, many=True, context=ctx).data
            )
        apt_rows = _attach_partner_users(
            _list_apartment_rows(request.query_params, **list_kwargs)
        )
        cot_rows = _attach_partner_users(
            _list_cottage_rows(request.query_params, **list_kwargs)
        )
        hotel_rows = list_admin_hotels(
            search=request.query_params.get("search"),
            organization_id=_parse_int(request.query_params.get("organization_id")),
            tenant_schema=request.query_params.get("tenant_schema"),
            is_active=_parse_bool(request.query_params.get("is_active")),
            created_from=_parse_date(request.query_params.get("created_from")),
            created_to=_parse_date(request.query_params.get("created_to")),
        )
        data = (
            ApartmentAdminListSerializer(apt_rows, many=True, context=ctx).data
            + CottageAdminListSerializer(cot_rows, many=True, context=ctx).data
            + HotelAdminListSerializer(hotel_rows, many=True, context=ctx).data
        )
        return Response(data, status=status.HTTP_200_OK)


class AdminApartmentListCreateView(APIView):
    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        tags=["Admin / Property"],
        operation_summary="Create apartment (admin)",
        operation_description="Admin-only apartment creation endpoint.",
        request_body=ApartmentAdminUpdateSerializer,
        responses={201: ApartmentAdminListSerializer},
    )
    def post(self, request, *args, **kwargs):
        serializer = ApartmentAdminUpdateSerializer(
            data=request.data,
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        prepared = serializer.validated_data.get("values") or {}
        title = str(prepared.get("title") or "").strip()
        if not title:
            raise ValidationError({"title": _("This field is required.")})
        prepared.setdefault("title", title)
        prepared.setdefault("title_sort", title.lower())
        partner_user_id = _parse_int(request.data.get("partner_user_id"))
        created = admin_create_apartment(
            values=prepared,
            admin_user_id=getattr(request.user, "id", None),
            partner_user_id=partner_user_id,
        )
        if not created:
            raise APIException(_("Failed to create apartment"))
        partner_id = _parse_int(created.get("partner_user_id"))
        created = dict(created)
        created["partner_user"] = (
            _serialize_partner_user(get_user_by_id(partner_id))
            if partner_id is not None
            else None
        )
        ctx = {"request": request}
        return Response(
            ApartmentAdminListSerializer(created, context=ctx).data,
            status=status.HTTP_201_CREATED,
        )


class AdminCottageListCreateView(APIView):
    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        tags=["Admin / Property"],
        operation_summary="Create cottage (admin)",
        operation_description="Admin-only cottage creation endpoint.",
        request_body=CottageAdminUpdateSerializer,
        responses={201: CottageAdminListSerializer},
    )
    def post(self, request, *args, **kwargs):
        serializer = CottageAdminUpdateSerializer(
            data=request.data,
            partial=False,
            context={"is_admin": True, "request": request},
        )
        serializer.is_valid(raise_exception=True)
        normalized = serializer.validated_data.get("normalized_values") or {}
        partner_user_id = _parse_int(request.data.get("partner_user_id"))
        created = admin_create_cottage(
            values=normalized,
            admin_user_id=getattr(request.user, "id", None),
            partner_user_id=partner_user_id,
        )
        if not created:
            raise APIException(_("Failed to create cottage"))
        partner_id = _parse_int(created.get("partner_user_id"))
        created = dict(created)
        created["partner_user"] = (
            _serialize_partner_user(get_user_by_id(partner_id))
            if partner_id is not None
            else None
        )
        ctx = {"request": request}
        return Response(
            CottageAdminListSerializer(created, context=ctx).data,
            status=status.HTTP_201_CREATED,
        )


class AdminHotelOrganizationListView(APIView):
    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        tags=["Admin / Property"],
        operation_summary="List hotel organizations (admin)",
        operation_description="Returns PMS tenant organizations available for hotel administration.",
        responses={200: openapi.Schema(type=openapi.TYPE_ARRAY, items=openapi.Schema(type=openapi.TYPE_OBJECT))},
    )
    def get(self, request, *args, **kwargs):
        return Response(list_hotel_organizations(), status=status.HTTP_200_OK)


class AdminHotelListCreateView(APIView):
    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        tags=["Admin / Property"],
        operation_summary="Create hotel (admin)",
        operation_description="Admin-only hotel creation endpoint targeting a PMS tenant schema.",
        request_body=HotelAdminUpdateSerializer,
        responses={201: HotelAdminListSerializer},
    )
    def post(self, request, *args, **kwargs):
        serializer = HotelAdminUpdateSerializer(
            data=request.data,
            partial=False,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        prepared = serializer.validated_data.get("values") or {}
        tenant_schema = str(prepared.pop("tenant_schema", "") or "").strip()
        if not tenant_schema:
            raise ValidationError({"tenant_schema": _("This field is required.")})
        title = str(prepared.get("name") or "").strip()
        if not title:
            raise ValidationError({"title": _("This field is required.")})
        organization = get_organization_by_id(_parse_int(prepared.get("organization_id")) or 0)
        if organization is None:
            for candidate in list_organizations():
                if str(candidate.get("schema_name") or "") == tenant_schema:
                    organization = candidate
                    break
        if organization is None:
            raise ValidationError({"organization_id": _("Organization not found.")})
        prepared["organization_id"] = organization.get("id")
        created = create_admin_hotel(schema_name=tenant_schema, values=prepared)
        if not created:
            raise APIException(_("Failed to create hotel"))
        return Response(
            HotelAdminListSerializer(created, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class AdminHotelPatchView(APIView):
    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        tags=["Admin / Property"],
        operation_summary="Retrieve hotel (admin)",
        operation_description="Returns the full admin view of a hotel by its tenant-aware guid.",
        responses={200: HotelAdminListSerializer},
    )
    def get(self, request, hotel_id, *args, **kwargs):
        row = get_admin_hotel(str(hotel_id))
        if not row:
            raise NotFound(_("Hotel not found"))
        return Response(
            HotelAdminListSerializer(row, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        tags=["Admin / Property"],
        operation_summary="Patch hotel (admin)",
        operation_description="Admin-only partial update for hotel records stored in PMS tenant schemas.",
        request_body=HotelAdminUpdateSerializer,
        responses={200: HotelAdminListSerializer},
    )
    def patch(self, request, hotel_id, *args, **kwargs):
        current = get_admin_hotel(str(hotel_id))
        if not current:
            raise NotFound(_("Hotel not found"))
        serializer = HotelAdminUpdateSerializer(
            data=request.data,
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        prepared = serializer.validated_data.get("values") or {}
        updated = update_admin_hotel(hotel_guid=str(hotel_id), values=prepared)
        if not updated:
            raise NotFound(_("Hotel not found"))
        return Response(
            HotelAdminListSerializer(updated, context={"request": request}).data,
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        tags=["Admin / Property"],
        operation_summary="Delete hotel (admin)",
        operation_description="Admin-only soft delete for hotel records in PMS tenant schemas.",
        responses={204: None},
    )
    def delete(self, request, hotel_id, *args, **kwargs):
        deleted = delete_admin_hotel(hotel_guid=str(hotel_id))
        if not deleted:
            raise NotFound(_("Hotel not found"))
        return Response(status=status.HTTP_204_NO_CONTENT)


class AdminRegionListView(APIView):
    """Admin-only list of all regions."""

    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        tags=["Admin / Property"],
        operation_summary="List regions (admin)",
        operation_description="Returns all regions without caching (admin access).",
        responses={200: RegionListSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        rows = list_regions()
        data = []
        for row in rows:
            payload = dict(row)
            payload["img"] = _build_media_url(request, payload.get("img"))
            data.append(payload)
        return Response(
            RegionListSerializer(data, many=True).data, status=status.HTTP_200_OK
        )


class AdminDistrictListView(APIView):
    """Admin-only list of districts, optionally filtered by region."""

    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        tags=["Admin / Property"],
        operation_summary="List districts (admin)",
        operation_description="Returns districts, optionally filtered by region_id or region guid.",
        manual_parameters=[
            openapi.Parameter(
                "region_id",
                openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                required=False,
                description="Optional region id (integer) or region guid.",
            ),
        ],
        responses={200: DistrictListSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        region_id = _parse_region_id_or_guid(request.query_params.get("region_id"))
        rows = list_districts(region_id=region_id)
        data = []
        for row in rows:
            data.append(
                {
                    "id": row.get("id"),
                    "guid": row.get("guid"),
                    "title": row.get("title"),
                    "region_id": row.get("region_id"),
                    "region": {
                        "id": row.get("region_id"),
                        "guid": row.get("region_guid"),
                        "title": row.get("region_title"),
                        "img": _build_media_url(request, row.get("region_img")),
                    }
                    if row.get("region_guid")
                    else None,
                }
            )
        return Response(
            DistrictListSerializer(data, many=True).data, status=status.HTTP_200_OK
        )


class AdminPrefectureListView(APIView):
    """Admin-only list of prefectures, optionally filtered by district."""

    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        tags=["Admin / Property"],
        operation_summary="List prefectures (admin)",
        operation_description="Returns prefectures, optionally filtered by district_id or district_guid.",
        manual_parameters=[
            openapi.Parameter(
                "district_id",
                openapi.IN_QUERY,
                type=openapi.TYPE_INTEGER,
                required=False,
                description="Optional district id filter.",
            ),
            openapi.Parameter(
                "district_guid",
                openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                required=False,
                description="Optional district guid filter.",
            ),
        ],
        responses={200: PrefectureListSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        district_id = _parse_int(request.query_params.get("district_id"))
        district_guid = (
            request.query_params.get("district_guid") or ""
        ).strip() or None
        rows = list_prefectures(district_id=district_id, district_guid=district_guid)
        data = []
        for row in rows:
            data.append(
                {
                    "guid": row.get("guid"),
                    "title": row.get("title"),
                    "district": {
                        "guid": row.get("district_guid"),
                        "title": row.get("district_title"),
                    }
                    if row.get("district_guid")
                    else None,
                }
            )
        return Response(
            PrefectureListSerializer(data, many=True).data, status=status.HTTP_200_OK
        )


class AdminApartmentPatchView(APIView):
    """Admin-only endpoint for patching any field of an apartment record."""

    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        tags=["Admin / Property"],
        operation_summary="Retrieve apartment (admin)",
        operation_description="Returns the full admin view of an apartment by its guid.",
        responses={200: ApartmentAdminListSerializer},
    )
    def get(self, request, apartment_id, *args, **kwargs):
        row = admin_get_apartment(str(apartment_id))
        if not row:
            raise NotFound(_("Apartment not found"))
        partner_id = _parse_int(row.get("partner_user_id"))
        row = dict(row)
        row["partner_user"] = (
            _serialize_partner_user(get_user_by_id(partner_id))
            if partner_id is not None
            else None
        )
        ctx = {"request": request}
        return Response(
            ApartmentAdminListSerializer(row, context=ctx).data,
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        tags=["Admin / Property"],
        operation_summary="Patch apartment (Admin)",
        operation_description=(
            "Admin-only full update for every writable field on the apartment table, "
        ),
        request_body=ApartmentAdminUpdateSerializer,
        responses={200: ApartmentAdminListSerializer},
    )
    def patch(self, request, apartment_id, *args, **kwargs):
        current = admin_get_apartment(str(apartment_id))
        if not current:
            raise NotFound(_("Apartment not found"))

        serializer = ApartmentAdminUpdateSerializer(
            data=request.data,
            partial=True,
            context={"request": request},
        )
        serializer.is_valid(raise_exception=True)
        prepared = serializer.validated_data.get("values") or {}

        logger.info(
            "partner_apartment_patch apartment_guid=%s partner_user_id=%s prepared_keys=%s",
            apartment_id,
            getattr(request.user, "id", None),
            sorted(prepared.keys()),
        )

        updated = admin_update_apartment(
            apartment_guid=str(apartment_id),
            values=prepared,
            admin_user_id=getattr(request.user, "id", None),
        )
        if not updated:
            raise NotFound(_("Apartment not found"))
        partner_id = _parse_int(updated.get("partner_user_id"))
        updated = dict(updated)
        updated["partner_user"] = (
            _serialize_partner_user(get_user_by_id(partner_id))
            if partner_id is not None
            else None
        )
        ctx = {"request": request}
        return Response(
            ApartmentAdminListSerializer(updated, context=ctx).data,
            status=status.HTTP_200_OK,
        )


class AdminCottagePatchView(APIView):
    """Admin-only endpoint for patching any field of a cottage record."""

    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        tags=["Admin / Property"],
        operation_summary="Retrieve cottage (admin)",
        operation_description="Returns the full admin view of a cottage by its guid.",
        responses={200: CottageAdminListSerializer},
    )
    def get(self, request, cottage_id, *args, **kwargs):
        row = admin_get_cottage(str(cottage_id))
        if not row:
            raise NotFound(_("Cottage not found"))
        partner_id = _parse_int(row.get("partner_user_id"))
        row = dict(row)
        row["partner_user"] = (
            _serialize_partner_user(get_user_by_id(partner_id))
            if partner_id is not None
            else None
        )
        ctx = {"request": request}
        return Response(
            CottageAdminListSerializer(row, context=ctx).data, status=status.HTTP_200_OK
        )

    @swagger_auto_schema(
        tags=["Admin / Property"],
        operation_summary="Patch cottage (admin)",
        operation_description=(
            "Admin-only partial update for every writable field on the cottage table, "
            "including verification/archival/recommendation flags and owner reassignment. "
            "Unlike the partner endpoint, this does NOT auto-reset verification on save."
        ),
        request_body=CottageAdminUpdateSerializer,
        responses={200: CottageAdminListSerializer},
    )
    def patch(self, request, cottage_id, *args, **kwargs):
        current = admin_get_cottage(str(cottage_id))
        if not current:
            raise NotFound(_("Cottage not found"))

        serializer = CottageAdminUpdateSerializer(
            data=request.data,
            partial=True,
            context={"is_update": True, "is_admin": True, "request": request},
        )
        serializer.is_valid(raise_exception=True)
        normalized = serializer.validated_data.get("normalized_values") or {}

        logger.info(
            "admin_cottage_patch cottage_guid=%s admin_user_id=%s normalized_keys=%s",
            cottage_id,
            getattr(request.user, "id", None),
            sorted(normalized.keys()),
        )

        updated = admin_update_cottage(
            cottage_guid=str(cottage_id),
            values=normalized,
            admin_user_id=getattr(request.user, "id", None),
        )
        if not updated:
            raise NotFound(_("Cottage not found"))
        partner_id = _parse_int(updated.get("partner_user_id"))
        updated = dict(updated)
        updated["partner_user"] = (
            _serialize_partner_user(get_user_by_id(partner_id))
            if partner_id is not None
            else None
        )
        ctx = {"request": request}
        return Response(
            CottageAdminListSerializer(updated, context=ctx).data,
            status=status.HTTP_200_OK,
        )

    @swagger_auto_schema(
        tags=["Admin / Property"],
        operation_id="deleteAdminCottage",
        operation_summary="Delete cottage (admin)",
        operation_description="Admin-only hard delete of a cottage by its guid.",
        responses={
            204: None,
            401: _ERROR_DETAIL_SCHEMA,
            403: _ERROR_DETAIL_SCHEMA,
            404: _ERROR_DETAIL_SCHEMA,
        },
    )
    def delete(self, request, cottage_id, *args, **kwargs):
        try:
            deleted = admin_delete_cottage(cottage_guid=str(cottage_id))
        except Exception as exc:
            logger.exception("admin_cottage_delete failed cottage_guid=%s", cottage_id)
            raise APIException(_("Failed to delete cottage")) from exc
        if not deleted:
            raise NotFound(_("Cottage not found"))
        return Response(status=status.HTTP_204_NO_CONTENT)


class ApartmentPartnerPropertyListView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    @swagger_auto_schema(
        operation_id="listPartnerApartments",
        operation_summary="List partner apartments",
        operation_description="Partner-only. Returns the authenticated partner's own apartments, including unverified and archived. Supports the same filters as public list.",
        tags=["Property / Partner"],
        manual_parameters=PROPERTY_LIST_QUERY_PARAMS,
        responses={
            200: ApartmentPartnerListSerializer(many=True),
            401: _ERROR_DETAIL_SCHEMA,
            403: _ERROR_DETAIL_SCHEMA,
        },
    )
    def get(self, request, *args, **kwargs):
        ctx = {"request": request}
        rows = _list_apartment_rows(
            request.query_params,
            public_only=False,
            partner_user_id=int(request.user.id),
            default_limit=_DEFAULT_PARTNER_LIST_LIMIT,
        )
        return Response(
            ApartmentPartnerListSerializer(rows, many=True, context=ctx).data,
            status=status.HTTP_200_OK,
        )


class CottagePartnerPropertyListView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    @swagger_auto_schema(
        operation_id="listPartnerCottages",
        operation_summary="List partner cottages",
        operation_description="Partner-only. Returns the authenticated partner's own cottages, including unverified and archived. Supports the same filters as public list.",
        tags=["Property / Partner"],
        manual_parameters=PROPERTY_LIST_QUERY_PARAMS,
        responses={
            200: CottagePartnerListSerializer(many=True),
            401: _ERROR_DETAIL_SCHEMA,
            403: _ERROR_DETAIL_SCHEMA,
        },
    )
    def get(self, request, *args, **kwargs):
        ctx = {"request": request}
        rows = _list_cottage_rows(
            request.query_params,
            public_only=False,
            partner_user_id=int(request.user.id),
            default_limit=_DEFAULT_PARTNER_LIST_LIMIT,
        )
        return Response(
            CottagePartnerListSerializer(rows, many=True, context=ctx).data,
            status=status.HTTP_200_OK,
        )


# ---------------------------------------------------------------------------
# Favorites
# ---------------------------------------------------------------------------


class SavedPropertyListView(APIView):
    authentication_classes = [ClientJWTAuthentication]
    permission_classes = [IsClient]

    @swagger_auto_schema(
        operation_id="listSavedProperties",
        operation_summary="List saved (favorite) properties",
        operation_description="Client-only. Returns the authenticated client's favorited properties (apartments and cottages). Supports the same filters as public list.",
        tags=["Property / Client"],
        manual_parameters=PROPERTY_LIST_QUERY_PARAMS,
        responses={
            200: MIXED_PROPERTY_LIST_RESPONSE_SCHEMA,
            401: _ERROR_DETAIL_SCHEMA,
            403: _ERROR_DETAIL_SCHEMA,
        },
    )
    def get(self, request, *args, **kwargs):
        favorite_guids = _load_favorite_guids(int(request.user.id))
        if not favorite_guids:
            return Response([], status=status.HTTP_200_OK)
        ctx = {"request": request, "favorite_guids": favorite_guids}
        apt_rows = [
            r
            for r in _list_apartment_rows(request.query_params, public_only=True)
            if str(r.get("guid")) in favorite_guids
        ]
        cot_rows = [
            r
            for r in _list_cottage_rows(request.query_params, public_only=True)
            if str(r.get("guid")) in favorite_guids
        ]
        data = (
            ApartmentListSerializer(apt_rows, many=True, context=ctx).data
            + CottageListSerializer(cot_rows, many=True, context=ctx).data
        )
        return Response(data, status=status.HTTP_200_OK)


class PropertyFavoriteToggleView(APIView):
    authentication_classes = [ClientJWTAuthentication]
    permission_classes = [IsClient]

    @swagger_auto_schema(
        operation_id="togglePropertyFavorite",
        operation_summary="Toggle property favorite",
        operation_description="Client-only. Adds the property to favorites if not present, or removes it if already favorited. Returns the new is_favorite state.",
        tags=["Property / Client"],
        manual_parameters=[
            openapi.Parameter(
                "property_id",
                openapi.IN_PATH,
                type=openapi.TYPE_STRING,
                format="uuid",
                description="Property GUID.",
            ),
        ],
        responses={
            200: openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    "detail": openapi.Schema(type=openapi.TYPE_STRING),
                    "is_favorite": openapi.Schema(type=openapi.TYPE_BOOLEAN),
                },
            ),
            201: openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    "detail": openapi.Schema(type=openapi.TYPE_STRING),
                    "is_favorite": openapi.Schema(type=openapi.TYPE_BOOLEAN),
                },
            ),
            401: _ERROR_DETAIL_SCHEMA,
            403: _ERROR_DETAIL_SCHEMA,
            404: _ERROR_DETAIL_SCHEMA,
        },
    )
    def post(self, request, property_id, *args, **kwargs):
        row = _get_property_for_public(str(property_id))
        if not row:
            raise NotFound(_("Property not found"))
        favorite_guids = _load_favorite_guids(int(request.user.id))
        guid = str(row["guid"])
        if guid in favorite_guids:
            favorite_guids.remove(guid)
            _store_favorite_guids(int(request.user.id), favorite_guids)
            return Response(
                {"detail": _("Removed from favorites"), "is_favorite": False},
                status=status.HTTP_200_OK,
            )
        favorite_guids.add(guid)
        _store_favorite_guids(int(request.user.id), favorite_guids)
        return Response(
            {"detail": _("Added to favorites"), "is_favorite": True},
            status=status.HTTP_201_CREATED,
        )

    @swagger_auto_schema(
        operation_id="removePropertyFavorite",
        operation_summary="Remove property from favorites",
        operation_description="Client-only. Removes a property from the authenticated client's favorites.",
        tags=["Property / Client"],
        manual_parameters=[
            openapi.Parameter(
                "property_id",
                openapi.IN_PATH,
                type=openapi.TYPE_STRING,
                format="uuid",
                description="Property GUID.",
            ),
        ],
        responses={
            200: openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    "detail": openapi.Schema(type=openapi.TYPE_STRING),
                    "is_favorite": openapi.Schema(type=openapi.TYPE_BOOLEAN),
                },
            ),
            401: _ERROR_DETAIL_SCHEMA,
            403: _ERROR_DETAIL_SCHEMA,
            404: _ERROR_DETAIL_SCHEMA,
        },
    )
    def delete(self, request, property_id, *args, **kwargs):
        row = _get_property_for_public(str(property_id))
        if not row:
            raise NotFound(_("Property not found"))
        favorite_guids = _load_favorite_guids(int(request.user.id))
        favorite_guids.discard(str(row["guid"]))
        _store_favorite_guids(int(request.user.id), favorite_guids)
        return Response(
            {"detail": _("Removed from favorites"), "is_favorite": False},
            status=status.HTTP_200_OK,
        )
