from __future__ import annotations

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
    star_rating = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=5)
    weel_classification = serializers.ChoiceField(choices=WEEL_CLASS_CHOICES, required=False, allow_null=True)
    is_recommended = serializers.BooleanField(required=False, allow_null=True, default=None)
    themes = serializers.ListField(child=serializers.CharField(), required=False)
    price_min = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    price_max = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
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
        return data


class HotelCardSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    guid = serializers.CharField(allow_null=True, required=False)
    organization_name = serializers.CharField(allow_null=True, required=False)
    name = serializers.CharField()
    city = serializers.CharField(allow_null=True)
    full_address = serializers.CharField(allow_null=True)
    star_rating = serializers.IntegerField(allow_null=True)
    weel_classification = serializers.CharField(allow_null=True)
    is_recommended = serializers.BooleanField(default=False)
    booking_count = serializers.IntegerField(default=0)
    themes = serializers.ListField(child=serializers.CharField(), default=list)
    description = serializers.CharField(allow_null=True)
    rating = serializers.DecimalField(max_digits=3, decimal_places=2, allow_null=True)
    review_count = serializers.IntegerField(default=0)
    check_in_time = serializers.TimeField(allow_null=True)
    check_out_time = serializers.TimeField(allow_null=True)
    latitude = serializers.FloatField(allow_null=True)
    longitude = serializers.FloatField(allow_null=True)
    min_price = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
    available_rooms = serializers.IntegerField(default=0)


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
    amenities_preview = serializers.ListField(child=serializers.CharField(), default=list)
    images = serializers.ListField(child=serializers.CharField(), default=list)
    is_favorite = serializers.BooleanField(default=False)
    created_at = serializers.DateTimeField(allow_null=True, required=False)
    room_types = serializers.ListField(child=serializers.DictField(), default=list)
    reviews = serializers.ListField(child=serializers.DictField(), default=list)

    class Meta:
        ref_name = "HotelDetail"

    def to_representation(self, instance):
        request = self.context.get("request")
        row = dict(instance)
        lang = _preferred_language(request)
        row["description"] = (
            row.get(f"description_{lang}")
            or row.get("description_uz")
            or row.get("description_en")
            or row.get("description_ru")
        )
        row["full_address"] = row.get("full_address") or row.get("address")
        try:
            row["latitude"] = float(row.get("latitude") or 0)
        except (TypeError, ValueError):
            row["latitude"] = None
        try:
            row["longitude"] = float(row.get("longitude") or 0)
        except (TypeError, ValueError):
            row["longitude"] = None
        row["amenities"] = row.get("amenities") or []
        row["amenities_preview"] = (row.get("amenities") or [])[:5]
        row["min_price"] = _convert_price_for_output(row.get("min_price"), row.get("currency"))
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
        row["images"] = _build_media_url(request, row.get("photos") or [])
        row["is_verified"] = bool(row.get("is_verified", False))
        row["is_recommended"] = bool(row.get("is_recommended", False))
        row["is_favorite"] = str(row.get("guid")) in _favorite_guid_set(self.context)
        row["themes"] = row.get("themes") or []
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
        room["photos"] = _build_media_url(request, room.get("photos") or [])
        room["amenities_snippet"] = (room.get("amenities") or [])[:4]
        room["price_from"] = _convert_price_for_output(room.get("price_from"), room.get("currency"))
        return room


class RoomAvailabilitySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    room_number = serializers.CharField(allow_null=True)
    floor = serializers.IntegerField(allow_null=True)
    display_name = serializers.CharField(allow_null=True)
    bedroom_count = serializers.IntegerField(default=1)
    price_per_night = serializers.DecimalField(max_digits=10, decimal_places=2, allow_null=True)
    currency = serializers.CharField(allow_null=True)
    beds = serializers.JSONField(default=list)
    amenities = serializers.ListField(child=serializers.CharField(), default=list)
    capacity_adults = serializers.IntegerField(allow_null=True)
    capacity_children = serializers.IntegerField(allow_null=True)
    room_type_name = serializers.CharField(allow_null=True)
    preset = serializers.CharField(allow_null=True)
    area_sqm = serializers.FloatField(allow_null=True)
    meal_plan = serializers.CharField(allow_null=True)
    images = serializers.JSONField(default=list)


class RoomSelectParamsSerializer(serializers.Serializer):
    check_in = serializers.DateField(required=False)
    check_out = serializers.DateField(required=False)
    guests = serializers.IntegerField(default=1, min_value=1)

    def validate(self, data):
        if "check_in" not in data:
            data["check_in"] = date.today()
        if "check_out" not in data:
            data["check_out"] = data["check_in"] + timedelta(days=30)
        if data["check_out"] <= data["check_in"]:
            raise serializers.ValidationError({"check_out": "check_out must be after check_in."})
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
