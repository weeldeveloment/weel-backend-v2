from __future__ import annotations

from decimal import InvalidOperation
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.core.files.storage import default_storage
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers

from payment.exchange_rate import to_uzs
from .apartment_repository import (
    APARTMENT_TYPE_GUID,
    is_prefecture_linked_to_district,
    resolve_district_id_by_guid,
    resolve_region_id_by_guid,
)


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
        ref_name = "ApartmentPropertyLocationRegionOutput"


class PropertyLocationDistrictOutputSerializer(serializers.Serializer):
    id = serializers.IntegerField(allow_null=True)
    guid = serializers.UUIDField(allow_null=True)
    name = serializers.CharField(allow_blank=True, allow_null=True)

    class Meta:
        ref_name = "ApartmentPropertyLocationDistrictOutput"


class PropertyLocationPrefectureOutputSerializer(serializers.Serializer):
    id = serializers.CharField(allow_blank=True, allow_null=True)
    name = serializers.CharField(allow_blank=True, allow_null=True)

    class Meta:
        ref_name = "ApartmentPropertyLocationPrefectureOutput"


class PropertyLocationOutputSerializer(serializers.Serializer):
    latitude = serializers.CharField(allow_blank=True, allow_null=True)
    longitude = serializers.CharField(allow_blank=True, allow_null=True)
    country = serializers.CharField(allow_blank=True, allow_null=True)
    city = serializers.CharField(allow_blank=True, allow_null=True)
    region = PropertyLocationRegionOutputSerializer(allow_null=True)
    district = PropertyLocationDistrictOutputSerializer(allow_null=True)
    prefecture = PropertyLocationPrefectureOutputSerializer(allow_null=True)

    class Meta:
        ref_name = "ApartmentPropertyLocationOutput"


def _parse_int_maybe(value: Any) -> int | None:
    if value in (None, "", "null", "None", "undefined"):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _parse_region_id_maybe(value: Any) -> int | None:
    parsed = _parse_int_maybe(value)
    if parsed is not None:
        return parsed
    return resolve_region_id_by_guid(str(value or "").strip())


def _parse_district_id_maybe(value: Any) -> int | None:
    parsed = _parse_int_maybe(value)
    if parsed is not None:
        return parsed
    return resolve_district_id_by_guid(str(value or "").strip())


def _parse_decimal_maybe(value: Any, allow_invalid: bool = False) -> Decimal | None:
    if value in (None, "", "null", "None", "undefined"):
        return None
    amount = _to_decimal(value)
    if amount is None:
        if allow_invalid:
            return None
        raise serializers.ValidationError(_("Invalid numeric value."))
    return amount


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
            raise serializers.ValidationError(_("Services must contain valid UUID values."))
    return normalized


class ApartmentListSerializer(serializers.Serializer):
    guid = serializers.UUIDField()
    title = serializers.CharField()
    img = serializers.ListField(child=serializers.CharField(), allow_empty=True)
    price = serializers.DecimalField(max_digits=18, decimal_places=2, allow_null=True)
    currency = serializers.CharField(allow_blank=True, allow_null=True)
    latitude = serializers.CharField(allow_blank=True, allow_null=True)
    longitude = serializers.CharField(allow_blank=True, allow_null=True)
    country = serializers.CharField(allow_blank=True, allow_null=True)
    city = serializers.CharField(allow_blank=True, allow_null=True)
    property_location = PropertyLocationOutputSerializer(required=False)
    services = serializers.ListField()
    region_id = serializers.IntegerField(allow_null=True)
    district_id = serializers.IntegerField(allow_null=True)
    prefecture_id = serializers.CharField(allow_blank=True, allow_null=True)
    guests = serializers.IntegerField(allow_null=True)
    rooms = serializers.IntegerField(allow_null=True)
    beds = serializers.IntegerField(allow_null=True)
    bathrooms = serializers.IntegerField(allow_null=True)
    apartment_number = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    home_number = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    entrance_number = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    floor_number = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    pass_code = serializers.CharField(allow_blank=True, allow_null=True, required=False)
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
        row["price"] = _convert_price_for_output(row.get("price"), row_currency)
        row["services"] = row.get("services") or []
        row["property_location"] = _build_property_location(row)
        row["guests"] = _parse_int_maybe(row.get("guests"))
        row["rooms"] = _parse_int_maybe(row.get("rooms"))
        row["beds"] = _parse_int_maybe(row.get("beds"))
        row["bathrooms"] = _parse_int_maybe(row.get("bathrooms"))
        row["property_type_id"] = str(APARTMENT_TYPE_GUID)
        row["property_type"] = {
            "guid": str(APARTMENT_TYPE_GUID),
            "title": "Apartment",
        }
        favorites = _favorite_guid_set(self.context)
        row["is_favorite"] = str(row.get("guid")) in favorites
        return super().to_representation(row)


class ApartmentPartnerListSerializer(ApartmentListSerializer):
    verification_status = serializers.CharField(allow_blank=True, allow_null=True)


class ApartmentPartnerUserSerializer(serializers.Serializer):
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
        ref_name = "ApartmentPartnerUser"


class ApartmentPartnerUserUpdateSerializer(serializers.Serializer):
    id = serializers.IntegerField(required=False, allow_null=True)
    role = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    first_name = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    last_name = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    phone_number = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    email = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    username = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    avatar = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    is_active = serializers.BooleanField(required=False)
    is_verified = serializers.BooleanField(required=False)

    class Meta:
        ref_name = "ApartmentPartnerUserUpdate"


class ApartmentAdminListSerializer(ApartmentPartnerListSerializer):
    is_verified = serializers.BooleanField(read_only=True)
    is_archived = serializers.BooleanField(read_only=True)
    partner_user = ApartmentPartnerUserSerializer(allow_null=True, read_only=True)

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


class ApartmentDetailSerializer(serializers.Serializer):
    guid = serializers.UUIDField()
    title = serializers.CharField()
    img = serializers.ListField(child=serializers.CharField(), allow_empty=True)
    created_at = serializers.DateTimeField()
    currency = serializers.CharField(allow_blank=True, allow_null=True)
    price = serializers.DecimalField(max_digits=18, decimal_places=2, allow_null=True)
    minimum_weekend_day_stay = serializers.BooleanField()
    weekend_only_sunday_inclusive = serializers.BooleanField()
    description_en = serializers.CharField(allow_blank=True, allow_null=True)
    description_ru = serializers.CharField(allow_blank=True, allow_null=True)
    description_uz = serializers.CharField(allow_blank=True, allow_null=True)
    comment_count = serializers.IntegerField()
    average_rating = serializers.FloatField(allow_null=True)
    is_favorite = serializers.BooleanField()
    services = serializers.ListField()
    region_id = serializers.IntegerField(allow_null=True)
    district_id = serializers.IntegerField(allow_null=True)
    prefecture_id = serializers.CharField(allow_blank=True, allow_null=True)
    latitude = serializers.CharField(allow_blank=True, allow_null=True)
    longitude = serializers.CharField(allow_blank=True, allow_null=True)
    country = serializers.CharField(allow_blank=True, allow_null=True)
    city = serializers.CharField(allow_blank=True, allow_null=True)
    property_location = PropertyLocationOutputSerializer(required=False)
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

    def to_representation(self, instance):
        request = self.context.get("request")
        row = dict(instance)
        row["img"] = _build_media_url(request, row.get("img"))
        row_currency = row.get("currency")
        row["price"] = _convert_price_for_output(row.get("price"), row_currency)
        # Keep Uzbek as the default/fallback description value.
        if not row.get("description_uz"):
            row["description_uz"] = (
                row.get("description_en")
                or row.get("description_ru")
                or ""
            )
        row["comment_count"] = int(row.get("comment_count") or 0)
        favorites = _favorite_guid_set(self.context)
        row["is_favorite"] = str(row.get("guid")) in favorites
        row["services"] = row.get("services") or []
        row["property_location"] = _build_property_location(row)
        return super().to_representation(row)


class ApartmentCreateSerializer(serializers.Serializer):
    title = serializers.CharField(required=False, allow_blank=True)
    price = serializers.DecimalField(max_digits=18, decimal_places=2, required=False)
    currency = serializers.ChoiceField(required=False, choices=["USD", "UZS"])
    minimum_weekend_day_stay = serializers.BooleanField(required=False, default=False)
    weekend_only_sunday_inclusive = serializers.BooleanField(required=False, default=False)
    property_location = serializers.DictField(required=False)
    property_detail = serializers.DictField(required=False)
    latitude = serializers.DecimalField(max_digits=18, decimal_places=8, required=False, allow_null=True)
    longitude = serializers.DecimalField(max_digits=18, decimal_places=8, required=False, allow_null=True)
    country = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    city = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    services = serializers.ListField(required=False, allow_empty=True)
    region_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    district_id = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    prefecture_id = serializers.UUIDField(required=False, allow_null=True)
    guests = serializers.IntegerField(required=False, allow_null=True)
    rooms = serializers.IntegerField(required=False, allow_null=True)
    beds = serializers.IntegerField(required=False, allow_null=True)
    bathrooms = serializers.IntegerField(required=False, allow_null=True)
    img = serializers.JSONField(required=False)
    apartment_number = serializers.CharField(required=True, allow_blank=False, allow_null=False)
    home_number = serializers.CharField(required=True, allow_blank=False, allow_null=False)
    entrance_number = serializers.CharField(required=True, allow_blank=False, allow_null=False)
    floor_number = serializers.CharField(required=True, allow_blank=False, allow_null=False)
    pass_code = serializers.CharField(required=True, allow_blank=False, allow_null=False)

    def validate(self, attrs):
        is_update = bool(self.context.get("is_update"))
        is_admin = bool(self.context.get("is_admin"))

        title = (attrs.get("title") or "").strip()
        if not is_update and not title:
            raise serializers.ValidationError({"title": _("This field is required.")})

        price = _to_decimal(attrs.get("price"))
        if not is_update and price is None:
            price = Decimal("0")
        if price is not None and price < 0:
            raise serializers.ValidationError(_("Price must be non-negative"))

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
        if price is not None:
            normalized["price"] = price
        for key in (
            "latitude",
            "longitude",
            "country",
            "city",
            "apartment_number",
            "home_number",
            "entrance_number",
            "floor_number",
            "pass_code",
            "check_in",
            "check_out",
            "is_allowed_alcohol",
            "is_allowed_corporate",
            "is_allowed_pets",
            "is_quiet_hours",
        ):
            if key in attrs:
                normalized[key] = attrs.get(key)
        for key in ("guests", "rooms", "beds", "bathrooms"):
            if key in attrs:
                normalized[key] = _parse_int_maybe(attrs.get(key))
        if "services" in attrs:
            normalized["services"] = _normalize_uuid_list(attrs.get("services"))
        if "property_services" in attrs:
            normalized["services"] = _normalize_uuid_list(attrs.get("property_services"))
        detail_payload = attrs.get("property_detail") or {}
        if detail_payload:
            for key, value in detail_payload.items():
                if key in {"check_in", "check_out"}:
                    try:
                        normalized[key] = value
                    except Exception:
                        pass
                elif key.startswith("is_"):
                    normalized[key] = bool(value)
                elif key in {"description_en", "description_ru", "description_uz", "apartment_number", "home_number", "entrance_number", "floor_number", "pass_code"}:
                    normalized[key] = value
        location_payload = attrs.get("property_location") or {}
        if location_payload:
            for key in ("latitude", "longitude", "country", "city", "region_id", "district_id", "prefecture_id"):
                if key not in location_payload:
                    continue
                value = location_payload.get(key)
                if key in {"latitude", "longitude"}:
                    normalized[key] = _parse_decimal_maybe(value, allow_invalid=is_update)
                elif key in {"region_id", "district_id"}:
                    if key == "region_id":
                        normalized[key] = _parse_region_id_maybe(value)
                    else:
                        normalized[key] = _parse_district_id_maybe(value)
                else:
                    normalized[key] = None if value in ("", "null", "None", "undefined") else value
        if "region_id" in attrs or "region_id" in location_payload:
            normalized["region_id"] = _parse_region_id_maybe(
                attrs.get("region_id") if attrs.get("region_id") is not None else location_payload.get("region_id")
            )
        if "district_id" in attrs or "district_id" in location_payload:
            normalized["district_id"] = _parse_district_id_maybe(
                attrs.get("district_id") if attrs.get("district_id") is not None else location_payload.get("district_id")
            )
        if "prefecture_id" in attrs or "prefecture_id" in location_payload:
            pref_val = attrs.get("prefecture_id") if attrs.get("prefecture_id") is not None else location_payload.get("prefecture_id")
            normalized["prefecture_id"] = str(pref_val) if pref_val not in (None, "", "null", "None", "undefined") else None

        if not is_admin:
            if not is_update:
                if normalized.get("region_id") is None:
                    raise serializers.ValidationError({"region_id": _("This field is required.")})
                if normalized.get("district_id") is None:
                    raise serializers.ValidationError({"district_id": _("This field is required.")})
            else:
                touches_location = any(
                    key in attrs
                    for key in (
                        "region_id",
                        "district_id",
                        "prefecture_id",
                        "latitude",
                        "longitude",
                        "city",
                        "country",
                        "property_location",
                    )
                )
                if touches_location:
                    if normalized.get("region_id") is None:
                        raise serializers.ValidationError({"region_id": _("This field is required.")})
                    if normalized.get("district_id") is None:
                        raise serializers.ValidationError({"district_id": _("This field is required.")})

        district_id = normalized.get("district_id")
        prefecture_id = normalized.get("prefecture_id")
        if not is_admin:
            if district_id in {75, 82}:
                if not prefecture_id:
                    raise serializers.ValidationError({"prefecture_id": _("This field is required for selected district.")})
                if not is_prefecture_linked_to_district(district_id=district_id, prefecture_guid=prefecture_id):
                    raise serializers.ValidationError({"prefecture_id": _("Invalid prefecture for selected district.")})
            elif prefecture_id:
                raise serializers.ValidationError({"prefecture_id": _("Prefecture can be set only for district 75 or 82.")})

        attrs["normalized_values"] = normalized
        return attrs


class ApartmentUpdateSerializer(ApartmentCreateSerializer):
    apartment_number = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    home_number = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    entrance_number = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    floor_number = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    pass_code = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class ApartmentAdminUpdateSerializer(ApartmentUpdateSerializer):
    """Admin-only partial updater that permits mutating every apartment field.

    Extends the partner update flow with verification/archival flags and
    owner reassignment that only administrators are allowed to touch.
    """

    is_verified = serializers.BooleanField(required=False)
    verified_at = serializers.DateTimeField(required=False, allow_null=True)
    verification_status = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    is_archived = serializers.BooleanField(required=False)
    is_recommended = serializers.BooleanField(required=False)
    partner_user = ApartmentPartnerUserUpdateSerializer(required=False, allow_null=True)
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
        fields = super().get_fields()
        # Admin update endpoint should no longer accept nested location payloads.
        fields.pop("property_location", None)
        # Admin update endpoint should no longer accept nested detail payloads.
        fields.pop("property_detail", None)
        return fields

    def validate(self, attrs):
        admin_overrides = {
            key: attrs.get(key) for key in self._ADMIN_ONLY_FIELDS if key in attrs
        }
        attrs = super().validate(attrs)
        normalized = attrs.get("normalized_values") or {}
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
                        raise serializers.ValidationError({"partner_user": {"id": _("This field is required.")}})
                    normalized["partner_user_id"] = int(partner_id)
                else:
                    raise serializers.ValidationError({"partner_user": _("Expected an object payload.")})
            else:
                normalized[key] = value
        attrs["normalized_values"] = normalized
        return attrs
