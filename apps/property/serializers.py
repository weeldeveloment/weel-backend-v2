from __future__ import annotations

from rest_framework import serializers

from .apartment_serializers import (
    ApartmentCreateSerializer as RawPropertyCreateSerializer,
    ApartmentDetailSerializer as RawPropertyDetailSerializer,
    ApartmentListSerializer as RawPropertyListSerializer,
    ApartmentPartnerListSerializer as RawPartnerPropertyListSerializer,
    ApartmentUpdateSerializer as RawPropertyUpdateSerializer,
)
from .cottage_serializers import RawDistrictSerializer, RawRegionSerializer


class RawPropertyTypeSerializer(serializers.Serializer):
    guid = serializers.UUIDField()
    title = serializers.CharField()
    icon_url = serializers.CharField(allow_null=True)


class PropertyTypeSlugRelatedField(serializers.UUIDField):
    pass


class PropertyTypeListSerializer(RawPropertyTypeSerializer):
    pass


class PropertyLocationSerializer(serializers.Serializer):
    latitude = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    longitude = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    country = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    city = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class RegionListSerializer(RawRegionSerializer):
    pass


class DistrictListSerializer(RawDistrictSerializer):
    pass


class LocationDistrictSerializer(RawDistrictSerializer):
    pass


class LocationRegionSerializer(RawRegionSerializer):
    districts = serializers.ListField(required=False, default=list)


class PropertyServiceListSerializer(serializers.Serializer):
    guid = serializers.UUIDField(required=False, allow_null=True)
    title = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    icon_url = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class PrefectureListSerializer(serializers.Serializer):
    guid = serializers.UUIDField(required=False, allow_null=True)
    title = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class CategoryListSerializer(serializers.Serializer):
    guid = serializers.UUIDField(required=False, allow_null=True)
    title = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    icon_url = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class PropertyImageSerializer(serializers.Serializer):
    guid = serializers.UUIDField(required=False, allow_null=True)
    order = serializers.IntegerField(required=False, allow_null=True)
    image_url = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class PropertyListSerializer(RawPropertyListSerializer):
    pass


class PartnerPropertyListSerializer(RawPartnerPropertyListSerializer):
    pass


class PropertyListPriceSerializer(serializers.Serializer):
    price = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, allow_null=True)


class PropertyPriceSerializer(PropertyListPriceSerializer):
    pass


class PropertyRoomSerializer(serializers.Serializer):
    guests = serializers.IntegerField(required=False, allow_null=True)
    rooms = serializers.IntegerField(required=False, allow_null=True)
    beds = serializers.IntegerField(required=False, allow_null=True)
    bathrooms = serializers.IntegerField(required=False, allow_null=True)


class PropertyReviewClientSerializer(serializers.Serializer):
    guid = serializers.UUIDField(required=False, allow_null=True)
    first_name = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    last_name = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class PropertyReviewSerializer(serializers.Serializer):
    guid = serializers.UUIDField(required=False, allow_null=True)
    client = PropertyReviewClientSerializer(required=False)
    rating = serializers.DecimalField(max_digits=2, decimal_places=1, required=False, allow_null=True)
    comment = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    created_at = serializers.DateTimeField(required=False)


class PropertyReviewCreateSerializer(serializers.Serializer):
    rating = serializers.DecimalField(max_digits=2, decimal_places=1, required=True)
    comment = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class PropertyDetailSerializer(RawPropertyDetailSerializer):
    pass


class PropertyDetailCreateSerializer(serializers.Serializer):
    description_en = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    description_ru = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    description_uz = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    check_in = serializers.TimeField(required=False, allow_null=True)
    check_out = serializers.TimeField(required=False, allow_null=True)
    is_allowed_alcohol = serializers.BooleanField(required=False, default=False)
    is_allowed_corporate = serializers.BooleanField(required=False, default=False)
    is_allowed_pets = serializers.BooleanField(required=False, default=False)
    is_quiet_hours = serializers.BooleanField(required=False, default=False)
    apartment_number = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    home_number = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    entrance_number = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    floor_number = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    pass_code = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class PropertyCreateSerializer(RawPropertyCreateSerializer):
    pass


class PropertyUpdateSerializer(RawPropertyUpdateSerializer):
    pass


class PropertyPutSerializer(RawPropertyUpdateSerializer):
    pass


class PropertyPatchSerializer(RawPropertyUpdateSerializer):
    pass


class PropertyImageCreateSerializer(serializers.Serializer):
    image = serializers.ImageField()
    order = serializers.IntegerField(required=False, default=1)


class PropertyImageUpdateSerializer(serializers.Serializer):
    order = serializers.IntegerField(required=False)
