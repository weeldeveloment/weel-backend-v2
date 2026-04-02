import uuid

from decimal import Decimal
from datetime import datetime, timedelta

from dateutil.relativedelta import relativedelta
from django.core.cache import cache
from django.db.models import Q, F
from django.utils import timezone
from django_filters import rest_framework as filters
from rest_framework.filters import OrderingFilter

from .models import PropertyService, Property
from booking.models import CalendarDate
from payment.choices import Currency
from payment.exchange_rate import to_uzs, to_usd
from .pricing import day_type_flags


class PropertyServiceFilter(filters.FilterSet):
    property_id = filters.UUIDFilter(
        field_name="properties__guid",
        lookup_expr="exact",
    )
    property_type_id = filters.UUIDFilter(
        field_name="property_type__guid",
        lookup_expr="exact",
        label="Filter services by property type (e.g. Apartment GUID)",
    )

    class Meta:
        model = PropertyService
        fields = ["property_id", "property_type_id"]


# Mobil ilova sort=price_high kabi parametr yuboradi; backend ordering=-order_price kutadi.
# Bu mapping ikkala formatni qoʻllab-quvvatlash uchun.
SORT_TO_ORDERING = {
    "price_high": ["-order_price"],
    "price_low": ["order_price"],
    "rating_high": ["-average_rating"],
    "rating_low": ["average_rating"],
    "reviews_high": ["-comment_count"],
    "reviews_low": ["comment_count"],
    "title_asc": ["title"],
    "title_desc": ["-title"],
}


class PropertyOrderingFilter(OrderingFilter):
    """
    ordering (DRF standart) bilan bir qatorda sort parametrini qabul qiladi.
    Masalan: ?sort=price_high, ?sort=rating_low, ?ordering=-order_price
    """

    def get_ordering(self, request, queryset, view):
        sort = request.query_params.get("sort", "").strip().lower()
        if sort and sort in SORT_TO_ORDERING:
            return SORT_TO_ORDERING[sort]
        return super().get_ordering(request, queryset, view)

    def filter_queryset(self, request, queryset, view):
        ordering = self.get_ordering(request, queryset, view)
        if not ordering:
            return queryset

        normalized_ordering = []
        for field in ordering:
            if field.lstrip("-") == "order_price":
                if field.startswith("-"):
                    normalized_ordering.append(F("order_price").desc(nulls_last=True))
                else:
                    normalized_ordering.append(F("order_price").asc(nulls_last=True))
            else:
                normalized_ordering.append(field)

        return queryset.order_by(*normalized_ordering)


class PropertyFilter(filters.FilterSet):
    property_type = filters.UUIDFilter(
        field_name="property_type__guid", lookup_expr="exact"
    )
    location_id = filters.CharFilter(
        method="filter_location_id",
        label="location_id",
    )
    property_services = filters.CharFilter(
        field_name="property_services",
        method="filter_property_services",
    )
    min_price = filters.NumberFilter(method="filter_price_range")
    currency = filters.ChoiceFilter(choices=Currency.choices)
    max_price = filters.NumberFilter(method="filter_price_range")
    from_date = filters.DateFilter(method="filter_price_range")
    to_date = filters.DateFilter(method="filter_price_range")
    adults = filters.NumberFilter(method="filter_guests")
    children = filters.NumberFilter(method="filter_guests")
    alcohol = filters.BooleanFilter(field_name="property_detail__is_allowed_alcohol")
    pets = filters.BooleanFilter(field_name="property_detail__is_allowed_pets")
    corporate = filters.BooleanFilter(field_name="property_detail__is_allowed_corporate")
    is_allowed_corporate = filters.BooleanFilter(
        field_name="property_detail__is_allowed_corporate"
    )

    class Meta:
        model = Property
        fields = [
            "property_type",
            "location_id",
            "property_services",
            "min_price",
            "max_price",
            "currency",
            "from_date",
            "to_date",
        ]

    def filter_location_id(self, queryset, name, value):
        raw = str(value).strip() if value is not None else ""
        if raw.lower() in {"", "null", "none", "undefined"}:
            return queryset.none()
        try:
            location_uuid = uuid.UUID(raw)
        except (ValueError, TypeError):
            return queryset.none()
        return queryset.filter(Q(region__guid=location_uuid) | Q(district__guid=location_uuid))

    def _bound_filter_price_range(
        self, value: str, currency: str
    ) -> dict[str, Decimal]:
        """Normalize user-entered filter price range into both currencies USA and UZS"""
        value = Decimal(value)
        if currency == "USD":
            return {"usd": value, "uzs": to_uzs(value)}
        else:
            return {"uzs": value, "usd": to_usd(value)}

    def _build_currency_price_q(
        self,
        *,  # this means that all parameters after * are keyword only
        field_name: str,
        min_bounds: dict,
        max_bounds: dict,
        currency: str,
    ) -> Q:
        """Build currency-aware Q object for a given price field"""
        usd_q = Q(currency="USD")
        uzs_q = Q(currency="UZS")

        if currency == "USD":
            if min_bounds.get("usd"):
                usd_q &= Q(**{f"{field_name}__gte": min_bounds["usd"]})
            if max_bounds.get("usd"):
                usd_q &= Q(**{f"{field_name}__lte": max_bounds["usd"]})

            # Also search UZS properties that converted to USD
            if min_bounds.get("uzs"):
                uzs_q &= Q(**{f"{field_name}__gte": min_bounds["uzs"]})
            if max_bounds.get("uzs"):
                uzs_q &= Q(**{f"{field_name}__lte": max_bounds["uzs"]})
        else:  # UZS
            if min_bounds.get("uzs"):
                uzs_q &= Q(**{f"{field_name}__gte": min_bounds["uzs"]})
            if max_bounds.get("uzs"):
                uzs_q &= Q(**{f"{field_name}__lte": max_bounds["uzs"]})

            # Also search USD properties that converted to UZS
            if min_bounds.get("usd"):
                usd_q &= Q(**{f"{field_name}__gte": min_bounds["usd"]})
            if max_bounds.get("usd"):
                usd_q &= Q(**{f"{field_name}__lte": max_bounds["usd"]})

        return usd_q | uzs_q

    def _is_property_blocked_or_booked(self, queryset, from_date, end_date):
        return queryset.exclude(
            calendar_date_property__date__range=(
                from_date,
                end_date - timedelta(days=1),
            ),
            calendar_date_property__status__in=[
                CalendarDate.CalendarStatus.BOOKED,
                CalendarDate.CalendarStatus.BLOCKED,
            ],
        )

    def _is_property_is_held(self, property: Property, from_date, end_date) -> bool:
        for day in (
            from_date + timedelta(n) for n in range((end_date - from_date).days)
        ):
            cache_key = f"calendar:hold:{property.guid}:{day.isoformat()}"
            if cache.get(cache_key):
                return True
        return False

    def _normalize_dates(self, from_date, to_date):
        if not from_date:
            return None

        today = timezone.localdate()
        max_allowed_date = today.replace(day=1) + relativedelta(months=2)
        end_date = to_date or (from_date + timedelta(days=1))

        if from_date < today:
            return None

        if end_date <= from_date:
            return None

        if end_date > max_allowed_date:
            return None

        return from_date, end_date

    def _filter_availability(self, queryset, from_date, to_date):
        normalized = self._normalize_dates(from_date, to_date)
        if not normalized:
            return queryset.none()

        from_date, to_date = normalized
        queryset = self._is_property_blocked_or_booked(queryset, from_date, to_date)

        available_ids = []
        for property in queryset:
            if not self._is_property_is_held(property, from_date, to_date):
                available_ids.append(property.guid)
        return queryset.filter(guid__in=available_ids)

    def filter_price_range(self, queryset, name, value):
        min_price = self.data.get("min_price")
        max_price = self.data.get("max_price")
        currency = self.data.get("currency", "UZS")
        from_date = self.data.get("from_date")
        to_date = self.data.get("to_date")

        if from_date:
            from_date = datetime.strptime(from_date, "%Y-%m-%d").date()
        if to_date:
            to_date = datetime.strptime(to_date, "%Y-%m-%d").date()

        queryset = self._filter_availability(queryset, from_date, to_date)

        min_bounds = (
            self._bound_filter_price_range(min_price, currency) if min_price else {}
        )
        max_bounds = (
            self._bound_filter_price_range(max_price, currency) if max_price else {}
        )

        reference_start = from_date or timezone.localdate()
        reference_end = to_date or reference_start
        if reference_end < reference_start:
            return queryset.none()

        price_q = Q(
            property_price__month_from__lte=reference_end,
            property_price__month_to__gte=reference_start,
        )

        weekdays, weekends = day_type_flags(reference_start, reference_end)
        if weekdays:
            price_q &= self._build_currency_price_q(
                field_name="property_price__price_on_working_days",
                min_bounds=min_bounds,
                max_bounds=max_bounds,
                currency=currency,
            )

        if weekends:
            price_q &= self._build_currency_price_q(
                field_name="property_price__price_on_weekends",
                min_bounds=min_bounds,
                max_bounds=max_bounds,
                currency=currency,
            )

        return queryset.filter(price_q).distinct()

    def filter_property_services(self, queryset, name, value):
        property_services_ids = [v.strip() for v in value.split(",") if v.strip()]

        if not property_services_ids:
            return queryset.none()

        valid_property_services_ids = []
        for property_services_id in property_services_ids:
            try:
                uuid.UUID(property_services_id)
                valid_property_services_ids.append(property_services_id)
            except (ValueError, TypeError):
                continue  # Skip invalid UUIDs, filter only by valid ones

        if not valid_property_services_ids:
            return queryset.none()

        for service_guid in valid_property_services_ids:
            queryset = queryset.filter(property_services__guid=service_guid)
        return queryset.distinct()

    def filter_guests(self, queryset, name, value):
        try:
            adults = int(self.data.get("adults", 0))
            children = int(self.data.get("children", 0))
        except (TypeError, ValueError):
            return queryset.none()

        total_guests = adults + children
        if total_guests <= 0:
            return queryset.none()

        return queryset.filter(property_room__guests__gte=total_guests)
