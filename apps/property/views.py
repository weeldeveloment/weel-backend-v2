import uuid as uuid_module
from datetime import date

from django.db.models import Avg, Case, When, F, Q, DecimalField, Value, IntegerField
from django.db.models.aggregates import Count
from django.db.models.functions import Coalesce
from django_filters.rest_framework import DjangoFilterBackend
from django.utils.translation import gettext_lazy as _

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema

from rest_framework import status, parsers
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.filters import SearchFilter
from rest_framework.permissions import (
    AllowAny,
)
from rest_framework.response import Response
from rest_framework.status import HTTP_204_NO_CONTENT
from rest_framework.views import APIView
from rest_framework.generics import (
    ListAPIView,
    ListCreateAPIView,
    RetrieveUpdateDestroyAPIView,
)

from .filters import PropertyFilter, PropertyOrderingFilter, PropertyServiceFilter
from .models import (
    Property,
    PropertyType,
    PropertyImage,
    PropertyDetail,
    PropertyReview,
    PropertyFavorite,
    PropertyService,
    Category,
    Region,
    District,
)
from .serializers import (
    PropertyListSerializer,
    PartnerPropertyListSerializer,
    PropertyCreateSerializer,
    PropertyTypeListSerializer,
    PropertyDetailSerializer,
    PropertyImageCreateSerializer,
    PropertyReviewSerializer,
    PropertyUpdateSerializer,
    PropertyPutSerializer,
    PropertyPatchSerializer,
    PropertyServiceListSerializer,
    CategoryListSerializer,
    PropertyImageSerializer,
    PropertyImageUpdateSerializer,
    PropertyReviewCreateSerializer,
    RegionListSerializer,
    DistrictListSerializer,
    LocationRegionSerializer,
)
from users.models import Partner
from users.authentication import ClientJWTAuthentication, PartnerJWTAuthentication
from shared.permissions import IsPartner, IsClient, IsPartnerOwnerProperty
from payment.exchange_rate import exchange_rate
from .pricing import property_price_expression, resolve_reference_date

try:
    # Recommendations with kind=.sanatorium (optional module)
    from sanatorium.models import Sanatorium
    from sanatorium.serializers import SanatoriumListSerializer
    SANATORIUM_AVAILABLE = True
except Exception:
    Sanatorium = None
    SanatoriumListSerializer = None
    SANATORIUM_AVAILABLE = False

property_id_param = openapi.Parameter(
    "property_id",
    openapi.IN_PATH,
    description="Unique property GUID",
    type=openapi.TYPE_STRING,
    format=openapi.FORMAT_UUID,
)

image_id_param = openapi.Parameter(
    "image_id",
    openapi.IN_PATH,
    description="Unique property image GUID",
    type=openapi.TYPE_STRING,
    format=openapi.FORMAT_UUID,
)

# Create your views here.

PINNED_PROPERTY_TYPE_GUID = uuid_module.UUID("c185fd69-1faa-4c61-a3f1-59ecd0830e0c")


class PropertyTypeListView(ListAPIView):
    serializer_class = PropertyTypeListSerializer

    def get_queryset(self):
        return (
            PropertyType.objects.annotate(
                _order=Case(
                    When(guid=PINNED_PROPERTY_TYPE_GUID, then=Value(0)),
                    default=Value(1),
                    output_field=IntegerField(),
                )
            ).order_by("_order", "title_uz")
        )

    @swagger_auto_schema(
        tags=["Property"],
        operation_summary="Retrieve a list of property types",
        operation_description="Retrieve all property types available in the system",
        responses={status.HTTP_200_OK: PropertyTypeListSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class PropertyServiceListView(ListAPIView):
    queryset = PropertyService.objects.all().select_related("property_type")
    serializer_class = PropertyServiceListSerializer
    filterset_class = PropertyServiceFilter

    @swagger_auto_schema(
        tags=["Property"],
        operation_summary="Retrieve a list of property services",
        operation_description=(
            "List of all property services. If **property_id** is provided — returns only services "
            "belonging to that property."
        ),
        manual_parameters=[
            openapi.Parameter(
                "property_id",
                openapi.IN_QUERY,
                description="Property GUID — services for this property only",
                type=openapi.TYPE_STRING,
                format=openapi.FORMAT_UUID,
                required=False,
            ),
        ],
        responses={status.HTTP_200_OK: PropertyServiceListSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class RegionListView(ListAPIView):
    """Oʻzbekiston viloyatlari roʻyxati (filter va property yaratish uchun)."""
    queryset = Region.objects.all()
    serializer_class = RegionListSerializer

    @swagger_auto_schema(
        tags=["Property"],
        operation_summary="List of regions",
        operation_description="All regions — for adding properties and filtering",
        responses={status.HTTP_200_OK: RegionListSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


def _parse_uuid_param(value: str, param_name: str) -> uuid_module.UUID | None:
    """Query param dan bitta UUID qaytaradi; notoʻgʻri boʻlsa ValidationError."""
    if not value or not (value := str(value).strip()):
        return None
    # Agar "uuid1/uuid2" kabi yuborilsa, faqat birinchi UUID ni oladi
    first = value.split("/")[0].strip()
    try:
        return uuid_module.UUID(first)
    except (ValueError, TypeError):
        raise ValidationError({param_name: _("Must be a valid UUID.")})


def _is_null_like_param(value: str | None) -> bool:
    if value is None:
        return True
    return str(value).strip().lower() in {"", "null", "none", "undefined"}


def _resolve_sort_reference_date(raw_from_date: str | None, default_date: date) -> date:
    return resolve_reference_date(raw_from_date, default_date)


def _property_price_expression(price_field: str, reference_date: date):
    return property_price_expression(price_field, reference_date)


class DistrictListView(ListAPIView):
    """Tuman/shahar roʻyxati — ixtiyoriy filter: region_id (viloyat guid)."""
    serializer_class = DistrictListSerializer

    def get_queryset(self):
        qs = District.objects.all().select_related("region")
        region_guid = self.request.query_params.get("region_id")
        if region_guid:
            region_uuid = _parse_uuid_param(region_guid, "region_id")
            if region_uuid:
                qs = qs.filter(region__guid=region_uuid)
        return qs

    @swagger_auto_schema(
        tags=["Property"],
        operation_summary="List of districts",
        operation_description="All districts or filtered by region_id — for adding properties and filtering",
        manual_parameters=[
            openapi.Parameter(
                "region_id",
                openapi.IN_QUERY,
                description="Region GUID — districts of this region only",
                type=openapi.TYPE_STRING,
                format=openapi.FORMAT_UUID,
            ),
        ],
        responses={status.HTTP_200_OK: DistrictListSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class LocationListView(APIView):
    """
    Bitta API: region va district — hamma joylashuv ma'lumotlari.
    Response: { "regions": [ { "guid", "title", "img", "districts": [ { "guid", "title" } ] } ] }
    """
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        tags=["Property"],
        operation_summary="Location data (regions, districts)",
        operation_description="Single endpoint returning all location data: regions with nested districts.",
        responses={status.HTTP_200_OK: openapi.Response(
            description="regions with nested districts",
            examples={"application/json": {
                "regions": [{"guid": "...", "title": "...", "img": "...", "districts": [{"guid": "...", "title": "..."}]}],
            }},
        )},
    )
    def get(self, request):
        regions_qs = (
            Region.objects.all()
            .prefetch_related("districts")
            .order_by("title_uz")
        )

        regions_data = LocationRegionSerializer(regions_qs, many=True).data
        return Response({"regions": regions_data})


RECOMMENDATION_TYPES = ("best-by-reviews", "featured", "most-booked")
RECOMMENDATION_KINDS = ("property", ".sanatorium") if SANATORIUM_AVAILABLE else ("property",)


class UnifiedRecommendationsListView(ListAPIView):
    """
    Bitta API: type va kind query parametrlari orqali recommendation ro'yxatlari.
    kind=property (default): Property ro'yxati; ixtiyoriy property_type=<uuid> — shu turdagi property.
    kind=.sanatorium: Sanatorium ro'yxati (type bo'yicha order: best-by-reviews, featured, most-booked).
    """

    serializer_class = PropertyListSerializer

    def _get_kind(self):
        kind = self.request.query_params.get("kind", "property").strip().lower()
        if kind not in RECOMMENDATION_KINDS:
            raise ValidationError(
                {"kind": _("Use one of: %(kinds)s") % {"kinds": ", ".join(RECOMMENDATION_KINDS)}}
            )
        return kind

    def _get_property_type_guid(self):
        raw = self.request.query_params.get("property_type", "").strip()
        if not raw:
            return None
        try:
            return uuid_module.UUID(raw)
        except (ValueError, TypeError):
            raise ValidationError({"property_type": _("Must be a valid UUID.")})

    def get_serializer_class(self):
        if self._get_kind() == ".sanatorium" and SANATORIUM_AVAILABLE and SanatoriumListSerializer:
            return SanatoriumListSerializer
        return PropertyListSerializer

    def get_queryset(self):
        rec_type = self.request.query_params.get("type", "").strip().lower()
        kind = self._get_kind()

        if kind == ".sanatorium":
            return self._get_sanatorium_queryset(rec_type)

        # kind == "property"
        if rec_type not in RECOMMENDATION_TYPES:
            rec_type = "featured"
        base = Property.objects.filter(is_verified=True)
        property_type_guid = self._get_property_type_guid()
        if property_type_guid:
            base = base.filter(property_type__guid=property_type_guid)
        if rec_type == "featured":
            base = base.filter(is_recommended=True)
        annotate_kw = {
            "average_rating": Coalesce(
                Avg(
                    "property_review__rating",
                    filter=Q(property_review__is_hidden=False)
                    | Q(property_review__is_hidden__isnull=True),
                ),
                Value(0),
                output_field=DecimalField(),
            ),
        }
        if rec_type == "most-booked":
            annotate_kw["booking_count"] = Count("booking_property")
        base = base.annotate(**annotate_kw)

        if rec_type == "best-by-reviews":
            base = base.order_by("-average_rating", "-comment_count")[:10]
        elif rec_type == "featured":
            base = base.order_by("-created_at")
        else:  # most-booked
            base = base.order_by("-booking_count")[:10]

        return base.prefetch_related(
            "property_price", "property_services", "property_images"
        ).select_related(
            "property_location",
            "property_type",
            "property_room",
            "region",
            "district",
        )

    def _get_sanatorium_queryset(self, rec_type):
        if not SANATORIUM_AVAILABLE or Sanatorium is None:
            return Property.objects.none()  # pragma: no cover
        if rec_type not in RECOMMENDATION_TYPES:
            rec_type = "featured"
        base = Sanatorium.objects.filter(is_verified=True)
        annotate_kw = {}
        if rec_type == "best-by-reviews":
            annotate_kw["average_rating"] = Coalesce(
                Avg(
                    "reviews__rating",
                    filter=Q(reviews__is_hidden=False) | Q(reviews__is_hidden__isnull=True),
                ),
                Value(0),
                output_field=DecimalField(),
            )
        if rec_type == "most-booked":
            annotate_kw["booking_count"] = Count("bookings")
        if annotate_kw:
            base = base.annotate(**annotate_kw)
        if rec_type == "best-by-reviews":
            base = base.order_by("-average_rating", "-comment_count")[:10]
        elif rec_type == "featured":
            base = base.order_by("-comment_count")[:10]
        else:  # most-booked
            base = base.order_by("-booking_count")[:10]
        return base.select_related("location").prefetch_related("images", "specializations")

    @swagger_auto_schema(
        tags=["Property"],
        operation_summary="Recommendations — by type and kind (property | .sanatorium)",
        operation_description=(
            "Query params:\n\n"
            "- **type** (optional): best-by-reviews | featured | most-booked (default: featured)\n"
            "- **kind** (optional): property (default) | .sanatorium\n"
            "- **property_type** (optional): PropertyType UUID — berilsa faqat shu turdagi property, berilmasa hammasi\n\n"
            "Examples:\n"
            "- `?type=featured` — featured properties\n"
            "- `?property_type=<uuid>` — faqat shu type dagi propertylar (type default: featured)\n"
            "- `?type=best-by-reviews&property_type=<uuid>` — shu turdagi eng yaxshi propertylar\n"
            "- Parametrsiz — barcha (featured) propertylar"
        ),
        manual_parameters=[
            openapi.Parameter(
                "type",
                openapi.IN_QUERY,
                description="best-by-reviews | featured | most-booked (required for property)",
                type=openapi.TYPE_STRING,
                required=False,
                enum=list(RECOMMENDATION_TYPES),
            ),
            openapi.Parameter(
                "kind",
                openapi.IN_QUERY,
                description="property (default) or .sanatorium",
                type=openapi.TYPE_STRING,
                required=False,
                enum=list(RECOMMENDATION_KINDS),
            ),
            openapi.Parameter(
                "property_type",
                openapi.IN_QUERY,
                description="PropertyType UUID — filter properties by type (only when kind=property)",
                type=openapi.TYPE_STRING,
                format=openapi.FORMAT_UUID,
                required=False,
            ),
        ],
        responses={status.HTTP_200_OK: PropertyListSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class CategoryListView(ListAPIView):
    """Свежие предложения tablar uchun kategoriyalar — admin da yaratilgan Category roʻyxati."""

    queryset = Category.objects.all().order_by("title_uz")
    serializer_class = CategoryListSerializer

    @swagger_auto_schema(
        tags=["Property"],
        operation_summary="Fresh offers — list of categories (tabs)",
        operation_description=(
            "| Block | API | Admin |\n"
            "|-------|-----|-------|\n"
            "| Fresh offers (tab) | GET /api/property/categories/<id>/properties/ | Property → **categories** |\n\n"
            "List of categories for tabs (created in Admin under Property → Categories). "
            "Then use **categories/{id}/properties/** to get properties in that category. "
            "In Admin: select categories in the **Categories** field on the Property form."
        ),
        responses={status.HTTP_200_OK: CategoryListSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class CategoryLatestPropertyListView(ListAPIView):
    """Berilgan kategoriyadagi eng oxirgi (yangilangan) 10 ta property."""

    serializer_class = PropertyListSerializer

    def get_queryset(self):
        category_id = self.kwargs.get("category_id")
        if not Category.objects.filter(guid=category_id).exists():
            raise NotFound(_("Category not found"))
        return (
            Property.objects.filter(
                is_verified=True,
                categories__guid=category_id,
            )
            .distinct()
            .annotate(
                average_rating=Coalesce(
                    Avg(
                        "property_review__rating",
                        filter=Q(property_review__is_hidden=False)
                        | Q(property_review__is_hidden__isnull=True),
                    ),
                    Value(0),
                    output_field=DecimalField(),
                ),
            )
            .order_by("-created_at")[:10]
            .prefetch_related("property_price", "property_services", "categories", "property_images")
            .select_related(
                "property_location",
                "property_type",
                "property_room",
                "region",
                "district",
            )
        )

    @swagger_auto_schema(
        tags=["Property"],
        operation_summary="Latest 10 properties in category",
        operation_description=(
            "Returns the latest **10** verified properties in the given **category_id**, "
            "ordered by creation date."
        ),
        manual_parameters=[
            openapi.Parameter(
                "category_id",
                openapi.IN_PATH,
                description="Category GUID",
                type=openapi.TYPE_STRING,
                format=openapi.FORMAT_UUID,
                required=True,
            ),
        ],
        responses={status.HTTP_200_OK: PropertyListSerializer(many=True), status.HTTP_404_NOT_FOUND: "Category not found"},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class CategoryPropertyRecommendationView(ListAPIView):
    """Свежие предложения (tab) — berilgan kategoriya (Category) boʻyicha dachalar."""

    serializer_class = PropertyListSerializer

    def get_queryset(self):
        category_id = self.kwargs.get("category_id")
        if not Category.objects.filter(guid=category_id).exists():
            raise NotFound(_("Category not found"))
        return (
            Property.objects.filter(
                is_verified=True,
                categories__guid=category_id,
            )
            .distinct()
            .annotate(
                average_rating=Coalesce(
                    Avg(
                        "property_review__rating",
                        filter=Q(property_review__is_hidden=False)
                        | Q(property_review__is_hidden__isnull=True),
                    ),
                    Value(0),
                    output_field=DecimalField(),
                ),
            )
            .order_by("-average_rating", "-comment_count")
            .prefetch_related("property_price", "property_services", "categories", "property_images")
            .select_related(
                "property_location",
                "property_type",
                "property_room",
                "region",
                "district",
            )
        )

    @swagger_auto_schema(
        tags=["Property"],
        operation_summary="Fresh offers (tab) — properties in category",
        operation_description=(
            "| Block | API | Admin |\n"
            "|-------|-----|-------|\n"
            "| Fresh offers (tab) | GET /api/property/categories/<id>/properties/ | Property → **categories** |\n\n"
            "When selecting a tab: **category_id** = GUID from GET /api/property/categories/. "
            "In Admin: select categories in the **Categories** field on the Property form."
        ),
        manual_parameters=[
            openapi.Parameter(
                "category_id",
                openapi.IN_PATH,
                description="Category GUID (created in Admin)",
                type=openapi.TYPE_STRING,
                format=openapi.FORMAT_UUID,
                required=True,
            ),
        ],
        responses={status.HTTP_200_OK: PropertyListSerializer(many=True), status.HTTP_404_NOT_FOUND: "Category not found"},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class PropertyListCreateView(ListCreateAPIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartnerOwnerProperty]
    filter_backends = [DjangoFilterBackend, SearchFilter, PropertyOrderingFilter]
    filterset_class = PropertyFilter
    search_fields = ["title"]

    def get_authenticators(self):
        # GET ro'yxat ochiq — token tekshirilmaydi, 401 bo'lmaydi
        if self.request.method == "GET":
            return []
        return [auth() for auth in self.authentication_classes]

    ordering_fields = [
        "title",
        "order_price",
        "comment_count",
        "average_rating",
    ]
    ordering = ["order_price"]

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsPartner()]
        return [AllowAny()]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return PropertyCreateSerializer
        return PropertyListSerializer

    def _resolve_location_filter_uuid(self) -> tuple[bool, uuid_module.UUID | None]:
        """
        location filter uchun bir nechta query param aliasini qoʻllab-quvvatlaydi.
        location_id/id/region_id/district_id/guid dan birinchi non-null qiymat olinadi.

        Returns:
            (False, None): location filter yuborilmagan.
            (True, <uuid>): valid location filter topildi.
            (True, None): location filter yuborilgan, lekin null/invalid.
        """
        query_params = self.request.query_params
        location_keys = ("location_id", "id", "region_id", "district_id", "guid")

        if not any(key in query_params for key in location_keys):
            return False, None

        candidates = (
            query_params.get("location_id"),
            query_params.get("id"),
            query_params.get("region_id"),
            query_params.get("district_id"),
            query_params.get("guid"),
        )
        selected = next((v for v in candidates if not _is_null_like_param(v)), None)
        if selected is None:
            return True, None

        raw = str(selected).strip()
        first = raw.split("/")[0].strip()
        try:
            return True, uuid_module.UUID(first)
        except (ValueError, TypeError):
            return True, None

    def get_queryset(self):
        has_location_filter, location_uuid = self._resolve_location_filter_uuid()
        invalid_location_filter = has_location_filter and location_uuid is None

        rate = exchange_rate()

        today = date.today()
        sort_reference_date = _resolve_sort_reference_date(
            self.request.query_params.get("from_date"),
            today,
        )
        is_weekend = sort_reference_date.weekday() >= 4  # Fri-Sun

        property_price_fields = (
            "property_price__price_on_weekends"
            if is_weekend
            else "property_price__price_on_working_days"
        )

        if isinstance(self.request.user, Partner):
            base_qs = Property.objects.filter(partner=self.request.user)
        else:
            base_qs = Property.objects.filter(is_verified=True)

        if invalid_location_filter:
            base_qs = base_qs.none()
        elif location_uuid is not None:
            base_qs = base_qs.filter(
                Q(region__guid=location_uuid) | Q(district__guid=location_uuid)
            )

        property = (
            base_qs
            .annotate(
                effective_price=_property_price_expression(
                    property_price_fields,
                    sort_reference_date,
                ),
                average_rating=Coalesce(
                    Avg(
                        "property_review__rating",
                        filter=Q(property_review__is_hidden=False)
                        | Q(property_review__is_hidden__isnull=True),
                    ),
                    Value(0),
                    output_field=DecimalField(),
                ),
                order_price=Case(
                    When(currency="UZS", then=F("effective_price")),
                    When(currency="USD", then=F("effective_price") * rate),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                ),
            )
            .prefetch_related("property_price", "property_services", "property_images")
            .select_related(
                "property_location",
                "property_type",
                "property_room",
                "region",
                "district",
            )
        )

        return property

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        property = serializer.save()

        return Response(
            status=status.HTTP_201_CREATED,
            data={
                "detail": _(
                    "Property has been created successfully, please wait while we verify it"
                ),
                "property_id": str(property.guid),
                "status_code": 201,
            },
        )
    @swagger_auto_schema(
        tags=["Property"],
        operation_summary="List verified properties (filters, search, ordering)",
        operation_description=(
            "Returns all **verified** properties. Supports filters, search, and ordering.\n\n"
            "**Query params:**\n"
            "- `search` — search by title\n"
            "- `sort` — price_high, price_low, rating_high, rating_low, reviews_high, reviews_low, title_asc, title_desc\n"
            "- `ordering` — `order_price`, `-order_price`, `average_rating`, `-average_rating`, `title`, `comment_count`\n"
            "- `property_type` — property type GUID (GET /api/property/types/)\n"
            "- `location_id` — bitta location GUID (region yoki district GUID)\n"
            "- `property_services` — service GUIDs (comma-separated: `uuid1,uuid2`)\n"
            "- `min_price`, `max_price`, `currency` — price range (USD or UZS)\n"
            "- `from_date`, `to_date` — date range\n"
            "- `corporate` — corporate kirishga ruxsat (`true/false`)\n"
            "- `adults`, `children` — guest counts\n\n"
            "**Response:** Property list (guid, title, price, property_location, property_images, "
            "region, district, guests, rooms, average_rating, created_at)."
        ),
        manual_parameters=[
            openapi.Parameter("search", openapi.IN_QUERY, type=openapi.TYPE_STRING, description="Search by title"),
            openapi.Parameter(
                "sort",
                openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                description="price_high, price_low, rating_high, rating_low, reviews_high, reviews_low, title_asc, title_desc",
            ),
            openapi.Parameter(
                "ordering",
                openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                description="order_price, -order_price, average_rating, -average_rating, title, comment_count",
            ),
            openapi.Parameter("property_type", openapi.IN_QUERY, type=openapi.TYPE_STRING, format=openapi.FORMAT_UUID),
            openapi.Parameter(
                "location_id",
                openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                format=openapi.FORMAT_UUID,
                description="Region yoki district GUID",
            ),
            openapi.Parameter(
                "property_services",
                openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                description="Service GUIDs, comma-separated",
            ),
            openapi.Parameter("min_price", openapi.IN_QUERY, type=openapi.TYPE_NUMBER),
            openapi.Parameter("max_price", openapi.IN_QUERY, type=openapi.TYPE_NUMBER),
            openapi.Parameter("currency", openapi.IN_QUERY, type=openapi.TYPE_STRING, enum=["USD", "UZS"]),
            openapi.Parameter(
                "corporate",
                openapi.IN_QUERY,
                type=openapi.TYPE_BOOLEAN,
                description="Only properties where corporate entry is allowed",
            ),
        ],
        responses={status.HTTP_200_OK: PropertyListSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @swagger_auto_schema(
        tags=["Property"],
        operation_summary="Create a new property",
        operation_description=(
            "Partners or admins can create a new property listing. "
            "Newly created properties must be verified before becoming visible.\n\n"
            "Location: use GET /api/property/regions/ → GET /api/property/districts/?region_id=<id> → "
            "send the GUIDs as **region_id** and **district_id**; they are stored on the property."
        ),
        request_body=PropertyCreateSerializer,
        responses={
            status.HTTP_201_CREATED: openapi.Response(
                "Success",
                schema=openapi.Schema(type=openapi.TYPE_OBJECT),
                examples={
                    "application/json": {
                        "detail": "Property has been created successfully, please wait while we verify it",
                        "status_code": 201,
                    }
                },
            ),
        },
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class PropertyFilterByLinkView(PropertyListCreateView):
    """
    Server-side rendering (SSR): frontend URL yuboradi, backend shu URL bo'yicha filter qilib
    natijani qaytaradi. DB ga hech narsa saqlanmaydi.
    """

    http_method_names = ["post"]

    def get_authenticators(self):
        return []

    def get_permissions(self):
        return [AllowAny()]

    def get_serializer_class(self):
        return PropertyListSerializer

    @swagger_auto_schema(
        tags=["Property"],
        operation_summary="Properties filter by URL (SSR)",
        operation_description=(
            "**Server-side rendering:** frontend dan **url** keladi, backend shu URL ni olib "
            "query parametrlariga ko'ra propertylarni filter qilib natijani qaytaradi.\n\n"
            "Body: `{\"url\": \"https://weel.uz/properties?location_id=xxx&min_price=100\"}`. "
            "URL dagi parametrlar GET /api/property/properties/ dagi kabi (location_id, property_type, "
            "min_price, max_price, from_date, to_date, ...) qo'llanadi. Natija faqat response da, DB ga yozilmaydi."
        ),
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=["url"],
            properties={"url": openapi.Schema(type=openapi.TYPE_STRING, description="Frontend dan keladigan filter URL")},
        ),
        responses={status.HTTP_200_OK: PropertyListSerializer(many=True), status.HTTP_400_BAD_REQUEST: "url required"},
    )
    def post(self, request, *args, **kwargs):
        from urllib.parse import urlparse
        from django.http import QueryDict

        data = request.data or {}
        url = (data.get("url") or data.get("link") or "").strip()
        if not url:
            return Response(
                {"url": [_("This field is required.")]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        query_string = urlparse(url).query
        request._request.GET = QueryDict(query_string)
        return self.list(request)


class _PropertyTypeListCreateMixin:
    property_type_title_en = None

    def _get_property_type(self):
        if not self.property_type_title_en:
            return None
        return (
            PropertyType.objects.filter(title_en__iexact=self.property_type_title_en)
            .only("guid", "id")
            .first()
        )

    def _coerce_property_type_id(self, request):
        data = request.data.copy()
        property_type = self._get_property_type()
        if property_type is None:
            return None, Response(
                status=status.HTTP_400_BAD_REQUEST,
                data={"detail": _("Property type is not configured")},
            )
        incoming = data.get("property_type_id")
        if incoming:
            if str(incoming).strip() != str(property_type.guid):
                return None, Response(
                    status=status.HTTP_400_BAD_REQUEST,
                    data={"property_type_id": _("Invalid property type for this endpoint")},
                )
        else:
            data["property_type_id"] = str(property_type.guid)
        return data, None

    def get_queryset(self):
        queryset = super().get_queryset()
        property_type = self._get_property_type()
        if not property_type:
            return queryset.none()
        return queryset.filter(property_type_id=property_type.id)

    def create(self, request, *args, **kwargs):
        data, error = self._coerce_property_type_id(request)
        if error:
            return error
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        property = serializer.save()

        return Response(
            status=status.HTTP_201_CREATED,
            data={
                "detail": _(
                    "Property has been created successfully, please wait while we verify it"
                ),
                "property_id": str(property.guid),
                "status_code": 201,
            },
        )


class ApartmentPropertyListCreateView(_PropertyTypeListCreateMixin, PropertyListCreateView):
    property_type_title_en = "Apartment"

    @swagger_auto_schema(
        tags=["Property"],
        operation_summary="Create an apartment property",
        operation_description=(
            "Apartment create endpoint. `property_type_id` is enforced as Apartment.\n\n"
            "**Required in `property_detail` for apartments:**\n"
            "- `apartment_number`\n"
            "- `home_number`\n"
            "- `entrance_number`\n"
            "- `floor_number`\n"
            "- `pass_code`\n"
        ),
        request_body=PropertyCreateSerializer,
        responses={
            status.HTTP_201_CREATED: openapi.Response(
                "Success",
                schema=openapi.Schema(type=openapi.TYPE_OBJECT),
                examples={
                    "application/json": {
                        "detail": "Property has been created successfully, please wait while we verify it",
                        "status_code": 201,
                    }
                },
            ),
        },
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class CottagePropertyListCreateView(_PropertyTypeListCreateMixin, PropertyListCreateView):
    property_type_title_en = "Cottages"

    @swagger_auto_schema(
        tags=["Property"],
        operation_summary="Create a cottage property",
        operation_description=(
            "Cottages create endpoint. `property_type_id` is enforced as Cottages."
        ),
        request_body=PropertyCreateSerializer,
        responses={
            status.HTTP_201_CREATED: openapi.Response(
                "Success",
                schema=openapi.Schema(type=openapi.TYPE_OBJECT),
                examples={
                    "application/json": {
                        "detail": "Property has been created successfully, please wait while we verify it",
                        "status_code": 201,
                    }
                },
            ),
        },
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class RegionPropertyListView(PropertyListCreateView):
    """List verified properties by region (GET only)."""

    http_method_names = ["get"]

    def get_queryset(self):
        region_id = self.kwargs.get("region_id")
        if not Region.objects.filter(guid=region_id).exists():
            raise NotFound(_("Region not found"))
        return super().get_queryset().filter(region__guid=region_id)

    @swagger_auto_schema(
        tags=["Property"],
        operation_summary="Properties by region",
        operation_description=(
            "Returns verified properties for the given **region_id** (region GUID). "
            "All filters and ordering params are supported (search, ordering, min_price, max_price, etc.)."
        ),
        manual_parameters=[
            openapi.Parameter(
                "region_id",
                openapi.IN_PATH,
                description="Region GUID (from GET /api/property/regions/)",
                type=openapi.TYPE_STRING,
                format=openapi.FORMAT_UUID,
                required=True,
            ),
            openapi.Parameter("search", openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter("sort", openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter("ordering", openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter(
                "location_id",
                openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                format=openapi.FORMAT_UUID,
                description="Region yoki district GUID",
            ),
            openapi.Parameter("min_price", openapi.IN_QUERY, type=openapi.TYPE_NUMBER),
            openapi.Parameter("max_price", openapi.IN_QUERY, type=openapi.TYPE_NUMBER),
        ],
        responses={status.HTTP_200_OK: PropertyListSerializer(many=True), status.HTTP_404_NOT_FOUND: "Region not found"},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class PropertyRetrieveUpdateDestroyView(RetrieveUpdateDestroyAPIView):
    queryset = PropertyDetail.objects.select_related("property")
    authentication_classes = [PartnerJWTAuthentication]

    def get_serializer_class(self):
        if self.request.method == "PUT":
            return PropertyPutSerializer
        if self.request.method == "PATCH":
            return PropertyPatchSerializer
        return PropertyDetailSerializer

    def get_permissions(self):
        if self.request.method in ["PUT", "PATCH", "DELETE"]:
            return [IsPartner(), IsPartnerOwnerProperty()]
        return [AllowAny()]

    def get_object(self):
        property_id = self.kwargs.get("property_id")
        user = getattr(self.request, "user", None)

        # Public access: only verified properties.
        # Partner access: partner can access their own properties (verified + unverified).
        property_qs = Property.objects.all()
        if isinstance(user, Partner):
            property_qs = property_qs.filter(guid=property_id, partner=user)
        else:
            property_qs = property_qs.filter(guid=property_id, is_verified=True)

        prop = (
            property_qs.select_related(
                "property_room",
                "property_location",
            )
            .prefetch_related(
                "property_price",
                "property_images",
                "property_services",
            )
            .first()
        )
        if not prop:
            raise NotFound(_("Property not found"))

        # Some legacy rows may exist without PropertyDetail; create an empty detail to avoid 404
        # when the property itself is visible in list endpoints.
        PropertyDetail.objects.get_or_create(
            property=prop,
            defaults={
                "description_en": "",
                "description_ru": "",
                "description_uz": "",
            },
        )

        property_detail = (
            PropertyDetail.objects.select_related(
                "property",
                "property__property_room",
                "property__property_location",
            )
            .prefetch_related(
                "property__property_price",
                "property__property_images",
                "property__property_services",
            )
            .filter(property=prop)
            .first()
        )

        self.check_object_permissions(self.request, property_detail)
        return property_detail

    def update(self, request, *args, **kwargs):
        """Handle PUT and PATCH request"""
        partial = kwargs.get("partial", False)
        instance = self.get_object()
        serializer = self.get_serializer(
            instance,
            data=request.data,
            partial=partial,
        )
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)

        return Response(
            status=status.HTTP_200_OK,
            data={
                "detail": "Your changes have been saved successfully",
                "warning": "Property has been sent for re-verification, please wait while we verify it",
                "status_code": 200,
            },
        )

    def perform_destroy(self, instance):
        property = instance.property
        property.delete()
        return Response(status=HTTP_204_NO_CONTENT)

    @swagger_auto_schema(
        tags=["Property"],
        operation_summary="Get property details by GUID",
        operation_description=(
            "Returns full details of a single verified property by **property_id** (GUID).\n\n"
            "**Response fields:** guid, title, description, price, property_location, property_images, "
            "property_services, property_room (guests, rooms, beds, bathrooms), region, district, "
            "average_rating, comment_count, check_in, check_out, currency, etc."
        ),
        manual_parameters=[property_id_param],
        responses={
            status.HTTP_200_OK: openapi.Response(
                "Property details",
                PropertyDetailSerializer,
            ),
            status.HTTP_404_NOT_FOUND: openapi.Response(description="Property not found"),
        },
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @swagger_auto_schema(
        tags=["Property"],
        operation_summary="Replace property details (PUT)",
        operation_description="Replace the entire property details resource. Use PUT for full updates.",
        manual_parameters=[property_id_param],
        request_body=PropertyPutSerializer,
        responses={
            status.HTTP_200_OK: PropertyUpdateSerializer,
            status.HTTP_400_BAD_REQUEST: "Bad request",
            status.HTTP_404_NOT_FOUND: "Property not found",
        },
    )
    def put(self, request, *args, **kwargs):
        return super().put(request, *args, **kwargs)

    @swagger_auto_schema(
        tags=["Property"],
        operation_summary="Partial update property details (PATCH)",
        operation_description="Partially update property details (only provide fields to change).",
        manual_parameters=[property_id_param],
        request_body=PropertyPatchSerializer,
        responses={
            status.HTTP_200_OK: PropertyUpdateSerializer,
            status.HTTP_400_BAD_REQUEST: "Bad request",
            status.HTTP_404_NOT_FOUND: "Property not found",
        },
    )
    def patch(self, request, *args, **kwargs):
        return super().patch(request, *args, **kwargs)

    @swagger_auto_schema(
        tags=["Property"],
        operation_summary="Delete property",
        operation_description="Delete a property permanently.",
        manual_parameters=[property_id_param],
        responses={
            status.HTTP_204_NO_CONTENT: None,
            status.HTTP_404_NOT_FOUND: "Property not found",
        },
    )
    def delete(self, request, *args, **kwargs):
        return super().delete(request, *args, **kwargs)


class PropertyImageCreateView(APIView):
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    @swagger_auto_schema(
        tags=["Property"],
        operation_summary="Upload property images",
        operation_description="Upload one or more images for a specific property",
        manual_parameters=[
            property_id_param,
            openapi.Parameter(
                "images",
                openapi.IN_FORM,
                type=openapi.TYPE_FILE,
                description="Image files to upload",
                required=True,
            ),
        ],
        responses={
            status.HTTP_201_CREATED: PropertyImageSerializer(many=True),
        },
    )
    def post(self, request, property_id):
        serializer = PropertyImageCreateSerializer(
            data=request.data,
            context={
                "request": request,
                "property_id": property_id,
            },
        )
        serializer.is_valid(raise_exception=True)
        images = serializer.save()

        property = serializer.context["property"]
        if not property.is_verified:
            return Response(
                status=status.HTTP_200_OK,
                data={
                    "detail": "Your image(s) are pending approval",
                    "status": "pending",
                },
            )

        return Response(
            status=status.HTTP_201_CREATED,
            data=PropertyImageSerializer(
                images, many=True, context={"request": request}
            ).data,
        )


class PropertyImageUpdateDeleteView(APIView):
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner, IsPartnerOwnerProperty]

    def get_object(self, property_id, image_id):
        q_filter = Q(guid=property_id)
        if isinstance(self.request.user, Partner):
             q_filter &= (Q(is_verified=True) | Q(partner=self.request.user))
        else:
             q_filter &= Q(is_verified=True)

        property = Property.objects.filter(q_filter).first()
        if not property:
            raise NotFound("Property not found")

        image = PropertyImage.objects.filter(
            guid=image_id, property=property
        )
        if isinstance(self.request.user, Partner) and property.partner == self.request.user:
             # Owner can see all images
             image = image.first()
        else:
             # Others (and non-owners) can only see processed images (not pending)
             image = image.filter(is_pending=False).first()
        if not image:
            raise NotFound("Property image not found")

        self.check_object_permissions(self.request, image)
        return image

    @swagger_auto_schema(
        tags=["Property"],
        operation_summary="Update property image",
        operation_description="Update a property image by its GUID",
        manual_parameters=[
            property_id_param,
            image_id_param,
        ],
        request_body=PropertyImageUpdateSerializer,
        responses={
            status.HTTP_200_OK: PropertyImageUpdateSerializer,
            status.HTTP_404_NOT_FOUND: "Property or image not found",
        },
    )
    def patch(self, request, property_id, image_id):
        image = self.get_object(property_id, image_id)
        serializer = PropertyImageUpdateSerializer(
            image,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(
            status=status.HTTP_200_OK,
            data={
                "detail": _("Your image has been updated and is pending approval"),
                "status": "pending",
            },
        )

    @swagger_auto_schema(
        tags=["Property"],
        operation_summary="Delete property image",
        operation_description="Delete a property image by its GUID",
        manual_parameters=[property_id_param, image_id_param],
        responses={
            status.HTTP_204_NO_CONTENT: None,
            status.HTTP_404_NOT_FOUND: "Property or image not found",
        },
    )
    def delete(self, request, property_id, image_id):
        image = self.get_object(property_id, image_id)
        image.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PropertyReviewListCreateView(ListCreateAPIView):
    authentication_classes = [ClientJWTAuthentication]

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsClient()]
        return [AllowAny()]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return PropertyReviewCreateSerializer
        return PropertyReviewSerializer

    def get_object(self):
        property_id = self.kwargs.get("property_id")
        property = Property.objects.filter(
            guid=property_id,
            is_verified=True,
        ).first()
        if not property:
            raise NotFound("Property not found")
        return property

    def get_queryset(self):
        property = self.get_object()
        return PropertyReview.objects.filter(
            property=property
        ).filter(Q(is_hidden=False) | Q(is_hidden__isnull=True))

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["property"] = self.get_object()
        return context

    @swagger_auto_schema(
        tags=["Property"],
        operation_summary="Retrieve a list of property reviews",
        operation_description="Retrieve all property reviews available in the system",
        manual_parameters=[property_id_param],
        responses={status.HTTP_200_OK: PropertyReviewSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @swagger_auto_schema(
        tags=["Property"],
        operation_summary="Create a new property review",
        operation_description="Allow a user to create a review for a specific property (1–5 rating)",
        manual_parameters=[property_id_param],
        request_body=PropertyReviewCreateSerializer,
        responses={
            status.HTTP_201_CREATED: PropertyReviewSerializer,
            status.HTTP_400_BAD_REQUEST: "Bad request",
            status.HTTP_404_NOT_FOUND: "Property not found",
        },
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class PartnerPropertyReviewListView(ListAPIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartnerOwnerProperty]
    serializer_class = PropertyReviewSerializer

    def get_property(self):
        property_id = self.kwargs.get("property_id")

        property = Property.objects.filter(
            guid=property_id,
            partner=self.request.user,
        ).first()

        if not property:
            raise NotFound("Property not found")
        return property

    def get_queryset(self):
        property = self.get_property()
        return PropertyReview.objects.filter(property=property).select_related(
            "property", "client"
        )

    @swagger_auto_schema(
        tags=["Property"],
        operation_summary="Retrieve reviews for a partner’s property",
        operation_description="Retrieve all reviews for a property owned by the authenticated partner",
        manual_parameters=[property_id_param],
        responses={
            status.HTTP_200_OK: PropertyReviewSerializer(many=True),
            status.HTTP_404_NOT_FOUND: "Property not found",
        },
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class PartnerPropertyListView(ListAPIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]
    serializer_class = PartnerPropertyListSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, PropertyOrderingFilter]
    filterset_class = PropertyFilter
    search_fields = ["title"]
    ordering_fields = [
        "title",
        "order_price",
        "comment_count",
        "average_rating",
    ]
    ordering = ["order_price"]

    def get_queryset(self):
        rate = exchange_rate()

        today = date.today()
        sort_reference_date = _resolve_sort_reference_date(
            self.request.query_params.get("from_date"),
            today,
        )
        is_weekend = sort_reference_date.weekday() >= 4

        property_price_fields = (
            "property_price__price_on_weekends"
            if is_weekend
            else "property_price__price_on_working_days"
        )

        base_qs = Property.objects.filter(partner=self.request.user)

        return (
            base_qs
            .annotate(
                effective_price=_property_price_expression(
                    property_price_fields,
                    sort_reference_date,
                ),
                average_rating=Coalesce(
                    Avg(
                        "property_review__rating",
                        filter=Q(property_review__is_hidden=False)
                        | Q(property_review__is_hidden__isnull=True),
                    ),
                    Value(0),
                    output_field=DecimalField(),
                ),
                order_price=Case(
                    When(currency="UZS", then=F("effective_price")),
                    When(currency="USD", then=F("effective_price") * rate),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                ),
            )
            .prefetch_related("property_price", "property_services", "property_images")
            .select_related("property_location", "property_type", "region", "district")
        )

    @swagger_auto_schema(
        tags=["Property"],
        operation_summary="Retrieve partner's own properties",
        operation_description=(
            "Requires partner JWT token. Returns only authenticated partner's own "
            "properties (including unverified)."
        ),
        responses={status.HTTP_200_OK: PartnerPropertyListSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class SavedPropertyListView(ListAPIView):
    """Faqat joriy client o‘zi saqlagan (favorit) propertylar ro‘yxati."""
    authentication_classes = [ClientJWTAuthentication]
    permission_classes = [IsClient]
    serializer_class = PropertyListSerializer

    def get_queryset(self):
        rate = exchange_rate()
        today = date.today()
        sort_reference_date = _resolve_sort_reference_date(
            self.request.query_params.get("from_date"),
            today,
        )
        is_weekend = sort_reference_date.weekday() >= 4
        property_price_fields = (
            "property_price__price_on_weekends"
            if is_weekend
            else "property_price__price_on_working_days"
        )
        base_qs = Property.objects.filter(
            is_verified=True,
            favorites__client=self.request.user,
        ).distinct()
        return (
            base_qs
            .annotate(
                effective_price=_property_price_expression(
                    property_price_fields,
                    sort_reference_date,
                ),
                average_rating=Coalesce(
                    Avg(
                        "property_review__rating",
                        filter=Q(property_review__is_hidden=False)
                        | Q(property_review__is_hidden__isnull=True),
                    ),
                    Value(0),
                    output_field=DecimalField(),
                ),
                order_price=Case(
                    When(currency="UZS", then=F("effective_price")),
                    When(currency="USD", then=F("effective_price") * rate),
                    output_field=DecimalField(max_digits=12, decimal_places=2),
                ),
            )
            .prefetch_related("property_price", "property_services", "property_images")
            .select_related(
                "property_location",
                "property_type",
                "property_room",
                "region",
                "district",
            )
        )

    @swagger_auto_schema(
        tags=["Property"],
        operation_summary="List of saved (favorite) properties",
        operation_description=(
            "Returns only the authenticated client's saved (favorite) properties. "
            "Response format is the same as other property list APIs."
        ),
        responses={status.HTTP_200_OK: PropertyListSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class PropertyFavoriteToggleView(APIView):
    authentication_classes = [ClientJWTAuthentication]
    permission_classes = [IsClient]

    @swagger_auto_schema(
        tags=["Property"],
        operation_summary="Toggle property favorite (saved property)",
        operation_description=(
            "Toggle favorite status for a property for the authenticated client. "
            "If the property is not yet in favorites, it will be added; otherwise it will be removed."
        ),
        manual_parameters=[property_id_param],
    )
    def post(self, request, property_id):
        property_obj = Property.objects.filter(
            guid=property_id,
            is_verified=True,
        ).first()
        if not property_obj:
            raise NotFound(_("Property not found"))

        favorite, created = PropertyFavorite.objects.get_or_create(
            client=request.user,
            property=property_obj,
        )
        if not created:
            favorite.delete()
            return Response(
                {
                    "detail": _("Removed from favorites"),
                    "is_favorite": False,
                },
                status=status.HTTP_200_OK,
            )

        return Response(
            {
                "detail": _("Added to favorites"),
                "is_favorite": True,
            },
            status=status.HTTP_201_CREATED,
        )

    @swagger_auto_schema(
        tags=["Property"],
        operation_summary="Remove property from favorites",
        operation_description=(
            "Remove the property from the authenticated client's saved/favorites list. "
            "If the property was not in favorites, the response is still success (idempotent)."
        ),
        manual_parameters=[property_id_param],
        responses={
            status.HTTP_200_OK: openapi.Response(
                description="Removed from favorites (or was not in favorites)",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "detail": openapi.Schema(type=openapi.TYPE_STRING),
                        "is_favorite": openapi.Schema(type=openapi.TYPE_BOOLEAN),
                    },
                ),
            ),
            status.HTTP_404_NOT_FOUND: "Property not found",
        },
    )
    def delete(self, request, property_id):
        property_obj = Property.objects.filter(
            guid=property_id,
            is_verified=True,
        ).first()
        if not property_obj:
            raise NotFound(_("Property not found"))

        PropertyFavorite.objects.filter(
            client=request.user,
            property=property_obj,
        ).delete()

        return Response(
            {
                "detail": _("Removed from favorites"),
                "is_favorite": False,
            },
            status=status.HTTP_200_OK,
        )
