from __future__ import annotations

from rest_framework import serializers

from .raw_serializers import (
    RawDistrictSerializer,
    RawPartnerPropertyListSerializer,
    RawPropertyCreateSerializer,
    RawPropertyDetailSerializer,
    RawPropertyImageSerializer,
    RawPropertyListPriceSerializer,
    RawPropertyListSerializer,
    RawPropertyLocationSerializer,
    RawPropertyReviewClientSerializer,
    RawPropertyReviewCreateSerializer,
    RawPropertyReviewSerializer,
    RawPropertyTypeSerializer,
    RawPropertyUpdateSerializer,
    RawRegionSerializer,
)


class PropertyTypeSlugRelatedField(serializers.UUIDField):
    pass


class PropertyTypeListSerializer(RawPropertyTypeSerializer):
    pass


class PropertyLocationSerializer(RawPropertyLocationSerializer):
    pass


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


class CategoryListSerializer(serializers.Serializer):
    guid = serializers.UUIDField(required=False, allow_null=True)
    title = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    icon_url = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class PropertyImageSerializer(RawPropertyImageSerializer):
    pass


class PropertyListSerializer(RawPropertyListSerializer):
    pass


class PartnerPropertyListSerializer(RawPartnerPropertyListSerializer):
    pass


class PropertyListPriceSerializer(RawPropertyListPriceSerializer):
    pass


class PropertyPriceSerializer(RawPropertyListPriceSerializer):
    pass


class PropertyRoomSerializer(serializers.Serializer):
    guests = serializers.IntegerField(required=False, allow_null=True)
    rooms = serializers.IntegerField(required=False, allow_null=True)
    beds = serializers.IntegerField(required=False, allow_null=True)
    bathrooms = serializers.IntegerField(required=False, allow_null=True)


class PropertyReviewClientSerializer(RawPropertyReviewClientSerializer):
    pass


class PropertyReviewSerializer(RawPropertyReviewSerializer):
    pass


class PropertyReviewCreateSerializer(RawPropertyReviewCreateSerializer):
    pass


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
