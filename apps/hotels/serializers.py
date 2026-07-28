from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from rest_framework import serializers

from apps.property.hotel_serializers import (
    _build_media_url,
    _convert_price_for_output,
    _favorite_guid_set,
    _iso_time_str,
    _preferred_language,
)


SORT_CHOICES = ["popular", "rating", "reviews", "cheap", "expensive", "weel_recommended"]

WEEL_CLASS_CHOICES = [
    "standard", "essential", "comfort", "comfort_plus",
    "business", "premium", "signature",
]


class HotelSearchParamsSerializer(serializers.Serializer):
    city = serializers.CharField(required=False, allow_blank=True)
    check_in = serializers.DateField(required=False)
    check_out = serializers.DateField(required=False)
    guests = serializers.IntegerField(required=False, default=1, min_value=1)
    adults = serializers.IntegerField(required=False, min_value=1)
    children = serializers.IntegerField(required=False, min_value=0)
    babies = serializers.IntegerField(required=False, min_value=0)
    star_rating = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=5)
    weel_classification = serializers.ChoiceField(choices=WEEL_CLASS_CHOICES, required=False, allow_null=True)
    is_recommended = serializers.BooleanField(required=False, allow_null=True, default=None)
    themes = serializers.ListField(child=serializers.CharField(), required=False)
    price_min = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    price_max = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    budget_max = serializers.DecimalField(max_digits=14, decimal_places=2, required=False, allow_null=True, min_value=0)
    room_types = serializers.CharField(required=False, allow_blank=True)
    room_type_presets = serializers.CharField(required=False, allow_blank=True)
    rate_plans = serializers.CharField(required=False, allow_blank=True)
    meal_plans = serializers.CharField(required=False, allow_blank=True)
    min_capacity = serializers.IntegerField(required=False, min_value=1)
    max_capacity = serializers.IntegerField(required=False, min_value=1)
    lat = serializers.FloatField(required=False, allow_null=True)
    lon = serializers.FloatField(required=False, allow_null=True)
    radius_km = serializers.FloatField(required=False, default=10.0, min_value=0.1)
    sort_by = serializers.ChoiceField(choices=SORT_CHOICES, required=False, default="popular")
    page = serializers.IntegerField(required=False, default=1, min_value=1)
    page_size = serializers.IntegerField(required=False, default=20, min_value=1, max_value=100)

    def validate(self, data):
        check_in = data.get("check_in")
        check_out = data.get("check_out")
        if check_in and check_out and check_out <= check_in:
            raise serializers.ValidationError({"check_out": "check_out must be after check_in."})
        if (data.get("lat") is None) != (data.get("lon") is None):
            raise serializers.ValidationError({"lon": "Both lat and lon must be provided together."})
        if data.get("budget_max") is not None and (check_in is None or check_out is None):
            raise serializers.ValidationError({
                "budget_max": "check_in and check_out are required when budget_max is provided.",
            })
        if "adults" in data or "children" in data or "babies" in data:
            data["guests"] = int(data.get("adults") or 0) + int(data.get("children") or 0) + int(data.get("babies") or 0)
            if data["guests"] <= 0:
                raise serializers.ValidationError({"guests": "At least one guest is required."})
        elif data.get("guests") is None:
            data["guests"] = 1
        def _split(value: str | None) -> list[str]:
            if not value:
                return []
            return [item.strip() for item in value.split(",") if item.strip()]

        data["room_types"] = _split(data.get("room_types"))
        data["room_type_presets"] = _split(data.get("room_type_presets"))
        data["rate_plans"] = _split(data.get("rate_plans"))
        data["meal_plans"] = _split(data.get("meal_plans"))
        if data.get("min_capacity") is None and data.get("guests") is not None:
            data["min_capacity"] = int(data["guests"])
        return data


class HotelSearchMatchingRoomSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    room_number = serializers.CharField(allow_null=True, read_only=True)
    floor = serializers.IntegerField(allow_null=True, read_only=True)
    display_name = serializers.CharField(allow_null=True, read_only=True)
    room_type_id = serializers.IntegerField(allow_null=True, required=False, read_only=True)
    bedroom_count = serializers.IntegerField(default=1, read_only=True)
    price_per_night = serializers.DecimalField(max_digits=10, decimal_places=2, allow_null=True, read_only=True)
    currency = serializers.CharField(allow_null=True, read_only=True)
    beds = serializers.JSONField(read_only=True)
    amenities = serializers.ListField(child=serializers.CharField(), read_only=True)
    capacity_adults = serializers.IntegerField(allow_null=True, read_only=True)
    capacity_children = serializers.IntegerField(allow_null=True, read_only=True)
    room_type_name = serializers.CharField(allow_null=True, read_only=True)
    preset = serializers.CharField(allow_null=True, read_only=True)
    sellability = serializers.CharField(allow_null=True, required=False, read_only=True)
    is_available = serializers.BooleanField(required=False, default=True, read_only=True)
    area_sqm = serializers.FloatField(allow_null=True, read_only=True)
    meal_plan = serializers.CharField(allow_null=True, read_only=True)
    img = serializers.JSONField(read_only=True)
    nights = serializers.IntegerField(read_only=True)
    total_price = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True, allow_null=True)


class HotelCardSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    guid = serializers.CharField(allow_null=True, required=False, read_only=True)
    organization_name = serializers.CharField(allow_null=True, required=False, read_only=True)
    title = serializers.CharField(read_only=True)
    city = serializers.CharField(allow_null=True, read_only=True)
    country = serializers.CharField(allow_null=True, required=False, read_only=True)
    # The repository already selects these; without them declared here the
    # street address never reached the client (hotel detail showed no address).
    address = serializers.CharField(allow_null=True, required=False, read_only=True)
    full_address = serializers.CharField(allow_null=True, required=False, read_only=True)
    description = serializers.CharField(allow_null=True, read_only=True)
    description_uz = serializers.CharField(allow_null=True, required=False, read_only=True)
    description_ru = serializers.CharField(allow_null=True, required=False, read_only=True)
    description_en = serializers.CharField(allow_null=True, required=False, read_only=True)
    star_rating = serializers.IntegerField(allow_null=True, read_only=True)
    weel_classification = serializers.CharField(allow_null=True, read_only=True)
    is_recommended = serializers.BooleanField(default=False, read_only=True)
    is_verified = serializers.BooleanField(default=False, read_only=True)
    is_active = serializers.BooleanField(default=True, read_only=True)
    is_testing = serializers.BooleanField(default=False, read_only=True)
    is_archived = serializers.BooleanField(default=False, read_only=True)
    verification_status = serializers.CharField(allow_null=True, required=False, read_only=True)
    themes = serializers.ListField(child=serializers.CharField(), read_only=True)
    amenities = serializers.ListField(child=serializers.CharField(), read_only=True)
    legal_info = serializers.DictField(read_only=True)
    booking_count = serializers.IntegerField(default=0, read_only=True)
    rating = serializers.DecimalField(max_digits=3, decimal_places=2, allow_null=True, read_only=True)
    review_count = serializers.IntegerField(default=0, read_only=True)
    available_rooms = serializers.IntegerField(default=0, read_only=True)
    total_estimated_price = serializers.DecimalField(max_digits=14, decimal_places=2, allow_null=True, required=False, read_only=True)
    matching_rooms = HotelSearchMatchingRoomSerializer(many=True, required=False, read_only=True)
    check_in_time = serializers.TimeField(allow_null=True, read_only=True)
    check_out_time = serializers.TimeField(allow_null=True, read_only=True)
    cancellation_policy = serializers.CharField(allow_null=True, required=False, read_only=True)
    policies = serializers.DictField(read_only=True)
    currency = serializers.CharField(allow_null=True, required=False, read_only=True)
    timezone = serializers.CharField(allow_null=True, required=False, read_only=True)
    latitude = serializers.FloatField(allow_null=True, read_only=True)
    longitude = serializers.FloatField(allow_null=True, read_only=True)
    min_price = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True, read_only=True)
    img = serializers.ListField(child=serializers.CharField(), required=False, read_only=True)
    is_favorite = serializers.BooleanField(default=False, required=False, read_only=True)
    organization = serializers.DictField(required=False, read_only=True)
    partner_user = serializers.DictField(required=False, read_only=True)
    property_detail = serializers.DictField(required=False, read_only=True)
    tenant_schema = serializers.CharField(allow_null=True, required=False, read_only=True)
    created_at = serializers.DateTimeField(allow_null=True, required=False, read_only=True)
    updated_at = serializers.DateTimeField(allow_null=True, required=False, read_only=True)

    def to_representation(self, instance):
        request = self.context.get("request")
        row = dict(instance)
        lang = _preferred_language(request)
        row["title"] = row.get("title") or row.get("name") or ""
        row["description"] = (
            row.get(f"description_{lang}")
            or row.get("description_uz")
            or row.get("description_en")
            or row.get("description_ru")
        )
        try:
            row["latitude"] = float(row.get("latitude") or 0)
        except (TypeError, ValueError):
            row["latitude"] = None
        try:
            row["longitude"] = float(row.get("longitude") or 0)
        except (TypeError, ValueError):
            row["longitude"] = None
        row["amenities"] = row.get("amenities") or []
        row["rating"] = row.get("rating")
        row["review_count"] = int(row.get("review_count") or 0)
        row["booking_count"] = int(row.get("booking_count") or 0)
        row["available_rooms"] = int(row.get("available_rooms") or 0)
        row["check_in_time"] = _iso_time_str(row.get("check_in_time"))
        row["check_out_time"] = _iso_time_str(row.get("check_out_time"))
        row["cancellation_policy"] = row.get("cancellation_policy")
        row["policies"] = {
            "alcohol_allowed": bool(row.get("alcohol_allowed", False)),
            "pets_allowed": bool(row.get("pets_allowed", False)),
            "quiet_hours": bool(row.get("quiet_hours", True)),
        }
        row["img"] = _build_media_url(request, row.get("photos") or [])
        row["is_verified"] = bool(row.get("is_verified", False))
        row["is_recommended"] = bool(row.get("is_recommended", False))
        row["is_favorite"] = str(row.get("guid")) in _favorite_guid_set(self.context)
        row["themes"] = row.get("themes") or []
        raw_legal_info = row.get("legal_info")
        if isinstance(raw_legal_info, str):
            try:
                raw_legal_info = json.loads(raw_legal_info)
            except (TypeError, ValueError):
                raw_legal_info = {}
        row["legal_info"] = (
            raw_legal_info if isinstance(raw_legal_info, dict) else {}
        )
        row["weel_classification"] = row.get("weel_classification")
        row["star_rating"] = row.get("star_rating")
        row["country"] = row.get("country")
        row["currency"] = row.get("currency")
        row["timezone"] = row.get("timezone")
        room_types = row.get("room_types") or []
        if room_types:
            room_types = [
                self._build_room_summary(r, request) for r in room_types
            ]
        row["room_types"] = room_types
        row["reviews"] = row.get("reviews") or []
        return super().to_representation(row)

    @staticmethod
    def _build_room_summary(room: dict, request: Any) -> dict:
        room = dict(room)
        room["img"] = _build_media_url(request, room.get("images") or [])
        return room


class HotelDetailSerializer(HotelCardSerializer):
    check_in_time = serializers.CharField(allow_null=True, required=False)
    check_out_time = serializers.CharField(allow_null=True, required=False)
    country = serializers.CharField(allow_null=True, required=False)
    currency = serializers.CharField(allow_null=True, required=False)
    is_verified = serializers.BooleanField(default=False)
    cancellation_policy = serializers.CharField(allow_null=True, required=False)
    timezone = serializers.CharField(allow_null=True, required=False)
    policies = serializers.DictField(default=dict)
    amenities = serializers.ListField(child=serializers.CharField(), default=list)
    is_favorite = serializers.BooleanField(default=False)
    created_at = serializers.DateTimeField(allow_null=True, required=False)
    room_types = serializers.ListField(child=serializers.DictField(), default=list)
    reviews = serializers.ListField(child=serializers.DictField(), default=list)

    class Meta:
        ref_name = "HotelDetail"

    def to_representation(self, instance):
        request = self.context.get("request")
        row = dict(instance)
        row["title"] = row.get("title") or row.get("name") or ""
        row["description"] = (
            row.get("description")
            or row.get("description_uz")
            or row.get("description_en")
            or row.get("description_ru")
        )
        room_types = row.get("room_types") or []
        if room_types:
            room_types = [
                self._build_room_summary(r, request) for r in room_types
            ]
        row["room_types"] = room_types
        return super().to_representation(row)


class RoomAvailabilitySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    room_number = serializers.CharField(allow_null=True)
    floor = serializers.IntegerField(allow_null=True)
    display_name = serializers.CharField(allow_null=True)
    room_type_id = serializers.IntegerField(allow_null=True, required=False)
    bedroom_count = serializers.IntegerField(default=1)
    price_per_night = serializers.DecimalField(max_digits=10, decimal_places=2, allow_null=True)
    currency = serializers.CharField(allow_null=True)
    beds = serializers.JSONField(default=list)
    amenities = serializers.ListField(child=serializers.CharField(), default=list)
    capacity_adults = serializers.IntegerField(allow_null=True)
    capacity_children = serializers.IntegerField(allow_null=True)
    room_type_name = serializers.CharField(allow_null=True)
    preset = serializers.CharField(allow_null=True)
    sellability = serializers.CharField(allow_null=True, required=False)
    is_available = serializers.BooleanField(required=False, default=True)
    area_sqm = serializers.FloatField(allow_null=True)
    meal_plan = serializers.CharField(allow_null=True)
    img = serializers.JSONField(default=list)

    def to_representation(self, instance):
        # get_available_rooms() aliases the photo column as `images`, so without
        # this the declared `img` field always fell back to its empty default
        # and room photos never reached the client.
        row = dict(instance)
        if not row.get("img"):
            row["img"] = row.get("images") or []
        return super().to_representation(row)


class RoomSelectParamsSerializer(serializers.Serializer):
    check_in = serializers.DateField(required=False)
    check_out = serializers.DateField(required=False)
    guests = serializers.IntegerField(default=1, min_value=1)
    room_types = serializers.CharField(required=False, allow_blank=True)
    room_type_presets = serializers.CharField(required=False, allow_blank=True)
    rate_plans = serializers.CharField(required=False, allow_blank=True)
    meal_plans = serializers.CharField(required=False, allow_blank=True)
    min_capacity = serializers.IntegerField(required=False, min_value=1)
    max_capacity = serializers.IntegerField(required=False, min_value=1)

    def validate(self, data):
        if "check_in" not in data:
            data["check_in"] = date.today()
        if "check_out" not in data:
            data["check_out"] = data["check_in"] + timedelta(days=30)
        if data["check_out"] <= data["check_in"]:
            raise serializers.ValidationError({"check_out": "check_out must be after check_in."})
        def _split(value: str | None) -> list[str]:
            if not value:
                return []
            return [item.strip() for item in value.split(",") if item.strip()]

        data["room_types"] = _split(data.get("room_types"))
        data["room_type_presets"] = _split(data.get("room_type_presets"))
        data["rate_plans"] = _split(data.get("rate_plans"))
        data["meal_plans"] = _split(data.get("meal_plans"))
        if data.get("min_capacity") is None:
            data["min_capacity"] = data["guests"]
        return data


class StayPriceSerializer(serializers.Serializer):
    nights = serializers.IntegerField()
    price_per_night = serializers.DecimalField(max_digits=10, decimal_places=2)
    total_price = serializers.DecimalField(max_digits=12, decimal_places=2)
    hold_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    remaining_on_arrival = serializers.DecimalField(max_digits=12, decimal_places=2)


class ReviewListSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    guest_name = serializers.CharField()
    rating = serializers.DecimalField(max_digits=3, decimal_places=2)
    text = serializers.CharField()
    hotel_response = serializers.CharField(allow_null=True)
    created_at = serializers.DateTimeField()


class HotelCalendarSerializer(serializers.Serializer):
    room_id = serializers.IntegerField(read_only=True)
    room_name = serializers.CharField(read_only=True, allow_null=True)
    date = serializers.DateField(read_only=True)
    status = serializers.CharField(read_only=True)
    room_type_id = serializers.IntegerField(read_only=True, allow_null=True)
    room_type_name = serializers.CharField(read_only=True, allow_null=True)
    room_type_preset = serializers.CharField(read_only=True, allow_null=True)
    capacity = serializers.IntegerField(read_only=True, allow_null=True)
    price_per_night = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True, allow_null=True)
    sellability = serializers.CharField(read_only=True, allow_null=True)
    status_reason = serializers.CharField(read_only=True, allow_null=True)


class HotelSearchPageSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    page = serializers.IntegerField()
    page_size = serializers.IntegerField()
    results = HotelCardSerializer(many=True)


class HotelCitySerializer(serializers.Serializer):
    city = serializers.CharField(read_only=True)
    hotel_count = serializers.IntegerField(read_only=True)


class HotelCityListSerializer(serializers.Serializer):
    results = HotelCitySerializer(many=True)
