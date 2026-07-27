from __future__ import annotations

import json
from decimal import Decimal, ROUND_HALF_UP

from rest_framework import serializers


class LenientDecimalField(serializers.DecimalField):
    def to_internal_value(self, data):
        try:
            value = Decimal(str(data).strip())
            quantized = value.quantize(
                Decimal('0.' + '0' * self.decimal_places),
                rounding=ROUND_HALF_UP,
            )
            data = str(quantized)
        except Exception:
            self.fail('invalid')
        return super().to_internal_value(data)


class JSONStringField(serializers.Field):
    """Handles JSON strings stored in DB columns, converting them to Python objects for output."""
    def to_representation(self, value):
        if value is None:
            return None
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return value
        return value

    def to_internal_value(self, data):
        return data


class PropertyImageSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    property_id = serializers.IntegerField(read_only=True)
    image_url = serializers.CharField()
    order = serializers.IntegerField(default=0)
    created_at = serializers.DateTimeField(read_only=True)


class RoomTypeSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    property_id = serializers.IntegerField(read_only=True)
    preset = serializers.ChoiceField(
        choices=["standard", "superior", "deluxe", "suite", "studio", "apartment", "family", "dormitory", "custom"],
        required=False,
        allow_null=True,
    )
    custom_name = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=100)
    name = serializers.CharField(max_length=100)
    description = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    base_rate = LenientDecimalField(max_digits=12, decimal_places=2, required=False, allow_null=True)
    capacity = serializers.IntegerField(required=False, default=2)
    amenities = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    photos = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    is_active = serializers.BooleanField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)


class PropertySerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    guid = serializers.CharField(read_only=True, required=False)
    organization_id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(max_length=200)
    description_uz = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    description_ru = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    description_en = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    address = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    full_address = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    city = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    country = serializers.CharField(required=False, default="UZ")
    latitude = LenientDecimalField(max_digits=17, decimal_places=14, required=False, allow_null=True)
    longitude = LenientDecimalField(max_digits=17, decimal_places=14, required=False, allow_null=True)
    star_rating = serializers.IntegerField(required=False, allow_null=True, min_value=0, max_value=5)
    weel_classification = serializers.ChoiceField(
        choices=["standard", "essential", "comfort", "comfort_plus", "business", "premium", "signature"],
        required=False,
        allow_null=True,
    )
    themes = serializers.ListField(
        child=serializers.ChoiceField(choices=["beach", "ski", "city", "countryside", "lakefront", "mountain", "boutique", "business", "historic", "nature", "spa", "family"]),
        required=False,
        default=list,
    )
    amenities = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    legal_info = JSONStringField(required=False, default=dict)
    check_in_time = serializers.TimeField(required=False, allow_null=True)
    check_out_time = serializers.TimeField(required=False, allow_null=True)
    cancellation_policy = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    quiet_hours = serializers.BooleanField(required=False, default=True)
    alcohol_allowed = serializers.BooleanField(required=False, default=True)
    pets_allowed = serializers.BooleanField(required=False, default=False)
    timezone = serializers.CharField(required=False, default="Asia/Tashkent")
    photos = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    is_active = serializers.BooleanField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)


class RoomSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    property_id = serializers.IntegerField(read_only=True)
    room_type_id = serializers.IntegerField(required=False, allow_null=True)
    room_type = serializers.CharField(read_only=True, allow_null=True)
    room_type_name = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    room_type_preset = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    room_number = serializers.CharField(max_length=20)
    display_name = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    floor = serializers.IntegerField(required=False, default=1)
    area = serializers.DecimalField(max_digits=8, decimal_places=2, required=False, allow_null=True)
    bedroom_count = serializers.IntegerField(required=False, default=1)
    beds = JSONStringField(required=False, default=list)
    amenities = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    photos = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    condition = serializers.ChoiceField(
        choices=["clean", "dirty", "inspection", "maintenance"],
        required=False,
        default="clean",
    )
    availability = serializers.ChoiceField(
        choices=["available", "occupied", "blocked"],
        required=False,
        default="available",
    )
    capacity = serializers.IntegerField(required=False, default=2)
    meal_plan = serializers.ChoiceField(
        choices=["RO", "BB", "HB", "FB", "AI", "UAI"],
        required=False,
        default="BB",
    )
    base_price = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    currency = serializers.CharField(required=False, allow_blank=True, allow_null=True, default="UZS")
    cover_photo_index = serializers.IntegerField(required=False, default=0)
    is_active = serializers.BooleanField(required=False)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    def to_representation(self, instance):
        row = dict(instance)
        if not row.get("room_type"):
            row["room_type"] = row.get("room_type_name")
        return super().to_representation(row)


class RoomMassUpdateItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    room_number = serializers.CharField(max_length=20, required=False)
    display_name = serializers.CharField(max_length=200, required=False, allow_blank=True, allow_null=True)
    floor = serializers.IntegerField(required=False)
    area = serializers.DecimalField(max_digits=8, decimal_places=2, required=False, allow_null=True)
    bedroom_count = serializers.IntegerField(required=False)
    beds = JSONStringField(required=False)
    amenities = serializers.ListField(child=serializers.CharField(), required=False)
    condition = serializers.ChoiceField(choices=["clean", "dirty", "inspection", "maintenance"], required=False)
    availability = serializers.ChoiceField(choices=["available", "occupied", "blocked"], required=False)
    capacity = serializers.IntegerField(required=False)
    meal_plan = serializers.ChoiceField(choices=["RO", "BB", "HB", "FB", "AI", "UAI"], required=False)
    base_price = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    currency = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    cover_photo_index = serializers.IntegerField(required=False)
    is_active = serializers.BooleanField(required=False)


class CalendarSlotSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    room_id = serializers.IntegerField(read_only=True)
    room_number = serializers.CharField(read_only=True, allow_null=True)
    room_type_name = serializers.CharField(read_only=True, allow_null=True)
    date = serializers.DateField(read_only=True)
    status = serializers.CharField(read_only=True)
    hold_expires_at = serializers.DateTimeField(read_only=True, allow_null=True)


class DateRangeSerializer(serializers.Serializer):
    from_date = serializers.DateField()
    to_date = serializers.DateField()


class RoomIdsSerializer(serializers.Serializer):
    room_ids = serializers.ListField(child=serializers.IntegerField(), min_length=1)
    from_date = serializers.DateField()
    to_date = serializers.DateField()
    hold_duration_minutes = serializers.IntegerField(required=False, default=30)


class GuestSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    first_name = serializers.CharField(max_length=100)
    last_name = serializers.CharField(max_length=100, required=False, allow_blank=True, allow_null=True)
    email = serializers.EmailField(required=False, allow_blank=True, allow_null=True)
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True, allow_null=True)
    id_document = JSONStringField(required=False, default=dict)
    preferences = JSONStringField(required=False, default=dict)
    is_vip = serializers.BooleanField(required=False, default=False)
    is_blacklisted = serializers.BooleanField(required=False, default=False)
    notes = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)


class BookingSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    property_id = serializers.IntegerField(read_only=True)
    room_id = serializers.IntegerField()
    guest_id = serializers.IntegerField(required=False, allow_null=True)
    booking_number = serializers.CharField(read_only=True)
    check_in = serializers.DateField()
    check_out = serializers.DateField()
    status = serializers.CharField(read_only=True)
    source = serializers.ChoiceField(
        choices=["direct", "ota", "b2b", "walk_in"],
        required=False,
        default="direct",
    )
    meal_plan = serializers.ChoiceField(
        choices=["RO", "BB", "HB", "FB", "AI", "UAI"],
        required=False,
        default="RO",
    )
    adult_count = serializers.IntegerField(required=False, default=1, min_value=1)
    child_count = serializers.IntegerField(required=False, default=0, min_value=0)
    rate = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    currency = serializers.CharField(required=False, default="USD", max_length=3)
    payment_status = serializers.ChoiceField(
        choices=["pending", "paid", "partial", "refunded"],
        required=False,
        default="pending",
    )
    total_cost = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)
    hold_amount = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True, allow_null=True)
    confirmed_at = serializers.DateTimeField(read_only=True, allow_null=True)
    confirmation_deadline = serializers.DateTimeField(read_only=True, allow_null=True)
    b2b_company_id = serializers.IntegerField(read_only=True, allow_null=True)
    voucher_number = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    notes = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    room_number = serializers.CharField(read_only=True, allow_null=True)
    guest_first_name = serializers.CharField(read_only=True, allow_null=True)
    guest_last_name = serializers.CharField(read_only=True, allow_null=True)
    external_provider = serializers.CharField(read_only=True, allow_null=True)
    external_reservation_id = serializers.CharField(read_only=True, allow_null=True)
    external_room_id = serializers.CharField(read_only=True, allow_null=True)
    external_payload_ref = JSONStringField(read_only=True)
    imported_at = serializers.DateTimeField(read_only=True, allow_null=True)
    last_synced_at = serializers.DateTimeField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    def validate(self, data):
        check_in = data.get("check_in")
        check_out = data.get("check_out")
        if check_in and check_out and check_out <= check_in:
            raise serializers.ValidationError({"check_out": "Check-out must be after check-in."})
        return data


class BookingHistorySerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    booking_id = serializers.IntegerField(read_only=True)
    action = serializers.CharField(read_only=True)
    previous_value = JSONStringField(read_only=True)
    new_value = JSONStringField(read_only=True)
    user_id = serializers.IntegerField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)


class ReviewSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    property_id = serializers.IntegerField(read_only=True)
    booking_id = serializers.IntegerField(required=False, allow_null=True)
    guest_name = serializers.CharField(max_length=200)
    rating = serializers.DecimalField(max_digits=2, decimal_places=1, min_value=1, max_value=5)
    categories = JSONStringField(required=False, default=dict)
    text = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    hotel_response = serializers.CharField(read_only=True, allow_null=True)
    response_date = serializers.DateTimeField(read_only=True, allow_null=True)
    is_complained = serializers.BooleanField(read_only=True)
    complaint_reason = serializers.CharField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)


class ReviewRespondSerializer(serializers.Serializer):
    response = serializers.CharField(max_length=2000)


class ReviewComplainSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500)


class MoveBookingSerializer(serializers.Serializer):
    new_room_id = serializers.IntegerField()
    new_check_in = serializers.DateField(required=False, allow_null=True)
    new_check_out = serializers.DateField(required=False, allow_null=True)


class ResizeBookingSerializer(serializers.Serializer):
    check_in = serializers.DateField()
    check_out = serializers.DateField()

    def validate(self, data):
        if data["check_out"] <= data["check_in"]:
            raise serializers.ValidationError({"check_out": "Check-out must be after check-in."})
        return data


class MealPlanChangeSerializer(serializers.Serializer):
    meal_plan = serializers.ChoiceField(choices=["RO", "BB", "HB", "FB", "AI", "UAI"])


class AnalyticsQuerySerializer(serializers.Serializer):
    date_from = serializers.DateField()
    date_to = serializers.DateField()
    metric = serializers.ChoiceField(
        choices=["check_ins", "revenue", "bookings", "occupancy"],
        required=False,
        default="revenue",
    )
    category = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    floor = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    search = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    def validate(self, data):
        if data["date_to"] < data["date_from"]:
            raise serializers.ValidationError({"date_to": "date_to must be >= date_from."})
        return data


class ChangeSerializer(serializers.Serializer):
    value = serializers.FloatField()
    change = serializers.FloatField()
    change_percent = serializers.FloatField()


class ChangeWithCurrencySerializer(serializers.Serializer):
    value = serializers.FloatField()
    change = serializers.FloatField()
    change_percent = serializers.FloatField()
    currency = serializers.CharField()


class KPIResponseSerializer(serializers.Serializer):
    check_ins = ChangeSerializer()
    revenue = ChangeWithCurrencySerializer()
    bookings = ChangeSerializer()
    occupancy = ChangeSerializer()
    current_guests = serializers.SerializerMethodField()

    def get_current_guests(self, obj):
        return {"value": obj.get("current_guests", {}).get("value", 0)}


class ChartPointSerializer(serializers.Serializer):
    date = serializers.CharField()
    value = serializers.FloatField()
    previous_value = serializers.FloatField(allow_null=True)


class ChartResponseSerializer(serializers.Serializer):
    points = ChartPointSerializer(many=True)
    metric = serializers.CharField()


class RoomAnalyticsSerializer(serializers.Serializer):
    room_id = serializers.IntegerField()
    room_number = serializers.CharField()
    category = serializers.CharField()
    occupancy = ChangeSerializer()
    revenue = ChangeWithCurrencySerializer()
    adr = ChangeWithCurrencySerializer()
    revpar = ChangeWithCurrencySerializer()


class PeriodResponseSerializer(serializers.Serializer):
    type = serializers.CharField()
    date_from = serializers.CharField()
    date_to = serializers.CharField()


class AnalyticsResponseSerializer(serializers.Serializer):
    kpi = KPIResponseSerializer()
    chart = ChartResponseSerializer()
    rooms = RoomAnalyticsSerializer(many=True)
    period = PeriodResponseSerializer()
