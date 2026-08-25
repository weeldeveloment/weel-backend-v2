from __future__ import annotations

from rest_framework import serializers

from apps.hotels.models import CancellationType, MealPlan, PersonTitle


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

class OccupancySerializer(serializers.Serializer):
    """One room's worth of guests. Children are given by age, not by count."""

    adults = serializers.IntegerField(min_value=1, max_value=10)
    children_ages = serializers.ListField(
        child=serializers.IntegerField(min_value=0, max_value=17),
        required=False,
        allow_empty=True,
        max_length=10,
    )


class HotelSearchSerializer(serializers.Serializer):
    city_id = serializers.IntegerField(required=False, allow_null=True)
    hotel_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, allow_empty=True, max_length=200
    )
    check_in = serializers.DateField()
    check_out = serializers.DateField()
    occupancies = OccupancySerializer(many=True, min_length=1, max_length=10)
    currency = serializers.CharField(max_length=8, default="uzs")

    nationality = serializers.CharField(min_length=2, max_length=2, required=False)
    residence = serializers.CharField(min_length=2, max_length=2, required=False)

    price_min = serializers.DecimalField(max_digits=14, decimal_places=2, required=False)
    price_max = serializers.DecimalField(max_digits=14, decimal_places=2, required=False)
    stars = serializers.ListField(child=serializers.IntegerField(), required=False)
    facilities = serializers.ListField(child=serializers.IntegerField(), required=False)
    equipments = serializers.ListField(child=serializers.IntegerField(), required=False)
    cancellation_type = serializers.ChoiceField(
        choices=[CancellationType.REFUNDABLE, CancellationType.NON_REFUNDABLE, CancellationType.ALL],
        required=False,
    )
    meal_plans = serializers.ListField(
        child=serializers.ChoiceField(choices=sorted(MealPlan.CHOICES)), required=False
    )
    hotel_types = serializers.ListField(child=serializers.IntegerField(), required=False)

    def validate(self, attrs):
        if not attrs.get("city_id") and not attrs.get("hotel_ids"):
            raise serializers.ValidationError(
                "Provide either city_id or hotel_ids."
            )
        if attrs["check_out"] <= attrs["check_in"]:
            raise serializers.ValidationError(
                "check_out must be later than check_in."
            )
        return attrs

    def provider_filters(self) -> dict:
        data = self.validated_data
        keys = (
            "price_min", "price_max", "stars", "facilities", "equipments",
            "cancellation_type", "meal_plans", "hotel_types",
        )
        return {key: data.get(key) for key in keys if data.get(key) is not None}

    def provider_dates(self) -> tuple[str, str]:
        """Hotelios wants `YYYY/MM/DD HH:MM`; the hotel's own policy fills the time."""
        data = self.validated_data
        return (
            data["check_in"].strftime("%Y/%m/%d 14:00"),
            data["check_out"].strftime("%Y/%m/%d 12:00"),
        )


class QuoteSerializer(serializers.Serializer):
    option_ref_ids = serializers.ListField(
        child=serializers.CharField(max_length=512), min_length=1, max_length=20
    )


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------

class GuestSerializer(serializers.Serializer):
    person_title = serializers.ChoiceField(choices=sorted(PersonTitle.ALL))
    first_name = serializers.CharField(max_length=120)
    last_name = serializers.CharField(max_length=120)
    nationality = serializers.CharField(min_length=2, max_length=2)
    age = serializers.IntegerField(min_value=0, max_value=17, required=False, allow_null=True)

    def validate(self, attrs):
        if attrs["person_title"] == PersonTitle.CHILD and attrs.get("age") is None:
            raise serializers.ValidationError(
                "A CHILD guest must have an age between 0 and 17."
            )
        return attrs


class BookingRoomSerializer(serializers.Serializer):
    option_ref_id = serializers.CharField(max_length=512)
    price = serializers.DecimalField(max_digits=14, decimal_places=2)
    currency = serializers.CharField(max_length=8, required=False)
    guests = GuestSerializer(many=True, min_length=1, max_length=10)
    # Which employee this room is for, on a corporate booking.
    employee_id = serializers.IntegerField(required=False, allow_null=True)

    def validate_guests(self, guests):
        if not any(g["person_title"] != PersonTitle.CHILD for g in guests):
            raise serializers.ValidationError(
                "Each room needs at least one adult guest."
            )
        return guests


class DeltaPriceSerializer(serializers.Serializer):
    """How much of a price rise between quote and booking is acceptable."""

    amount = serializers.DecimalField(max_digits=14, decimal_places=2, required=False)
    percent = serializers.DecimalField(max_digits=6, decimal_places=2, required=False)
    matches = serializers.ChoiceField(choices=["ALL", "ANY"], default="ALL")


class CreateHotelBookingSerializer(serializers.Serializer):
    quote_id = serializers.CharField(max_length=64)
    hotel_id = serializers.IntegerField()
    check_in = serializers.DateField()
    check_out = serializers.DateField()
    booking_rooms = BookingRoomSerializer(many=True, min_length=1, max_length=10)
    comment = serializers.CharField(max_length=1000, required=False, allow_blank=True)
    delta_price = DeltaPriceSerializer(required=False)
    nationality = serializers.CharField(min_length=2, max_length=2, required=False)
    residence = serializers.CharField(min_length=2, max_length=2, required=False)
    is_resident = serializers.BooleanField(default=False)
    trip_id = serializers.IntegerField(required=False, allow_null=True)

    def validate(self, attrs):
        if attrs["check_out"] <= attrs["check_in"]:
            raise serializers.ValidationError("check_out must be later than check_in.")
        return attrs

    def provider_rooms(self) -> list[dict]:
        """Room lines shaped for both the provider call and our own row."""
        rooms = []
        for room in self.validated_data["booking_rooms"]:
            rooms.append({
                "option_ref_id": room["option_ref_id"],
                "price": float(room["price"]),
                "currency": room.get("currency"),
                "guests": [
                    {k: v for k, v in guest.items() if v is not None}
                    for guest in room["guests"]
                ],
                "b2b_employee_id": room.get("employee_id"),
            })
        return rooms


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

class HotelSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    hotel_type_id = serializers.IntegerField(read_only=True, allow_null=True)
    city_id = serializers.IntegerField(read_only=True, allow_null=True)
    star_id = serializers.IntegerField(read_only=True, allow_null=True)
    currency = serializers.CharField(read_only=True, allow_null=True)
    latitude = serializers.DecimalField(max_digits=10, decimal_places=7, read_only=True, allow_null=True)
    longitude = serializers.DecimalField(max_digits=10, decimal_places=7, read_only=True, allow_null=True)
    postal_code = serializers.CharField(read_only=True, allow_null=True)
    names = serializers.JSONField(read_only=True)
    address = serializers.JSONField(read_only=True)
    description = serializers.JSONField(read_only=True)
    check_in = serializers.JSONField(read_only=True)
    check_out = serializers.JSONField(read_only=True)
    guest_age_rules = serializers.JSONField(read_only=True)
    facilities = serializers.JSONField(read_only=True)
    photos = serializers.JSONField(read_only=True)
    nearby_places = serializers.JSONField(read_only=True)
    services_in_room = serializers.JSONField(read_only=True)
    synced_at = serializers.DateTimeField(read_only=True)


class RoomTypeSerializer(serializers.Serializer):
    # `room_type_id` is only unique within its hotel, despite what the Hotelios
    # docs claim — the pair identifies a room type, and both are exposed.
    room_type_id = serializers.IntegerField(read_only=True)
    hotel_id = serializers.IntegerField(read_only=True)
    holding_capacity = serializers.IntegerField(read_only=True, allow_null=True)
    bed_type = serializers.IntegerField(read_only=True, allow_null=True)
    extra_bed = serializers.BooleanField(read_only=True)
    area = serializers.DecimalField(max_digits=8, decimal_places=2, read_only=True, allow_null=True)
    names = serializers.JSONField(read_only=True)
    description = serializers.JSONField(read_only=True)
    photos = serializers.JSONField(read_only=True)
    equipments = serializers.JSONField(read_only=True)


class CitySerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    region_id = serializers.IntegerField(read_only=True, allow_null=True)
    names = serializers.JSONField(read_only=True)
    hotel_count = serializers.IntegerField(read_only=True, required=False)


class HotelBookingRoomSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    option_ref_id = serializers.CharField(read_only=True, allow_null=True)
    room_type_id = serializers.IntegerField(read_only=True, allow_null=True)
    room_type_name = serializers.CharField(read_only=True, allow_null=True)
    rate_plan_id = serializers.IntegerField(read_only=True, allow_null=True)
    meal_plan = serializers.CharField(read_only=True, allow_null=True)
    included_meal_options = serializers.JSONField(read_only=True)
    extra_bed_added = serializers.BooleanField(read_only=True)
    cancellation_policy = serializers.JSONField(read_only=True, allow_null=True)
    price = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True, allow_null=True)
    price_breakdown = serializers.JSONField(read_only=True, allow_null=True)
    guests = serializers.JSONField(read_only=True)
    b2b_employee_id = serializers.IntegerField(read_only=True, allow_null=True)


class HotelBookingSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    guid = serializers.UUIDField(read_only=True)
    external_id = serializers.CharField(read_only=True)
    provider_booking_id = serializers.CharField(read_only=True, allow_null=True)
    hotel_id = serializers.IntegerField(read_only=True, allow_null=True)
    status = serializers.CharField(read_only=True)
    check_in = serializers.DateTimeField(read_only=True, allow_null=True)
    check_out = serializers.DateTimeField(read_only=True, allow_null=True)
    is_resident = serializers.BooleanField(read_only=True)
    price = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True, allow_null=True)
    currency = serializers.CharField(read_only=True, allow_null=True)
    comment = serializers.CharField(read_only=True, allow_null=True)
    hotel_confirmation_number = serializers.CharField(read_only=True, allow_null=True)
    additional_information = serializers.JSONField(read_only=True, allow_null=True)
    b2b_trip_id = serializers.IntegerField(read_only=True, allow_null=True)
    provider_created_at = serializers.DateTimeField(read_only=True, allow_null=True)
    rooms = HotelBookingRoomSerializer(many=True, read_only=True, required=False)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)
