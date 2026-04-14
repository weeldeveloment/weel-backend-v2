from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from decimal import InvalidOperation
from typing import Any
from uuid import UUID

from django.core.files.storage import default_storage
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from payment.exchange_rate import to_uzs
from .apartment_repository import COTTAGE_TYPE_GUID, is_prefecture_linked_to_district


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


def _build_property_location(row: dict[str, Any]) -> dict[str, Any]:
    region_id = _parse_int_maybe(row.get("region_id"))
    district_id = _parse_int_maybe(row.get("district_id"))
    prefecture_guid = row.get("prefecture_guid") or row.get("prefecture_id")

    return {
        "latitude": row.get("latitude"),
        "longitude": row.get("longitude"),
        "country": row.get("country"),
        "city": row.get("city"),
        "region": {
            "id": region_id,
            "guid": row.get("region_guid"),
            "name": row.get("region_name"),
        }
        if region_id is not None
        else None,
        "district": {
            "id": district_id,
            "guid": row.get("district_guid"),
            "name": row.get("district_name"),
        }
        if district_id is not None
        else None,
        "prefecture": {
            "id": str(prefecture_guid),
            "name": row.get("prefecture_name"),
        }
        if prefecture_guid not in (None, "", "null", "None", "undefined")
        else None,
    }


class PropertyLocationRegionOutputSerializer(serializers.Serializer):
    id = serializers.IntegerField(allow_null=True)
    guid = serializers.UUIDField(allow_null=True)
    name = serializers.CharField(allow_blank=True, allow_null=True)


class PropertyLocationDistrictOutputSerializer(serializers.Serializer):
    id = serializers.IntegerField(allow_null=True)
    guid = serializers.UUIDField(allow_null=True)
    name = serializers.CharField(allow_blank=True, allow_null=True)


class PropertyLocationPrefectureOutputSerializer(serializers.Serializer):
    id = serializers.CharField(allow_blank=True, allow_null=True)
    name = serializers.CharField(allow_blank=True, allow_null=True)


class PropertyLocationOutputSerializer(serializers.Serializer):
    latitude = serializers.CharField(allow_blank=True, allow_null=True)
    longitude = serializers.CharField(allow_blank=True, allow_null=True)
    country = serializers.CharField(allow_blank=True, allow_null=True)
    city = serializers.CharField(allow_blank=True, allow_null=True)
    region = PropertyLocationRegionOutputSerializer(allow_null=True)
    district = PropertyLocationDistrictOutputSerializer(allow_null=True)
    prefecture = PropertyLocationPrefectureOutputSerializer(allow_null=True)


def _parse_int_maybe(value: Any) -> int | None:
    if value in (None, "", "null", "None", "undefined"):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _parse_decimal_maybe(value: Any) -> Decimal | None:
    if value in (None, "", "null", "None", "undefined"):
        return None
    amount = _to_decimal(value)
    if amount is None:
        raise serializers.ValidationError(_("Invalid numeric value."))
    return amount


def _parse_decimal_maybe_permissive(value: Any) -> Decimal | None:
    """Parse decimal but return None on invalid values instead of raising"""
    if value in (None, "", "null", "None", "undefined"):
        return None
    return _to_decimal(value)


def _normalize_uuid_list(values: Any) -> list[UUID]:
    if values in (None, "", "null", "None", "undefined"):
        return []
    if not isinstance(values, list):
        raise serializers.ValidationError(_("Expected a list of UUID values."))
    normalized: list[UUID] = []
    for value in values:
        if value in (None, "", "null", "None", "undefined"):
            continue
        try:
            normalized.append(UUID(str(value)))
        except (ValueError, TypeError, AttributeError):
            raise serializers.ValidationError(_("Property services must contain valid UUID values."))
    return normalized


class _PropertyLocationInputSerializer(serializers.Serializer):
    latitude = serializers.DecimalField(max_digits=18, decimal_places=8, required=False, allow_null=True)
    longitude = serializers.DecimalField(max_digits=18, decimal_places=8, required=False, allow_null=True)
    country = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    city = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    region_id = serializers.IntegerField(required=False, allow_null=True)
    district_id = serializers.IntegerField(required=False, allow_null=True)
    prefecture_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)


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


class RawRegionSerializer(serializers.Serializer):
    id = serializers.IntegerField(required=False, allow_null=True)
    guid = serializers.UUIDField(allow_null=True)
    title = serializers.CharField(allow_blank=True, allow_null=True)
    img = serializers.CharField(allow_blank=True, allow_null=True)


class RawDistrictSerializer(serializers.Serializer):
    id = serializers.IntegerField(required=False, allow_null=True)
    region_id = serializers.IntegerField(required=False, allow_null=True)
    guid = serializers.UUIDField(allow_null=True)
    title = serializers.CharField(allow_blank=True, allow_null=True)
    region = RawRegionSerializer(allow_null=True)


class CottageListSerializer(serializers.Serializer):
    guid = serializers.UUIDField()
    title = serializers.CharField()
    img = serializers.ListField(child=serializers.CharField(), allow_empty=True)
    price_per_person = serializers.DecimalField(max_digits=18, decimal_places=2, allow_null=True)
    price_on_working_days = serializers.DecimalField(max_digits=18, decimal_places=2, allow_null=True)
    price_on_weekends = serializers.DecimalField(max_digits=18, decimal_places=2, allow_null=True)
    currency = serializers.CharField(allow_blank=True, allow_null=True)
    latitude = serializers.CharField(allow_blank=True, allow_null=True)
    longitude = serializers.CharField(allow_blank=True, allow_null=True)
    country = serializers.CharField(allow_blank=True, allow_null=True)
    city = serializers.CharField(allow_blank=True, allow_null=True)
    property_location = PropertyLocationOutputSerializer(required=False)
    services = serializers.ListField()
    region = RawRegionSerializer(allow_null=True)
    district = RawDistrictSerializer(allow_null=True)
    prefecture_id = serializers.CharField(allow_blank=True, allow_null=True)
    guests = serializers.IntegerField(allow_null=True)
    rooms = serializers.IntegerField(allow_null=True)
    average_rating = serializers.FloatField(allow_null=True)
    is_favorite = serializers.BooleanField()
    is_allowed_corporate = serializers.BooleanField()
    created_at = serializers.DateTimeField()
    property_type_id = serializers.UUIDField()
    property_type = serializers.DictField()

    def to_representation(self, instance):
        request = self.context.get("request")
        row = dict(instance)
        row["img"] = _build_media_url(request, row.get("img"))
        row_currency = row.get("currency")
        row["price_per_person"] = _convert_price_for_output(row.get("price_per_person"), row_currency)
        row["price_on_working_days"] = _convert_price_for_output(row.get("price_on_working_days"), row_currency)
        row["price_on_weekends"] = _convert_price_for_output(row.get("price_on_weekends"), row_currency)
        row["services"] = row.get("services") or []
        row["property_location"] = _build_property_location(row)
        if row.get("region_id") is None:
            row["region"] = None
        else:
            row["region"] = {
                "id": _parse_int_maybe(row.get("region_id")),
                "guid": row.get("region_guid"),
                "title": row.get("region_name"),
                "img": None,
            }
        if row.get("district_id") is None:
            row["district"] = None
        else:
            row["district"] = {
                "id": _parse_int_maybe(row.get("district_id")),
                "region_id": _parse_int_maybe(row.get("region_id")),
                "guid": row.get("district_guid"),
                "title": row.get("district_name"),
                "region": row.get("region"),
            }
        row["guests"] = None
        row["rooms"] = None
        row["property_type_id"] = str(COTTAGE_TYPE_GUID)
        row["property_type"] = {
            "guid": str(COTTAGE_TYPE_GUID),
            "title": "Cottage",
        }
        favorites = _favorite_guid_set(self.context)
        row["is_favorite"] = str(row.get("guid")) in favorites
        return super().to_representation(row)


class CottagePartnerListSerializer(CottageListSerializer):
    verification_status = serializers.CharField(allow_blank=True, allow_null=True)


class CottageDetailSerializer(serializers.Serializer):
    guid = serializers.UUIDField()
    title = serializers.CharField()
    img = serializers.ListField(child=serializers.CharField(), allow_empty=True)
    created_at = serializers.DateTimeField()
    currency = serializers.CharField(allow_blank=True, allow_null=True)
    price_per_person = serializers.DecimalField(max_digits=18, decimal_places=2, allow_null=True)
    price_on_working_days = serializers.DecimalField(max_digits=18, decimal_places=2, allow_null=True)
    price_on_weekends = serializers.DecimalField(max_digits=18, decimal_places=2, allow_null=True)
    price = serializers.ListField(required=False, allow_empty=True)
    minimum_weekend_day_stay = serializers.BooleanField()
    description = serializers.CharField(allow_blank=True, allow_null=True)
    comment_count = serializers.IntegerField()
    average_rating = serializers.FloatField(allow_null=True)
    is_favorite = serializers.BooleanField()
    property_services = serializers.ListField()
    property_room = serializers.DictField()
    region_id = serializers.IntegerField(allow_null=True)
    district_id = serializers.IntegerField(allow_null=True)
    prefecture_id = serializers.CharField(allow_blank=True, allow_null=True)
    latitude = serializers.CharField(allow_blank=True, allow_null=True)
    longitude = serializers.CharField(allow_blank=True, allow_null=True)
    country = serializers.CharField(allow_blank=True, allow_null=True)
    city = serializers.CharField(allow_blank=True, allow_null=True)
    property_location = PropertyLocationOutputSerializer(required=False)
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
        row["img"] = _build_media_url(request, row.get("img"))
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
            "guests": _parse_int_maybe(row.get("guests")),
            "rooms": _parse_int_maybe(row.get("rooms")),
            "beds": _parse_int_maybe(row.get("beds")),
            "bathrooms": _parse_int_maybe(row.get("bathrooms")),
        }
        row["property_location"] = _build_property_location(row)
        # Process monthly price data from separate cottage_price table
        price_data = row.get("price")
        if price_data:
            if isinstance(price_data, str):
                try:
                    import json
                    price_data = json.loads(price_data)
                except (json.JSONDecodeError, TypeError):
                    price_data = []
            if isinstance(price_data, list):
                # Convert Decimal prices to strings in price items
                processed_prices = []
                for item in price_data:
                    if isinstance(item, dict):
                        processed_item = dict(item)
                        # Convert any Decimal values to string for JSON serialization
                        for key in ('price_per_person', 'price_on_working_days', 'price_on_weekends'):
                            if key in processed_item and processed_item[key] is not None:
                                processed_item[key] = str(processed_item[key])
                        processed_prices.append(processed_item)
                row["price"] = processed_prices
            else:
                row["price"] = []
        else:
            row["price"] = []
        return super().to_representation(row)


class CottageCreateSerializer(serializers.Serializer):
    title = serializers.CharField(required=False, allow_blank=True)
    price_per_person = serializers.DecimalField(max_digits=18, decimal_places=2, required=False)
    price_on_working_days = serializers.DecimalField(max_digits=18, decimal_places=2, required=False)
    price_on_weekends = serializers.DecimalField(max_digits=18, decimal_places=2, required=False)
    currency = serializers.ChoiceField(required=False, choices=["USD", "UZS"])
    minimum_weekend_day_stay = serializers.BooleanField(required=False, default=False)
    weekend_only_sunday_inclusive = serializers.BooleanField(required=False, default=False)
    price = serializers.ListField(required=False, allow_empty=False)
    property_location = serializers.DictField(required=False)
    property_detail = serializers.DictField(required=False)
    property_services = serializers.ListField(required=False, allow_empty=True)
    property_room = serializers.DictField(required=False)
    guests = serializers.IntegerField(required=False, allow_null=True)
    rooms = serializers.IntegerField(required=False, allow_null=True)
    beds = serializers.IntegerField(required=False, allow_null=True)
    bathrooms = serializers.IntegerField(required=False, allow_null=True)
    region = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    district = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    region_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    district_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    prefecture_id = serializers.UUIDField(required=False, allow_null=True)
    img = serializers.JSONField(required=False)

    def validate(self, attrs):
        is_update = bool(self.context.get("is_update"))

        title = (attrs.get("title") or "").strip()
        if not is_update and not title:
            raise serializers.ValidationError({"title": _("This field is required.")})

        detail_payload = attrs.get("property_detail") or {}
        detail_serializer = _PropertyDetailInputSerializer(data=detail_payload, partial=True)
        detail_serializer.is_valid(raise_exception=True)

        raw_monthly_prices = attrs.get("price")
        monthly_prices_present = isinstance(raw_monthly_prices, list)

        price_fields_present = any(
            key in attrs for key in ("price_per_person", "price_on_working_days", "price_on_weekends")
        )
        per_person = _to_decimal(attrs.get("price_per_person")) if "price_per_person" in attrs else None
        working = _to_decimal(attrs.get("price_on_working_days")) if "price_on_working_days" in attrs else None
        weekends = _to_decimal(attrs.get("price_on_weekends")) if "price_on_weekends" in attrs else None

        if not is_update:
            if working is None and weekends is None and per_person is None:
                per_person = Decimal("0")
                working = Decimal("0")
                weekends = Decimal("0")
            else:
                if working is None:
                    working = Decimal("0")
                if weekends is None:
                    weekends = working
                if per_person is None:
                    per_person = Decimal("0")

        for val in (per_person, working, weekends):
            if val is not None and val < 0:
                raise serializers.ValidationError(_("Price values must be non-negative"))

        normalized_monthly_prices: list[dict[str, Any]] = []
        if monthly_prices_present:
            for index, item in enumerate(raw_monthly_prices):
                if not isinstance(item, dict):
                    raise serializers.ValidationError({"price": _("Each price item must be an object.")})

                raw_month_from = item.get("month_from")
                raw_month_to = item.get("month_to")
                if not raw_month_from or not raw_month_to:
                    raise serializers.ValidationError({"price": _("month_from and month_to are required for each price item.")})

                try:
                    month_from = date.fromisoformat(str(raw_month_from))
                    month_to = date.fromisoformat(str(raw_month_to))
                except ValueError:
                    raise serializers.ValidationError({"price": _("Invalid month date format in price items.")})

                if month_to < month_from:
                    raise serializers.ValidationError({"price": _("month_to must be greater than or equal to month_from.")})

                item_per_person = _to_decimal(item.get("price_per_person"))
                item_working = _to_decimal(item.get("price_on_working_days"))
                item_weekends = _to_decimal(item.get("price_on_weekends"))

                if item_working is None or item_weekends is None:
                    raise serializers.ValidationError({"price": _("Working days and weekends prices are required for each item.")})
                if item_per_person is None:
                    item_per_person = Decimal("0")

                if item_per_person < 0 or item_working < 0 or item_weekends < 0:
                    raise serializers.ValidationError({"price": _("Price values must be non-negative." )})

                normalized_monthly_prices.append(
                    {
                        "month_from": month_from,
                        "month_to": month_to,
                        "price_per_person": item_per_person,
                        "price_on_working_days": item_working,
                        "price_on_weekends": item_weekends,
                    }
                )

            normalized_monthly_prices.sort(key=lambda item: item["month_from"])
            if len({item["month_from"] for item in normalized_monthly_prices}) != len(normalized_monthly_prices):
                raise serializers.ValidationError({"price": _("Duplicate month_from values are not allowed.")})

        if monthly_prices_present:
            if len(normalized_monthly_prices) != 2:
                raise serializers.ValidationError({"price": _("Exactly 2 monthly prices are required.")})

            current_month_start = date.today().replace(day=1)
            next_month_start = (current_month_start.replace(day=28) + timedelta(days=4)).replace(day=1)
            required_months = {
                (current_month_start.year, current_month_start.month),
                (next_month_start.year, next_month_start.month),
            }
            provided_months = {
                (item["month_from"].year, item["month_from"].month)
                for item in normalized_monthly_prices
            }
            if provided_months != required_months:
                raise serializers.ValidationError({"price": _("Prices must be provided for current and next month.")})
        elif not is_update:
            raise serializers.ValidationError({"price": _("Provide exactly 2 monthly prices.")})

        normalized: dict[str, Any] = {}
        if title:
            normalized["title"] = title
            normalized["title_sort"] = title.lower()
        if "minimum_weekend_day_stay" in attrs:
            normalized["minimum_weekend_day_stay"] = bool(attrs.get("minimum_weekend_day_stay"))
        elif not is_update:
            normalized["minimum_weekend_day_stay"] = False
        if "weekend_only_sunday_inclusive" in attrs:
            normalized["weekend_only_sunday_inclusive"] = bool(attrs.get("weekend_only_sunday_inclusive"))
        elif not is_update:
            normalized["weekend_only_sunday_inclusive"] = False
        if "currency" in attrs:
            normalized["currency"] = attrs.get("currency") or "UZS"
        elif not is_update:
            normalized["currency"] = "UZS"
        if "img" in attrs:
            image_value = attrs.get("img")
            if isinstance(image_value, list):
                normalized["img"] = [str(v) for v in image_value if v]
            elif image_value:
                normalized["img"] = [str(image_value)]
            else:
                normalized["img"] = []
        if price_fields_present or not is_update:
            normalized["price_per_person"] = per_person if per_person is not None else Decimal("0")
            normalized["price_on_working_days"] = working if working is not None else Decimal("0")
            normalized["price_on_weekends"] = weekends if weekends is not None else normalized["price_on_working_days"]
        if normalized_monthly_prices:
            normalized["price"] = normalized_monthly_prices
            first_item = normalized_monthly_prices[0]
            normalized["price_per_person"] = first_item["price_per_person"]
            normalized["price_on_working_days"] = first_item["price_on_working_days"]
            normalized["price_on_weekends"] = first_item["price_on_weekends"]
        if attrs.get("property_location") is not None:
            cleaned_location_payload = {}
            for key, value in (attrs.get("property_location") or {}).items():
                if key in {"latitude", "longitude"}:
                    cleaned_location_payload[key] = _parse_decimal_maybe_permissive(value)
                elif key in {"region_id", "district_id"}:
                    cleaned_location_payload[key] = _parse_int_maybe(value)
                else:
                    cleaned_location_payload[key] = None if value in ("", "null", "None", "undefined") else value
            location_serializer = _PropertyLocationInputSerializer(data=cleaned_location_payload, partial=True)
            location_serializer.is_valid(raise_exception=True)
            normalized.update(location_serializer.validated_data)
        if detail_serializer.validated_data:
            normalized.update(detail_serializer.validated_data)
        if "property_services" in attrs:
            normalized["services"] = _normalize_uuid_list(attrs.get("property_services"))
        if "property_room" in attrs:
            room_payload = attrs.get("property_room") or {}
            if not isinstance(room_payload, dict):
                raise serializers.ValidationError({"property_room": _("Expected an object.")})
            for key in ("guests", "rooms", "beds", "bathrooms"):
                if key in room_payload:
                    normalized[key] = _parse_int_maybe(room_payload.get(key))
        # Also handle flat fields at top level (guests, rooms, beds, bathrooms directly)
        for key in ("guests", "rooms", "beds", "bathrooms"):
            if key in attrs:
                normalized[key] = _parse_int_maybe(attrs.get(key))
        if "region_id" in attrs or "region" in attrs:
            normalized["region_id"] = _parse_int_maybe(
                attrs.get("region_id") if attrs.get("region_id") is not None else attrs.get("region")
            )
        if "district_id" in attrs or "district" in attrs:
            normalized["district_id"] = _parse_int_maybe(
                attrs.get("district_id") if attrs.get("district_id") is not None else attrs.get("district")
            )
        prefecture_value = attrs.get("prefecture_id")
        if prefecture_value is not None:
            normalized["prefecture_id"] = str(prefecture_value)

        if not is_update:
            if normalized.get("region_id") is None:
                raise serializers.ValidationError({"region_id": _("This field is required.")})
            if normalized.get("district_id") is None:
                raise serializers.ValidationError({"district_id": _("This field is required.")})

        district_id = normalized.get("district_id")
        prefecture_id = normalized.get("prefecture_id")
        if district_id in {75, 82}:
            if not prefecture_id:
                raise serializers.ValidationError({"prefecture_id": _("This field is required for selected district.")})
            if not is_prefecture_linked_to_district(district_id=district_id, prefecture_guid=prefecture_id):
                raise serializers.ValidationError({"prefecture_id": _("Invalid prefecture for selected district.")})
        elif prefecture_id:
            raise serializers.ValidationError({"prefecture_id": _("Prefecture can be set only for district 75 or 82.")})

        attrs["normalized_values"] = normalized
        return attrs


class CottageUpdateSerializer(CottageCreateSerializer):
    pass
