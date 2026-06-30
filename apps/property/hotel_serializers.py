from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from django.core.files.storage import default_storage
from django.utils.translation import gettext_lazy as _
from payment.exchange_rate import to_uzs
from rest_framework import serializers

from .apartment_repository import HOTEL_TYPE_GUID


def _preferred_language(request: Any) -> str:
    if request is None:
        return "uz"
    raw = str(request.headers.get("Accept-Language") or "").strip().lower()
    if raw.startswith("ru"):
        return "ru"
    if raw.startswith("en"):
        return "en"
    return "uz"


def _hotel_type_title(language: str) -> str:
    if language == "ru":
        return "Отель"
    if language == "en":
        return "Hotel"
    return "Mehmonxona"


def _build_media_url(request, media_path: Any) -> list[str]:
    if not media_path:
        return []
    values = media_path if isinstance(media_path, list) else [media_path]
    urls: list[str] = []
    for value in values:
        if not value:
            continue
        item = str(value)
        if item.startswith("http://") or item.startswith("https://"):
            urls.append(item)
            continue
        if item.startswith("blob:"):
            continue
        try:
            url = default_storage.url(item)
        except Exception:
            url = item
        if request:
            url = request.build_absolute_uri(url)
        urls.append(url)
    return urls


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


def _favorite_guid_set(context: dict[str, Any] | None) -> set[str]:
    if not context:
        return set()
    raw_value = context.get("favorite_guids") or []
    return {str(value) for value in raw_value if value is not None}


class HotelListSerializer(serializers.Serializer):
    guid = serializers.CharField()
    title = serializers.CharField()
    img = serializers.ListField(child=serializers.CharField(), allow_empty=True)
    price = serializers.DecimalField(max_digits=18, decimal_places=2, allow_null=True)
    currency = serializers.CharField(allow_blank=True, allow_null=True)
    latitude = serializers.CharField(allow_blank=True, allow_null=True)
    longitude = serializers.CharField(allow_blank=True, allow_null=True)
    country = serializers.CharField(allow_blank=True, allow_null=True)
    city = serializers.CharField(allow_blank=True, allow_null=True)
    property_location = serializers.DictField(required=False)
    services = serializers.ListField(required=False)
    guests = serializers.IntegerField(allow_null=True)
    rooms = serializers.IntegerField(allow_null=True)
    beds = serializers.IntegerField(allow_null=True)
    bathrooms = serializers.IntegerField(allow_null=True)
    property_room = serializers.DictField(required=False)
    average_rating = serializers.FloatField(allow_null=True)
    comment_count = serializers.IntegerField(required=False)
    is_favorite = serializers.BooleanField()
    is_allowed_corporate = serializers.BooleanField()
    created_at = serializers.DateTimeField()
    property_type_id = serializers.UUIDField()
    property_type = serializers.DictField()

    def to_representation(self, instance):
        request = self.context.get("request")
        row = dict(instance)
        row["img"] = _build_media_url(request, row.get("img"))
        row["price"] = _convert_price_for_output(row.get("price"), row.get("currency"))
        row["services"] = row.get("services") or []
        row["property_location"] = {
            "latitude": row.get("latitude"),
            "longitude": row.get("longitude"),
            "country": row.get("country"),
            "city": row.get("city"),
            "region": None,
            "district": None,
            "prefecture": None,
        }
        row["property_room"] = {
            "guests": row.get("guests"),
            "rooms": row.get("rooms"),
            "beds": row.get("beds"),
            "bathrooms": row.get("bathrooms"),
        }
        lang = _preferred_language(request)
        row["property_type_id"] = str(HOTEL_TYPE_GUID)
        row["property_type"] = {
            "guid": str(HOTEL_TYPE_GUID),
            "title": _hotel_type_title(lang),
        }
        row["comment_count"] = int(row.get("comment_count") or 0)
        row["average_rating"] = (
            float(row.get("average_rating") or 0)
            if row.get("average_rating") is not None
            else None
        )
        favorites = _favorite_guid_set(self.context)
        row["is_favorite"] = str(row.get("guid")) in favorites
        return super().to_representation(row)


class HotelAdminOrganizationSerializer(serializers.Serializer):
    id = serializers.IntegerField(allow_null=True)
    name = serializers.CharField(allow_blank=True, allow_null=True)
    slug = serializers.CharField(allow_blank=True, allow_null=True)
    schema_name = serializers.CharField(allow_blank=True, allow_null=True)


class HotelAdminPropertyDetailSerializer(serializers.Serializer):
    description_ru = serializers.CharField(allow_null=True)
    description_uz = serializers.CharField(allow_null=True)
    description_en = serializers.CharField(allow_null=True)
    address = serializers.CharField(allow_blank=True, allow_null=True)
    check_in_time = serializers.CharField(allow_blank=True, allow_null=True)
    check_out_time = serializers.CharField(allow_blank=True, allow_null=True)
    cancellation_policy = serializers.CharField(allow_blank=True, allow_null=True)
    timezone = serializers.CharField(allow_blank=True, allow_null=True)
    amenities = serializers.ListField(child=serializers.CharField(), allow_empty=True)
    is_allowed_alcohol = serializers.BooleanField()
    is_allowed_pets = serializers.BooleanField()
    is_quiet_hours = serializers.BooleanField()
    star_rating = serializers.IntegerField(allow_null=True)


class HotelAdminListSerializer(HotelListSerializer):
    is_active = serializers.BooleanField(read_only=True)
    is_testing = serializers.BooleanField(read_only=True)
    organization = HotelAdminOrganizationSerializer(read_only=True)
    property_detail = HotelAdminPropertyDetailSerializer(source="*", read_only=True)
    tenant_schema = serializers.CharField(allow_blank=True, allow_null=True)

    def to_representation(self, instance):
        row = dict(instance)
        data = super().to_representation(row)
        data["is_active"] = bool(row.get("is_active", True))
        data["is_testing"] = bool(row.get("is_testing", False))
        data["tenant_schema"] = row.get("tenant_schema")
        data["organization"] = {
            "id": row.get("organization_id"),
            "name": row.get("organization_name"),
            "slug": row.get("organization_slug"),
            "schema_name": row.get("tenant_schema"),
        }
        data["property_detail"] = {
            "description_ru": row.get("description_ru") or None,
            "description_uz": row.get("description_uz") or None,
            "description_en": row.get("description_en") or None,
            "address": row.get("address") or None,
            "check_in_time": row.get("check_in_time") or None,
            "check_out_time": row.get("check_out_time") or None,
            "cancellation_policy": row.get("cancellation_policy") or None,
            "timezone": row.get("timezone") or None,
            "amenities": row.get("amenities") or [],
            "is_allowed_alcohol": bool(row.get("is_allowed_alcohol", False)),
            "is_allowed_pets": bool(row.get("is_allowed_pets", False)),
            "is_quiet_hours": bool(row.get("is_quiet_hours", True)),
            "star_rating": row.get("star_rating"),
        }
        return data


class HotelAdminUpdateSerializer(serializers.Serializer):
    organization_id = serializers.IntegerField(required=False, allow_null=True)
    tenant_schema = serializers.CharField(required=False, allow_blank=False)
    title = serializers.CharField(required=False, allow_blank=False)
    description_ru = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    description_uz = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    description_en = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    address = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    city = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    country = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    latitude = serializers.DecimalField(max_digits=17, decimal_places=14, required=False, allow_null=True)
    longitude = serializers.DecimalField(max_digits=17, decimal_places=14, required=False, allow_null=True)
    star_rating = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=7)
    amenities = serializers.ListField(
        child=serializers.CharField(allow_blank=False),
        required=False,
        allow_empty=True,
    )
    check_in_time = serializers.TimeField(required=False, allow_null=True)
    check_out_time = serializers.TimeField(required=False, allow_null=True)
    cancellation_policy = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    is_quiet_hours = serializers.BooleanField(required=False)
    is_allowed_alcohol = serializers.BooleanField(required=False)
    is_allowed_pets = serializers.BooleanField(required=False)
    quiet_hours = serializers.BooleanField(required=False)
    alcohol_allowed = serializers.BooleanField(required=False)
    pets_allowed = serializers.BooleanField(required=False)
    currency = serializers.ChoiceField(choices=["USD", "UZS"], required=False)
    timezone = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    is_active = serializers.BooleanField(required=False)
    is_testing = serializers.BooleanField(required=False)
    img = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )
    property_detail = HotelAdminPropertyDetailSerializer(required=False)

    def validate(self, attrs):
        prepared: dict[str, Any] = {}
        property_detail = attrs.get("property_detail")

        if "organization_id" in attrs:
            prepared["organization_id"] = attrs.get("organization_id")
        if "tenant_schema" in attrs:
            prepared["tenant_schema"] = str(attrs.get("tenant_schema") or "").strip()

        if "title" in attrs:
            title = str(attrs.get("title") or "").strip()
            if not title:
                raise serializers.ValidationError({"title": _("This field is required.")})
            prepared["name"] = title

        for key in ("description_ru", "description_uz", "description_en"):
            if key in attrs:
                prepared[key] = attrs.get(key)
        for key in (
            "address",
            "city",
            "country",
            "latitude",
            "longitude",
            "star_rating",
            "check_in_time",
            "check_out_time",
            "cancellation_policy",
            "quiet_hours",
            "alcohol_allowed",
            "pets_allowed",
            "currency",
            "timezone",
            "is_active",
            "is_testing",
        ):
            if key in attrs:
                prepared[key] = attrs.get(key)

        if "is_quiet_hours" in attrs:
            prepared["quiet_hours"] = attrs.get("is_quiet_hours")
        if "is_allowed_alcohol" in attrs:
            prepared["alcohol_allowed"] = attrs.get("is_allowed_alcohol")
        if "is_allowed_pets" in attrs:
            prepared["pets_allowed"] = attrs.get("is_allowed_pets")

        if "amenities" in attrs:
            prepared["amenities"] = [str(value).strip() for value in attrs.get("amenities") or [] if str(value).strip()]
        if "img" in attrs:
            prepared["photos"] = [str(value) for value in attrs.get("img") or [] if value]

        if isinstance(property_detail, dict):
            for key in (
                "description_ru",
                "description_uz",
                "description_en",
                "address",
                "check_in_time",
                "check_out_time",
                "cancellation_policy",
                "timezone",
                "star_rating",
            ):
                if key in property_detail:
                    prepared[key] = property_detail.get(key)
            if "amenities" in property_detail:
                prepared["amenities"] = [
                    str(value).strip()
                    for value in property_detail.get("amenities") or []
                    if str(value).strip()
                ]
            if "is_allowed_alcohol" in property_detail:
                prepared["alcohol_allowed"] = bool(property_detail.get("is_allowed_alcohol"))
            if "is_allowed_pets" in property_detail:
                prepared["pets_allowed"] = bool(property_detail.get("is_allowed_pets"))
            if "is_quiet_hours" in property_detail:
                prepared["quiet_hours"] = bool(property_detail.get("is_quiet_hours"))

        attrs["values"] = prepared
        return attrs
