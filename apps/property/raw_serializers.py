from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from django.core.files.storage import default_storage
from django.utils.translation import gettext_lazy as _

from rest_framework import serializers

from payment.exchange_rate import to_uzs

from .raw_repository import (
    parse_property_kind,
)


def _build_media_url(request, media_path: Any) -> str | None:
    if not media_path:
        return None
    if isinstance(media_path, list) and len(media_path) > 0:
        media_path = str(media_path[0])
    elif isinstance(media_path, list):
        return None
    media_path = str(media_path)
    if media_path.startswith("http://") or media_path.startswith("https://"):
        return media_path
    try:
        url = default_storage.url(media_path)
    except Exception:
        url = media_path
    if not request:
        return url
    return request.build_absolute_uri(url)


def _synthetic_user_guid(user_id: int | None) -> UUID | None:
    if user_id is None:
        return None
    try:
        return UUID(int=int(user_id))
    except Exception:
        return None


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _convert_price_for_output(value: Any, currency: str | None) -> Decimal | None:
    amount = _to_decimal(value)
    if amount is None:
        return None
    row_currency = str(currency or "UZS").upper()
    if row_currency == "USD":
        try:
            return to_uzs(amount)
        except Exception:
            return amount
    return amount


def _parse_int_maybe(value: Any) -> int | None:
    if value in (None, "", "null", "None", "undefined"):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _favorite_guid_set(context: dict[str, Any] | None) -> set[str]:
    if not context:
        return set()
    raw_value = context.get("favorite_guids") or []
    return {str(value) for value in raw_value if value is not None}


class RawPropertyTypeSerializer(serializers.Serializer):
    guid = serializers.UUIDField()
    title = serializers.CharField()
    icon_url = serializers.CharField(allow_null=True)


class RawPropertyLocationSerializer(serializers.Serializer):
    guid = serializers.UUIDField(allow_null=True)
    latitude = serializers.CharField(allow_null=True)
    longitude = serializers.CharField(allow_null=True)
    country = serializers.CharField(allow_blank=True, allow_null=True)
    city = serializers.CharField(allow_blank=True, allow_null=True)


class RawRegionSerializer(serializers.Serializer):
    guid = serializers.UUIDField(allow_null=True)
    title = serializers.CharField(allow_blank=True, allow_null=True)
    img = serializers.CharField(allow_blank=True, allow_null=True)


class RawDistrictSerializer(serializers.Serializer):
    guid = serializers.UUIDField(allow_null=True)
    title = serializers.CharField(allow_blank=True, allow_null=True)
    region = RawRegionSerializer(allow_null=True)




class RawPropertyListSerializer(serializers.Serializer):
    guid = serializers.UUIDField()
    title = serializers.CharField()
    img = serializers.CharField(allow_blank=True, allow_null=True)
    price_per_person = serializers.DecimalField(max_digits=18, decimal_places=2, allow_null=True)
    price_on_working_days = serializers.DecimalField(max_digits=18, decimal_places=2, allow_null=True)
    price_on_weekends = serializers.DecimalField(max_digits=18, decimal_places=2, allow_null=True)
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
        row_currency = row.get("currency")
        row["price_per_person"] = _convert_price_for_output(row.get("price_per_person"), row_currency)
        row["price_on_working_days"] = _convert_price_for_output(row.get("price_on_working_days"), row_currency)
        row["price_on_weekends"] = _convert_price_for_output(row.get("price_on_weekends"), row_currency)
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
            row["region"] = {
                "guid": None,
                "title": str(row.get("region_id")),
                "img": None,
            }
        if row.get("district_id") is None:
            row["district"] = None
        else:
            row["district"] = {
                "guid": None,
                "title": str(row.get("district_id")),
                "region": row.get("region"),
            }
        row["guests"] = None
        row["rooms"] = None
        favorites = _favorite_guid_set(self.context)
        row["is_favorite"] = str(row.get("guid")) in favorites
        return super().to_representation(row)


class RawPartnerPropertyListSerializer(RawPropertyListSerializer):
    verification_status = serializers.CharField(allow_blank=True, allow_null=True)


class RawPropertyDetailSerializer(serializers.Serializer):
    guid = serializers.UUIDField()
    title = serializers.CharField()
    img = serializers.CharField(allow_blank=True, allow_null=True)
    created_at = serializers.DateTimeField()
    currency = serializers.CharField(allow_blank=True, allow_null=True)
    price_per_person = serializers.DecimalField(max_digits=18, decimal_places=2, allow_null=True)
    price_on_working_days = serializers.DecimalField(max_digits=18, decimal_places=2, allow_null=True)
    price_on_weekends = serializers.DecimalField(max_digits=18, decimal_places=2, allow_null=True)
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
        row_currency = row.get("currency")
        row["price_per_person"] = _convert_price_for_output(row.get("price_per_person"), row_currency)
        row["price_on_working_days"] = _convert_price_for_output(row.get("price_on_working_days"), row_currency)
        row["price_on_weekends"] = _convert_price_for_output(row.get("price_on_weekends"), row_currency)
        row["description"] = self._resolve_description(row)
        row["comment_count"] = int(row.get("review_count") or row.get("comment_count") or 0)
        favorites = _favorite_guid_set(self.context)
        row["is_favorite"] = str(row.get("guid")) in favorites
        row["property_services"] = row.get("services") or []
        row["property_room"] = {
            "guid": None,
            "guests": None,
            "rooms": None,
            "beds": None,
            "bathrooms": None,
        }
        row["property_location"] = {
            "guid": None,
            "latitude": row.get("latitude"),
            "longitude": row.get("longitude"),
            "country": row.get("country"),
            "city": row.get("city"),
        }
        return super().to_representation(row)


class _PropertyLocationInputSerializer(serializers.Serializer):
    latitude = serializers.DecimalField(max_digits=18, decimal_places=8, required=False, allow_null=True)
    longitude = serializers.DecimalField(max_digits=18, decimal_places=8, required=False, allow_null=True)
    country = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    city = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class _PropertyDetailInputSerializer(serializers.Serializer):
    description_en = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    description_ru = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    description_uz = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    check_in = serializers.TimeField(required=False, allow_null=True)
    check_out = serializers.TimeField(required=False, allow_null=True)
    is_allowed_alcohol = serializers.BooleanField(required=False)
    is_allowed_corporate = serializers.BooleanField(required=False)
    is_allowed_pets = serializers.BooleanField(required=False)
    is_quiet_hours = serializers.BooleanField(required=False)
    apartment_number = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    home_number = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    entrance_number = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    floor_number = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    pass_code = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class RawPropertyCreateSerializer(serializers.Serializer):
    title = serializers.CharField(required=False, allow_blank=True)
    price = serializers.JSONField(required=False)
    currency = serializers.ChoiceField(required=False, choices=["USD", "UZS"])
    minimum_weekend_day_stay = serializers.BooleanField(required=False, default=False)
    weekend_only_sunday_inclusive = serializers.BooleanField(required=False, default=False)
    property_type_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    property_type = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    kind = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    property_location = serializers.DictField(required=False)
    property_detail = serializers.DictField(required=False)
    property_services = serializers.ListField(required=False, allow_empty=True)
    property_room = serializers.DictField(required=False)
    region = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    district = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    region_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    district_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    img = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    price_per_person = serializers.DecimalField(max_digits=18, decimal_places=2, required=False)
    price_on_working_days = serializers.DecimalField(max_digits=18, decimal_places=2, required=False)
    price_on_weekends = serializers.DecimalField(max_digits=18, decimal_places=2, required=False)
    apartment_number = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    home_number = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    entrance_number = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    floor_number = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    pass_code = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    def _parse_price(self, attrs: dict[str, Any], *, required: bool) -> dict[str, Decimal | None]:
        # 1. Yangi format: To'g'ridan-to'g'ri root (asosiy) maydonlardan olamiz
        per_person = _to_decimal(attrs.get("price_per_person"))
        working = _to_decimal(attrs.get("price_on_working_days"))
        weekends = _to_decimal(attrs.get("price_on_weekends"))

        # 2. Eski formatni ushlab qolish (agar frontend hali eski JSON jo'natsa, kod sinib qolmasligi uchun)
        if working is None and weekends is None and per_person is None:
            raw_price = attrs.get("price")
            if isinstance(raw_price, list) and raw_price:
                first = raw_price[0]
                if isinstance(first, dict):
                    per_person = _to_decimal(first.get("price_per_person"))
                    working = _to_decimal(first.get("price_on_working_days"))
                    weekends = _to_decimal(first.get("price_on_weekends"))
            elif isinstance(raw_price, dict):
                per_person = _to_decimal(raw_price.get("price_per_person"))
                working = _to_decimal(raw_price.get("price_on_working_days"))
                weekends = _to_decimal(raw_price.get("price_on_weekends"))
            elif raw_price is not None:
                # Agar faqat bitta narx berilsa
                working = _to_decimal(raw_price)

        # 3. Default qiymatlarni o'rnatish
        if required and working is None and weekends is None and per_person is None:
            return {
                "price": Decimal("0"),
                "price_per_person": Decimal("0"),
                "price_on_working_days": Decimal("0"),
                "price_on_weekends": Decimal("0"),
            }

        if working is None:
            working = Decimal("0")
        if weekends is None:
            weekends = working
        if per_person is None:
            per_person = Decimal("0")

        # 4. Manfiy narxlarni tekshirish
        for value in (per_person, working, weekends):
            if value < 0:
                raise serializers.ValidationError(_("Price values must be non-negative"))

        # Baza (DB) uchun umumiy 'price' ga 'working' day narxini yozib yuboramiz
        return {
            "price": working,
            "price_per_person": per_person,
            "price_on_working_days": working,
            "price_on_weekends": weekends,
        }

    def validate(self, attrs):
        is_update = bool(self.context.get("is_update"))
        forced_kind = self.context.get("forced_kind")

        raw_kind = forced_kind or attrs.get("kind") or attrs.get("property_type") or attrs.get("property_type_id")
        property_kind = parse_property_kind(raw_kind)
        if not property_kind:
            if not is_update:
                raise serializers.ValidationError(
                    {"property_type_id": _("Property type must be apartment or cottage")}
                )
            property_kind = forced_kind

        title = (attrs.get("title") or "").strip()
        if not is_update and not title:
            raise serializers.ValidationError({"title": _("This field is required.")})

        location_serializer = _PropertyLocationInputSerializer(
            data=attrs.get("property_location") or {},
            partial=True,
        )
        location_serializer.is_valid(raise_exception=True)

        detail_payload = attrs.get("property_detail") or {}
        for key in ("apartment_number", "home_number", "entrance_number", "floor_number", "pass_code"):
            if attrs.get(key) is not None and key not in detail_payload:
                detail_payload[key] = attrs.get(key)
        detail_serializer = _PropertyDetailInputSerializer(data=detail_payload, partial=True)
        detail_serializer.is_valid(raise_exception=True)

        if property_kind == "apartment" and not is_update:
            required_detail = [
                "apartment_number",
                "home_number",
                "entrance_number",
                "floor_number",
                "pass_code",
            ]
            missing = [field for field in required_detail if not detail_serializer.validated_data.get(field)]
            if missing:
                raise serializers.ValidationError(
                    {
                        "property_detail": {
                            field: _("This field is required for apartment properties.")
                            for field in missing
                        }
                    }
                )

        price_values = self._parse_price(attrs, required=not is_update)
        normalized_values: dict[str, Any] = {}
        if title:
            normalized_values["title"] = title
            normalized_values["title_sort"] = title.lower()
        if "minimum_weekend_day_stay" in attrs:
            normalized_values["minimum_weekend_day_stay"] = bool(attrs.get("minimum_weekend_day_stay"))
        elif not is_update:
            normalized_values["minimum_weekend_day_stay"] = False

        if "weekend_only_sunday_inclusive" in attrs:
            normalized_values["weekend_only_sunday_inclusive"] = bool(attrs.get("weekend_only_sunday_inclusive"))
        elif not is_update:
            normalized_values["weekend_only_sunday_inclusive"] = False

        if "currency" in attrs:
            normalized_values["currency"] = attrs.get("currency") or "UZS"
        elif not is_update:
            normalized_values["currency"] = "UZS"

        if "img" in attrs:
            normalized_values["img"] = attrs.get("img")
        normalized_values.update(price_values)
        if attrs.get("property_location") is not None:
            normalized_values.update(location_serializer.validated_data)
        if detail_serializer.validated_data:
            normalized_values.update(detail_serializer.validated_data)
        if "region_id" in attrs or "region" in attrs:
            normalized_values["region_id"] = _parse_int_maybe(
                attrs.get("region_id") if attrs.get("region_id") is not None else attrs.get("region")
            )
        if "district_id" in attrs or "district" in attrs:
            normalized_values["district_id"] = _parse_int_maybe(
                attrs.get("district_id") if attrs.get("district_id") is not None else attrs.get("district")
            )

        attrs["property_kind"] = property_kind
        attrs["normalized_values"] = normalized_values
        return attrs


class RawPropertyUpdateSerializer(RawPropertyCreateSerializer):
    pass


class RawPropertyReviewClientSerializer(serializers.Serializer):
    guid = serializers.UUIDField(allow_null=True)
    first_name = serializers.CharField(allow_blank=True, allow_null=True)
    last_name = serializers.CharField(allow_blank=True, allow_null=True)


class RawPropertyReviewSerializer(serializers.Serializer):
    guid = serializers.UUIDField()
    client = RawPropertyReviewClientSerializer()
    rating = serializers.DecimalField(max_digits=2, decimal_places=1, allow_null=True)
    comment = serializers.CharField(allow_blank=True, allow_null=True)
    created_at = serializers.DateTimeField()

    def to_representation(self, instance):
        row = dict(instance)
        row["client"] = {
            "guid": _synthetic_user_guid(row.get("client_id")),
            "first_name": row.get("client_first_name"),
            "last_name": row.get("client_last_name"),
        }
        return super().to_representation(row)


class RawPropertyReviewCreateSerializer(serializers.Serializer):
    rating = serializers.DecimalField(max_digits=2, decimal_places=1, required=True)
    comment = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    @staticmethod
    def validate_rating(value):
        if not (Decimal("1.0") <= Decimal(str(value)) <= Decimal("5.0")):
            raise serializers.ValidationError(_("Rating must be between 1 and 5"))
        return value
