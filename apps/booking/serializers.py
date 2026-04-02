from datetime import timedelta

from rest_framework import serializers

from django.db.models import Avg
from django.utils.translation import gettext_lazy as _

from .models import CalendarDate, Booking, BookingPrice
from .mixins import DateRangeValidationMixin
from users.models import Partner
from users.models.clients import Client
from property.models import PropertyReview


class CalendarDateSerializer(serializers.ModelSerializer):
    class Meta:
        model = CalendarDate
        fields = ["date", "status"]


class PropertyCalendarDateSerializer(serializers.Serializer):
    guid = serializers.UUIDField()
    calendar = CalendarDateSerializer(many=True)


class PropertyCalendarDateRangeSerializer(
    DateRangeValidationMixin,
    serializers.Serializer,
):

    from_date = serializers.DateField()
    to_date = serializers.DateField(required=False)

    def validate(self, attrs):
        return self.validate_date_range(attrs)


class ClientBookingCreateSerializer(
    DateRangeValidationMixin,
    serializers.Serializer,
):
    property_id = serializers.UUIDField()
    card_id = serializers.CharField()
    check_in = serializers.DateField()
    check_out = serializers.DateField()
    adults = serializers.IntegerField(min_value=1)
    children = serializers.IntegerField(min_value=0, required=False, default=0)
    babies = serializers.IntegerField(min_value=0, max_value=5, required=False, default=0)

    start_field = "check_in"
    end_field = "check_out"
    is_single_day = False

    def validate(self, attrs):
        attrs = self.validate_date_range(attrs)

        check_in = attrs["check_in"]
        check_out = attrs["check_out"]
        property = self.context["property"]

        guests = attrs["adults"] + attrs.get("children", 0)
        if guests <= 0 or guests > 15:
            raise serializers.ValidationError(
                _("The total number of adults and children shouldn't greater than 15")
            )

        nights = (check_out - check_in).days
        if property.minimum_weekend_day_stay:
            if check_in.weekday() == 4 and nights < 2:
                raise serializers.ValidationError(
                    _(
                        "This property requires minimum 2 nights stay, when booking starts on Friday"
                    )
                )

        if getattr(property, "weekend_only_sunday_inclusive", False):
            if check_in.weekday() not in (4, 5):
                raise serializers.ValidationError(
                    _(
                        "This property can only be booked with check-in on Friday or Saturday."
                    )
                )
            day = check_in
            has_sunday = False
            while day < check_out:
                if day.weekday() == 6:  # Sunday
                    has_sunday = True
                    break
                day += timedelta(days=1)
            if not has_sunday:
                raise serializers.ValidationError(
                    _(
                        "This property requires the stay to include Sunday. "
                        "Please choose check-out on Monday or later."
                    )
                )

        return attrs


class PropertyLocationBookingSerializer(serializers.Serializer):
    latitude = serializers.CharField()
    longitude = serializers.CharField()
    city = serializers.CharField()
    country = serializers.CharField()


class PropertyBookingSerializer(serializers.Serializer):
    guid = serializers.UUIDField(read_only=True)
    title = serializers.CharField(read_only=True)
    property_images = serializers.SerializerMethodField("get_property_images")

    def get_property_images(self, obj):
        request = self.context.get("request")
        images = getattr(obj, "property_images", None)
        if not images:
            return []

        property_images = images.filter(is_pending=False).order_by("order")

        partner = getattr(request, "user", None)
        if isinstance(partner, Partner):
            property_images = property_images[:1]

        result = []
        for property_image in property_images:
            if property_image.image:
                url = property_image.image.url
                if request:
                    url = request.build_absolute_uri(url)
                result.append(url)

        return result


class ClientBookingSerializer(serializers.Serializer):
    first_name = serializers.CharField(read_only=True)
    last_name = serializers.CharField(read_only=True)


class PartnerBookingSerializer(serializers.Serializer):
    username = serializers.CharField(read_only=True)
    first_name = serializers.CharField(read_only=True)
    last_name = serializers.CharField(read_only=True)
    phone_number = serializers.CharField(read_only=True)


class BookingPriceSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookingPrice
        fields = [
            "guid",
            "subtotal",
            "hold_amount",
            "charge_amount",
            "service_fee",
            "service_fee_percentage",
        ]


class PartnerBookingListSerializer(serializers.ModelSerializer):
    property = PropertyBookingSerializer()
    client = ClientBookingSerializer()
    booking_price = BookingPriceSerializer()

    class Meta:
        model = Booking
        fields = [
            "guid",
            "property",
            "client",
            "check_in",
            "check_out",
            "adults",
            "children",
            "babies",
            "booking_price",
            "booking_number",
            "status",
            "cancellation_reason",
            "confirmed_at",
            "cancelled_at",
            "completed_at",
        ]


class ClientBookingListSerializer(serializers.ModelSerializer):
    property = PropertyBookingSerializer(read_only=True)
    partner = PartnerBookingSerializer(source="property.partner", read_only=True)

    class Meta:
        model = Booking
        fields = [
            "guid",
            "property",
            "partner",
            "status",
        ]


class ClientBookingDetailSerializer(serializers.ModelSerializer):
    partner = PartnerBookingSerializer(source="property.partner", read_only=True)
    property = PropertyLocationBookingSerializer(
        source="property.property_location", read_only=True
    )

    class Meta:
        model = Booking
        fields = ["guid", "partner", "property", "check_in", "check_out"]


class PropertyBookingHistorySerializer(serializers.Serializer):
    guid = serializers.UUIDField(read_only=True)
    title = serializers.CharField(read_only=True)
    image_url = serializers.SerializerMethodField("get_image_url")

    def get_image_url(self, obj):
        request = self.context.get("request")

        images = getattr(obj, "property_images", None)
        if not images:
            return None

        first_image = images.filter(is_pending=False).order_by("order").first()
        if first_image and first_image.image:
            url = first_image.image.url
            return request.build_absolute_uri(url)
        return None


class ClientBookingHistoryListSerializer(serializers.ModelSerializer):
    property_type = serializers.CharField(
        source="property.property_type.title",
        read_only=True,
    )
    property = PropertyBookingHistorySerializer(read_only=True)

    class Meta:
        model = Booking
        fields = ["guid", "property_type", "property", "status", "created_at"]


class PropertyBookingHistoryDetailSerializer(PropertyBookingHistorySerializer):
    property_location = PropertyLocationBookingSerializer(read_only=True)
    average_rating = serializers.SerializerMethodField("get_average_rating")

    @staticmethod
    def get_average_rating(obj):
        average_rating = (
            PropertyReview.objects.filter(
                property=obj, rating__isnull=False, is_hidden=False
            )
            .aggregate(avg=Avg("rating"))
            .get("avg")
        )
        return round(average_rating, 2) if average_rating else 1.0


class ClientBookingHistoryDetailSerializer(serializers.ModelSerializer):
    property = PropertyBookingHistoryDetailSerializer()
    booking_price = BookingPriceSerializer(read_only=True)

    class Meta:
        model = Booking
        fields = [
            "guid",
            "check_in",
            "check_out",
            "property",
            "booking_price",
            "booking_number",
        ]


class AdminBookingClientSerializer(serializers.ModelSerializer):
    class Meta:
        model = Client
        fields = ["id", "first_name", "last_name", "phone_number"]


class AdminBookingPropertySerializer(serializers.Serializer):
    guid = serializers.UUIDField(read_only=True)
    title = serializers.CharField(read_only=True)
    property_type = serializers.CharField(
        source="property_type.title",
        read_only=True,
    )


class AdminBookingPriceSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookingPrice
        fields = ["subtotal", "hold_amount", "charge_amount", "service_fee"]


class AdminBookingListSerializer(serializers.ModelSerializer):
    client = AdminBookingClientSerializer(read_only=True)
    property = AdminBookingPropertySerializer(read_only=True)
    booking_price = AdminBookingPriceSerializer(read_only=True)

    class Meta:
        model = Booking
        fields = [
            "guid",
            "booking_number",
            "check_in",
            "check_out",
            "adults",
            "children",
            "babies",
            "status",
            "cancellation_reason",
            "confirmed_at",
            "cancelled_at",
            "completed_at",
            "created_at",
            "client",
            "property",
            "booking_price",
        ]
