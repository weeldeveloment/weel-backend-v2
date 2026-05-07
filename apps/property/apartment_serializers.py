from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from django.core.files.storage import default_storage
from django.utils.translation import gettext_lazy as _
from payment.exchange_rate import to_uzs
from rest_framework import serializers

from .apartment_repository import (
    APARTMENT_TYPE_GUID,
    is_prefecture_linked_to_district,
    resolve_district_id_by_guid,
    resolve_region_id_by_guid,
)


def _preferred_language(request: Any) -> str:
    if request is None:
        return "uz"
    raw = str(request.headers.get("Accept-Language") or "").strip().lower()
    if raw.startswith("ru"):
        return "ru"
    if raw.startswith("en"):
        return "en"
    return "uz"


def _apartment_type_title(language: str) -> str:
    if language == "ru":
        return "Квартира"
    if language == "en":
        return "Apartment"
    return "Kvartira"


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
    apartment_number = serializers.CharField(
        allow_blank=True, allow_null=True, required=False
    )
    home_number = serializers.CharField(
        allow_blank=True, allow_null=True, required=False
    )
    entrance_number = serializers.CharField(
        allow_blank=True, allow_null=True, required=False
    )
    floor_number = serializers.CharField(
        allow_blank=True, allow_null=True, required=False
    )
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
        lang = _preferred_language(request)
        row["property_type_id"] = str(APARTMENT_TYPE_GUID)
        row["property_type"] = {
            "guid": str(APARTMENT_TYPE_GUID),
            "title": _apartment_type_title(lang),
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
    guests = serializers.IntegerField(allow_null=True)
    rooms = serializers.IntegerField(allow_null=True)
    beds = serializers.IntegerField(allow_null=True)
    bathrooms = serializers.IntegerField(allow_null=True)

    def to_representation(self, instance):
        request = self.context.get("request")
        row = dict(instance)
        row["img"] = _build_media_url(request, row.get("img"))
        row_currency = row.get("currency")
        row["price"] = _convert_price_for_output(row.get("price"), row_currency)
        # Keep Uzbek as the default/fallback description value.
        if not row.get("description_uz"):
            row["description_uz"] = (
                row.get("description_en") or row.get("description_ru") or ""
            )
        row["comment_count"] = int(row.get("comment_count") or 0)
        favorites = _favorite_guid_set(self.context)
        row["is_favorite"] = str(row.get("guid")) in favorites
        row["services"] = row.get("services") or []
        row["property_location"] = _build_property_location(row)
        row["guests"] = _parse_int_maybe(row.get("guests"))
        row["rooms"] = _parse_int_maybe(row.get("rooms"))
        row["beds"] = _parse_int_maybe(row.get("beds"))
        row["bathrooms"] = _parse_int_maybe(row.get("bathrooms"))
        return super().to_representation(row)


class ApartmentCreateSerializer(serializers.Serializer):
    """
    It is only for partner usage for post requests don't use this serializer function for admin usage.
    """

    title = serializers.CharField(
        required=True,
        allow_blank=False,
        error_messages={
            "required": "Укажите название.",
            "blank": "Название не может быть пустым.",
        },
    )

    price = serializers.DecimalField(
        max_digits=18,
        decimal_places=2,
        required=False,
        min_value=Decimal("0"),
        error_messages={
            "invalid": "Введите корректную цену.",
            "min_value": "Цена не может быть меньше 0.",
        },
    )

    currency = serializers.ChoiceField(
        choices=["USD", "UZS"],
        required=False,
        default="UZS",
        error_messages={
            "invalid_choice": "Выберите корректную валюту.",
        },
    )

    latitude = serializers.DecimalField(
        max_digits=18,
        decimal_places=8,
        required=False,
        allow_null=True,
        error_messages={"invalid": "Некорректная широта."},
    )
    longitude = serializers.DecimalField(
        max_digits=18,
        decimal_places=8,
        required=False,
        allow_null=True,
        error_messages={"invalid": "Некорректная долгота."},
    )

    country = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    city = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    region_id = serializers.IntegerField(
        required=False,
        allow_null=True,
        error_messages={"invalid": "Некорректный регион."},
    )
    district_id = serializers.IntegerField(
        required=False,
        allow_null=True,
        error_messages={"invalid": "Некорректный район."},
    )
    prefecture_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        error_messages={"invalid": "Некорректная префектура."},
    )

    services = serializers.ListField(
        child=serializers.UUIDField(
            error_messages={"invalid": "Некорректный формат услуги."}
        ),
        required=False,
        allow_empty=True,
    )

    img = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )

    apartment_number = serializers.IntegerField(
        required=True,
        error_messages={"required": "Укажите номер квартиры."},
    )
    home_number = serializers.IntegerField(
        required=True,
        error_messages={"required": "Укажите номер дома."},
    )
    entrance_number = serializers.IntegerField(
        required=True,
        error_messages={"required": "Укажите подъезд."},
    )
    floor_number = serializers.IntegerField(
        required=True,
        error_messages={"required": "Укажите этаж."},
    )
    pass_code = serializers.IntegerField(
        required=True,
        error_messages={"required": "Укажите код доступа."},
    )

    description_ru = serializers.CharField(
        required=True,
        allow_blank=False,
        error_messages={
            "required": "Добавьте описание на русском языке.",
            "blank": "Описание на русском языке не может быть пустым.",
        },
    )
    description_uz = serializers.CharField(
        required=True,
        allow_blank=False,
        error_messages={
            "required": "Добавьте описание на узбекском языке.",
            "blank": "Описание на узбекском языке не может быть пустым.",
        },
    )
    description_en = serializers.CharField(
        required=False,
        allow_blank=False,
        error_messages={
            "blank": "Описание на английском языке не может быть пустым.",
        },
    )

    check_in = serializers.TimeField(
        format="%H:%M:%S",
        required=True,
        error_messages={
            "required": "Укажите время заезда.",
            "invalid": "Неверный формат времени (чч:мм:сс).",
        },
    )
    check_out = serializers.TimeField(
        format="%H:%M:%S",
        required=True,
        error_messages={
            "required": "Укажите время выезда.",
            "invalid": "Неверный формат времени (чч:мм:сс).",
        },
    )

    is_allowed_alcohol = serializers.BooleanField(required=True)
    is_allowed_corporate = serializers.BooleanField(required=True)
    is_allowed_pets = serializers.BooleanField(required=True)
    is_quiet_hours = serializers.BooleanField(required=True)

    guests = serializers.IntegerField(
        required=True,
        error_messages={"required": "Укажите количество гостей."},
    )
    rooms = serializers.IntegerField(
        required=True,
        error_messages={"required": "Укажите количество комнат."},
    )
    beds = serializers.IntegerField(
        required=True,
        error_messages={"required": "Укажите количество кроватей."},
    )
    bathrooms = serializers.IntegerField(
        required=True,
        error_messages={"required": "Укажите количество ванных комнат."},
    )

    def validate_title(self, value):
        return value.strip()

    def validate(self, attrs):
        prepared: dict[str, Any] = {}

        title = str(attrs.get("title") or "").strip()
        if not title:
            raise serializers.ValidationError({"title": "Укажите название."})
        prepared["title"] = title
        prepared["title_sort"] = title.lower()

        price = attrs.get("price")
        if price is None:
            prepared["price"] = Decimal("0")
        else:
            prepared["price"] = price

        prepared["currency"] = str(attrs.get("currency") or "UZS")

        img_value = attrs.get("img")
        if isinstance(img_value, list):
            prepared["img"] = [str(v) for v in img_value if v]
        elif img_value:
            prepared["img"] = [str(img_value)]
        else:
            prepared["img"] = []

        prepared["comment_count"] = 0

        for key in ("latitude", "longitude", "country", "city"):
            if key in attrs:
                prepared[key] = attrs.get(key)

        if "region_id" in attrs:
            prepared["region_id"] = _parse_int_maybe(attrs.get("region_id"))
        if "district_id" in attrs:
            prepared["district_id"] = _parse_int_maybe(attrs.get("district_id"))
        if "prefecture_id" in attrs:
            prefecture_id = attrs.get("prefecture_id")
            prepared["prefecture_id"] = (
                str(prefecture_id)
                if prefecture_id not in (None, "", "null", "None", "undefined")
                else None
            )

        for key in ("description_en", "description_ru", "description_uz"):
            if key in attrs:
                prepared[key] = attrs.get(key)

        for key in ("check_in", "check_out"):
            if key in attrs:
                prepared[key] = attrs.get(key)

        for key in (
            "is_allowed_alcohol",
            "is_allowed_corporate",
            "is_allowed_pets",
            "is_quiet_hours",
        ):
            if key in attrs:
                prepared[key] = bool(attrs.get(key))

        for key in (
            "apartment_number",
            "home_number",
            "entrance_number",
            "floor_number",
            "pass_code",
        ):
            if key in attrs:
                prepared[key] = attrs.get(key)

        for key in ("guests", "rooms", "beds", "bathrooms"):
            if key in attrs:
                prepared[key] = _parse_int_maybe(attrs.get(key))

        services = attrs.get("services")
        if services is not None:
            prepared["services"] = [str(s) for s in services if s is not None]
        else:
            prepared["services"] = []

        district_id = prepared.get("district_id")
        prefecture_id = prepared.get("prefecture_id")
        if district_id in {75, 82}:
            if not prefecture_id:
                raise serializers.ValidationError(
                    {"prefecture_id": "Укажите префектуру для выбранного района."}
                )
            if not is_prefecture_linked_to_district(
                district_id=district_id, prefecture_guid=prefecture_id
            ):
                raise serializers.ValidationError(
                    {"prefecture_id": "Выбранная префектура не соответствует району."}
                )
        elif prefecture_id:
            raise serializers.ValidationError(
                {"prefecture_id": "Префектура недоступна для выбранного района."}
            )

        attrs["values"] = prepared
        return attrs


class ApartmentUpdateSerializer(serializers.Serializer):
    # ===== Base fields =====
    title = serializers.CharField(required=False, allow_blank=False)

    price = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        min_value=Decimal("0"),
        error_messages={
            "min_value": "Цена не может быть меньше 0.",
            "invalid": "Введите корректную цену.",
        },
    )

    currency = serializers.ChoiceField(
        choices=["USD", "UZS"],
        required=False,
        error_messages={"invalid_choice": "Выберите корректную валюту."},
    )

    latitude = serializers.DecimalField(
        max_digits=17, decimal_places=14, required=False, allow_null=True
    )
    longitude = serializers.DecimalField(
        max_digits=17, decimal_places=14, required=False, allow_null=True
    )

    city = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    country = serializers.CharField(required=False, allow_blank=True, allow_null=True)

    region_id = serializers.IntegerField(required=False, allow_null=True)
    district_id = serializers.IntegerField(required=False, allow_null=True)
    prefecture_id = serializers.UUIDField(required=False, allow_null=True)

    services = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        allow_empty=True,
    )

    img = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )

    description_ru = serializers.CharField(required=False, allow_blank=False)
    description_uz = serializers.CharField(required=False, allow_blank=False)
    description_en = serializers.CharField(required=False, allow_blank=False)

    check_in = serializers.TimeField(required=False)
    check_out = serializers.TimeField(required=False)

    is_allowed_alcohol = serializers.BooleanField(required=False)
    is_allowed_corporate = serializers.BooleanField(required=False)
    is_allowed_pets = serializers.BooleanField(required=False)
    is_quiet_hours = serializers.BooleanField(required=False)

    apartment_number = serializers.CharField(required=False, allow_blank=True)
    home_number = serializers.CharField(required=False, allow_blank=True)
    entrance_number = serializers.CharField(required=False, allow_blank=True)
    floor_number = serializers.CharField(required=False, allow_blank=True)
    pass_code = serializers.CharField(required=False, allow_blank=True)

    guests = serializers.IntegerField(required=False)
    rooms = serializers.IntegerField(required=False)
    beds = serializers.IntegerField(required=False)
    bathrooms = serializers.IntegerField(required=False)

    def validate_title(self, value):
        return value.strip()

    def validate(self, attrs):
        prepared: dict[str, Any] = {}

        if "title" in attrs:
            title = str(attrs.get("title") or "").strip()
            if title:
                prepared["title"] = title
                prepared["title_sort"] = title.lower()

        if "price" in attrs:
            prepared["price"] = attrs.get("price")

        if "currency" in attrs:
            prepared["currency"] = str(attrs.get("currency") or "UZS")

        if "img" in attrs:
            img_value = attrs.get("img")
            if isinstance(img_value, list):
                prepared["img"] = [str(v) for v in img_value if v]
            elif img_value:
                prepared["img"] = [str(img_value)]
            else:
                prepared["img"] = []

        for key in ("latitude", "longitude", "country", "city"):
            if key in attrs:
                prepared[key] = attrs.get(key)

        if "region_id" in attrs:
            prepared["region_id"] = _parse_int_maybe(attrs.get("region_id"))
        if "district_id" in attrs:
            prepared["district_id"] = _parse_int_maybe(attrs.get("district_id"))
        if "prefecture_id" in attrs:
            prefecture_id = attrs.get("prefecture_id")
            prepared["prefecture_id"] = (
                str(prefecture_id)
                if prefecture_id not in (None, "", "null", "None", "undefined")
                else None
            )

        for key in ("description_en", "description_ru", "description_uz"):
            if key in attrs:
                prepared[key] = attrs.get(key)

        for key in ("check_in", "check_out"):
            if key in attrs:
                prepared[key] = attrs.get(key)

        for key in (
            "is_allowed_alcohol",
            "is_allowed_corporate",
            "is_allowed_pets",
            "is_quiet_hours",
        ):
            if key in attrs:
                prepared[key] = bool(attrs.get(key))

        for key in (
            "apartment_number",
            "home_number",
            "entrance_number",
            "floor_number",
            "pass_code",
        ):
            if key in attrs:
                prepared[key] = attrs.get(key)

        for key in ("guests", "rooms", "beds", "bathrooms"):
            if key in attrs:
                prepared[key] = _parse_int_maybe(attrs.get(key))

        if "services" in attrs:
            services = attrs.get("services")
            prepared["services"] = (
                [str(s) for s in services if s is not None] if services else []
            )

        district_id = prepared.get("district_id")
        prefecture_id = prepared.get("prefecture_id")
        if district_id in {75, 82}:
            if not prefecture_id:
                raise serializers.ValidationError(
                    {"prefecture_id": "Укажите префектуру для выбранного района."}
                )
            if not is_prefecture_linked_to_district(
                district_id=district_id, prefecture_guid=prefecture_id
            ):
                raise serializers.ValidationError(
                    {"prefecture_id": "Выбранная префектура не соответствует району."}
                )
        elif prefecture_id:
            raise serializers.ValidationError(
                {"prefecture_id": "Префектура недоступна для выбранного района."}
            )

        attrs["values"] = prepared
        return attrs

    def update(self, instance, validated_data):
        for key, value in validated_data.items():
            setattr(instance, key, value)

        if "title" in validated_data:
            instance.title_sort = instance.title.lower()

        instance.save()
        return instance


class ApartmentAdminUpdateSerializer(ApartmentUpdateSerializer):
    """Admin-only partial updater that permits mutating every apartment field.

    Extends the partner update flow with verification/archival flags and
    owner reassignment that only administrators are allowed to touch.
    """

    is_verified = serializers.BooleanField(required=False)
    verified_at = serializers.DateTimeField(required=False, allow_null=True)
    verification_status = serializers.CharField(required=False, allow_blank=True)
    is_archived = serializers.BooleanField(required=False)
    is_recommended = serializers.BooleanField(required=False)
    partner_user_id = serializers.IntegerField(required=False, allow_null=True)
    verified_by_user_id = serializers.IntegerField(required=False, allow_null=True)
    comment_count = serializers.IntegerField(
        required=False,
        min_value=0,
        error_messages={"min_value": "Количество комментариев не может быть меньше 0."},
    )
    legacy_property_id = serializers.IntegerField(required=False, allow_null=True)

    _ADMIN_ONLY_FIELDS = (
        "is_verified",
        "verified_at",
        "verification_status",
        "is_archived",
        "is_recommended",
        "partner_user_id",
        "verified_by_user_id",
        "comment_count",
        "legacy_property_id",
    )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        prepared = attrs.get("values") or {}

        for key in self._ADMIN_ONLY_FIELDS:
            if key in attrs:
                if key == "verification_status":
                    value = attrs.get(key)
                    if value not in (None, ""):
                        prepared[key] = str(value).strip().lower()
                else:
                    prepared[key] = attrs.get(key)

        attrs["values"] = prepared
        return attrs
