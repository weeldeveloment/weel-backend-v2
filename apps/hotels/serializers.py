from __future__ import annotations

from datetime import date, timedelta

from rest_framework import serializers


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
    # Map/location filter: hotels within `radius_km` of (lat, lon).
    lat = serializers.FloatField(required=False, allow_null=True)
    lon = serializers.FloatField(required=False, allow_null=True)
    radius_km = serializers.FloatField(required=False, default=10.0, min_value=0.1)
    # "mashhur" | "weel_recommended" (weel-tavsiya) | "cheap" (eng arzon) | "expensive" (eng qimmat)
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
    amenities = serializers.ListField(child=serializers.CharField(), default=list)
    wifi = serializers.BooleanField(default=False)
    parking = serializers.BooleanField(default=False)
    pool = serializers.BooleanField(default=False)
    restaurant = serializers.BooleanField(default=False)
    gym = serializers.BooleanField(default=False)
    pets_allowed = serializers.BooleanField(default=False)
    alcohol_allowed = serializers.BooleanField(default=False)
    quiet_hours = serializers.BooleanField(default=False)
    images = serializers.ListField(child=serializers.CharField(), default=list)
    reviews = serializers.ListField(child=serializers.DictField(), default=list)


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
