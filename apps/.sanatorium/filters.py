from django_filters import rest_framework as filters

from .models import Sanatorium, SanatoriumRoom


class SanatoriumFilter(filters.FilterSet):
    specialization = filters.UUIDFilter(
        field_name="specializations__guid", lookup_expr="exact"
    )
    city = filters.CharFilter(field_name="location__city", lookup_expr="icontains")
    min_price = filters.NumberFilter(method="filter_min_price")
    max_price = filters.NumberFilter(method="filter_max_price")

    class Meta:
        model = Sanatorium
        fields = ["specialization", "city"]

    def filter_min_price(self, queryset, name, value):
        from .models import SanatoriumRoomPrice

        room_ids = SanatoriumRoomPrice.objects.filter(
            price__gte=value
        ).values_list("room__sanatorium_id", flat=True)
        return queryset.filter(id__in=room_ids)

    def filter_max_price(self, queryset, name, value):
        from .models import SanatoriumRoomPrice

        room_ids = SanatoriumRoomPrice.objects.filter(
            price__lte=value
        ).values_list("room__sanatorium_id", flat=True)
        return queryset.filter(id__in=room_ids)


class SanatoriumRoomFilter(filters.FilterSet):
    room_type = filters.UUIDFilter(
        field_name="room_type__guid", lookup_expr="exact"
    )
    package_type = filters.UUIDFilter(method="filter_package_type")
    min_capacity = filters.NumberFilter(
        field_name="capacity", lookup_expr="gte"
    )

    class Meta:
        model = SanatoriumRoom
        fields = ["room_type"]

    def filter_package_type(self, queryset, name, value):
        return queryset.filter(prices__package_type__guid=value).distinct()
