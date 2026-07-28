"""Serializers for the map view, the search cards and the filter sheet."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from rest_framework import serializers

from .apartment_repository import (
    APARTMENT_TYPE_GUID,
    COTTAGE_TYPE_GUID,
    HOTEL_TYPE_GUID,
    PROPERTY_KIND_APARTMENT,
    PROPERTY_KIND_COTTAGE,
    PROPERTY_KIND_HOTEL,
)
from .apartment_serializers import (
    _build_media_url,
    _convert_price_for_output,
    _to_decimal,
)


KIND_TO_TYPE_GUID = {
    PROPERTY_KIND_APARTMENT: str(APARTMENT_TYPE_GUID),
    PROPERTY_KIND_COTTAGE: str(COTTAGE_TYPE_GUID),
    PROPERTY_KIND_HOTEL: str(HOTEL_TYPE_GUID),
}


def row_kind(row: dict[str, Any]) -> str:
    return str(row.get("property_kind") or PROPERTY_KIND_APARTMENT)


def row_coordinates(row: dict[str, Any]) -> tuple[float, float] | None:
    try:
        latitude = float(row.get("latitude"))
        longitude = float(row.get("longitude"))
    except (TypeError, ValueError):
        return None
    if latitude == 0.0 and longitude == 0.0:
        return None
    return latitude, longitude


def row_price(row: dict[str, Any]) -> Decimal | None:
    """The nightly price the map pin and the card headline show."""
    kind = row_kind(row)
    if kind == PROPERTY_KIND_HOTEL:
        raw = row.get("min_price")
        currency = row.get("min_price_currency") or row.get("currency")
    else:
        raw = row.get("effective_price")
        if raw is None:
            raw = row.get("price_on_working_days") or row.get("price")
        currency = row.get("currency")
    return _convert_price_for_output(raw, currency)


def row_currency(row: dict[str, Any]) -> str:
    if row_kind(row) == PROPERTY_KIND_HOTEL:
        raw = row.get("min_price_currency") or row.get("currency")
    else:
        raw = row.get("currency")
    # Prices are converted to UZS on output, so the label follows.
    return "UZS" if str(raw or "UZS").upper() in {"USD", "UZS"} else str(raw)


def row_location_label(row: dict[str, Any]) -> str:
    """"Яккасарой, Ташкент" — the district / city line under the title."""
    if row_kind(row) == PROPERTY_KIND_HOTEL:
        parts = [row.get("city"), row.get("country")]
    else:
        parts = [
            row.get("prefecture_name") or row.get("district_name"),
            row.get("region_name") or row.get("city"),
        ]
    seen: list[str] = []
    for part in parts:
        value = str(part or "").strip()
        if value and value not in seen:
            seen.append(value)
    return ", ".join(seen)


def row_rating(row: dict[str, Any]) -> float | None:
    raw = row.get("average_rating") if row_kind(row) != PROPERTY_KIND_HOTEL else row.get("rating")
    amount = _to_decimal(raw)
    return round(float(amount), 2) if amount is not None else None


def row_comment_count(row: dict[str, Any]) -> int:
    for key in ("comment_count", "review_count"):
        value = row.get(key)
        if value not in (None, ""):
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return 0


def _int_or_none(value: Any) -> int | None:
    if value in (None, "", "null", "None"):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def build_map_pin(row: dict[str, Any], *, favorites: set[str]) -> dict[str, Any] | None:
    coordinates = row_coordinates(row)
    if coordinates is None:
        return None
    latitude, longitude = coordinates
    guid = str(row.get("guid") or "")
    return {
        "guid": guid,
        "kind": row_kind(row),
        "latitude": latitude,
        "longitude": longitude,
        "price": row_price(row),
        "currency": row_currency(row),
        "is_favorite": guid in favorites,
    }


def build_property_card(
    row: dict[str, Any],
    *,
    request=None,
    favorites: set[str] | None = None,
) -> dict[str, Any]:
    """The card shown when a map pin is tapped and in the search results list."""
    favorites = favorites or set()
    kind = row_kind(row)
    guid = str(row.get("guid") or "")
    coordinates = row_coordinates(row)
    images = _build_media_url(request, row.get("img") or row.get("photos") or [])
    price_per_person = None
    if kind != PROPERTY_KIND_HOTEL:
        price_per_person = _convert_price_for_output(
            row.get("price_per_person"), row.get("currency")
        )
    return {
        "guid": guid,
        "kind": kind,
        "property_type_id": KIND_TO_TYPE_GUID.get(kind),
        "title": str(row.get("title") or row.get("name") or ""),
        "img": images,
        "price": row_price(row),
        "price_per_person": price_per_person,
        "currency": row_currency(row),
        "rating": row_rating(row),
        "comment_count": row_comment_count(row),
        "location_label": row_location_label(row),
        "guests": _int_or_none(row.get("guests")),
        "star_rating": _int_or_none(row.get("star_rating")),
        "latitude": coordinates[0] if coordinates else None,
        "longitude": coordinates[1] if coordinates else None,
        "is_favorite": guid in favorites,
    }


class MapPinSerializer(serializers.Serializer):
    guid = serializers.CharField()
    kind = serializers.CharField()
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    price = serializers.DecimalField(max_digits=18, decimal_places=2, allow_null=True)
    currency = serializers.CharField(allow_blank=True)
    is_favorite = serializers.BooleanField()

    class Meta:
        ref_name = "PropertyMapPin"


class MapClusterSerializer(serializers.Serializer):
    latitude = serializers.FloatField()
    longitude = serializers.FloatField()
    count = serializers.IntegerField()
    min_price = serializers.DecimalField(
        max_digits=18, decimal_places=2, allow_null=True
    )
    currency = serializers.CharField(allow_blank=True)

    class Meta:
        ref_name = "PropertyMapCluster"


class PropertyCardSerializer(serializers.Serializer):
    guid = serializers.CharField()
    kind = serializers.CharField()
    property_type_id = serializers.CharField(allow_null=True)
    title = serializers.CharField(allow_blank=True)
    img = serializers.ListField(child=serializers.CharField(), allow_empty=True)
    price = serializers.DecimalField(max_digits=18, decimal_places=2, allow_null=True)
    price_per_person = serializers.DecimalField(
        max_digits=18, decimal_places=2, allow_null=True
    )
    currency = serializers.CharField(allow_blank=True)
    rating = serializers.FloatField(allow_null=True)
    comment_count = serializers.IntegerField()
    location_label = serializers.CharField(allow_blank=True)
    guests = serializers.IntegerField(allow_null=True)
    star_rating = serializers.IntegerField(allow_null=True)
    latitude = serializers.FloatField(allow_null=True)
    longitude = serializers.FloatField(allow_null=True)
    is_favorite = serializers.BooleanField()

    class Meta:
        ref_name = "PropertyCard"


class MapResponseSerializer(serializers.Serializer):
    total = serializers.IntegerField()
    truncated = serializers.BooleanField()
    pins = MapPinSerializer(many=True)
    clusters = MapClusterSerializer(many=True)

    class Meta:
        ref_name = "PropertyMapResponse"


class PriceHistogramBucketSerializer(serializers.Serializer):
    min_price = serializers.DecimalField(max_digits=18, decimal_places=2)
    max_price = serializers.DecimalField(max_digits=18, decimal_places=2)
    count = serializers.IntegerField()

    class Meta:
        ref_name = "PropertyPriceHistogramBucket"


class PriceHistogramSerializer(serializers.Serializer):
    currency = serializers.CharField()
    total = serializers.IntegerField()
    min_price = serializers.DecimalField(
        max_digits=18, decimal_places=2, allow_null=True
    )
    max_price = serializers.DecimalField(
        max_digits=18, decimal_places=2, allow_null=True
    )
    buckets = PriceHistogramBucketSerializer(many=True)

    class Meta:
        ref_name = "PropertyPriceHistogram"


class DestinationSerializer(serializers.Serializer):
    id = serializers.CharField()
    type = serializers.CharField()
    title = serializers.CharField()
    subtitle = serializers.CharField(allow_blank=True)
    latitude = serializers.FloatField(allow_null=True)
    longitude = serializers.FloatField(allow_null=True)
    property_count = serializers.IntegerField()
    distance_km = serializers.FloatField(allow_null=True)

    class Meta:
        ref_name = "SearchDestination"
