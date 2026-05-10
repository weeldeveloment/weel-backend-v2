from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from django.core.files.storage import default_storage
from django.utils.translation import gettext_lazy as _
from payment.exchange_rate import to_uzs
from rest_framework import serializers
from shared.date import month_end, month_start

from .apartment_repository import COTTAGE_TYPE_GUID, is_prefecture_linked_to_district

MAX_PRICE_ABS = Decimal("9999999999.99")


def _parse_jsonb_price(raw_price: Any) -> list[dict[str, Any]]:
    """Parse a JSONB `price` column returned by psycopg2.

    Plain psycopg2 cursors return JSONB as a string; this normalises
    both string and already-deserialised list representations.
    """
    if isinstance(raw_price, list):
        return raw_price
    if isinstance(raw_price, str):
        try:
            parsed = json.loads(raw_price)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return []


def _preferred_language(request: Any) -> str:
    if request is None:
        return "uz"
    raw = str(request.headers.get("Accept-Language") or "").strip().lower()
    if raw.startswith("ru"):
        return "ru"
    if raw.startswith("en"):
        return "en"
    return "uz"


def _cottage_type_title(language: str) -> str:
    if language == "ru":
        return "Дача"
    if language == "en":
        return "Cottage"
    return "Dacha"


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

    class Meta:
        ref_name = "CottagePropertyLocationRegionOutput"


class PropertyLocationDistrictOutputSerializer(serializers.Serializer):
    id = serializers.IntegerField(allow_null=True)
    guid = serializers.UUIDField(allow_null=True)
    name = serializers.CharField(allow_blank=True, allow_null=True)

    class Meta:
        ref_name = "CottagePropertyLocationDistrictOutput"


class PropertyLocationPrefectureOutputSerializer(serializers.Serializer):
    id = serializers.CharField(allow_blank=True, allow_null=True)
    name = serializers.CharField(allow_blank=True, allow_null=True)

    class Meta:
        ref_name = "CottagePropertyLocationPrefectureOutput"


class PropertyLocationOutputSerializer(serializers.Serializer):
    latitude = serializers.CharField(allow_blank=True, allow_null=True)
    longitude = serializers.CharField(allow_blank=True, allow_null=True)
    country = serializers.CharField(allow_blank=True, allow_null=True)
    city = serializers.CharField(allow_blank=True, allow_null=True)
    region = PropertyLocationRegionOutputSerializer(allow_null=True)
    district = PropertyLocationDistrictOutputSerializer(allow_null=True)
    prefecture = PropertyLocationPrefectureOutputSerializer(allow_null=True)

    class Meta:
        ref_name = "CottagePropertyLocationOutput"


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


def _validate_price_bounds(value: Decimal | None, field_name: str) -> None:
    if value is None:
        return
    if value < 0:
        raise serializers.ValidationError(_(f"{field_name} must be non-negative."))
    if value > MAX_PRICE_ABS:
        raise serializers.ValidationError(
            _(f"{field_name} must be less than or equal to {MAX_PRICE_ABS}.")
        )


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
            raise serializers.ValidationError(
                _("Property services must contain valid UUID values.")
            )
    return normalized


class _PropertyLocationInputSerializer(serializers.Serializer):
    latitude = serializers.DecimalField(
        max_digits=18, decimal_places=8, required=False, allow_null=True
    )
    longitude = serializers.DecimalField(
        max_digits=18, decimal_places=8, required=False, allow_null=True
    )
    country = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    city = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    region_id = serializers.IntegerField(required=False, allow_null=True)
    district_id = serializers.IntegerField(required=False, allow_null=True)
    prefecture_id = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )

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
    price_per_person = serializers.DecimalField(
        max_digits=18, decimal_places=2, allow_null=True
    )
    price_on_working_days = serializers.DecimalField(
        max_digits=18, decimal_places=2, allow_null=True
    )
    price_on_weekends = serializers.DecimalField(
        max_digits=18, decimal_places=2, allow_null=True
    )
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
    beds = serializers.IntegerField(allow_null=True)
    bathrooms = serializers.IntegerField(allow_null=True)
    property_room = serializers.DictField(required=False)
    average_rating = serializers.FloatField(allow_null=True)
    is_favorite = serializers.BooleanField()
    is_allowed_corporate = serializers.BooleanField()
    created_at = serializers.DateTimeField()
    property_type_id = serializers.UUIDField()
    property_type = serializers.DictField()
    price = serializers.ListField(required=False, allow_empty=True)

    def to_representation(self, instance):
        request = self.context.get("request")
        row = dict(instance)
        row["img"] = _build_media_url(request, row.get("img"))
        row_currency = row.get("currency")
        row["price_per_person"] = _convert_price_for_output(
            row.get("price_per_person"), row_currency
        )
        row["price_on_working_days"] = _convert_price_for_output(
            row.get("price_on_working_days"), row_currency
        )
        row["price_on_weekends"] = _convert_price_for_output(
            row.get("price_on_weekends"), row_currency
        )
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
        row["guests"] = _parse_int_maybe(row.get("guests"))
        row["rooms"] = _parse_int_maybe(row.get("rooms"))
        row["beds"] = _parse_int_maybe(row.get("beds"))
        row["bathrooms"] = _parse_int_maybe(row.get("bathrooms"))
        row["property_room"] = {
            "guests": _parse_int_maybe(row.get("guests")),
            "rooms": _parse_int_maybe(row.get("rooms")),
            "beds": _parse_int_maybe(row.get("beds")),
            "bathrooms": _parse_int_maybe(row.get("bathrooms")),
        }
        lang = _preferred_language(request)
        row["property_type_id"] = str(COTTAGE_TYPE_GUID)
        row["property_type"] = {
            "guid": str(COTTAGE_TYPE_GUID),
            "title": _cottage_type_title(lang),
        }
        favorites = _favorite_guid_set(self.context)
        row["is_favorite"] = str(row.get("guid")) in favorites
        row["price"] = _parse_jsonb_price(row.get("price"))
        return super().to_representation(row)


class CottagePartnerListSerializer(CottageListSerializer):
    verification_status = serializers.CharField(allow_blank=True, allow_null=True)


class CottagePartnerUserSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    role = serializers.CharField(allow_blank=True, allow_null=True)
    first_name = serializers.CharField(allow_blank=True, allow_null=True)
    last_name = serializers.CharField(allow_blank=True, allow_null=True)
    phone_number = serializers.CharField(allow_blank=True, allow_null=True)
    email = serializers.CharField(allow_blank=True, allow_null=True)
    username = serializers.CharField(allow_blank=True, allow_null=True)
    avatar = serializers.CharField(allow_blank=True, allow_null=True)
    is_active = serializers.BooleanField()
    is_verified = serializers.BooleanField()

    class Meta:
        ref_name = "CottagePartnerUser"


class CottagePartnerUserUpdateSerializer(serializers.Serializer):
    id = serializers.IntegerField(required=False, allow_null=True)
    role = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    first_name = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    last_name = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    phone_number = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    email = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    username = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    avatar = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    is_active = serializers.BooleanField(required=False)
    is_verified = serializers.BooleanField(required=False)

    class Meta:
        ref_name = "CottagePartnerUserUpdate"


class CottageAdminListSerializer(CottagePartnerListSerializer):
    is_verified = serializers.BooleanField(read_only=True)
    is_archived = serializers.BooleanField(read_only=True)
    partner_user = CottagePartnerUserSerializer(allow_null=True, read_only=True)

    def to_representation(self, instance):
        row = dict(instance)
        partner_payload = row.get("partner_user")
        if isinstance(partner_payload, dict):
            row["partner_user"] = partner_payload
        else:
            row["partner_user"] = None
        data = super().to_representation(row)
        data["is_verified"] = bool(row.get("is_verified"))
        data["is_archived"] = bool(row.get("is_archived"))
        data["partner_user"] = row.get("partner_user")
        return data


class CottageDetailSerializer(serializers.Serializer):
    guid = serializers.UUIDField()
    title = serializers.CharField()
    img = serializers.ListField(child=serializers.CharField(), allow_empty=True)
    created_at = serializers.DateTimeField()
    currency = serializers.CharField(allow_blank=True, allow_null=True)
    price_per_person = serializers.DecimalField(
        max_digits=18, decimal_places=2, allow_null=True
    )
    price_on_working_days = serializers.DecimalField(
        max_digits=18, decimal_places=2, allow_null=True
    )
    price_on_weekends = serializers.DecimalField(
        max_digits=18, decimal_places=2, allow_null=True
    )
    description = serializers.CharField(allow_blank=True, allow_null=True)
    comment_count = serializers.IntegerField()
    average_rating = serializers.FloatField(allow_null=True)
    is_favorite = serializers.BooleanField()
    property_services = serializers.ListField()
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
    guests = serializers.IntegerField(allow_null=True)
    rooms = serializers.IntegerField(allow_null=True)
    beds = serializers.IntegerField(allow_null=True)
    bathrooms = serializers.IntegerField(allow_null=True)
    property_room = serializers.DictField(required=False)
    price = serializers.ListField(required=False, allow_empty=True)

    def _resolve_description(self, row: dict[str, Any]) -> str:
        request = self.context.get("request")
        lang = ""
        if request is not None:
            lang = str(request.query_params.get("lang") or "").strip().lower()
            if not lang:
                header = (
                    str(request.headers.get("Accept-Language") or "").strip().lower()
                )
                if header:
                    lang = header.split(",")[0].split("-")[0]
        if lang not in {"en", "ru", "uz"}:
            lang = "en"
        value = row.get(f"description_{lang}")
        if value:
            return str(value)
        return str(
            row.get("description_en")
            or row.get("description_ru")
            or row.get("description_uz")
            or ""
        )

    def to_representation(self, instance):
        request = self.context.get("request")
        row = dict(instance)
        row["img"] = _build_media_url(request, row.get("img"))
        row_currency = row.get("currency")
        row["price_per_person"] = _convert_price_for_output(
            row.get("price_per_person"), row_currency
        )
        row["price_on_working_days"] = _convert_price_for_output(
            row.get("price_on_working_days"), row_currency
        )
        row["price_on_weekends"] = _convert_price_for_output(
            row.get("price_on_weekends"), row_currency
        )
        row["description"] = self._resolve_description(row)
        row["comment_count"] = int(
            row.get("review_count") or row.get("comment_count") or 0
        )
        favorites = _favorite_guid_set(self.context)
        row["is_favorite"] = str(row.get("guid")) in favorites
        row["property_services"] = row.get("services") or []
        row["property_location"] = _build_property_location(row)
        row["guests"] = _parse_int_maybe(row.get("guests"))
        row["rooms"] = _parse_int_maybe(row.get("rooms"))
        row["beds"] = _parse_int_maybe(row.get("beds"))
        row["bathrooms"] = _parse_int_maybe(row.get("bathrooms"))
        row["property_room"] = {
            "guests": _parse_int_maybe(row.get("guests")),
            "rooms": _parse_int_maybe(row.get("rooms")),
            "beds": _parse_int_maybe(row.get("beds")),
            "bathrooms": _parse_int_maybe(row.get("bathrooms")),
        }
        row["price"] = _parse_jsonb_price(row.get("price"))
        return super().to_representation(row)


class CottageCreateSerializer(serializers.Serializer):
    title = serializers.CharField(required=False, allow_blank=True)
    currency = serializers.ChoiceField(required=False, choices=["USD", "UZS"])
    weekend_only_sunday_inclusive = serializers.BooleanField(
        required=False, default=False
    )
    price_per_person = serializers.DecimalField(
        max_digits=18, decimal_places=2, required=False, allow_null=True
    )
    price_on_working_days = serializers.DecimalField(
        max_digits=18, decimal_places=2, required=False, allow_null=True
    )
    price_on_weekends = serializers.DecimalField(
        max_digits=18, decimal_places=2, required=False, allow_null=True
    )
    month_from = serializers.DateField(required=False, allow_null=True)
    month_to = serializers.DateField(required=False, allow_null=True)
    next_month_from = serializers.DateField(required=False, allow_null=True)
    next_month_to = serializers.DateField(required=False, allow_null=True)
    latitude = serializers.CharField(required=True, allow_blank=True, allow_null=True)
    longitude = serializers.CharField(required=True, allow_blank=True, allow_null=True)
    country = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    city = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    region_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    district_id = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    prefecture_id = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    description_en = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    description_ru = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    description_uz = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    check_in = serializers.TimeField(required=False, allow_null=True)
    check_out = serializers.TimeField(required=False, allow_null=True)
    is_allowed_alcohol = serializers.BooleanField(required=False)
    is_allowed_corporate = serializers.BooleanField(required=False)
    is_allowed_pets = serializers.BooleanField(required=False)
    is_quiet_hours = serializers.BooleanField(required=False)
    services = serializers.ListField(required=False, allow_empty=True)
    guests = serializers.IntegerField(required=False, allow_null=True)
    rooms = serializers.IntegerField(required=False, allow_null=True)
    beds = serializers.IntegerField(required=False, allow_null=True)
    bathrooms = serializers.IntegerField(required=False, allow_null=True)
    img = serializers.JSONField(required=False)
    price = serializers.ListField(required=False, allow_empty=True)

    def validate(self, attrs):
        is_update = bool(self.context.get("is_update"))
        is_admin = bool(self.context.get("is_admin"))

        title = (attrs.get("title") or "").strip()
        if not is_update and not title:
            raise serializers.ValidationError({"title": _("This field is required.")})

        price_fields_present = any(
            key in attrs
            for key in (
                "price_per_person",
                "price_on_working_days",
                "price_on_weekends",
            )
        )
        per_person = (
            _to_decimal(attrs.get("price_per_person"))
            if "price_per_person" in attrs
            else None
        )
        working = (
            _to_decimal(attrs.get("price_on_working_days"))
            if "price_on_working_days" in attrs
            else None
        )
        weekends = (
            _to_decimal(attrs.get("price_on_weekends"))
            if "price_on_weekends" in attrs
            else None
        )

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

        _validate_price_bounds(per_person, "price_per_person")
        _validate_price_bounds(working, "price_on_working_days")
        _validate_price_bounds(weekends, "price_on_weekends")

        m1f, m1t, m2f, m2t = (
            attrs.get("month_from"),
            attrs.get("month_to"),
            attrs.get("next_month_from"),
            attrs.get("next_month_to"),
        )
        flat_months_complete = all(x is not None for x in (m1f, m1t, m2f, m2t))
        flat_months_any = any(x is not None for x in (m1f, m1t, m2f, m2t))
        if flat_months_any and not flat_months_complete:
            raise serializers.ValidationError(
                _(
                    "month_from, month_to, next_month_from, and next_month_to must all be set together, "
                    "or omit all four to use default current and next calendar month."
                )
            )

        raw_price_list = attrs.get("price")
        list_nonempty = isinstance(raw_price_list, list) and len(raw_price_list) > 0
        if list_nonempty and flat_months_complete:
            raise serializers.ValidationError(
                {
                    "price": _(
                        "Do not send `price` together with month_from/month_to/next_month_*; use flat month fields only."
                    )
                }
            )

        if list_nonempty:
            raw_monthly_prices = raw_price_list
            monthly_prices_present = True
        elif flat_months_complete:
            raw_monthly_prices = [
                {"month_from": m1f, "month_to": m1t},
                {"month_from": m2f, "month_to": m2t},
            ]
            monthly_prices_present = True
        elif raw_price_list is None or (isinstance(raw_price_list, list) and len(raw_price_list) == 0):
            raw_monthly_prices = None
            monthly_prices_present = False
        else:
            raise serializers.ValidationError({"price": _("Must be a list of price objects.")})

        normalized_monthly_prices: list[dict[str, Any]] = []
        if monthly_prices_present:
            for item in raw_monthly_prices:
                if not isinstance(item, dict):
                    raise serializers.ValidationError(
                        {"price": _("Each price item must be an object.")}
                    )

                raw_month_from = item.get("month_from")
                raw_month_to = item.get("month_to")
                if not raw_month_from or not raw_month_to:
                    raise serializers.ValidationError(
                        {
                            "price": _(
                                "month_from and month_to are required for each price item."
                            )
                        }
                    )

                try:
                    raw_mf = str(raw_month_from).split("T", 1)[0]
                    raw_mt = str(raw_month_to).split("T", 1)[0]
                    parsed_from = date.fromisoformat(raw_mf)
                    parsed_to = date.fromisoformat(raw_mt)
                except ValueError:
                    raise serializers.ValidationError(
                        {"price": _("Invalid month date format in price items.")}
                    )

                if month_start(parsed_from) != month_start(parsed_to):
                    raise serializers.ValidationError(
                        {
                            "price": _(
                                "month_from and month_to must be in the same calendar month."
                            )
                        }
                    )
                month_from = month_start(parsed_from)
                month_to = month_end(month_from)
                if parsed_to < parsed_from:
                    raise serializers.ValidationError(
                        {
                            "price": _(
                                "month_to must be greater than or equal to month_from."
                            )
                        }
                    )

                item_per_person = _to_decimal(item.get("price_per_person"))
                item_working = _to_decimal(item.get("price_on_working_days"))
                item_weekends = _to_decimal(item.get("price_on_weekends"))

                if item_per_person is None:
                    item_per_person = per_person
                if item_working is None:
                    item_working = working
                if item_weekends is None:
                    item_weekends = weekends
                if item_weekends is None and item_working is not None:
                    item_weekends = item_working

                if item_working is None or item_weekends is None:
                    raise serializers.ValidationError(
                        {
                            "price": _(
                                "Working days and weekend prices are required in each item "
                                "or via top-level price_on_working_days / price_on_weekends."
                            )
                        }
                    )
                if item_per_person is None:
                    item_per_person = Decimal("0")

                _validate_price_bounds(item_per_person, "price.price_per_person")
                _validate_price_bounds(item_working, "price.price_on_working_days")
                _validate_price_bounds(item_weekends, "price.price_on_weekends")

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
            if len({item["month_from"] for item in normalized_monthly_prices}) != len(
                normalized_monthly_prices
            ):
                raise serializers.ValidationError(
                    {"price": _("Duplicate month_from values are not allowed.")}
                )

        if monthly_prices_present:
            if len(normalized_monthly_prices) != 2:
                raise serializers.ValidationError(
                    {"price": _("Exactly 2 monthly prices are required.")}
                )

            current_month_start = date.today().replace(day=1)
            next_month_start = (
                current_month_start.replace(day=28) + timedelta(days=4)
            ).replace(day=1)
            required_months = {
                (current_month_start.year, current_month_start.month),
                (next_month_start.year, next_month_start.month),
            }
            provided_months = {
                (item["month_from"].year, item["month_from"].month)
                for item in normalized_monthly_prices
            }
            if provided_months != required_months:
                raise serializers.ValidationError(
                    {"price": _("Prices must be provided for current and next month.")}
                )
        elif not is_update:
            pp = per_person if per_person is not None else Decimal("0")
            wd = working if working is not None else Decimal("0")
            we = weekends if weekends is not None else wd
            current_month_start = date.today().replace(day=1)
            next_month_start = (
                current_month_start.replace(day=28) + timedelta(days=4)
            ).replace(day=1)
            normalized_monthly_prices = [
                {
                    "month_from": current_month_start,
                    "month_to": month_end(current_month_start),
                    "price_per_person": pp,
                    "price_on_working_days": wd,
                    "price_on_weekends": we,
                },
                {
                    "month_from": next_month_start,
                    "month_to": month_end(next_month_start),
                    "price_per_person": pp,
                    "price_on_working_days": wd,
                    "price_on_weekends": we,
                },
            ]

        normalized: dict[str, Any] = {}
        if title:
            normalized["title"] = title
            normalized["title_sort"] = title.lower()
        if "weekend_only_sunday_inclusive" in attrs:
            normalized["weekend_only_sunday_inclusive"] = bool(
                attrs.get("weekend_only_sunday_inclusive")
            )
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
            normalized["price_per_person"] = (
                per_person if per_person is not None else Decimal("0")
            )
            normalized["price_on_working_days"] = (
                working if working is not None else Decimal("0")
            )
            normalized["price_on_weekends"] = (
                weekends
                if weekends is not None
                else normalized["price_on_working_days"]
            )
        if normalized_monthly_prices:
            normalized["price"] = normalized_monthly_prices
            first_item = normalized_monthly_prices[0]
            normalized["price_per_person"] = first_item["price_per_person"]
            normalized["price_on_working_days"] = first_item["price_on_working_days"]
            normalized["price_on_weekends"] = first_item["price_on_weekends"]
        location_payload: dict[str, Any] = {}
        for key in (
            "latitude",
            "longitude",
            "country",
            "city",
            "region_id",
            "district_id",
            "prefecture_id",
        ):
            if key in attrs:
                location_payload[key] = attrs[key]
        if location_payload:
            cleaned_location = {}
            for key, value in location_payload.items():
                if key in {"latitude", "longitude"}:
                    cleaned_location[key] = _parse_decimal_maybe_permissive(value)
                elif key in {"region_id", "district_id"}:
                    cleaned_location[key] = _parse_int_maybe(value)
                else:
                    cleaned_location[key] = (
                        None if value in ("", "null", "None", "undefined") else value
                    )
            location_serializer = _PropertyLocationInputSerializer(
                data=cleaned_location, partial=True
            )
            location_serializer.is_valid(raise_exception=True)
            normalized.update(location_serializer.validated_data)

        for key in (
            "description_en",
            "description_ru",
            "description_uz",
            "check_in",
            "check_out",
            "is_allowed_alcohol",
            "is_allowed_corporate",
            "is_allowed_pets",
            "is_quiet_hours",
        ):
            if key in attrs:
                normalized[key] = attrs[key]

        if "services" in attrs:
            normalized["services"] = _normalize_uuid_list(attrs.get("services"))

        for key in ("guests", "rooms", "beds", "bathrooms"):
            if key in attrs:
                normalized[key] = _parse_int_maybe(attrs.get(key))

        if "region_id" in attrs:
            normalized["region_id"] = _parse_int_maybe(attrs.get("region_id"))
        if "district_id" in attrs:
            normalized["district_id"] = _parse_int_maybe(attrs.get("district_id"))
        prefecture_value = attrs.get("prefecture_id")
        if prefecture_value is not None:
            normalized["prefecture_id"] = str(prefecture_value)

        district_id = normalized.get("district_id")
        prefecture_id = normalized.get("prefecture_id")
        if not is_admin:
            if district_id in {75, 82}:
                if not prefecture_id:
                    raise serializers.ValidationError(
                        {
                            "prefecture_id": _(
                                "This field is required for selected district."
                            )
                        }
                    )
                if not is_prefecture_linked_to_district(
                    district_id=district_id, prefecture_guid=prefecture_id
                ):
                    raise serializers.ValidationError(
                        {
                            "prefecture_id": _(
                                "Invalid prefecture for selected district."
                            )
                        }
                    )
            elif prefecture_id:
                raise serializers.ValidationError(
                    {
                        "prefecture_id": _(
                            "Prefecture can be set only for district 75 or 82."
                        )
                    }
                )

        attrs["normalized_values"] = normalized
        return attrs


class CottageUpdateSerializer(CottageCreateSerializer):
    pass


class CottageAdminUpdateSerializer(CottageUpdateSerializer):
    """Admin-only partial updater that permits mutating every cottage field.

    Extends the partner update flow with verification/archival flags and
    owner reassignment that only administrators are allowed to touch.
    """

    is_verified = serializers.BooleanField(required=False)
    verified_at = serializers.DateTimeField(required=False, allow_null=True)
    verification_status = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    is_archived = serializers.BooleanField(required=False)
    is_recommended = serializers.BooleanField(required=False)
    services = serializers.ListField(required=False, allow_empty=True)
    img = serializers.ListField(
        child=serializers.CharField(), required=False, allow_empty=True
    )
    partner_user = CottagePartnerUserUpdateSerializer(required=False, allow_null=True)
    verified_by_user_id = serializers.IntegerField(required=False, allow_null=True)
    comment_count = serializers.IntegerField(required=False, min_value=0)
    legacy_property_id = serializers.IntegerField(required=False, allow_null=True)

    _ADMIN_ONLY_FIELDS = (
        "is_verified",
        "verified_at",
        "verification_status",
        "is_archived",
        "is_recommended",
        "partner_user",
        "verified_by_user_id",
        "comment_count",
        "legacy_property_id",
    )

    def get_fields(self):
        return super().get_fields()

    def validate(self, attrs):
        admin_overrides = {
            key: attrs.get(key) for key in self._ADMIN_ONLY_FIELDS if key in attrs
        }
        attrs = super().validate(attrs)
        normalized = attrs.get("normalized_values") or {}
        if "services" in attrs:
            normalized["services"] = _normalize_uuid_list(attrs.get("services"))
        for key, value in admin_overrides.items():
            if key == "verification_status":
                if value in (None, ""):
                    continue
                normalized[key] = str(value).strip().lower()
            elif key == "partner_user":
                if value in (None, "", "null", "None", "undefined"):
                    normalized["partner_user_id"] = None
                elif isinstance(value, dict):
                    partner_id = value.get("id")
                    if partner_id in (None, "", "null", "None", "undefined"):
                        raise serializers.ValidationError(
                            {"partner_user": {"id": _("This field is required.")}}
                        )
                    normalized["partner_user_id"] = int(partner_id)
                else:
                    raise serializers.ValidationError(
                        {"partner_user": _("Expected an object payload.")}
                    )
            else:
                normalized[key] = value
        attrs["normalized_values"] = normalized
        return attrs
