from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from .raw_serializers import (
    _build_media_url,
    _convert_price_for_output,
    _favorite_guid_set,
    _parse_int_maybe,
    _to_decimal,
    RawPropertyLocationSerializer,
    RawRegionSerializer,
    RawDistrictSerializer,
    _PropertyLocationInputSerializer,
    _PropertyDetailInputSerializer,
)


class ApartmentListSerializer(serializers.Serializer):
    guid = serializers.UUIDField()
    title = serializers.CharField()
    img = serializers.ListField(child=serializers.CharField(), allow_empty=True)
    price = serializers.DecimalField(max_digits=18, decimal_places=2, allow_null=True)
    currency = serializers.CharField(allow_blank=True, allow_null=True)
    property_location = RawPropertyLocationSerializer(allow_null=True)
    services = serializers.ListField()
    region = RawRegionSerializer(allow_null=True)
    district = RawDistrictSerializer(allow_null=True)
    guests = serializers.IntegerField(allow_null=True)
    rooms = serializers.IntegerField(allow_null=True)
    average_rating = serializers.FloatField(allow_null=True)
    is_favorite = serializers.BooleanField()
    created_at = serializers.DateTimeField()

    def to_representation(self, instance):
        request = self.context.get("request")
        row = dict(instance)
        row["img"] = _build_media_url(request, row.get("img"))
        row_currency = row.get("currency")
        row["price"] = _convert_price_for_output(row.get("price"), row_currency)
        row["property_location"] = {
            "guid": None,
            "latitude": row.get("latitude"),
            "longitude": row.get("longitude"),
            "country": row.get("country"),
            "city": row.get("city"),
        }
        row["services"] = row.get("services") or []
        if row.get("region_id") is None:
            row["region"] = None
        else:
            row["region"] = {"guid": None, "title": str(row.get("region_id")), "img": None}
        if row.get("district_id") is None:
            row["district"] = None
        else:
            row["district"] = {"guid": None, "title": str(row.get("district_id")), "region": row.get("region")}
        row["guests"] = None
        row["rooms"] = None
        favorites = _favorite_guid_set(self.context)
        row["is_favorite"] = str(row.get("guid")) in favorites
        return super().to_representation(row)


class ApartmentPartnerListSerializer(ApartmentListSerializer):
    verification_status = serializers.CharField(allow_blank=True, allow_null=True)


class ApartmentDetailSerializer(serializers.Serializer):
    guid = serializers.UUIDField()
    title = serializers.CharField()
    img = serializers.ListField(child=serializers.CharField(), allow_empty=True)
    created_at = serializers.DateTimeField()
    currency = serializers.CharField(allow_blank=True, allow_null=True)
    price = serializers.DecimalField(max_digits=18, decimal_places=2, allow_null=True)
    minimum_weekend_day_stay = serializers.BooleanField()
    description = serializers.CharField(allow_blank=True, allow_null=True)
    comment_count = serializers.IntegerField()
    average_rating = serializers.FloatField(allow_null=True)
    is_favorite = serializers.BooleanField()
    property_services = serializers.ListField()
    property_room = serializers.DictField()
    property_location = RawPropertyLocationSerializer(allow_null=True)
    apartment_number = serializers.CharField(allow_blank=True, allow_null=True)
    home_number = serializers.CharField(allow_blank=True, allow_null=True)
    entrance_number = serializers.CharField(allow_blank=True, allow_null=True)
    floor_number = serializers.CharField(allow_blank=True, allow_null=True)
    pass_code = serializers.CharField(allow_blank=True, allow_null=True)
    check_in = serializers.TimeField(allow_null=True)
    check_out = serializers.TimeField(allow_null=True)
    is_allowed_alcohol = serializers.BooleanField()
    is_allowed_corporate = serializers.BooleanField()
    is_allowed_pets = serializers.BooleanField()
    is_quiet_hours = serializers.BooleanField()

    def _resolve_description(self, row: dict[str, Any]) -> str:
        request = self.context.get("request")
        lang = ""
        if request is not None:
            lang = str(request.query_params.get("lang") or "").strip().lower()
            if not lang:
                header = str(request.headers.get("Accept-Language") or "").strip().lower()
                if header:
                    lang = header.split(",")[0].split("-")[0]
        if lang not in {"en", "ru", "uz"}:
            lang = "en"
        value = row.get(f"description_{lang}")
        if value:
            return str(value)
        return str(row.get("description_en") or row.get("description_ru") or row.get("description_uz") or "")

    def to_representation(self, instance):
        request = self.context.get("request")
        row = dict(instance)
        row["img"] = _build_media_url(request, row.get("img"))
        row_currency = row.get("currency")
        row["price"] = _convert_price_for_output(row.get("price"), row_currency)
        row["description"] = self._resolve_description(row)
        row["comment_count"] = int(row.get("review_count") or row.get("comment_count") or 0)
        favorites = _favorite_guid_set(self.context)
        row["is_favorite"] = str(row.get("guid")) in favorites
        row["property_services"] = row.get("services") or []
        row["property_room"] = {"guid": None, "guests": None, "rooms": None, "beds": None, "bathrooms": None}
        row["property_location"] = {
            "guid": None,
            "latitude": row.get("latitude"),
            "longitude": row.get("longitude"),
            "country": row.get("country"),
            "city": row.get("city"),
        }
        return super().to_representation(row)


class ApartmentCreateSerializer(serializers.Serializer):
    title = serializers.CharField(required=False, allow_blank=True)
    price = serializers.DecimalField(max_digits=18, decimal_places=2, required=False)
    currency = serializers.ChoiceField(required=False, choices=["USD", "UZS"])
    minimum_weekend_day_stay = serializers.BooleanField(required=False, default=False)
    weekend_only_sunday_inclusive = serializers.BooleanField(required=False, default=False)
    property_location = serializers.DictField(required=False)
    property_detail = serializers.DictField(required=False)
    property_services = serializers.ListField(required=False, allow_empty=True)
    property_room = serializers.DictField(required=False)
    region = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    district = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    region_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    district_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    img = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    apartment_number = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    home_number = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    entrance_number = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    floor_number = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    pass_code = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    def validate(self, attrs):
        is_update = bool(self.context.get("is_update"))

        title = (attrs.get("title") or "").strip()
        if not is_update and not title:
            raise serializers.ValidationError({"title": _("This field is required.")})

        location_serializer = _PropertyLocationInputSerializer(data=attrs.get("property_location") or {}, partial=True)
        location_serializer.is_valid(raise_exception=True)

        detail_payload = attrs.get("property_detail") or {}
        for key in ("apartment_number", "home_number", "entrance_number", "floor_number", "pass_code"):
            if attrs.get(key) is not None and key not in detail_payload:
                detail_payload[key] = attrs.get(key)
        detail_serializer = _PropertyDetailInputSerializer(data=detail_payload, partial=True)
        detail_serializer.is_valid(raise_exception=True)

        if not is_update:
            required_detail = ["apartment_number", "home_number", "entrance_number", "floor_number", "pass_code"]
            missing = [f for f in required_detail if not detail_serializer.validated_data.get(f)]
            if missing:
                raise serializers.ValidationError({
                    "property_detail": {f: _("This field is required for apartment properties.") for f in missing}
                })

        price = _to_decimal(attrs.get("price"))
        if not is_update and price is None:
            price = Decimal("0")
        if price is not None and price < 0:
            raise serializers.ValidationError(_("Price must be non-negative"))

        normalized: dict[str, Any] = {}
        if title:
            normalized["title"] = title
            normalized["title_sort"] = title.lower()
        if "minimum_weekend_day_stay" in attrs:
            normalized["minimum_weekend_day_stay"] = bool(attrs.get("minimum_weekend_day_stay"))
        elif not is_update:
            normalized["minimum_weekend_day_stay"] = False
        if "weekend_only_sunday_inclusive" in attrs:
            normalized["weekend_only_sunday_inclusive"] = bool(attrs.get("weekend_only_sunday_inclusive"))
        elif not is_update:
            normalized["weekend_only_sunday_inclusive"] = False
        if "currency" in attrs:
            normalized["currency"] = attrs.get("currency") or "UZS"
        elif not is_update:
            normalized["currency"] = "UZS"
        if "img" in attrs:
            normalized["img"] = attrs.get("img")
        if price is not None:
            normalized["price"] = price
        if attrs.get("property_location") is not None:
            normalized.update(location_serializer.validated_data)
        if detail_serializer.validated_data:
            normalized.update(detail_serializer.validated_data)
        if "region_id" in attrs or "region" in attrs:
            normalized["region_id"] = _parse_int_maybe(
                attrs.get("region_id") if attrs.get("region_id") is not None else attrs.get("region")
            )
        if "district_id" in attrs or "district" in attrs:
            normalized["district_id"] = _parse_int_maybe(
                attrs.get("district_id") if attrs.get("district_id") is not None else attrs.get("district")
            )

        attrs["normalized_values"] = normalized
        return attrs


class ApartmentUpdateSerializer(ApartmentCreateSerializer):
    pass
