from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from django.core.cache import cache
from django.core.files.storage import default_storage
from django.conf import settings
from django.utils.translation import gettext_lazy as _
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema

from rest_framework import parsers, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from shared.raw.db import fetch_all

from shared.permissions import IsClient, IsPartner
from users.authentication import ClientJWTAuthentication, PartnerJWTAuthentication

from .apartment_repository import (
    APARTMENT_TYPE_GUID,
    COTTAGE_TYPE_GUID,
    PROPERTY_KIND_APARTMENT,
    PROPERTY_KIND_COTTAGE,
    parse_property_kind,
    list_property_types,
    list_property_services,
    list_regions,
    list_districts,
    list_prefectures,
    list_reviews,
    has_eligible_booking_for_review,
    create_review,
    prepare_property_rows,
)
from .serializers import PropertyServiceListSerializer, RegionListSerializer, DistrictListSerializer, PrefectureListSerializer
from .apartment_repository import (
    list_apartments,
    get_apartment_for_public,
    get_apartment_for_partner,
    create_apartment,
    update_apartment,
    delete_apartment,
    set_apartment_primary_image,
    effective_apartment_price,
)
from .cottage_repository import (
    list_cottages,
    get_cottage_for_public,
    get_cottage_for_partner,
    create_cottage,
    update_cottage,
    delete_cottage,
    set_cottage_primary_image,
    effective_cottage_price,
)
from .apartment_serializers import (
    ApartmentListSerializer,
    ApartmentPartnerListSerializer,
    ApartmentDetailSerializer,
    ApartmentCreateSerializer,
    ApartmentUpdateSerializer,
)
from .cottage_serializers import (
    CottageListSerializer,
    CottagePartnerListSerializer,
    CottageDetailSerializer,
    CottageCreateSerializer,
    CottageUpdateSerializer,
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
from rest_framework import serializers


_FAVORITES_CACHE_TTL_SECONDS = 60 * 60 * 24 * 30


PROPERTY_LIST_QUERY_PARAMS = [
    openapi.Parameter("search", openapi.IN_QUERY, type=openapi.TYPE_STRING),
    openapi.Parameter("region_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
    openapi.Parameter("district_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
    openapi.Parameter("corporate", openapi.IN_QUERY, type=openapi.TYPE_BOOLEAN),
    openapi.Parameter("min_price", openapi.IN_QUERY, type=openapi.TYPE_NUMBER),
    openapi.Parameter("max_price", openapi.IN_QUERY, type=openapi.TYPE_NUMBER),
    openapi.Parameter("currency", openapi.IN_QUERY, type=openapi.TYPE_STRING),
    openapi.Parameter("sort", openapi.IN_QUERY, type=openapi.TYPE_STRING),
    openapi.Parameter("ordering", openapi.IN_QUERY, type=openapi.TYPE_STRING),
    openapi.Parameter("from_date", openapi.IN_QUERY, type=openapi.TYPE_STRING, format="date"),
    openapi.Parameter("limit", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
]

LOCATION_QUERY_PARAMS = [
    openapi.Parameter("region_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
    openapi.Parameter("district_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
]

RECOMMENDATIONS_QUERY_PARAMS = PROPERTY_LIST_QUERY_PARAMS + [
    openapi.Parameter("kind", openapi.IN_QUERY, type=openapi.TYPE_STRING, enum=["property", "apartment", "cottage"]),
    openapi.Parameter("type", openapi.IN_QUERY, type=openapi.TYPE_STRING, enum=["featured", "best-by-reviews", "most-booked"]),
]

PROPERTY_FILTER_BY_LINK_SCHEMA = openapi.Schema(
    type=openapi.TYPE_OBJECT,
    properties={
        "url": openapi.Schema(type=openapi.TYPE_STRING),
        "link": openapi.Schema(type=openapi.TYPE_STRING),
    },
)


class RawPropertyTypeSerializer(serializers.Serializer):
    guid = serializers.UUIDField()
    title = serializers.CharField()
    icon_url = serializers.CharField(allow_null=True)


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
                "title": _select_title({"name": row.get("name"), "ru_name": row.get("ru_name")}, language),
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
                "guid": district["guid"],
                "title": district["title"],
                "prefectures": prefectures_by_district.get(district_id, []),
            }
        )

    return [
        {
            "guid": region["guid"],
            "title": region["title"],
            "districts": districts_by_region.get(int(region["id"]), []),
        }
        for region in regions
    ]


def _resolve_reference_date(value) -> date:
    raw = (str(value).strip() if value is not None else "")
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


def _validate_image_upload(uploaded):
    if uploaded is None:
        raise ValidationError({"image": [_("This field is required.")]})
    max_size = getattr(settings, "MAX_IMAGE_SIZE", 20 * 1024 * 1024)
    allowed_ext = {ext.lower() for ext in getattr(settings, "ALLOWED_PHOTO_EXTENSION", [])}
    name = (uploaded.name or "").lower()
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    content_type = (uploaded.content_type or "").lower()
    if allowed_ext and ext not in allowed_ext:
        raise ValidationError({"image": [_("Unsupported file extension.")]})
    if content_type and not content_type.startswith("image/"):
        raise ValidationError({"image": [_("Only image uploads are allowed.")]})
    if uploaded.size and uploaded.size > max_size:
        raise ValidationError({"image": [_("File too large.")]})
    return True


def _extract_list_params(source):
    return {
        "search": _source_get(source, "search"),
        "region_id": _parse_int(_source_get(source, "region_id") or _source_get(source, "location_id")),
        "district_id": _parse_int(_source_get(source, "district_id")),
        "corporate": _parse_bool(_source_get(source, "corporate")),
    }


def _extract_prepare_params(source, *, default_ordering="-created_at", default_limit=None):
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


def _list_apartment_rows(source, *, public_only, partner_user_id=None, recommended_only=False, default_ordering="-created_at", default_limit=None):
    lp = _extract_list_params(source)
    rows = list_apartments(public_only=public_only, partner_user_id=partner_user_id, recommended_only=recommended_only, **lp)
    pp = _extract_prepare_params(source, default_ordering=default_ordering, default_limit=default_limit)
    return prepare_property_rows(rows, **pp)


def _list_cottage_rows(source, *, public_only, partner_user_id=None, recommended_only=False, default_ordering="-created_at", default_limit=None):
    lp = _extract_list_params(source)
    rows = list_cottages(public_only=public_only, partner_user_id=partner_user_id, recommended_only=recommended_only, **lp)
    pp = _extract_prepare_params(source, default_ordering=default_ordering, default_limit=default_limit)
    return prepare_property_rows(rows, **pp)


# ---------------------------------------------------------------------------
# Static / simple views
# ---------------------------------------------------------------------------

class PropertyTypeListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        serializer = RawPropertyTypeSerializer(list_property_types(), many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class PropertyServiceListView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(responses={200: PropertyServiceListSerializer(many=True)})
    def get(self, request, *args, **kwargs):
        rows = list_property_services()
        data = []
        for row in rows:
            payload = dict(row)
            payload["icon_url"] = _build_media_url(request, payload.get("icon_url"))
            data.append(payload)
        serializer = PropertyServiceListSerializer(data, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class RegionListView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(responses={200: RegionListSerializer(many=True)})
    def get(self, request, *args, **kwargs):
        rows = list_regions()
        data = []
        for row in rows:
            payload = dict(row)
            payload["img"] = _build_media_url(request, payload.get("img"))
            data.append(payload)
        serializer = RegionListSerializer(data, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class DistrictListView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        manual_parameters=[openapi.Parameter("region_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER)],
        responses={200: DistrictListSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        region_id = _parse_int(request.query_params.get("region_id"))
        rows = list_districts(region_id=region_id)
        data = []
        for row in rows:
            data.append(
                {
                    "guid": row.get("guid"),
                    "title": row.get("title"),
                    "region": {
                        "guid": row.get("region_guid"),
                        "title": row.get("region_title"),
                        "img": _build_media_url(request, row.get("region_img")),
                    } if row.get("region_guid") else None,
                }
            )
        serializer = DistrictListSerializer(data, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class PrefectureListView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter("district_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter("district_guid", openapi.IN_QUERY, type=openapi.TYPE_STRING),
        ],
        responses={200: PrefectureListSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        district_id = _parse_int(request.query_params.get("district_id"))
        district_guid = (request.query_params.get("district_guid") or "").strip() or None
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
        serializer = PrefectureListSerializer(data, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class LocationListView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(responses={200: RegionsResponseSerializer()})
    def get(self, request, *args, **kwargs):
        rows = list_regions()
        data = []
        for row in rows:
            payload = dict(row)
            payload["img"] = _build_media_url(request, payload.get("img"))
            data.append(payload)
        serializer = RegionListSerializer(data, many=True)
        return Response({"regions": serializer.data}, status=status.HTTP_200_OK)


class CategoryListView(APIView):
    permission_classes = [AllowAny]
    def get(self, request, *args, **kwargs):
        return Response([], status=status.HTTP_200_OK)


class CategoryLatestPropertyListView(APIView):
    permission_classes = [AllowAny]
    def get(self, request, *args, **kwargs):
        return Response([], status=status.HTTP_200_OK)


class CategoryPropertyRecommendationView(APIView):
    permission_classes = [AllowAny]
    def get(self, request, *args, **kwargs):
        return Response([], status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Recommendations (unified, returns both kinds)
# ---------------------------------------------------------------------------

class UnifiedRecommendationsListView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(manual_parameters=RECOMMENDATIONS_QUERY_PARAMS)
    def get(self, request, *args, **kwargs):
        property_type = str(request.query_params.get("kind") or "property").strip().lower()
        if property_type not in {"property", "apartment", "cottage", "apartments", "cottages"}:
            return Response([], status=status.HTTP_200_OK)

        rec_type = str(request.query_params.get("type") or "featured").strip().lower()
        ordering = "-created_at"
        if rec_type == "best-by-reviews":
            ordering = "-average_rating"
        elif rec_type == "most-booked":
            ordering = "-review_count"

        ctx = {"request": request, "favorite_guids": _favorite_guids_from_request(request)}

        if property_type in {"apartment", "apartments"}:
            rows = _list_apartment_rows(request.query_params, public_only=True, recommended_only=True, default_ordering=ordering, default_limit=20)
            return Response(ApartmentListSerializer(rows, many=True, context=ctx).data)

        if property_type in {"cottage", "cottages"}:
            rows = _list_cottage_rows(request.query_params, public_only=True, recommended_only=True, default_ordering=ordering, default_limit=20)
            return Response(CottageListSerializer(rows, many=True, context=ctx).data)

        apt_rows = _list_apartment_rows(request.query_params, public_only=True, recommended_only=True, default_ordering=ordering, default_limit=20)
        cot_rows = _list_cottage_rows(request.query_params, public_only=True, recommended_only=True, default_ordering=ordering, default_limit=20)
        data = (
            ApartmentListSerializer(apt_rows, many=True, context=ctx).data
            + CottageListSerializer(cot_rows, many=True, context=ctx).data
        )
        return Response(data, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Apartment views
# ---------------------------------------------------------------------------

class ApartmentPropertyListCreateView(APIView):
    authentication_classes = [PartnerJWTAuthentication]

    def get_authenticators(self):
        if self.request.method == "GET":
            return []
        return super().get_authenticators()

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsPartner()]
        return [AllowAny()]

    @swagger_auto_schema(manual_parameters=PROPERTY_LIST_QUERY_PARAMS)
    def get(self, request, *args, **kwargs):
        rows = _list_apartment_rows(request.query_params, public_only=True)
        ctx = {"request": request, "favorite_guids": _favorite_guids_from_request(request)}
        return Response(ApartmentListSerializer(rows, many=True, context=ctx).data, status=status.HTTP_200_OK)

    @swagger_auto_schema(request_body=ApartmentCreateSerializer)
    def post(self, request, *args, **kwargs):
        serializer = ApartmentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        created = create_apartment(
            partner_user_id=int(request.user.id),
            values=serializer.validated_data["normalized_values"],
        )
        if not created:
            raise ValidationError(_("Apartment could not be created"))
        return Response(
            {"detail": _("Property has been created successfully, please wait while we verify it"), "property_id": str(created["guid"]), "status_code": 201},
            status=status.HTTP_201_CREATED,
        )


# ---------------------------------------------------------------------------
# Cottage views
# ---------------------------------------------------------------------------

class CottagePropertyListCreateView(APIView):
    authentication_classes = [PartnerJWTAuthentication]

    def get_authenticators(self):
        if self.request.method == "GET":
            return []
        return super().get_authenticators()

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsPartner()]
        return [AllowAny()]

    @swagger_auto_schema(manual_parameters=PROPERTY_LIST_QUERY_PARAMS)
    def get(self, request, *args, **kwargs):
        rows = _list_cottage_rows(request.query_params, public_only=True)
        ctx = {"request": request, "favorite_guids": _favorite_guids_from_request(request)}
        return Response(CottageListSerializer(rows, many=True, context=ctx).data, status=status.HTTP_200_OK)

    @swagger_auto_schema(request_body=CottageCreateSerializer)
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
            {"detail": _("Property has been created successfully, please wait while we verify it"), "property_id": str(created["guid"]), "status_code": 201},
            status=status.HTTP_201_CREATED,
        )


# ---------------------------------------------------------------------------
# Kept for URL compat: generic PropertyListCreateView delegates to kind-specific
# ---------------------------------------------------------------------------

class PropertyListCreateView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    forced_property_type: str | None = None

    def get_authenticators(self):
        if self.request.method == "GET":
            return []
        return super().get_authenticators()

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsPartner()]
        return [AllowAny()]

    @swagger_auto_schema(manual_parameters=PROPERTY_LIST_QUERY_PARAMS)
    def get(self, request, *args, **kwargs):
        ctx = {"request": request, "favorite_guids": _favorite_guids_from_request(request)}
        requested_kind = self.forced_property_type or parse_property_kind(
            request.query_params.get("property_type")
        )
        if requested_kind == PROPERTY_KIND_COTTAGE:
            rows = _list_cottage_rows(request.query_params, public_only=True)
            return Response(CottageListSerializer(rows, many=True, context=ctx).data)
        rows = _list_apartment_rows(request.query_params, public_only=True)
        return Response(ApartmentListSerializer(rows, many=True, context=ctx).data)

    def post(self, request, *args, **kwargs):
        if self.forced_property_type == PROPERTY_KIND_COTTAGE:
            serializer = CottageCreateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            created = create_cottage(partner_user_id=int(request.user.id), values=serializer.validated_data["normalized_values"])
        else:
            serializer = ApartmentCreateSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            created = create_apartment(partner_user_id=int(request.user.id), values=serializer.validated_data["normalized_values"])
        if not created:
            raise ValidationError(_("Property could not be created"))
        return Response(
            {"detail": _("Property has been created successfully, please wait while we verify it"), "property_id": str(created["guid"]), "status_code": 201},
            status=status.HTTP_201_CREATED,
        )


class PropertyFilterByLinkView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(request_body=PROPERTY_FILTER_BY_LINK_SCHEMA)
    def post(self, request, *args, **kwargs):
        payload = request.data or {}
        url = str(payload.get("url") or payload.get("link") or "").strip()
        if not url:
            return Response({"url": [_("This field is required.")]}, status=status.HTTP_400_BAD_REQUEST)
        parsed = parse_qs(urlparse(url).query)
        ctx = {"request": request, "favorite_guids": _favorite_guids_from_request(request)}
        apt_rows = _list_apartment_rows(parsed, public_only=True)
        cot_rows = _list_cottage_rows(parsed, public_only=True)
        data = (
            ApartmentListSerializer(apt_rows, many=True, context=ctx).data
            + CottageListSerializer(cot_rows, many=True, context=ctx).data
        )
        return Response(data, status=status.HTTP_200_OK)


class RegionPropertyListView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(manual_parameters=PROPERTY_LIST_QUERY_PARAMS)
    def get(self, request, *args, **kwargs):
        region_id = _parse_int(self.kwargs.get("region_id"))
        if region_id is None:
            return Response([], status=status.HTTP_200_OK)
        mutable = request.query_params.copy()
        mutable["region_id"] = str(region_id)
        ctx = {"request": request, "favorite_guids": _favorite_guids_from_request(request)}
        apt_rows = _list_apartment_rows(mutable, public_only=True)
        cot_rows = _list_cottage_rows(mutable, public_only=True)
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


class PropertyRetrieveUpdateDestroyView(APIView):
    authentication_classes = [PartnerJWTAuthentication]

    def get_permissions(self):
        if self.request.method in {"PUT", "PATCH", "DELETE"}:
            return [IsPartner()]
        return [AllowAny()]

    def _partner_property_or_404(self, property_id: str):
        row = _get_property_for_partner(str(property_id), int(self.request.user.id))
        if not row:
            raise NotFound(_("Property not found"))
        return row

    def _read_property_or_404(self, property_id: str):
        user = getattr(self.request, "user", None)
        if user is not None and getattr(user, "role", None) == "partner":
            return self._partner_property_or_404(property_id)
        row = _get_property_for_public(str(property_id))
        if not row:
            raise NotFound(_("Property not found"))
        return row

    def get(self, request, property_id, *args, **kwargs):
        row = self._read_property_or_404(str(property_id))
        ctx = {"request": request, "favorite_guids": _favorite_guids_from_request(request)}
        property_type = str(row.get("property_kind") or "")
        if property_type == PROPERTY_KIND_COTTAGE:
            serializer = CottageDetailSerializer(row, context=ctx)
        else:
            serializer = ApartmentDetailSerializer(row, context=ctx)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def put(self, request, property_id, *args, **kwargs):
        return self._update(request, property_id, partial=False)

    def patch(self, request, property_id, *args, **kwargs):
        return self._update(request, property_id, partial=True)

    def _update(self, request, property_id, *, partial: bool):
        current = self._partner_property_or_404(str(property_id))
        property_type = str(current["property_kind"])
        if property_type == PROPERTY_KIND_COTTAGE:
            serializer = CottageUpdateSerializer(data=request.data, partial=partial, context={"is_update": True})
            serializer.is_valid(raise_exception=True)
            updated = update_cottage(cottage_id=int(current["id"]), partner_user_id=int(request.user.id), values=serializer.validated_data["normalized_values"])
        else:
            serializer = ApartmentUpdateSerializer(data=request.data, partial=partial, context={"is_update": True})
            serializer.is_valid(raise_exception=True)
            updated = update_apartment(apartment_id=int(current["id"]), partner_user_id=int(request.user.id), values=serializer.validated_data["normalized_values"])
        if not updated:
            raise NotFound(_("Property not found"))
        return Response(
            {"detail": "Your changes have been saved successfully", "warning": "Property has been sent for re-verification, please wait while we verify it", "status_code": 200},
            status=status.HTTP_200_OK,
        )

    def delete(self, request, property_id, *args, **kwargs):
        current = self._partner_property_or_404(str(property_id))
        property_type = str(current["property_kind"])
        if property_type == PROPERTY_KIND_COTTAGE:
            deleted = delete_cottage(cottage_id=int(current["id"]), partner_user_id=int(request.user.id))
        else:
            deleted = delete_apartment(apartment_id=int(current["id"]), partner_user_id=int(request.user.id))
        if not deleted:
            raise NotFound(_("Property not found"))
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Image views
# ---------------------------------------------------------------------------

class PropertyImageCreateView(APIView):
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    def post(self, request, property_id, *args, **kwargs):
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

        uploaded = uploaded_files[0]
        saved_path = default_storage.save(f"property/{property_id}/{uuid4()}_{uploaded.name}", uploaded)

        property_type = str(property_row["property_kind"])
        if property_type == PROPERTY_KIND_COTTAGE:
            updated = set_cottage_primary_image(cottage_id=int(property_row["id"]), partner_user_id=int(request.user.id), image_path=saved_path)
        else:
            updated = set_apartment_primary_image(apartment_id=int(property_row["id"]), partner_user_id=int(request.user.id), image_path=saved_path)

        if not updated:
            raise NotFound(_("Property not found"))

        if not bool(updated.get("is_verified")):
            return Response({"detail": "Your image(s) are pending approval", "status": "pending"}, status=status.HTTP_200_OK)

        return Response(
            [{"guid": uuid4(), "order": 1, "is_pending": False, "image_url": _build_media_url(request, updated.get("img"))}],
            status=status.HTTP_201_CREATED,
        )


class PropertyImageUpdateDeleteView(APIView):
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    def patch(self, request, property_id, image_id, *args, **kwargs):
        property_row = _get_property_for_partner(str(property_id), int(request.user.id))
        if not property_row:
            raise NotFound(_("Property not found"))

        uploaded = request.FILES.get("image")
        if uploaded is None:
            images = request.FILES.getlist("images")
            if images:
                uploaded = images[0]
        image_path = property_row.get("img")
        if uploaded is not None:
            _validate_image_upload(uploaded)
            image_path = default_storage.save(f"property/{property_id}/{uuid4()}_{uploaded.name}", uploaded)
            property_type = str(property_row["property_kind"])
            if property_type == PROPERTY_KIND_COTTAGE:
                updated = set_cottage_primary_image(cottage_id=int(property_row["id"]), partner_user_id=int(request.user.id), image_path=image_path)
            else:
                updated = set_apartment_primary_image(apartment_id=int(property_row["id"]), partner_user_id=int(request.user.id), image_path=image_path)
            if not updated:
                raise NotFound(_("Property not found"))
        elif not image_path:
            raise NotFound(_("Property image not found"))

        return Response({"detail": _("Your image has been updated and is pending approval"), "status": "pending"}, status=status.HTTP_200_OK)

    def delete(self, request, property_id, image_id, *args, **kwargs):
        property_row = _get_property_for_partner(str(property_id), int(request.user.id))
        if not property_row:
            raise NotFound(_("Property not found"))
        image_path = property_row.get("img")
        if not image_path:
            raise NotFound(_("Property image not found"))

        property_type = str(property_row["property_kind"])
        if property_type == PROPERTY_KIND_COTTAGE:
            updated = set_cottage_primary_image(cottage_id=int(property_row["id"]), partner_user_id=int(request.user.id), image_path=None)
        else:
            updated = set_apartment_primary_image(apartment_id=int(property_row["id"]), partner_user_id=int(request.user.id), image_path=None)
        if not updated:
            raise NotFound(_("Property not found"))
        try:
            default_storage.delete(image_path)
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

    def get(self, request, property_id, *args, **kwargs):
        property_row = self._get_property_or_404(str(property_id))
        rows = list_reviews(property_kind=str(property_row["property_kind"]), property_id=int(property_row["id"]), include_hidden=False)
        return Response(RawPropertyReviewSerializer(rows, many=True).data, status=status.HTTP_200_OK)

    def post(self, request, property_id, *args, **kwargs):
        property_row = self._get_property_or_404(str(property_id))
        serializer = RawPropertyReviewCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        can_review = has_eligible_booking_for_review(client_user_id=int(request.user.id), property_kind=str(property_row["property_kind"]), property_id=int(property_row["id"]))
        if not can_review:
            raise ValidationError(_("You can leave a review only for accepted or completed bookings"))

        created = create_review(
            client_user_id=int(request.user.id),
            property_kind=str(property_row["property_kind"]),
            property_id=int(property_row["id"]),
            rating=serializer.validated_data["rating"],
            comment=serializer.validated_data.get("comment"),
        )
        if not created:
            raise ValidationError(_("Review could not be created"))
        return Response(RawPropertyReviewSerializer(created).data, status=status.HTTP_201_CREATED)


class PartnerPropertyReviewListView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    def get(self, request, property_id, *args, **kwargs):
        property_row = _get_property_for_partner(str(property_id), int(request.user.id))
        if not property_row:
            raise NotFound(_("Property not found"))
        rows = list_reviews(property_kind=str(property_row["property_kind"]), property_id=int(property_row["id"]), include_hidden=True)
        return Response(RawPropertyReviewSerializer(rows, many=True).data, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Partner property list
# ---------------------------------------------------------------------------

class PartnerPropertyListView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    @swagger_auto_schema(manual_parameters=PROPERTY_LIST_QUERY_PARAMS + [openapi.Parameter("property_type", openapi.IN_QUERY, type=openapi.TYPE_STRING)])
    def get(self, request, *args, **kwargs):
        property_type = parse_property_kind(request.query_params.get("property_type"))
        ctx = {"request": request}
        partner_id = int(request.user.id)

        if property_type == PROPERTY_KIND_APARTMENT:
            rows = _list_apartment_rows(request.query_params, public_only=False, partner_user_id=partner_id)
            return Response(ApartmentPartnerListSerializer(rows, many=True, context=ctx).data)
        if property_type == PROPERTY_KIND_COTTAGE:
            rows = _list_cottage_rows(request.query_params, public_only=False, partner_user_id=partner_id)
            return Response(CottagePartnerListSerializer(rows, many=True, context=ctx).data)

        apt_rows = _list_apartment_rows(request.query_params, public_only=False, partner_user_id=partner_id)
        cot_rows = _list_cottage_rows(request.query_params, public_only=False, partner_user_id=partner_id)
        data = (
            ApartmentPartnerListSerializer(apt_rows, many=True, context=ctx).data
            + CottagePartnerListSerializer(cot_rows, many=True, context=ctx).data
        )
        return Response(data, status=status.HTTP_200_OK)


class ApartmentPartnerPropertyListView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    @swagger_auto_schema(manual_parameters=PROPERTY_LIST_QUERY_PARAMS)
    def get(self, request, *args, **kwargs):
        ctx = {"request": request}
        rows = _list_apartment_rows(
            request.query_params,
            public_only=False,
            partner_user_id=int(request.user.id),
        )
        return Response(ApartmentPartnerListSerializer(rows, many=True, context=ctx).data, status=status.HTTP_200_OK)


class CottagePartnerPropertyListView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    @swagger_auto_schema(manual_parameters=PROPERTY_LIST_QUERY_PARAMS)
    def get(self, request, *args, **kwargs):
        ctx = {"request": request}
        rows = _list_cottage_rows(
            request.query_params,
            public_only=False,
            partner_user_id=int(request.user.id),
        )
        return Response(CottagePartnerListSerializer(rows, many=True, context=ctx).data, status=status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# Favorites
# ---------------------------------------------------------------------------

class SavedPropertyListView(APIView):
    authentication_classes = [ClientJWTAuthentication]
    permission_classes = [IsClient]

    def get(self, request, *args, **kwargs):
        favorite_guids = _load_favorite_guids(int(request.user.id))
        if not favorite_guids:
            return Response([], status=status.HTTP_200_OK)
        ctx = {"request": request, "favorite_guids": favorite_guids}
        apt_rows = [r for r in _list_apartment_rows(request.query_params, public_only=True) if str(r.get("guid")) in favorite_guids]
        cot_rows = [r for r in _list_cottage_rows(request.query_params, public_only=True) if str(r.get("guid")) in favorite_guids]
        data = (
            ApartmentListSerializer(apt_rows, many=True, context=ctx).data
            + CottageListSerializer(cot_rows, many=True, context=ctx).data
        )
        return Response(data, status=status.HTTP_200_OK)


class PropertyFavoriteToggleView(APIView):
    authentication_classes = [ClientJWTAuthentication]
    permission_classes = [IsClient]

    def post(self, request, property_id, *args, **kwargs):
        row = _get_property_for_public(str(property_id))
        if not row:
            raise NotFound(_("Property not found"))
        favorite_guids = _load_favorite_guids(int(request.user.id))
        guid = str(row["guid"])
        if guid in favorite_guids:
            favorite_guids.remove(guid)
            _store_favorite_guids(int(request.user.id), favorite_guids)
            return Response({"detail": _("Removed from favorites"), "is_favorite": False}, status=status.HTTP_200_OK)
        favorite_guids.add(guid)
        _store_favorite_guids(int(request.user.id), favorite_guids)
        return Response({"detail": _("Added to favorites"), "is_favorite": True}, status=status.HTTP_201_CREATED)

    def delete(self, request, property_id, *args, **kwargs):
        row = _get_property_for_public(str(property_id))
        if not row:
            raise NotFound(_("Property not found"))
        favorite_guids = _load_favorite_guids(int(request.user.id))
        favorite_guids.discard(str(row["guid"]))
        _store_favorite_guids(int(request.user.id), favorite_guids)
        return Response({"detail": _("Removed from favorites"), "is_favorite": False}, status=status.HTTP_200_OK)
