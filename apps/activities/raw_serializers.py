from __future__ import annotations

from rest_framework import serializers

from .models import ActivityCategory, ResourceStatus


class ActivityImageSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    guid = serializers.UUIDField(read_only=True)
    image_url = serializers.URLField()
    sort_order = serializers.IntegerField(required=False, default=0)


class ActivitySerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    guid = serializers.UUIDField(read_only=True)
    partner_user_id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(max_length=255)
    description = serializers.CharField(allow_blank=True, required=False)
    category = serializers.ChoiceField(choices=ActivityCategory.choices)
    address = serializers.CharField(max_length=500, required=False, allow_blank=True)
    latitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True)
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6, required=False, allow_null=True)
    buffer_minutes = serializers.IntegerField(min_value=0, required=False, default=10)
    requires_prepayment = serializers.BooleanField(required=False, default=True)
    is_active = serializers.BooleanField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


class WorkingHoursDaySerializer(serializers.Serializer):
    weekday = serializers.IntegerField(min_value=0, max_value=6)
    is_day_off = serializers.BooleanField(default=False)
    opens_at = serializers.TimeField(required=False, allow_null=True)
    closes_at = serializers.TimeField(required=False, allow_null=True)


class WorkingHoursUpdateSerializer(serializers.Serializer):
    days = WorkingHoursDaySerializer(many=True)


class TariffSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    guid = serializers.UUIDField(read_only=True)
    activity_id = serializers.IntegerField(read_only=True)
    name = serializers.CharField(max_length=255)
    duration_minutes = serializers.IntegerField(min_value=1)
    price = serializers.DecimalField(max_digits=12, decimal_places=2)
    min_age = serializers.IntegerField(required=False, allow_null=True)
    max_age = serializers.IntegerField(required=False, allow_null=True)
    min_weight_kg = serializers.IntegerField(required=False, allow_null=True)
    max_weight_kg = serializers.IntegerField(required=False, allow_null=True)
    is_active = serializers.BooleanField(read_only=True)


class ResourceSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    guid = serializers.UUIDField(read_only=True)
    activity_id = serializers.IntegerField(read_only=True)
    label = serializers.CharField(max_length=255)
    status = serializers.ChoiceField(choices=ResourceStatus.choices, required=False, default="active")


class AvailabilityQuerySerializer(serializers.Serializer):
    tariff_id = serializers.IntegerField()
    date = serializers.DateField()


class TimeSlotSerializer(serializers.Serializer):
    start = serializers.DateTimeField()
    end = serializers.DateTimeField()
    available_resource_count = serializers.IntegerField()


class CreateBookingSerializer(serializers.Serializer):
    tariff_id = serializers.IntegerField()
    starts_at = serializers.DateTimeField()


class ActivityBookingSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    guid = serializers.UUIDField(read_only=True)
    activity_id = serializers.IntegerField(read_only=True)
    tariff_id = serializers.IntegerField(read_only=True)
    resource_id = serializers.IntegerField(read_only=True)
    starts_at = serializers.DateTimeField(read_only=True)
    ends_at = serializers.DateTimeField(read_only=True)
    blocked_until = serializers.DateTimeField(read_only=True)
    price_snapshot = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    status = serializers.CharField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
