from __future__ import annotations

from datetime import timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.core.files.storage import default_storage
from django.utils.translation import gettext_lazy as _

from rest_framework import serializers

from payment.exchange_rate import exchange_rate
from shared.raw.compat import get_table_name
from shared.raw.db import fetch_one

from .guest_rules import booking_max_guests
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


def _build_media_urls(request, media_paths) -> list[str]:
    if not media_paths:
        return []
    if isinstance(media_paths, str):
        media_paths = [media_paths]
    if not isinstance(media_paths, (list, tuple)):
        return []

    urls: list[str] = []
    for media_path in media_paths:
        url = _build_media_url(request, media_path)
        if url:
            urls.append(url)
    return urls


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


def _to_decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _convert_amount(
    *,
    amount: Decimal | None,
    from_currency: str,
    to_currency: str,
    usd_to_uzs_rate: Decimal | None,
) -> Decimal | None:
    if amount is None:
        return None

    source = (from_currency or "UZS").upper()
    target = (to_currency or source).upper()

    if source == target:
        return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if not usd_to_uzs_rate or usd_to_uzs_rate <= 0:
        return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    if source == "UZS" and target == "USD":
        return (amount / usd_to_uzs_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if source == "USD" and target == "UZS":
        return (amount * usd_to_uzs_rate).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class RawBookingPriceSerializer(serializers.Serializer):
    guid = serializers.UUIDField(required=False, allow_null=True)
    subtotal = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, allow_null=True)
    hold_amount = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, allow_null=True)
    charge_amount = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, allow_null=True)
    service_fee = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, allow_null=True)
    service_fee_percentage = serializers.IntegerField(required=False, allow_null=True)
    currency = serializers.CharField(required=False, allow_null=True)

    def to_representation(self, instance):
        if not instance:
            return {
                "guid": None,
                "subtotal": None,
                "hold_amount": None,
                "charge_amount": None,
                "service_fee": None,
                "service_fee_percentage": None,
                "currency": None,
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
        max_guests = booking_max_guests()
        if guests <= 0 or guests > max_guests:
            raise serializers.ValidationError(
                _(
                    "The total number of adults and children must be between 1 and %(max)s. "
                    "If the guest count exceeds what the listing allows, the host may decline the request."
                )
                % {"max": max_guests}
            )

        nights = (check_out - check_in).days
        if property_row.get("minimum_weekend_day_stay"):
            if check_in.weekday() == 4 and nights < 2:
                raise serializers.ValidationError(
                    _(
                        "This property requires minimum 2 nights stay, when booking starts on Friday"
                    )
                )

        if property_row.get("property_kind") == "cottage" and property_row.get("weekend_only_sunday_inclusive"):
            from .raw_repository import fetch_calendar_dates_by_status

            property_id = property_row.get("property_id")
            property_kind = property_row.get("property_kind")

            day = check_in
            while day <= check_out:
                if day.weekday() in (4, 5):  # Friday or Saturday
                    following_sunday = day + timedelta(days=(6 - day.weekday()) % 7)

                    # If the Sunday is already inside the requested range, we're good.
                    if following_sunday < check_out:
                        day += timedelta(days=1)
                        continue

                    # Sunday is outside the range — check if partner blocked it externally.
                    blocked_or_booked = fetch_calendar_dates_by_status(
                        property_kind=property_kind,
                        property_id=property_id,
                        from_date=following_sunday,
                        to_date=following_sunday,
                        statuses=["booked", "blocked"],
                    )
                    if blocked_or_booked:
                        day += timedelta(days=1)
                        continue

                    raise serializers.ValidationError(
                        _(
                            "This property requires the stay to include Sunday for any weekend night booked. "
                            "Please extend your check-out to %(sunday_date)s or later."
                        )
                        % {"sunday_date": following_sunday.isoformat()}
                    )
                day += timedelta(days=1)

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
    check_in = serializers.DateField(read_only=True)
    check_out = serializers.DateField(read_only=True)
    confirmed_at = serializers.DateTimeField(read_only=True, allow_null=True)
    cancelled_at = serializers.DateTimeField(read_only=True, allow_null=True)
    completed_at = serializers.DateTimeField(read_only=True, allow_null=True)

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
    img = serializers.SerializerMethodField("get_img")

    def get_img(self, obj):
        request = self.context.get("request")
        return _build_media_urls(request, obj.get("property_img"))


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
    guests_over_listing_standard = serializers.SerializerMethodField()
    booking_price = serializers.SerializerMethodField("get_booking_price")
    booking_number = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    cancellation_reason = serializers.CharField(read_only=True, allow_blank=True, allow_null=True)
    confirmed_at = serializers.DateTimeField(read_only=True, allow_null=True)
    cancelled_at = serializers.DateTimeField(read_only=True, allow_null=True)
    completed_at = serializers.DateTimeField(read_only=True, allow_null=True)

    def get_guests_over_listing_standard(self, obj):
        booked = int(obj.get("adults") or 0) + int(obj.get("children") or 0)
        listing = int(obj.get("property_guests") or 0)
        if listing <= 0:
            return False
        return booked > listing

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
    currency = serializers.CharField(required=False, allow_null=True)

    def to_representation(self, instance):
        if not instance:
            return {
                "subtotal": None,
                "hold_amount": None,
                "charge_amount": None,
                "service_fee": None,
                "currency": None,
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
        source_currency = str(obj.get("booking_currency") or "UZS").upper()
        display_currency = str(obj.get("property_currency") or source_currency).upper()
        rate_from_context = self.context.get("usd_to_uzs_rate")
        usd_to_uzs_rate = _to_decimal(rate_from_context)
        if usd_to_uzs_rate is None:
            try:
                usd_to_uzs_rate = _to_decimal(exchange_rate())
            except Exception:
                usd_to_uzs_rate = None

        service_fee = _to_decimal(obj.get("booking_service_fee"))
        hold_amount = _to_decimal(obj.get("booking_hold_amount"))
        charge_amount = _to_decimal(obj.get("booking_charge_amount"))
        subtotal = _to_decimal(obj.get("booking_subtotal"))

        service_fee_percentage = _to_decimal(obj.get("booking_service_fee_percentage")) or Decimal("20")
        if service_fee is not None and service_fee_percentage > 0:
            subtotal = (service_fee * Decimal("100") / service_fee_percentage).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            )

        price_payload = {
            "subtotal": _convert_amount(
                amount=subtotal,
                from_currency=source_currency,
                to_currency=display_currency,
                usd_to_uzs_rate=usd_to_uzs_rate,
            ),
            "hold_amount": _convert_amount(
                amount=hold_amount,
                from_currency=source_currency,
                to_currency=display_currency,
                usd_to_uzs_rate=usd_to_uzs_rate,
            ),
            "charge_amount": _convert_amount(
                amount=charge_amount,
                from_currency=source_currency,
                to_currency=display_currency,
                usd_to_uzs_rate=usd_to_uzs_rate,
            ),
            "service_fee": _convert_amount(
                amount=service_fee,
                from_currency=source_currency,
                to_currency=display_currency,
                usd_to_uzs_rate=usd_to_uzs_rate,
            ),
            "currency": display_currency,
        }
        return RawAdminBookingPriceSerializer(price_payload).data

    def to_representation(self, instance):
        payload = dict(instance)
        payload["client"] = instance
        payload["property"] = instance
        return super().to_representation(payload)
