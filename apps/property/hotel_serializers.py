from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from django.core.files.storage import default_storage
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
        row["average_rating"] = float(row.get("average_rating") or 0) if row.get("average_rating") is not None else None
        favorites = _favorite_guid_set(self.context)
        row["is_favorite"] = str(row.get("guid")) in favorites
        return super().to_representation(row)
