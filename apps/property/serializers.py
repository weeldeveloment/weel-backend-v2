import os

from datetime import time
from decimal import Decimal

from django.db.models import Avg, Q
from django.db import transaction, models
from django.utils.translation import gettext_lazy as _

from rest_framework import serializers

from users.models.clients import Client
from users.models.partners import Partner

from core import settings
from .mixins import (
    LanguageFieldMixin,
    PropertyPriceValidateMixin,
    PropertyServicesValidateMixin,
)


class PropertyTypeSlugRelatedField(serializers.SlugRelatedField):
    """property_type_id uchun: template o'zgaruvchi ({{id}}) yuborilsa aniq xabar."""

    def to_internal_value(self, data):
        if data is not None:
            s = str(data).strip()
            if "{{" in s or "}}" in s:
                raise serializers.ValidationError(
                    _(
                        "property_type_id is not set. "
                        "Get a real UUID from GET /api/property/types/ and set it in Postman variables."
                    )
                )
        return super().to_internal_value(data)


from .models import (
    Property,
    PropertyType,
    PropertyRoom,
    PropertyImage,
    PropertyPrice,
    PropertyDetail,
    PropertyReview,
    PropertyFavorite,
    PropertyService,
    Category,
    PropertyLocation,
    Region,
    District,
    VerificationStatus,
)
from payment.choices import Currency
from payment.exchange_rate import to_uzs
from shared.date import parse_yyyy_mm_dd, month_start
from booking.models import Booking
from .pricing import (
    related_prices,
)


class PropertyTypeListSerializer(LanguageFieldMixin, serializers.ModelSerializer):
    title = serializers.SerializerMethodField("get_title")
    icon_url = serializers.ImageField(source="icon")

    class Meta:
        model = PropertyType
        fields = ["guid", "title", "icon_url"]

    def get_title(self, obj):
        return self.get_lang_field(obj, "title")


class PropertyLocationSerializer(serializers.ModelSerializer):
    guid = serializers.UUIDField(read_only=True)

    class Meta:
        model = PropertyLocation
        fields = ["guid", "latitude", "longitude", "country", "city"]


class RegionListSerializer(LanguageFieldMixin, serializers.ModelSerializer):
    title = serializers.SerializerMethodField("get_title")

    class Meta:
        model = Region
        fields = ["guid", "title", "img"]

    def get_title(self, obj):
        return self.get_lang_field(obj, "title")


class DistrictListSerializer(LanguageFieldMixin, serializers.ModelSerializer):
    title = serializers.SerializerMethodField("get_title")
    region = RegionListSerializer(read_only=True)

    class Meta:
        model = District
        fields = ["guid", "title", "region"]

    def get_title(self, obj):
        return self.get_lang_field(obj, "title")


class LocationDistrictSerializer(LanguageFieldMixin, serializers.ModelSerializer):
    title = serializers.SerializerMethodField("get_title")

    class Meta:
        model = District
        fields = ["guid", "title"]

    def get_title(self, obj):
        return self.get_lang_field(obj, "title")


class LocationRegionSerializer(LanguageFieldMixin, serializers.ModelSerializer):
    title = serializers.SerializerMethodField("get_title")
    districts = LocationDistrictSerializer(many=True, read_only=True)

    class Meta:
        model = Region
        fields = ["guid", "title", "img", "districts"]

    def get_title(self, obj):
        return self.get_lang_field(obj, "title")


class PropertyServiceListSerializer(LanguageFieldMixin, serializers.ModelSerializer):
    property_type = PropertyTypeListSerializer()
    title = serializers.SerializerMethodField("get_title")
    icon_url = serializers.FileField(source="icon")

    class Meta:
        model = PropertyService
        fields = ["guid", "property_type", "title", "icon_url"]

    def get_title(self, obj):
        return self.get_lang_field(obj, "title")


class CategoryListSerializer(LanguageFieldMixin, serializers.ModelSerializer):
    title = serializers.SerializerMethodField("get_title")
    icon_url = serializers.FileField(source="icon", allow_null=True)

    class Meta:
        model = Category
        fields = ["guid", "title", "icon_url"]

    def get_title(self, obj):
        return self.get_lang_field(obj, "title")


class PropertyImageSerializer(serializers.ModelSerializer):
    guid = serializers.UUIDField(read_only=True)
    order = serializers.IntegerField(default=1)
    image_url = serializers.ImageField(source="image", read_only=True)
    is_pending = serializers.BooleanField(read_only=True)

    class Meta:
        model = PropertyImage
        fields = ["guid", "order", "is_pending", "image_url"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not instance.is_pending:
            return data

        request = self.context.get("request")
        user = getattr(request, "user", None)
        is_owner = isinstance(user, Partner) and instance.property.partner_id == user.id

        if is_owner:
            data["detail"] = "Your image(s) are pending approval"
            data["status"] = "pending"
            return data

        return {"detail": "Your image(s) are pending approval", "status": "pending"}


class PropertyListSerializer(serializers.ModelSerializer):
    price = serializers.SerializerMethodField("get_price")
    property_location = PropertyLocationSerializer()
    property_images = PropertyImageSerializer(many=True)
    region = RegionListSerializer(read_only=True)
    district = DistrictListSerializer(read_only=True)
    guests = serializers.IntegerField(source="property_room.guests")
    rooms = serializers.IntegerField(source="property_room.rooms")
    average_rating = serializers.SerializerMethodField("get_average_rating")
    is_favorite = serializers.SerializerMethodField()

    class Meta:
        model = Property
        fields = [
            "guid",
            "title",
            "img",
            "price",
            "property_location",
            "property_images",
            "region",
            "district",
            "guests",
            "rooms",
            "average_rating",
            "is_favorite",
            "created_at",
        ]

    def get_price(self, obj):
        property_price = related_prices(obj)
        if property_price.exists():
            return PropertyListPriceSerializer(property_price, many=True).data
        return None

    @staticmethod
    def get_average_rating(obj):
        # View annotatsiyasidan foydalanamiz (ball toʻgʻri va N+1 yoʻq)
        annotated = getattr(obj, "average_rating", None)
        if annotated is not None:
            try:
                v = float(annotated)
                return round(v, 2) if v > 0 else 1.0
            except (TypeError, ValueError):
                pass
        # Fallback: alohida soʻrov (is_hidden=False yoki NULL — yashirin boʻlmagan reviewlar)
        average_rating = (
            PropertyReview.objects.filter(
                property=obj,
                rating__isnull=False,
            )
            .filter(Q(is_hidden=False) | Q(is_hidden__isnull=True))
            .aggregate(avg=Avg("rating"))
            .get("avg")
        )
        return round(average_rating, 2) if average_rating else 1.0

    def get_is_favorite(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not isinstance(user, Client):
            return False
        return PropertyFavorite.objects.filter(client=user, property=obj).exists()


class PartnerPropertyListSerializer(serializers.ModelSerializer):
    price = serializers.SerializerMethodField("get_price")
    property_location = PropertyLocationSerializer()
    property_images = PropertyImageSerializer(many=True)
    region = RegionListSerializer(read_only=True)
    district = DistrictListSerializer(read_only=True)
    guests = serializers.IntegerField(source="property_room.guests")
    rooms = serializers.IntegerField(source="property_room.rooms")
    average_rating = serializers.SerializerMethodField("get_average_rating")

    class Meta:
        model = Property
        fields = [
            "guid",
            "title",
            "img",
            "verification_status",
            "price",
            "property_location",
            "property_images",
            "region",
            "district",
            "guests",
            "rooms",
            "average_rating",
            "created_at",
        ]

    def get_price(self, obj):
        property_price = related_prices(obj)
        if property_price.exists():
            return PropertyListPriceSerializer(property_price, many=True).data
        return None

    @staticmethod
    def get_average_rating(obj):
        annotated = getattr(obj, "average_rating", None)
        if annotated is not None:
            try:
                v = float(annotated)
                return round(v, 2) if v > 0 else 1.0
            except (TypeError, ValueError):
                pass
        average_rating = (
            PropertyReview.objects.filter(
                property=obj,
                rating__isnull=False,
            )
            .filter(Q(is_hidden=False) | Q(is_hidden__isnull=True))
            .aggregate(avg=Avg("rating"))
            .get("avg")
        )
        return round(average_rating, 2) if average_rating else 1.0


class PropertyListPriceSerializer(serializers.ModelSerializer):
    price_per_person = serializers.SerializerMethodField("get_price_per_person")
    price_on_working_days = serializers.SerializerMethodField(
        "get_price_on_working_days"
    )
    price_on_weekends = serializers.SerializerMethodField("get_price_on_weekends")

    class Meta:
        model = PropertyPrice
        fields = [
            "guid",
            "month_from",
            "month_to",
            "price_per_person",
            "price_on_working_days",
            "price_on_weekends",
        ]

    def _convert(self, amount, currency):
        if currency == "USD":
            return to_uzs(amount)
        elif currency == "UZS":
            return amount
        else:
            raise serializers.ValidationError(_("Unsupported currency"))

    def get_price_per_person(self, obj):
        return self._convert(obj.price_per_person, obj.property.currency)

    def get_price_on_working_days(self, obj):
        return self._convert(obj.price_on_working_days, obj.property.currency)

    def get_price_on_weekends(self, obj):
        return self._convert(obj.price_on_weekends, obj.property.currency)


class PropertyPriceSerializer(PropertyPriceValidateMixin, serializers.ModelSerializer):
    month_from = serializers.DateField(required=True)
    month_to = serializers.DateField(required=True)
    price_per_person = serializers.DecimalField(max_digits=12, decimal_places=2)
    price_on_working_days = serializers.DecimalField(max_digits=12, decimal_places=2)
    price_on_weekends = serializers.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        model = PropertyPrice
        fields = [
            "month_from",
            "month_to",
            "price_per_person",
            "price_on_working_days",
            "price_on_weekends",
        ]

    def validate(self, attrs):
        month_from, month_to = self.validate_property_price_month_range(
            month_to=attrs["month_to"],
            month_from=attrs["month_from"],
        )

        # Update attrs
        attrs["month_from"] = month_from
        attrs["month_to"] = month_to
        return attrs


class PropertyRoomSerializer(serializers.ModelSerializer):
    guid = serializers.UUIDField(read_only=True)
    guests = serializers.IntegerField(default=1)
    rooms = serializers.IntegerField(default=1)
    beds = serializers.IntegerField(default=1)
    bathrooms = serializers.IntegerField(default=1)

    class Meta:
        model = PropertyRoom
        fields = ["guid", "guests", "rooms", "beds", "bathrooms"]


class PropertyReviewClientSerializer(serializers.ModelSerializer):
    class Meta:
        model = Client
        fields = ["guid", "first_name", "last_name"]


class PropertyReviewSerializer(serializers.ModelSerializer):
    guid = serializers.UUIDField(read_only=True)
    client = PropertyReviewClientSerializer(read_only=True)
    rating = serializers.DecimalField(max_digits=2, decimal_places=1)
    comment = serializers.CharField(read_only=True)

    class Meta:
        model = PropertyReview
        fields = ["guid", "client", "rating", "comment", "created_at"]


class PropertyReviewCreateSerializer(serializers.ModelSerializer):
    rating = serializers.DecimalField(
        max_digits=2,
        decimal_places=1,
        required=True,
        allow_null=True,
    )
    comment = serializers.CharField(required=False)

    class Meta:
        model = PropertyReview
        fields = ["client_id", "rating", "comment"]

    @staticmethod
    def validate_rating(value):
        """Ensure rating is between 1 and 5"""
        value = Decimal(value) if value is not None else Decimal("0.0")

        if not (1.0 <= value <= 5.0):
            raise serializers.ValidationError(_("Rating must be between 1 and 5"))
        return value

    def validate(self, attrs):
        """Allow review only, if client completed a booking"""
        request = self.context.get("request")

        client = request.user
        property = self.context["property"]

        has_eligible_booking = Booking.objects.filter(
            client=client,
            property=property,
            status__in=[
                Booking.BookingStatus.CONFIRMED,
                Booking.BookingStatus.COMPLETED,
                Booking.BookingStatus.CANCELLED,
            ],
        ).exists()

        if not has_eligible_booking:
            raise serializers.ValidationError(
                _("You can leave a review only for accepted or completed bookings")
            )

        return attrs

    def create(self, validated_data):
        client = self.context["request"].user
        property = self.context["property"]

        property_review = PropertyReview.objects.create(
            client=client,
            property=property,
            rating=validated_data["rating"],
            comment=validated_data.get("comment"),
        )

        return property_review


class PropertyDetailSerializer(LanguageFieldMixin, serializers.ModelSerializer):
    guid = serializers.UUIDField(source="property.guid")
    title = serializers.CharField(source="property.title")
    img = serializers.ImageField(source="property.img", read_only=True)
    created_at = serializers.DateTimeField(source="property.created_at", read_only=True)
    minimum_weekend_day_stay = serializers.BooleanField(
        source="property.minimum_weekend_day_stay"
    )
    currency = serializers.CharField(source="property.currency", read_only=True)
    price = serializers.SerializerMethodField("get_price")
    description = serializers.SerializerMethodField("get_description")
    average_rating = serializers.SerializerMethodField("get_average_rating")
    comment_count = serializers.IntegerField(source="property.comment_count")
    property_services = PropertyServiceListSerializer(
        many=True, source="property.property_services"
    )
    property_location = PropertyLocationSerializer(source="property.property_location")
    property_room = PropertyRoomSerializer(source="property.property_room")
    property_images = PropertyImageSerializer(
        many=True, source="property.property_images"
    )
    is_favorite = serializers.SerializerMethodField()

    class Meta:
        model = PropertyDetail
        fields = [
            "guid",
            "title",
            "img",
            "created_at",
            "currency",
            "price",
            "minimum_weekend_day_stay",
            "description",
            "comment_count",
            "average_rating",
            "is_favorite",
            "property_services",
            "property_room",
            "property_location",
            "property_images",
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
        ]

    def get_price(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)

        property_price = related_prices(obj.property)
        if not property_price.exists():
            return None

        if isinstance(user, Partner):
            return PropertyPriceSerializer(property_price, many=True).data
        return PropertyListPriceSerializer(property_price, many=True).data

    def get_description(self, obj):
        return self.get_lang_field(obj, "description")

    @staticmethod
    def get_average_rating(obj):
        average_rating = (
            PropertyReview.objects.filter(
                property=obj.property,
                rating__isnull=False,
            )
            .filter(Q(is_hidden=False) | Q(is_hidden__isnull=True))
            .aggregate(avg=Avg("rating"))
            .get("avg")
        )
        return round(average_rating, 2) if average_rating else 1.0

    def get_is_favorite(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not isinstance(user, Client):
            return False
        return PropertyFavorite.objects.filter(
            client=user, property=obj.property
        ).exists()


class PropertyDetailCreateSerializer(serializers.ModelSerializer):
    description_en = serializers.CharField(required=False, allow_null=True)
    check_in = serializers.TimeField(required=False, default=time(19, 0))
    check_out = serializers.TimeField(required=False, default=time(17, 0))
    is_allowed_alcohol = serializers.BooleanField(required=False, default=False)
    is_allowed_corporate = serializers.BooleanField(required=False, default=False)
    is_allowed_pets = serializers.BooleanField(required=False, default=False)
    is_quiet_hours = serializers.BooleanField(required=False, default=False)

    class Meta:
        model = PropertyDetail
        fields = [
            "apartment_number",
            "home_number",
            "entrance_number",
            "floor_number",
            "pass_code",
            "description_en",
            "description_ru",
            "description_uz",
            "check_in",
            "check_out",
            "is_allowed_alcohol",
            "is_allowed_corporate",
            "is_allowed_pets",
            "is_quiet_hours",
        ]

    def validate(self, attrs):
        description_en = attrs.get("description_en")
        if not description_en:
            attrs["description_en"] = "Description will be updated soon"
        return attrs


class PropertyCreateSerializer(
    PropertyPriceValidateMixin,
    PropertyServicesValidateMixin,
    serializers.ModelSerializer,
):
    property_type_id = PropertyTypeSlugRelatedField(
        queryset=PropertyType.objects.all(),
        slug_field="guid",
        source="property_type",
        write_only=True,
        error_messages={
            "invalid": _("Invalid value for property type"),
            "does_not_exist": _("Property type with this GUID doesn't exist"),
        },
    )
    price = serializers.JSONField()
    currency = serializers.ChoiceField(
        required=True,
        choices=Currency.choices,
        error_messages={
            "invalid": _(
                "Currency must be either the US dollar(USD) or the Uzbek sum(UZS)"
            )
        },
    )
    minimum_weekend_day_stay = serializers.BooleanField(default=False)
    property_location = PropertyLocationSerializer()
    property_detail = PropertyDetailCreateSerializer()
    property_services = serializers.SlugRelatedField(
        queryset=PropertyService.objects.all(),
        many=True,
        slug_field="guid",
        required=False,
        allow_empty=True,
        error_messages={
            "invalid": _("Invalid value in property services list"),
            "does_not_exist": _(
                "One or more of the selected property services don't exist"
            ),
        },
    )
    property_room = PropertyRoomSerializer()
    region = serializers.SlugRelatedField(
        slug_field="guid",
        queryset=Region.objects.all(),
        required=False,
        allow_null=True,
    )
    district = serializers.SlugRelatedField(
        slug_field="guid",
        queryset=District.objects.all(),
        required=False,
        allow_null=True,
    )
    region_id = serializers.UUIDField(required=False, allow_null=True, write_only=True)
    district_id = serializers.UUIDField(required=False, allow_null=True, write_only=True)

    class Meta:
        model = Property
        fields = [
            "title",
            "price",
            "currency",
            "minimum_weekend_day_stay",
            "property_type_id",
            "property_location",
            "region",
            "district",
            "region_id",
            "district_id",
            "property_services",
            "property_detail",
            "property_room",
        ]

    def validate(self, attrs):
        # region_id / district_id yuborilsa, tegishli obyektga o‘giramiz
        if attrs.get("region_id") is not None and attrs.get("region") is None:
            region = Region.objects.filter(guid=attrs.pop("region_id")).first()
            if region:
                attrs["region"] = region
        if attrs.get("district_id") is not None and attrs.get("district") is None:
            district = District.objects.filter(guid=attrs.pop("district_id")).first()
            if district:
                attrs["district"] = district
        for key in ("region_id", "district_id"):
            attrs.pop(key, None)
        region = attrs.get("region")
        district = attrs.get("district")
        if district and region and district.region_id != region.id:
            raise serializers.ValidationError(
                {"district": _("District must belong to the selected region.")}
            )
        if district and not region:
            attrs["region"] = district.region
        property_type = attrs.get("property_type")
        if property_type and property_type.title_en.lower() == "apartment":
            detail = attrs.get("property_detail") or {}
            required_fields = [
                "apartment_number",
                "home_number",
                "entrance_number",
                "floor_number",
                "pass_code",
            ]
            missing = [f for f in required_fields if not detail.get(f)]
            if missing:
                raise serializers.ValidationError(
                    {
                        "property_detail": {
                            field: _("This field is required for apartment properties.")
                            for field in missing
                        }
                    }
                )
        return attrs

    def validate_price(self, value):
        property_type_id = self.initial_data.get("property_type_id")
        if property_type_id is not None:
            property_type_id = str(property_type_id).strip()
        property_type = (
            PropertyType.objects.filter(guid=property_type_id).first()
            if property_type_id
            else None
        )
        return self.validate_property_price(value, property_type)

    @transaction.atomic
    def create(self, validated_data):
        request = self.context["request"]

        price_data = validated_data.pop("price")
        property_location_data = validated_data.pop("property_location")
        property_room_data = validated_data.pop("property_room")
        property_services_data = validated_data.pop("property_services", [])
        property_detail_data = validated_data.pop("property_detail")
        property_location = PropertyLocation.objects.create(**property_location_data)
        partner = getattr(request, "user")

        property = Property.objects.create(
            property_location=property_location,
            partner=partner,
            **validated_data,
        )

        property.property_services.set(property_services_data)
        PropertyRoom.objects.create(property=property, **property_room_data)
        PropertyDetail.objects.create(property=property, **property_detail_data)

        serializer = PropertyPriceSerializer(
            many=True,
            data=price_data,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save(property=property)

        return property


class PropertyUpdateSerializer(
    PropertyPriceValidateMixin,
    PropertyServicesValidateMixin,
    serializers.ModelSerializer,
):
    title = serializers.CharField(required=False)
    currency = serializers.ChoiceField(
        required=False,
        choices=Currency.choices,
        error_messages={
            "invalid": _(
                "Currency must be either the US dollar(USD) or the Uzbek sum(UZS)"
            )
        },
    )
    price = serializers.JSONField(required=False)
    minimum_weekend_day_stay = serializers.BooleanField(required=False, default=False)
    property_location = PropertyLocationSerializer(required=False)
    property_services = serializers.SlugRelatedField(
        queryset=PropertyService.objects.all(),
        many=True,
        slug_field="guid",
        required=False,
        error_messages={
            "invalid": _("Invalid value in property services list"),
            "does_not_exist": _(
                "One or more if the selected property services don't exist"
            ),
        },
    )
    property_room = PropertyRoomSerializer(required=False)
    property_detail = PropertyDetailCreateSerializer(required=False)
    region = serializers.SlugRelatedField(
        slug_field="guid",
        queryset=Region.objects.all(),
        required=False,
        allow_null=True,
    )
    district = serializers.SlugRelatedField(
        slug_field="guid",
        queryset=District.objects.all(),
        required=False,
        allow_null=True,
    )
    region_id = serializers.UUIDField(required=False, allow_null=True, write_only=True)
    district_id = serializers.UUIDField(required=False, allow_null=True, write_only=True)

    class Meta:
        model = PropertyDetail
        fields = [
            "title",
            "price",
            "currency",
            "minimum_weekend_day_stay",
            "property_location",
            "region",
            "district",
            "region_id",
            "district_id",
            "property_services",
            "property_room",
            "property_detail",
        ]

    def validate(self, attrs):
        if attrs.get("region_id") is not None and attrs.get("region") is None:
            region = Region.objects.filter(guid=attrs.pop("region_id")).first()
            if region:
                attrs["region"] = region
        if attrs.get("district_id") is not None and attrs.get("district") is None:
            district = District.objects.filter(guid=attrs.pop("district_id")).first()
            if district:
                attrs["district"] = district
        for key in ("region_id", "district_id"):
            attrs.pop(key, None)
        region = attrs.get("region")
        district = attrs.get("district")
        if district and region and district.region_id != region.id:
            raise serializers.ValidationError(
                {"district": _("District must belong to the selected region.")}
            )
        if district and not region:
            attrs["region"] = district.region
        title = attrs.get("title")
        if title:
            property = self.instance.property
            queryset = Property.objects.filter(title=title).exclude(guid=property.guid)

            if queryset.exists():
                raise serializers.ValidationError(
                    {"title": _("Property with this title already exists")}
                )

            attrs["title"] = title
            return attrs
        return attrs

    def validate_price(self, value):
        if value in ({}, [], None):
            raise serializers.ValidationError(_("Price can't be empty"))

        property_type = self._get_property_type()
        if property_type is None:
            raise serializers.ValidationError(
                _("Property type is required for price validation")
            )
        return self.validate_property_price(value, property_type)

    @transaction.atomic
    def update(self, instance, validated_data):
        property = instance.property

        property_updated = False
        update_price = False
        property_update_fields = set()

        price_data = validated_data.pop("price", None)
        property_location_data = validated_data.pop("property_location", None)
        region = validated_data.pop("region", None)
        district = validated_data.pop("district", None)
        property_services_data = validated_data.pop("property_services", None)
        property_room_data = validated_data.pop("property_room", None)
        property_detail_data = validated_data.pop("property_detail", None)

        if region is not None:
            property.region = region
            property_update_fields.add("region")
            property_updated = True
        if district is not None:
            property.district = district
            property_update_fields.add("district")
            property_updated = True

        if "currency" in validated_data:
            property.currency = validated_data["currency"]
            update_price = True
            property_updated = True
            property_update_fields.add("currency")

        # updating fields title, minimum_weekend_day_stay
        for field in ["title", "minimum_weekend_day_stay"]:
            if field in validated_data:
                setattr(property, field, validated_data[field])
                property_updated = True
                property_update_fields.add(field)

        if price_data is not None:
            existing_prices = {
                pp.month_from: pp for pp in property.property_price.all()
            }

            for item in price_data:
                month_from = parse_yyyy_mm_dd(item["month_from"], "month_from")
                m_from = month_start(month_from)

                property_price = existing_prices.get(m_from)
                if not property_price:
                    raise serializers.ValidationError(
                        _("You can only update price for existing price months")
                    )

                serializer = PropertyPriceSerializer(
                    property_price,
                    data=item,
                    partial=True,
                )
                serializer.is_valid(raise_exception=True)
                serializer.save()
            property_updated = True
            update_price = True

        if property_location_data:
            for attr, value in property_location_data.items():
                setattr(property.property_location, attr, value)
            property.property_location.save()
            property_updated = True

        if property_room_data:
            for attr, value in property_room_data.items():
                setattr(property.property_room, attr, value)
            property.property_room.save()
            property_updated = True

        if property_services_data is not None:
            property.property_services.set(property_services_data)
            property_updated = True

        if property_detail_data:
            for attr, value in property_detail_data.items():
                setattr(instance, attr, value)
            instance.save()
            property_updated = True

        # Any partner update should trigger re-verification.
        if update_price or property_updated:
            property.verification_status = VerificationStatus.WAITING
            property_update_fields.add("verification_status")
            property_update_fields.add("is_verified")

        if property_update_fields:
            property.save(update_fields=list(property_update_fields))

        return instance


class PropertyPutSerializer(PropertyUpdateSerializer):
    title = serializers.CharField(required=True)
    currency = serializers.ChoiceField(
        required=True,
        choices=Currency.choices,
        error_messages={
            "invalid": _(
                "Currency must be either the US dollar(USD) or the Uzbek sum(UZS)"
            )
        },
    )
    price = serializers.JSONField(required=True)
    minimum_weekend_day_stay = serializers.BooleanField(default=False)
    property_location = PropertyLocationSerializer(required=True)
    property_services = serializers.SlugRelatedField(
        queryset=PropertyService.objects.all(),
        many=True,
        slug_field="guid",
        required=True,
        error_messages={
            "invalid": _("Invalid value in property services list"),
            "does_not_exist": _(
                "One or more if the selected property services don't exist"
            ),
        },
    )
    property_room = PropertyRoomSerializer(required=True)
    property_detail = PropertyDetailCreateSerializer(required=True)


class PropertyPatchSerializer(PropertyUpdateSerializer):
    pass


class PropertyImageCreateSerializer(serializers.Serializer):
    images = serializers.ListField(
        child=serializers.ImageField(),
        write_only=True,
        required=True,
    )

    def validate_images(self, images):
        if not images:
            raise serializers.ValidationError(_("At least one image is required"))

        for image in images:
            if image.size > settings.MAX_IMAGE_SIZE:
                raise serializers.ValidationError(
                    _("Image file too large, maximum size is 20MB")
                )

            extension = image.name.split(".")[-1].lower()
            if extension not in settings.ALLOWED_PHOTO_EXTENSION:
                raise serializers.ValidationError(
                    _("Invalid image format, allowed are: jpg, jpeg, png, heif, heic")
                )

        return images

    def validate(self, attrs):
        request = self.context["request"]
        property_id = self.context.get("property_id")

        property = Property.objects.filter(
            guid=property_id, partner=request.user
        ).first()

        if property is None:
            raise serializers.ValidationError({"property_id": _("Property not found")})

        self.context["property"] = property
        return attrs

    def create(self, validated_data):
        property = self.context["property"]
        images = validated_data.pop("images", [])

        last_order = (
            property.property_images.aggregate(max_order=models.Max("order"))[
                "max_order"
            ]
            or 0
        )

        create_images = []

        for idx, image in enumerate(images, start=1):
            property_image = PropertyImage.objects.create(
                property=property,
                image=image,
                order=last_order + idx,
                is_pending=True,
            )
            create_images.append(property_image)

        if property.is_verified:
            property.verification_status = VerificationStatus.WAITING
            property.save(update_fields=["verification_status", "is_verified"])

        return create_images


class PropertyImageUpdateSerializer(serializers.Serializer):
    image = serializers.ImageField(required=False)
    order = serializers.IntegerField(required=False)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError(_("No fields provided for update"))
        return attrs

    def validate_image(self, image):
        """Validate a single image"""
        if image.size > settings.MAX_IMAGE_SIZE:
            raise serializers.ValidationError(
                _("Image file too large, maximum size is 20MB")
            )

        extension = image.name.split(".")[-1].lower()
        if extension not in settings.ALLOWED_PHOTO_EXTENSION:
            raise serializers.ValidationError(
                _("Invalid image format, allowed are: jpg, jpeg, png, heif, heic")
            )

        return image

    def update(self, instance, validated_data):
        update_fields = []

        if "image" in validated_data:
            instance.image = validated_data["image"]
            update_fields.append("image")

        if "order" in validated_data:
            instance.order = validated_data["order"]
            update_fields.append("order")

        instance.is_pending = True
        update_fields.append("is_pending")

        instance.save(update_fields=update_fields)

        property = instance.property
        if property.is_verified:
            property.verification_status = VerificationStatus.WAITING
            property.save(update_fields=["verification_status", "is_verified"])

        return instance
