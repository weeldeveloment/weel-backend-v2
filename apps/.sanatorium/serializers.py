from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction, models
from django.db.models import Avg
from django.utils.translation import gettext_lazy as _

from rest_framework import serializers

from core import settings
from users.models.clients import Client
from users.models.partners import Partner
from .mixins import LanguageFieldMixin
from .models import (
    MedicalSpecialization,
    Treatment,
    RoomType,
    PackageType,
    RoomAmenity,
    Sanatorium,
    SanatoriumImage,
    SanatoriumLocation,
    SanatoriumTreatment,
    SanatoriumRoom,
    SanatoriumRoomImage,
    SanatoriumRoomPrice,
    RoomCalendarDate,
    SanatoriumReview,
    SanatoriumFavorite,
    SanatoriumBooking,
    SanatoriumBookingPrice,
)


# ──────────────────────────────────────────────
# Lookup / reference serializers
# ──────────────────────────────────────────────


class MedicalSpecializationSerializer(LanguageFieldMixin, serializers.ModelSerializer):
    title = serializers.SerializerMethodField()
    icon_url = serializers.FileField(source="icon", read_only=True)

    class Meta:
        model = MedicalSpecialization
        fields = ["guid", "title", "icon_url"]

    def get_title(self, obj):
        return self.get_lang_field(obj, "title")


class TreatmentSerializer(LanguageFieldMixin, serializers.ModelSerializer):
    title = serializers.SerializerMethodField()
    icon_url = serializers.FileField(source="icon", read_only=True)

    class Meta:
        model = Treatment
        fields = ["guid", "title", "icon_url"]

    def get_title(self, obj):
        return self.get_lang_field(obj, "title")


class RoomTypeSerializer(LanguageFieldMixin, serializers.ModelSerializer):
    title = serializers.SerializerMethodField()

    class Meta:
        model = RoomType
        fields = ["guid", "title"]

    def get_title(self, obj):
        return self.get_lang_field(obj, "title")


class PackageTypeSerializer(LanguageFieldMixin, serializers.ModelSerializer):
    title = serializers.SerializerMethodField()

    class Meta:
        model = PackageType
        fields = ["guid", "title", "duration_days"]

    def get_title(self, obj):
        return self.get_lang_field(obj, "title")


class RoomAmenitySerializer(LanguageFieldMixin, serializers.ModelSerializer):
    title = serializers.SerializerMethodField()
    icon_url = serializers.FileField(source="icon", read_only=True)

    class Meta:
        model = RoomAmenity
        fields = ["guid", "title", "icon_url"]

    def get_title(self, obj):
        return self.get_lang_field(obj, "title")


# ──────────────────────────────────────────────
# Sanatorium location
# ──────────────────────────────────────────────


class SanatoriumLocationSerializer(serializers.ModelSerializer):
    guid = serializers.UUIDField(read_only=True)

    class Meta:
        model = SanatoriumLocation
        fields = ["guid", "latitude", "longitude", "country", "city"]


# ──────────────────────────────────────────────
# Sanatorium images
# ──────────────────────────────────────────────


class SanatoriumImageSerializer(serializers.ModelSerializer):
    guid = serializers.UUIDField(read_only=True)
    image_url = serializers.ImageField(source="image", read_only=True)

    class Meta:
        model = SanatoriumImage
        fields = ["guid", "order", "is_pending", "image_url"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not instance.is_pending:
            return data

        request = self.context.get("request")
        user = getattr(request, "user", None)
        is_owner = (
            isinstance(user, Partner) and instance.sanatorium.partner_id == user.id
        )
        if is_owner:
            data["detail"] = "Your image(s) are pending approval"
            data["status"] = "pending"
            return data
        return {"detail": "Your image(s) are pending approval", "status": "pending"}


class SanatoriumImageCreateSerializer(serializers.Serializer):
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
                    _(
                        "Invalid image format, allowed are: jpg, jpeg, png, heif, heic"
                    )
                )
        return images

    def validate(self, attrs):
        request = self.context["request"]
        sanatorium_id = self.context.get("sanatorium_id")
        sanatorium = Sanatorium.objects.filter(
            guid=sanatorium_id, partner=request.user
        ).first()
        if sanatorium is None:
            raise serializers.ValidationError(
                {"sanatorium_id": _("Sanatorium not found")}
            )
        self.context["..sanatorium"] = sanatorium
        return attrs

    def create(self, validated_data):
        sanatorium = self.context["..sanatorium"]
        images = validated_data.pop("images", [])
        last_order = (
            sanatorium.images.aggregate(max_order=models.Max("order"))["max_order"]
            or 0
        )
        created_images = []
        for idx, image in enumerate(images, start=1):
            obj = SanatoriumImage.objects.create(
                sanatorium=sanatorium,
                image=image,
                order=last_order + idx,
                is_pending=True,
            )
            created_images.append(obj)

        if sanatorium.is_verified:
            sanatorium.is_verified = False
            sanatorium.save(update_fields=["is_verified"])
        return created_images


# ──────────────────────────────────────────────
# Room images
# ──────────────────────────────────────────────


class SanatoriumRoomImageSerializer(serializers.ModelSerializer):
    guid = serializers.UUIDField(read_only=True)
    image_url = serializers.ImageField(source="image", read_only=True)

    class Meta:
        model = SanatoriumRoomImage
        fields = ["guid", "order", "image_url"]


# ──────────────────────────────────────────────
# Room prices
# ──────────────────────────────────────────────


class SanatoriumRoomPriceSerializer(serializers.ModelSerializer):
    package_type = PackageTypeSerializer(read_only=True)

    class Meta:
        model = SanatoriumRoomPrice
        fields = ["guid", "package_type", "price", "currency"]


# ──────────────────────────────────────────────
# Room serializers
# ──────────────────────────────────────────────


class SanatoriumRoomListSerializer(LanguageFieldMixin, serializers.ModelSerializer):
    room_type = RoomTypeSerializer(read_only=True)
    images = SanatoriumRoomImageSerializer(many=True, read_only=True)
    amenities = RoomAmenitySerializer(many=True, read_only=True)
    prices = SanatoriumRoomPriceSerializer(many=True, read_only=True)

    class Meta:
        model = SanatoriumRoom
        fields = [
            "guid",
            "title",
            "room_type",
            "area",
            "bed_type",
            "bed_count",
            "capacity",
            "amenities",
            "images",
            "prices",
        ]


class SanatoriumRoomDetailSerializer(LanguageFieldMixin, serializers.ModelSerializer):
    description = serializers.SerializerMethodField()
    room_type = RoomTypeSerializer(read_only=True)
    images = SanatoriumRoomImageSerializer(many=True, read_only=True)
    amenities = RoomAmenitySerializer(many=True, read_only=True)
    prices = SanatoriumRoomPriceSerializer(many=True, read_only=True)

    class Meta:
        model = SanatoriumRoom
        fields = [
            "guid",
            "title",
            "description",
            "room_type",
            "area",
            "bed_type",
            "bed_count",
            "capacity",
            "amenities",
            "images",
            "prices",
        ]

    def get_description(self, obj):
        return self.get_lang_field(obj, "description")


# ──────────────────────────────────────────────
# Sanatorium list / detail
# ──────────────────────────────────────────────


class SanatoriumListSerializer(LanguageFieldMixin, serializers.ModelSerializer):
    location = SanatoriumLocationSerializer(read_only=True)
    images = SanatoriumImageSerializer(many=True, read_only=True)
    specializations = MedicalSpecializationSerializer(many=True, read_only=True)
    average_rating = serializers.SerializerMethodField()
    min_price = serializers.SerializerMethodField()
    is_favorite = serializers.SerializerMethodField()

    class Meta:
        model = Sanatorium
        fields = [
            "guid",
            "title",
            "location",
            "images",
            "specializations",
            "average_rating",
            "comment_count",
            "min_price",
            "is_favorite",
        ]

    @staticmethod
    def get_average_rating(obj):
        avg = (
            SanatoriumReview.objects.filter(
                sanatorium=obj, rating__isnull=False, is_hidden=False
            )
            .aggregate(avg=Avg("rating"))
            .get("avg")
        )
        return round(avg, 2) if avg else None

    @staticmethod
    def get_min_price(obj):
        price = (
            SanatoriumRoomPrice.objects.filter(room__sanatorium=obj)
            .order_by("price")
            .values_list("price", flat=True)
            .first()
        )
        return price

    def get_is_favorite(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not isinstance(user, Client):
            return False
        return SanatoriumFavorite.objects.filter(
            client=user, sanatorium=obj
        ).exists()


class SanatoriumDetailSerializer(LanguageFieldMixin, serializers.ModelSerializer):
    description = serializers.SerializerMethodField()
    location = SanatoriumLocationSerializer(read_only=True)
    images = SanatoriumImageSerializer(many=True, read_only=True)
    specializations = MedicalSpecializationSerializer(many=True, read_only=True)
    treatments = TreatmentSerializer(many=True, read_only=True)
    average_rating = serializers.SerializerMethodField()
    is_favorite = serializers.SerializerMethodField()

    class Meta:
        model = Sanatorium
        fields = [
            "guid",
            "title",
            "description",
            "location",
            "images",
            "specializations",
            "treatments",
            "check_in_time",
            "check_out_time",
            "average_rating",
            "comment_count",
            "is_favorite",
        ]

    def get_description(self, obj):
        return self.get_lang_field(obj, "description")

    @staticmethod
    def get_average_rating(obj):
        avg = (
            SanatoriumReview.objects.filter(
                sanatorium=obj, rating__isnull=False, is_hidden=False
            )
            .aggregate(avg=Avg("rating"))
            .get("avg")
        )
        return round(avg, 2) if avg else None

    def get_is_favorite(self, obj):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if not isinstance(user, Client):
            return False
        return SanatoriumFavorite.objects.filter(
            client=user, sanatorium=obj
        ).exists()


class PartnerSanatoriumListSerializer(SanatoriumListSerializer):
    class Meta(SanatoriumListSerializer.Meta):
        fields = SanatoriumListSerializer.Meta.fields + ["verification_status"]


# ──────────────────────────────────────────────
# Sanatorium create / update (Partner)
# ──────────────────────────────────────────────


class SanatoriumCreateSerializer(serializers.ModelSerializer):
    location = SanatoriumLocationSerializer()
    specializations = serializers.SlugRelatedField(
        queryset=MedicalSpecialization.objects.all(),
        many=True,
        slug_field="guid",
        required=False,
        allow_empty=True,
    )
    treatments = serializers.SlugRelatedField(
        queryset=Treatment.objects.all(),
        many=True,
        slug_field="guid",
        required=False,
        allow_empty=True,
    )

    class Meta:
        model = Sanatorium
        fields = [
            "title",
            "description_en",
            "description_ru",
            "description_uz",
            "location",
            "specializations",
            "treatments",
            "check_in_time",
            "check_out_time",
        ]

    duplicate_title_message = _(
        "A ..sanatorium with this title already exists. Please choose a different title."
    )

    @staticmethod
    def _extract_constraint_name(exc):
        cause = getattr(exc, "__cause__", None)
        diag = getattr(cause, "diag", None)
        return getattr(diag, "constraint_name", None)

    def validate_title(self, value):
        if Sanatorium.objects.filter(title=value, is_archived=False).exists():
            raise serializers.ValidationError(self.duplicate_title_message)
        return value

    @transaction.atomic
    def create(self, validated_data):
        request = self.context["request"]
        location_data = validated_data.pop("location")
        specializations = validated_data.pop("specializations", [])
        treatments = validated_data.pop("treatments", [])

        location = SanatoriumLocation.objects.create(**location_data)
        address = f"{location.city}, {location.country}".strip(", ") or ""
        try:
            sanatorium = Sanatorium.objects.create(
                location=location,
                partner=request.user,
                address=address,
                **validated_data,
            )
        except IntegrityError as exc:
            constraint_name = self._extract_constraint_name(exc)
            title = validated_data.get("title")

            if constraint_name == "unique_active_sanatorium_title" or (
                title
                and Sanatorium.objects.filter(title=title, is_archived=False).exists()
            ):
                raise serializers.ValidationError(
                    {"title": self.duplicate_title_message}
                )
            raise serializers.ValidationError(
                {
                    "detail": _(
                        "Could not create ..sanatorium. Check whether the title is unique and try again."
                    )
                }
            )
        sanatorium.specializations.set(specializations)
        for treatment in treatments:
            SanatoriumTreatment.objects.create(sanatorium=sanatorium, treatment=treatment)
        return sanatorium


# ──────────────────────────────────────────────
# Review serializers
# ──────────────────────────────────────────────


class ReviewClientSerializer(serializers.ModelSerializer):
    class Meta:
        model = Client
        fields = ["guid", "first_name", "last_name"]


class SanatoriumReviewSerializer(serializers.ModelSerializer):
    guid = serializers.UUIDField(read_only=True)
    client = ReviewClientSerializer(read_only=True)

    class Meta:
        model = SanatoriumReview
        fields = ["guid", "client", "rating", "comment", "created_at"]


class SanatoriumReviewCreateSerializer(serializers.ModelSerializer):
    rating = serializers.DecimalField(
        max_digits=2,
        decimal_places=1,
        required=True,
        allow_null=True,
    )
    comment = serializers.CharField(required=False)

    class Meta:
        model = SanatoriumReview
        fields = ["rating", "comment"]

    @staticmethod
    def validate_rating(value):
        value = Decimal(value) if value is not None else Decimal("0.0")
        if not (1.0 <= value <= 5.0):
            raise serializers.ValidationError(_("Rating must be between 1 and 5"))
        return value

    def validate(self, attrs):
        request = self.context.get("request")
        client = request.user
        sanatorium = self.context["..sanatorium"]

        has_eligible_booking = SanatoriumBooking.objects.filter(
            client=client,
            sanatorium=sanatorium,
            status__in=[
                SanatoriumBooking.BookingStatus.CONFIRMED,
                SanatoriumBooking.BookingStatus.COMPLETED,
                SanatoriumBooking.BookingStatus.CANCELLED,
            ],
        ).exists()

        if not has_eligible_booking:
            raise serializers.ValidationError(
                _("You can leave a review only for accepted or completed bookings")
            )
        return attrs

    def create(self, validated_data):
        client = self.context["request"].user
        sanatorium = self.context["..sanatorium"]
        return SanatoriumReview.objects.create(
            client=client,
            sanatorium=sanatorium,
            **validated_data,
        )


# ──────────────────────────────────────────────
# Calendar
# ──────────────────────────────────────────────


class RoomCalendarDateSerializer(serializers.ModelSerializer):
    class Meta:
        model = RoomCalendarDate
        fields = ["date", "status"]


class RoomCalendarDateRangeSerializer(serializers.Serializer):
    from_date = serializers.DateField()
    to_date = serializers.DateField(required=False)

    def validate(self, attrs):
        from_date = attrs["from_date"]
        to_date = attrs.get("to_date", from_date)
        if to_date < from_date:
            raise serializers.ValidationError(
                _("to_date must be after or equal to from_date")
            )
        attrs["to_date"] = to_date
        return attrs


# ──────────────────────────────────────────────
# Booking serializers
# ──────────────────────────────────────────────


class SanatoriumBookingCreateSerializer(serializers.Serializer):
    sanatorium_id = serializers.UUIDField()
    room_id = serializers.UUIDField()
    card_id = serializers.CharField()
    check_in = serializers.DateField()
    package_type_id = serializers.UUIDField()
    treatment_id = serializers.UUIDField(required=False, allow_null=True)
    specialization_id = serializers.UUIDField(required=False, allow_null=True)


class SanatoriumBookingPriceSerializer(serializers.ModelSerializer):
    class Meta:
        model = SanatoriumBookingPrice
        fields = [
            "guid",
            "subtotal",
            "hold_amount",
            "charge_amount",
            "service_fee",
            "service_fee_percentage",
        ]


class SanatoriumBookingListSerializer(serializers.ModelSerializer):
    sanatorium_title = serializers.CharField(
        source="..sanatorium.title", read_only=True
    )
    room_title = serializers.CharField(source="room.title", read_only=True)
    room_type = serializers.SerializerMethodField()
    package = PackageTypeSerializer(source="package_type", read_only=True)
    treatment = TreatmentSerializer(read_only=True)
    specialization = MedicalSpecializationSerializer(read_only=True)
    booking_price = SanatoriumBookingPriceSerializer(read_only=True)
    sanatorium_image = serializers.SerializerMethodField()

    class Meta:
        model = SanatoriumBooking
        fields = [
            "guid",
            "sanatorium_title",
            "sanatorium_image",
            "room_title",
            "room_type",
            "package",
            "treatment",
            "specialization",
            "check_in",
            "check_out",
            "booking_number",
            "status",
            "booking_price",
            "cancellation_reason",
            "confirmed_at",
            "cancelled_at",
            "completed_at",
        ]

    @staticmethod
    def get_room_type(obj):
        rt = obj.room.room_type
        return {"guid": rt.guid, "title": rt.title_ru}

    @staticmethod
    def get_sanatorium_image(obj):
        img = obj.sanatorium.images.filter(is_pending=False).order_by("order").first()
        if img and img.image:
            return img.image.url
        return None


class ClientSanatoriumBookingDetailSerializer(serializers.ModelSerializer):
    sanatorium = SanatoriumDetailSerializer(read_only=True)
    room = SanatoriumRoomListSerializer(read_only=True)
    package = PackageTypeSerializer(source="package_type", read_only=True)
    treatment = TreatmentSerializer(read_only=True)
    specialization = MedicalSpecializationSerializer(read_only=True)
    booking_price = SanatoriumBookingPriceSerializer(read_only=True)
    partner_name = serializers.CharField(
        source="..sanatorium.partner.first_name", read_only=True
    )
    partner_phone = serializers.CharField(
        source="..sanatorium.partner.phone_number", read_only=True
    )

    class Meta:
        model = SanatoriumBooking
        fields = [
            "guid",
            "sanatorium",
            "room",
            "package",
            "treatment",
            "specialization",
            "check_in",
            "check_out",
            "booking_number",
            "status",
            "booking_price",
            "partner_name",
            "partner_phone",
            "cancellation_reason",
            "confirmed_at",
            "cancelled_at",
            "completed_at",
        ]
