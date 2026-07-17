from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from django.core.files.storage import default_storage
from django.utils.translation import gettext_lazy as _
from payment.exchange_rate import to_uzs
from rest_framework import serializers


def _preferred_language(request: Any) -> str:
    if request is None:
        return "uz"
    raw = str(request.headers.get("Accept-Language") or "").strip().lower()
    if raw.startswith("ru"):
        return "ru"
    if raw.startswith("en"):
        return "en"
    return "uz"


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


# ---------------------------------------------------------------------------
# Public hotel list + detail serializers
# ---------------------------------------------------------------------------


class RoomTypeSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    display_name = serializers.CharField(allow_null=True, read_only=True)
    room_type_preset = serializers.CharField(allow_null=True, read_only=True)
    capacity = serializers.IntegerField(read_only=True, default=2)
    bedroom_count = serializers.IntegerField(read_only=True, default=1)
    beds = serializers.ListField(read_only=True, default=list)
    img = serializers.ListField(child=serializers.CharField(), read_only=True, default=list)

    class Meta:
        ref_name = "PropertyRoomTypeSummary"


class HotelCardSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    guid = serializers.CharField(read_only=True)
    name = serializers.CharField(read_only=True)
    description = serializers.CharField(allow_null=True, read_only=True)
    description_uz = serializers.CharField(allow_null=True, read_only=True)
    description_ru = serializers.CharField(allow_null=True, read_only=True)
    description_en = serializers.CharField(allow_null=True, read_only=True)
    address = serializers.CharField(allow_null=True, read_only=True)
    img = serializers.ListField(child=serializers.CharField(), read_only=True, default=list)
    star_rating = serializers.IntegerField(allow_null=True, read_only=True)
    weel_classification = serializers.CharField(allow_null=True, read_only=True)
    themes = serializers.ListField(child=serializers.CharField(), read_only=True, default=list)
    city = serializers.CharField(allow_null=True, read_only=True)
    country = serializers.CharField(allow_null=True, read_only=True)
    latitude = serializers.FloatField(allow_null=True, read_only=True)
    longitude = serializers.FloatField(allow_null=True, read_only=True)
    min_price = serializers.DecimalField(max_digits=18, decimal_places=2, allow_null=True, read_only=True)
    currency = serializers.CharField(allow_blank=True, allow_null=True, read_only=True)
    timezone = serializers.CharField(allow_blank=True, allow_null=True, read_only=True)
    rating = serializers.DecimalField(max_digits=3, decimal_places=2, allow_null=True, read_only=True)
    review_count = serializers.IntegerField(read_only=True, default=0)
    booking_count = serializers.IntegerField(read_only=True, default=0)
    available_rooms = serializers.IntegerField(read_only=True, default=0)
    amenities = serializers.ListField(child=serializers.CharField(), read_only=True, default=list)
    legal_info = serializers.DictField(read_only=True, default=dict)
    check_in_time = serializers.CharField(allow_null=True, read_only=True)
    check_out_time = serializers.CharField(allow_null=True, read_only=True)
    cancellation_policy = serializers.CharField(allow_null=True, read_only=True)
    policies = serializers.DictField(read_only=True, default=dict)
    is_favorite = serializers.BooleanField(read_only=True)
    is_verified = serializers.BooleanField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)
    is_testing = serializers.BooleanField(read_only=True)
    is_archived = serializers.BooleanField(read_only=True)
    is_recommended = serializers.BooleanField(read_only=True)
    verification_status = serializers.CharField(allow_blank=True, allow_null=True, read_only=True)
    tenant_schema = serializers.CharField(allow_blank=True, allow_null=True, read_only=True)
    organization = serializers.DictField(read_only=True, default=dict)
    partner_user = serializers.DictField(read_only=True, default=dict)
    property_detail = serializers.DictField(read_only=True, default=dict)
    created_at = serializers.DateTimeField(allow_null=True, read_only=True)
    updated_at = serializers.DateTimeField(allow_null=True, read_only=True)

    class Meta:
        ref_name = "PropertyHotelCard"

    def to_representation(self, instance):
        request = self.context.get("request")
        row = dict(instance)
        lang = _preferred_language(request)
        row["name"] = row.get("name") or ""
        row["description"] = (
            row.get(f"description_{lang}")
            or row.get("description_uz")
            or row.get("description_en")
            or row.get("description_ru")
        )
        try:
            row["latitude"] = float(row.get("latitude") or 0)
        except (TypeError, ValueError):
            row["latitude"] = None
        try:
            row["longitude"] = float(row.get("longitude") or 0)
        except (TypeError, ValueError):
            row["longitude"] = None
        row["img"] = _build_media_url(request, row.get("img") or [])
        row["min_price"] = _convert_price_for_output(row.get("min_price"), row.get("currency"))
        row["amenities"] = row.get("amenities") or []
        row["rating"] = row.get("rating")
        row["review_count"] = int(row.get("review_count") or 0)
        row["booking_count"] = int(row.get("booking_count") or 0)
        row["available_rooms"] = int(row.get("available_rooms") or 0)
        row["star_rating"] = row.get("star_rating")
        row["weel_classification"] = row.get("weel_classification")
        row["themes"] = row.get("themes") or []
        row["is_verified"] = bool(row.get("is_verified", False))
        row["is_active"] = bool(row.get("is_active", True))
        row["is_testing"] = bool(row.get("is_testing", False))
        row["is_archived"] = bool(row.get("is_archived", False))
        row["is_recommended"] = bool(row.get("is_recommended", False))
        row["verification_status"] = row.get("verification_status") or "waiting"
        raw_legal = row.get("legal_info")
        row["legal_info"] = raw_legal if isinstance(raw_legal, dict) else {}
        row["check_in_time"] = _iso_time_str(row.get("check_in_time"))
        row["check_out_time"] = _iso_time_str(row.get("check_out_time"))
        row["cancellation_policy"] = row.get("cancellation_policy")
        row["policies"] = {
            "alcohol_allowed": bool(row.get("alcohol_allowed", False)),
            "pets_allowed": bool(row.get("pets_allowed", False)),
            "quiet_hours": bool(row.get("quiet_hours", True)),
        }
        row["tenant_schema"] = row.get("tenant_schema")
        row["organization"] = {
            "id": row.get("organization_id"),
            "name": row.get("organization_name"),
            "slug": row.get("organization_slug"),
            "schema_name": row.get("tenant_schema"),
        }
        partner_payload = row.get("partner_user")
        row["partner_user"] = partner_payload if isinstance(partner_payload, dict) else None
        row["property_detail"] = {
            "description_ru": row.get("description_ru") or None,
            "description_uz": row.get("description_uz") or None,
            "description_en": row.get("description_en") or None,
            "address": row.get("address") or None,
            "check_in_time": row.get("check_in_time") or None,
            "check_out_time": row.get("check_out_time") or None,
            "cancellation_policy": row.get("cancellation_policy") or None,
            "timezone": row.get("timezone") or None,
            "amenities": row.get("amenities") or [],
            "is_allowed_alcohol": bool(row.get("alcohol_allowed", False)),
            "is_allowed_pets": bool(row.get("pets_allowed", False)),
            "is_quiet_hours": bool(row.get("quiet_hours", True)),
            "star_rating": row.get("star_rating"),
        }
        favorites = _favorite_guid_set(self.context)
        row["is_favorite"] = str(row.get("guid")) in favorites
        return super().to_representation(row)


class HotelDetailSerializer(HotelCardSerializer):
    room_types = RoomTypeSummarySerializer(many=True, read_only=True, default=list)
    reviews = serializers.ListField(child=serializers.DictField(), read_only=True, default=list)

    class Meta:
        ref_name = "PropertyHotelDetail"

    def to_representation(self, instance):
        request = self.context.get("request")
        row = dict(instance)
        row["room_types"] = row.get("room_types") or []
        if row["room_types"]:
            row["room_types"] = [
                self._build_room_summary(r, request) for r in row["room_types"]
            ]
        row["reviews"] = row.get("reviews") or []
        return super().to_representation(row)

    @staticmethod
    def _build_room_summary(room: dict, request):
        room["img"] = _build_media_url(request, room.get("img") or [])
        return room


def _iso_time_str(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    text = str(value).strip()
    return text or None


# ---------------------------------------------------------------------------
# Admin serializers (extend public card for admin fields)
# ---------------------------------------------------------------------------


class HotelAdminOrganizationSerializer(serializers.Serializer):
    id = serializers.IntegerField(allow_null=True)
    name = serializers.CharField(allow_blank=True, allow_null=True)
    slug = serializers.CharField(allow_blank=True, allow_null=True)
    schema_name = serializers.CharField(allow_blank=True, allow_null=True)


class HotelPartnerUserSerializer(serializers.Serializer):
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
        ref_name = "HotelPartnerUser"


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


class HotelAdminListSerializer(HotelCardSerializer):

    class Meta:
        ref_name = "PropertyHotelAdminList"

    def to_representation(self, instance):
        row = dict(instance)
        row["partner_user"] = row.get("partner_user") if isinstance(row.get("partner_user"), dict) else None
        data = super().to_representation(row)
        return data


class HotelAdminUpdateSerializer(serializers.Serializer):
    organization_id = serializers.IntegerField(required=False, allow_null=True)
    partner_user_id = serializers.IntegerField(required=False, allow_null=True)
    tenant_schema = serializers.CharField(required=False, allow_blank=False)
    title = serializers.CharField(required=False, allow_blank=False)
    description_ru = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    description_uz = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    description_en = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
    address = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    city = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    country = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    latitude = serializers.DecimalField(
        max_digits=17, decimal_places=14, required=False, allow_null=True
    )
    longitude = serializers.DecimalField(
        max_digits=17, decimal_places=14, required=False, allow_null=True
    )
    star_rating = serializers.IntegerField(
        required=False, allow_null=True, min_value=1, max_value=7
    )
    amenities = serializers.ListField(
        child=serializers.CharField(allow_blank=False),
        required=False,
        allow_empty=True,
    )
    check_in_time = serializers.TimeField(required=False, allow_null=True)
    check_out_time = serializers.TimeField(required=False, allow_null=True)
    cancellation_policy = serializers.CharField(
        required=False, allow_blank=True, allow_null=True
    )
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
    is_verified = serializers.BooleanField(required=False)
    is_archived = serializers.BooleanField(required=False)
    is_recommended = serializers.BooleanField(required=False)
    verification_status = serializers.ChoiceField(
        choices=["waiting", "accepted", "cancelled"],
        required=False,
        allow_null=True,
    )
    img = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )
    legal_info = serializers.DictField(
        child=serializers.CharField(allow_blank=True, allow_null=True),
        required=False,
        allow_null=True,
    )
    property_detail = HotelAdminPropertyDetailSerializer(required=False)

    def validate(self, attrs):
        prepared: dict[str, Any] = {}
        property_detail = attrs.get("property_detail")

        if "organization_id" in attrs:
            prepared["organization_id"] = attrs.get("organization_id")
        if "partner_user_id" in attrs:
            prepared["partner_user_id"] = attrs.get("partner_user_id")
        if "tenant_schema" in attrs:
            prepared["tenant_schema"] = str(attrs.get("tenant_schema") or "").strip()

        if "title" in attrs:
            title = str(attrs.get("title") or "").strip()
            if not title:
                raise serializers.ValidationError(
                    {"title": _("This field is required.")}
                )
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
            "is_verified",
            "is_archived",
            "is_recommended",
            "verification_status",
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
            prepared["amenities"] = [
                str(value).strip()
                for value in attrs.get("amenities") or []
                if str(value).strip()
            ]
        if "img" in attrs:
            prepared["photos"] = [
                str(value) for value in attrs.get("img") or [] if value
            ]
        if "legal_info" in attrs and attrs.get("legal_info") is not None:
            raw_legal = attrs.get("legal_info") or {}
            prepared["legal_info"] = {
                k: (str(v) if v is not None else "") for k, v in raw_legal.items()
            }

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
                prepared["alcohol_allowed"] = bool(
                    property_detail.get("is_allowed_alcohol")
                )
            if "is_allowed_pets" in property_detail:
                prepared["pets_allowed"] = bool(property_detail.get("is_allowed_pets"))
            if "is_quiet_hours" in property_detail:
                prepared["quiet_hours"] = bool(property_detail.get("is_quiet_hours"))

        attrs["values"] = prepared
        return attrs
