from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from django.core.cache import cache
from django.core.files.storage import default_storage
from django.utils.translation import gettext_lazy as _
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema

from rest_framework import parsers, status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from shared.permissions import IsClient, IsPartner
from users.authentication import ClientJWTAuthentication, PartnerJWTAuthentication

from .raw_repository import (
    create_property,
    create_review,
    delete_property,
    get_property_for_partner,
    get_property_for_public,
    has_eligible_booking_for_review,
    list_properties,
    list_property_types,
    list_reviews,
    parse_property_kind,
    prepare_property_rows,
    set_property_primary_image,
    update_property,
)
from .raw_serializers import (
    RawPartnerPropertyListSerializer,
    RawPropertyCreateSerializer,
    RawPropertyDetailSerializer,
    RawPropertyListSerializer,
    RawPropertyReviewCreateSerializer,
    RawPropertyReviewSerializer,
    RawPropertyTypeSerializer,
    RawPropertyUpdateSerializer,
)


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


def _list_property_rows(
    source,
    *,
    public_only: bool,
    partner_user_id: int | None = None,
    recommended_only: bool = False,
    forced_kind: str | None = None,
    default_ordering: str = "-created_at",
    default_limit: int | None = None,
) -> list[dict]:
    kind = forced_kind or parse_property_kind(
        _source_get(source, "kind")
        or _source_get(source, "property_type")
        or _source_get(source, "property_type_id")
    )
    search = _source_get(source, "search")
    region_id = _parse_int(_source_get(source, "region_id") or _source_get(source, "location_id"))
    district_id = _parse_int(_source_get(source, "district_id"))
    corporate = _parse_bool(_source_get(source, "corporate"))

    rows = list_properties(
        public_only=public_only,
        partner_user_id=partner_user_id,
        property_kind=kind,
        recommended_only=recommended_only,
        search=search,
        region_id=region_id,
        district_id=district_id,
        corporate=corporate,
    )

    min_price = _parse_decimal(_source_get(source, "min_price"))
    max_price = _parse_decimal(_source_get(source, "max_price"))
    currency = _source_get(source, "currency")
    sort = _source_get(source, "sort")
    ordering = _source_get(source, "ordering") or default_ordering
    reference_date = _resolve_reference_date(_source_get(source, "from_date"))

    limit = _parse_int(_source_get(source, "limit"))
    if limit is None:
        limit = default_limit
    if limit is not None:
        limit = max(0, min(limit, 200))

    return prepare_property_rows(
        rows,
        reference_date=reference_date,
        min_price=min_price,
        max_price=max_price,
        currency=currency,
        sort=sort,
        ordering=ordering,
        limit=limit,
    )


class PropertyTypeListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        payload = list_property_types()
        serializer = RawPropertyTypeSerializer(payload, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class PropertyServiceListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        return Response([], status=status.HTTP_200_OK)


class RegionListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        return Response([], status=status.HTTP_200_OK)


class DistrictListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        return Response([], status=status.HTTP_200_OK)


class LocationListView(APIView):
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        return Response({"regions": []}, status=status.HTTP_200_OK)


class UnifiedRecommendationsListView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(manual_parameters=RECOMMENDATIONS_QUERY_PARAMS)
    def get(self, request, *args, **kwargs):
        kind = str(request.query_params.get("kind") or "property").strip().lower()
        if kind not in {"property", "", "apartment", "cottage", "apartments", "cottages"}:
            return Response([], status=status.HTTP_200_OK)

        rec_type = str(request.query_params.get("type") or "featured").strip().lower()
        ordering = "-created_at"
        if rec_type == "best-by-reviews":
            ordering = "-average_rating"
        elif rec_type == "most-booked":
            ordering = "-review_count"

        forced_kind = None
        if kind not in {"property", ""}:
            forced_kind = parse_property_kind(kind)

        rows = _list_property_rows(
            request.query_params,
            public_only=True,
            recommended_only=True,
            forced_kind=forced_kind,
            default_ordering=ordering,
            default_limit=20,
        )
        serializer = RawPropertyListSerializer(
            rows,
            many=True,
            context={
                "request": request,
                "favorite_guids": _favorite_guids_from_request(request),
            },
        )
        return Response(serializer.data, status=status.HTTP_200_OK)


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


class PropertyListCreateView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    forced_property_kind: str | None = None

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
        rows = _list_property_rows(
            request.query_params,
            public_only=True,
            forced_kind=self.forced_property_kind,
        )
        serializer = RawPropertyListSerializer(
            rows,
            many=True,
            context={
                "request": request,
                "favorite_guids": _favorite_guids_from_request(request),
            },
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @swagger_auto_schema(request_body=RawPropertyCreateSerializer)
    def post(self, request, *args, **kwargs):
        serializer = RawPropertyCreateSerializer(
            data=request.data,
            context={"forced_kind": self.forced_property_kind},
        )
        serializer.is_valid(raise_exception=True)
        created = create_property(
            property_kind=serializer.validated_data["property_kind"],
            partner_user_id=int(request.user.id),
            values=serializer.validated_data["normalized_values"],
        )
        if not created:
            raise ValidationError(_("Property could not be created"))
        return Response(
            {
                "detail": _("Property has been created successfully, please wait while we verify it"),
                "property_id": str(created["guid"]),
                "status_code": 201,
            },
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
        rows = _list_property_rows(parsed, public_only=True)
        serializer = RawPropertyListSerializer(
            rows,
            many=True,
            context={
                "request": request,
                "favorite_guids": _favorite_guids_from_request(request),
            },
        )
        return Response(serializer.data, status=status.HTTP_200_OK)


class ApartmentPropertyListCreateView(PropertyListCreateView):
    forced_property_kind = "apartment"


class CottagePropertyListCreateView(PropertyListCreateView):
    forced_property_kind = "cottage"


class RegionPropertyListView(PropertyListCreateView):
    @swagger_auto_schema(manual_parameters=PROPERTY_LIST_QUERY_PARAMS)
    def get(self, request, *args, **kwargs):
        region_id = _parse_int(self.kwargs.get("region_id"))
        if region_id is None:
            return Response([], status=status.HTTP_200_OK)
        mutable = request.query_params.copy()
        mutable["region_id"] = str(region_id)
        rows = _list_property_rows(mutable, public_only=True)
        serializer = RawPropertyListSerializer(
            rows,
            many=True,
            context={
                "request": request,
                "favorite_guids": _favorite_guids_from_request(request),
            },
        )
        return Response(serializer.data, status=status.HTTP_200_OK)


class PropertyRetrieveUpdateDestroyView(APIView):
    authentication_classes = [PartnerJWTAuthentication]

    def get_permissions(self):
        if self.request.method in {"PUT", "PATCH", "DELETE"}:
            return [IsPartner()]
        return [AllowAny()]

    def _partner_property_or_404(self, property_id: str):
        row = get_property_for_partner(str(property_id), int(self.request.user.id))
        if not row:
            raise NotFound(_("Property not found"))
        return row

    def _read_property_or_404(self, property_id: str):
        user = getattr(self.request, "user", None)
        if user is not None and getattr(user, "role", None) == "partner":
            return self._partner_property_or_404(property_id)
        row = get_property_for_public(str(property_id))
        if not row:
            raise NotFound(_("Property not found"))
        return row

    def get(self, request, property_id, *args, **kwargs):
        row = self._read_property_or_404(str(property_id))
        serializer = RawPropertyDetailSerializer(
            row,
            context={
                "request": request,
                "favorite_guids": _favorite_guids_from_request(request),
            },
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    def put(self, request, property_id, *args, **kwargs):
        return self._update(request, property_id, partial=False)

    def patch(self, request, property_id, *args, **kwargs):
        return self._update(request, property_id, partial=True)

    def _update(self, request, property_id, *, partial: bool):
        current = self._partner_property_or_404(str(property_id))
        serializer = RawPropertyUpdateSerializer(
            data=request.data,
            partial=partial,
            context={"is_update": True, "forced_kind": current["property_kind"]},
        )
        serializer.is_valid(raise_exception=True)
        updated = update_property(
            property_kind=str(current["property_kind"]),
            property_id=int(current["id"]),
            partner_user_id=int(request.user.id),
            values=serializer.validated_data["normalized_values"],
        )
        if not updated:
            raise NotFound(_("Property not found"))
        return Response(
            {
                "detail": "Your changes have been saved successfully",
                "warning": "Property has been sent for re-verification, please wait while we verify it",
                "status_code": 200,
            },
            status=status.HTTP_200_OK,
        )

    def delete(self, request, property_id, *args, **kwargs):
        current = self._partner_property_or_404(str(property_id))
        deleted = delete_property(
            property_kind=str(current["property_kind"]),
            property_id=int(current["id"]),
            partner_user_id=int(request.user.id),
        )
        if not deleted:
            raise NotFound(_("Property not found"))
        return Response(status=status.HTTP_204_NO_CONTENT)


class PropertyImageCreateView(APIView):
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    def post(self, request, property_id, *args, **kwargs):
        property_row = get_property_for_partner(str(property_id), int(request.user.id))
        if not property_row:
            raise NotFound(_("Property not found"))

        uploaded_files = request.FILES.getlist("images")
        if not uploaded_files:
            single = request.FILES.get("image")
            if single is not None:
                uploaded_files = [single]
        if not uploaded_files:
            raise ValidationError({"images": [_("This field is required.")]})

        uploaded = uploaded_files[0]
        saved_path = default_storage.save(
            f"property/{property_id}/{uuid4()}_{uploaded.name}",
            uploaded,
        )
        updated = set_property_primary_image(
            property_kind=str(property_row["property_kind"]),
            property_id=int(property_row["id"]),
            partner_user_id=int(request.user.id),
            image_path=saved_path,
        )
        if not updated:
            raise NotFound(_("Property not found"))

        if not bool(updated.get("is_verified")):
            return Response(
                status=status.HTTP_200_OK,
                data={
                    "detail": "Your image(s) are pending approval",
                    "status": "pending",
                },
            )

        return Response(
            status=status.HTTP_201_CREATED,
            data=[
                {
                    "guid": uuid4(),
                    "order": 1,
                    "is_pending": False,
                    "image_url": _build_media_url(request, updated.get("img")),
                }
            ],
        )


class PropertyImageUpdateDeleteView(APIView):
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    def patch(self, request, property_id, image_id, *args, **kwargs):
        property_row = get_property_for_partner(str(property_id), int(request.user.id))
        if not property_row:
            raise NotFound(_("Property not found"))

        uploaded = request.FILES.get("image")
        if uploaded is None:
            images = request.FILES.getlist("images")
            if images:
                uploaded = images[0]
        image_path = property_row.get("img")
        if uploaded is not None:
            image_path = default_storage.save(
                f"property/{property_id}/{uuid4()}_{uploaded.name}",
                uploaded,
            )
            updated = set_property_primary_image(
                property_kind=str(property_row["property_kind"]),
                property_id=int(property_row["id"]),
                partner_user_id=int(request.user.id),
                image_path=image_path,
            )
            if not updated:
                raise NotFound(_("Property not found"))
        elif not image_path:
            raise NotFound(_("Property image not found"))

        return Response(
            status=status.HTTP_200_OK,
            data={
                "detail": _("Your image has been updated and is pending approval"),
                "status": "pending",
            },
        )

    def delete(self, request, property_id, image_id, *args, **kwargs):
        property_row = get_property_for_partner(str(property_id), int(request.user.id))
        if not property_row:
            raise NotFound(_("Property not found"))
        image_path = property_row.get("img")
        if not image_path:
            raise NotFound(_("Property image not found"))

        updated = set_property_primary_image(
            property_kind=str(property_row["property_kind"]),
            property_id=int(property_row["id"]),
            partner_user_id=int(request.user.id),
            image_path=None,
        )
        if not updated:
            raise NotFound(_("Property not found"))
        try:
            default_storage.delete(image_path)
        except Exception:
            pass
        return Response(status=status.HTTP_204_NO_CONTENT)


class PropertyReviewListCreateView(APIView):
    authentication_classes = [ClientJWTAuthentication]

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsClient()]
        return [AllowAny()]

    def _get_property_or_404(self, property_id: str):
        row = get_property_for_public(str(property_id))
        if not row:
            raise NotFound(_("Property not found"))
        return row

    def get(self, request, property_id, *args, **kwargs):
        property_row = self._get_property_or_404(str(property_id))
        rows = list_reviews(
            property_kind=str(property_row["property_kind"]),
            property_id=int(property_row["id"]),
            include_hidden=False,
        )
        serializer = RawPropertyReviewSerializer(rows, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

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
        output = RawPropertyReviewSerializer(created)
        return Response(output.data, status=status.HTTP_201_CREATED)


class PartnerPropertyReviewListView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    def get(self, request, property_id, *args, **kwargs):
        property_row = get_property_for_partner(str(property_id), int(request.user.id))
        if not property_row:
            raise NotFound(_("Property not found"))
        rows = list_reviews(
            property_kind=str(property_row["property_kind"]),
            property_id=int(property_row["id"]),
            include_hidden=True,
        )
        serializer = RawPropertyReviewSerializer(rows, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class PartnerPropertyListView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    @swagger_auto_schema(
        manual_parameters=PROPERTY_LIST_QUERY_PARAMS
        + [openapi.Parameter("property_type", openapi.IN_QUERY, type=openapi.TYPE_STRING)]
    )
    def get(self, request, *args, **kwargs):
        rows = _list_property_rows(
            request.query_params,
            public_only=False,
            partner_user_id=int(request.user.id),
            forced_kind=parse_property_kind(request.query_params.get("property_type")),
        )
        serializer = RawPartnerPropertyListSerializer(rows, many=True, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)


class SavedPropertyListView(APIView):
    authentication_classes = [ClientJWTAuthentication]
    permission_classes = [IsClient]

    def get(self, request, *args, **kwargs):
        favorite_guids = _load_favorite_guids(int(request.user.id))
        if not favorite_guids:
            return Response([], status=status.HTTP_200_OK)
        rows = _list_property_rows(request.query_params, public_only=True)
        rows = [row for row in rows if str(row.get("guid")) in favorite_guids]
        serializer = RawPropertyListSerializer(
            rows,
            many=True,
            context={
                "request": request,
                "favorite_guids": favorite_guids,
            },
        )
        return Response(serializer.data, status=status.HTTP_200_OK)


class PropertyFavoriteToggleView(APIView):
    authentication_classes = [ClientJWTAuthentication]
    permission_classes = [IsClient]

    def post(self, request, property_id, *args, **kwargs):
        row = get_property_for_public(str(property_id))
        if not row:
            raise NotFound(_("Property not found"))
        favorite_guids = _load_favorite_guids(int(request.user.id))
        guid = str(row["guid"])
        if guid in favorite_guids:
            favorite_guids.remove(guid)
            _store_favorite_guids(int(request.user.id), favorite_guids)
            return Response(
                {
                    "detail": _("Removed from favorites"),
                    "is_favorite": False,
                },
                status=status.HTTP_200_OK,
            )
        favorite_guids.add(guid)
        _store_favorite_guids(int(request.user.id), favorite_guids)
        return Response(
            {
                "detail": _("Added to favorites"),
                "is_favorite": True,
            },
            status=status.HTTP_201_CREATED,
        )

    def delete(self, request, property_id, *args, **kwargs):
        row = get_property_for_public(str(property_id))
        if not row:
            raise NotFound(_("Property not found"))
        favorite_guids = _load_favorite_guids(int(request.user.id))
        favorite_guids.discard(str(row["guid"]))
        _store_favorite_guids(int(request.user.id), favorite_guids)
        return Response(
            {
                "detail": _("Removed from favorites"),
                "is_favorite": False,
            },
            status=status.HTTP_200_OK,
        )
