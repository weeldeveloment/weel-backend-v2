from __future__ import annotations

from datetime import timedelta

from django.core.files.storage import default_storage
from django.utils.translation import gettext_lazy as _

from rest_framework import serializers

from shared.raw.compat import get_table_name
from shared.raw.db import fetch_one

from .mixins import DateRangeValidationMixin


def _build_media_url(request, media_path: str | None) -> str | None:
    if not media_path:
        return None
    try:
        url = default_storage.url(media_path)
    except Exception:
        return None
    if not request:
        return url
    return request.build_absolute_uri(url)


def _resolve_property_average_rating(obj) -> float:
    apartment_id = obj.get("property_apartment_id")
    cottage_id = obj.get("property_cottage_id")
    if apartment_id is None and cottage_id is None:
        return 1.0

    if apartment_id is not None:
        row = fetch_one(
            f"""
            SELECT ROUND(COALESCE(AVG(r.rating), 1.0), 2) AS avg_rating
            FROM {get_table_name("review")} r
            WHERE r.apartment_id = %s
              AND r.rating IS NOT NULL
              AND COALESCE(r.is_hidden, FALSE) = FALSE
            """,
            [apartment_id],
        )
    else:
        row = fetch_one(
            f"""
            SELECT ROUND(COALESCE(AVG(r.rating), 1.0), 2) AS avg_rating
            FROM {get_table_name("review")} r
            WHERE r.cottage_id = %s
              AND r.rating IS NOT NULL
              AND COALESCE(r.is_hidden, FALSE) = FALSE
            """,
            [cottage_id],
        )
    value = row.get("avg_rating") if row else None
    if value is None:
        return 1.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 1.0


class RawBookingPriceSerializer(serializers.Serializer):
    guid = serializers.UUIDField(required=False, allow_null=True)
    subtotal = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, allow_null=True)
    hold_amount = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, allow_null=True)
    charge_amount = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, allow_null=True)
    service_fee = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, allow_null=True)
    service_fee_percentage = serializers.IntegerField(required=False, allow_null=True)

    def to_representation(self, instance):
        if not instance:
            return {
                "guid": None,
                "subtotal": None,
                "hold_amount": None,
                "charge_amount": None,
                "service_fee": None,
                "service_fee_percentage": None,
            }
        return super().to_representation(instance)


class RawCalendarDateSerializer(serializers.Serializer):
    date = serializers.DateField()
    status = serializers.CharField()


class RawPropertyCalendarDateRangeSerializer(
    DateRangeValidationMixin,
    serializers.Serializer,
):
    from_date = serializers.DateField()
    to_date = serializers.DateField(required=False)

    def validate(self, attrs):
        return self.validate_date_range(attrs)


class RawClientBookingCreateSerializer(
    DateRangeValidationMixin,
    serializers.Serializer,
):
    property_id = serializers.UUIDField()
    card_id = serializers.CharField()
    check_in = serializers.DateField()
    check_out = serializers.DateField()
    adults = serializers.IntegerField(min_value=1)
    children = serializers.IntegerField(min_value=0, required=False, default=0)
    babies = serializers.IntegerField(min_value=0, max_value=5, required=False, default=0)

    start_field = "check_in"
    end_field = "check_out"
    is_single_day = False

    def validate(self, attrs):
        attrs = self.validate_date_range(attrs)

        check_in = attrs["check_in"]
        check_out = attrs["check_out"]
        property_row = self.context["property_row"]

        guests = attrs["adults"] + attrs.get("children", 0)
        if guests <= 0 or guests > 15:
            raise serializers.ValidationError(
                _("The total number of adults and children shouldn't greater than 15")
            )

        if property_row.get("weekend_only_sunday_inclusive"):
            if check_in.weekday() not in (4, 5):
                raise serializers.ValidationError(
                    _(
                        "This property can only be booked with check-in on Friday or Saturday."
                    )
                )
            day = check_in
            has_sunday = False
            while day < check_out:
                if day.weekday() == 6:
                    has_sunday = True
                    break
                day += timedelta(days=1)
            if not has_sunday:
                raise serializers.ValidationError(
                    _(
                        "This property requires the stay to include Sunday. "
                        "Please choose check-out on Monday or later."
                    )
                )

        return attrs


class RawPropertyBookingSerializer(serializers.Serializer):
    guid = serializers.UUIDField(source="property_guid", read_only=True)
    title = serializers.CharField(source="property_title", read_only=True)
    img = serializers.SerializerMethodField("get_img")

    def get_img(self, obj):
        request = self.context.get("request")
        return _build_media_url(request, obj.get("property_img"))


class RawClientBookingSerializer(serializers.Serializer):
    first_name = serializers.CharField(source="client_first_name", read_only=True, allow_blank=True, allow_null=True)
    last_name = serializers.CharField(source="client_last_name", read_only=True, allow_blank=True, allow_null=True)


class RawPartnerBookingSerializer(serializers.Serializer):
    username = serializers.CharField(source="partner_username", read_only=True, allow_blank=True, allow_null=True)
    first_name = serializers.CharField(source="partner_first_name", read_only=True, allow_blank=True, allow_null=True)
    last_name = serializers.CharField(source="partner_last_name", read_only=True, allow_blank=True, allow_null=True)
    phone_number = serializers.CharField(source="partner_phone_number", read_only=True, allow_blank=True, allow_null=True)


class RawClientBookingListSerializer(serializers.Serializer):
    guid = serializers.UUIDField(read_only=True)
    property = RawPropertyBookingSerializer(read_only=True)
    partner = RawPartnerBookingSerializer(read_only=True)
    status = serializers.CharField(read_only=True)

    def to_representation(self, instance):
        payload = dict(instance)
        payload["property"] = instance
        payload["partner"] = instance
        return super().to_representation(payload)


class RawPropertyLocationBookingSerializer(serializers.Serializer):
    latitude = serializers.CharField(source="property_latitude", read_only=True, allow_null=True)
    longitude = serializers.CharField(source="property_longitude", read_only=True, allow_null=True)
    city = serializers.CharField(source="property_city", read_only=True, allow_blank=True, allow_null=True)
    country = serializers.CharField(source="property_country", read_only=True, allow_blank=True, allow_null=True)


class RawClientBookingDetailSerializer(serializers.Serializer):
    guid = serializers.UUIDField(read_only=True)
    partner = RawPartnerBookingSerializer(read_only=True)
    property = RawPropertyLocationBookingSerializer(read_only=True)
    check_in = serializers.DateField(read_only=True)
    check_out = serializers.DateField(read_only=True)

    def to_representation(self, instance):
        payload = dict(instance)
        payload["partner"] = instance
        payload["property"] = instance
        return super().to_representation(payload)


class RawPropertyBookingHistorySerializer(serializers.Serializer):
    guid = serializers.UUIDField(source="property_guid", read_only=True)
    title = serializers.CharField(source="property_title", read_only=True)
    image_url = serializers.SerializerMethodField("get_image_url")

    def get_image_url(self, obj):
        request = self.context.get("request")
        return _build_media_url(request, obj.get("property_img"))


class RawClientBookingHistoryListSerializer(serializers.Serializer):
    guid = serializers.UUIDField(read_only=True)
    property_type = serializers.CharField(source="property_type_title", read_only=True)
    property = RawPropertyBookingHistorySerializer(read_only=True)
    status = serializers.CharField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)

    def to_representation(self, instance):
        payload = dict(instance)
        payload["property"] = instance
        return super().to_representation(payload)


class RawPropertyBookingHistoryDetailSerializer(RawPropertyBookingHistorySerializer):
    property_location = RawPropertyLocationBookingSerializer(read_only=True)
    average_rating = serializers.SerializerMethodField("get_average_rating")

    def get_average_rating(self, obj):
        return _resolve_property_average_rating(obj)

    def to_representation(self, instance):
        payload = dict(instance)
        payload["property_location"] = instance
        return super().to_representation(payload)


class RawClientBookingHistoryDetailSerializer(serializers.Serializer):
    guid = serializers.UUIDField(read_only=True)
    check_in = serializers.DateField(read_only=True)
    check_out = serializers.DateField(read_only=True)
    property = RawPropertyBookingHistoryDetailSerializer(read_only=True)
    booking_price = serializers.SerializerMethodField("get_booking_price")
    booking_number = serializers.CharField(read_only=True)

    def get_booking_price(self, obj):
        price_payload = {
            "guid": obj.get("booking_price_guid"),
            "subtotal": obj.get("booking_subtotal"),
            "hold_amount": obj.get("booking_hold_amount"),
            "charge_amount": obj.get("booking_charge_amount"),
            "service_fee": obj.get("booking_service_fee"),
            "service_fee_percentage": obj.get("booking_service_fee_percentage"),
        }
        return RawBookingPriceSerializer(price_payload).data

    def to_representation(self, instance):
        payload = dict(instance)
        payload["property"] = instance
        return super().to_representation(payload)


class RawPartnerBookingListSerializer(serializers.Serializer):
    guid = serializers.UUIDField(read_only=True)
    property = RawPropertyBookingSerializer(read_only=True)
    client = RawClientBookingSerializer(read_only=True)
    check_in = serializers.DateField(read_only=True)
    check_out = serializers.DateField(read_only=True)
    adults = serializers.IntegerField(read_only=True)
    children = serializers.IntegerField(read_only=True)
    babies = serializers.IntegerField(read_only=True)
    booking_price = serializers.SerializerMethodField("get_booking_price")
    booking_number = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    cancellation_reason = serializers.CharField(read_only=True, allow_blank=True, allow_null=True)
    confirmed_at = serializers.DateTimeField(read_only=True, allow_null=True)
    cancelled_at = serializers.DateTimeField(read_only=True, allow_null=True)
    completed_at = serializers.DateTimeField(read_only=True, allow_null=True)

    def get_booking_price(self, obj):
        price_payload = {
            "guid": obj.get("booking_price_guid"),
            "subtotal": obj.get("booking_subtotal"),
            "hold_amount": obj.get("booking_hold_amount"),
            "charge_amount": obj.get("booking_charge_amount"),
            "service_fee": obj.get("booking_service_fee"),
            "service_fee_percentage": obj.get("booking_service_fee_percentage"),
        }
        return RawBookingPriceSerializer(price_payload).data

    def to_representation(self, instance):
        payload = dict(instance)
        payload["property"] = instance
        payload["client"] = instance
        return super().to_representation(payload)


class RawAdminBookingClientSerializer(serializers.Serializer):
    id = serializers.IntegerField(source="client_user_id", read_only=True)
    first_name = serializers.CharField(source="client_first_name", read_only=True, allow_blank=True, allow_null=True)
    last_name = serializers.CharField(source="client_last_name", read_only=True, allow_blank=True, allow_null=True)
    phone_number = serializers.CharField(source="client_phone_number", read_only=True, allow_blank=True, allow_null=True)


class RawAdminBookingPropertySerializer(serializers.Serializer):
    guid = serializers.UUIDField(source="property_guid", read_only=True)
    title = serializers.CharField(source="property_title", read_only=True)
    property_type = serializers.CharField(source="property_type_title", read_only=True)


class RawAdminBookingPriceSerializer(serializers.Serializer):
    subtotal = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, allow_null=True)
    hold_amount = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, allow_null=True)
    charge_amount = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, allow_null=True)
    service_fee = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, allow_null=True)

    def to_representation(self, instance):
        if not instance:
            return {
                "subtotal": None,
                "hold_amount": None,
                "charge_amount": None,
                "service_fee": None,
            }
        return super().to_representation(instance)


class RawAdminBookingListSerializer(serializers.Serializer):
    guid = serializers.UUIDField(read_only=True)
    booking_number = serializers.CharField(read_only=True)
    check_in = serializers.DateField(read_only=True)
    check_out = serializers.DateField(read_only=True)
    adults = serializers.IntegerField(read_only=True)
    children = serializers.IntegerField(read_only=True)
    babies = serializers.IntegerField(read_only=True)
    status = serializers.CharField(read_only=True)
    cancellation_reason = serializers.CharField(read_only=True, allow_blank=True, allow_null=True)
    confirmed_at = serializers.DateTimeField(read_only=True, allow_null=True)
    cancelled_at = serializers.DateTimeField(read_only=True, allow_null=True)
    completed_at = serializers.DateTimeField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)
    client = RawAdminBookingClientSerializer(read_only=True)
    property = RawAdminBookingPropertySerializer(read_only=True)
    booking_price = serializers.SerializerMethodField("get_booking_price")

    def get_booking_price(self, obj):
        price_payload = {
            "subtotal": obj.get("booking_subtotal"),
            "hold_amount": obj.get("booking_hold_amount"),
            "charge_amount": obj.get("booking_charge_amount"),
            "service_fee": obj.get("booking_service_fee"),
        }
        return RawAdminBookingPriceSerializer(price_payload).data

    def to_representation(self, instance):
        payload = dict(instance)
        payload["client"] = instance
        payload["property"] = instance
        return super().to_representation(payload)
