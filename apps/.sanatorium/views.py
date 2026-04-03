from datetime import timedelta

from django.db.models import Avg, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from django_filters.rest_framework import DjangoFilterBackend
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema

from rest_framework import status, parsers
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.filters import SearchFilter, OrderingFilter
from rest_framework.generics import (
    ListAPIView,
    ListCreateAPIView,
    RetrieveAPIView,
)
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from users.authentication import ClientJWTAuthentication, PartnerJWTAuthentication
from shared.permissions import IsPartner, IsClient

from .filters import SanatoriumFilter, SanatoriumRoomFilter
from .models import (
    MedicalSpecialization,
    Treatment,
    RoomType,
    PackageType,
    RoomAmenity,
    Sanatorium,
    SanatoriumRoom,
    RoomCalendarDate,
    SanatoriumReview,
    SanatoriumFavorite,
    SanatoriumBooking,
)
from .serializers import (
    MedicalSpecializationSerializer,
    TreatmentSerializer,
    RoomTypeSerializer,
    PackageTypeSerializer,
    RoomAmenitySerializer,
    SanatoriumListSerializer,
    SanatoriumDetailSerializer,
    PartnerSanatoriumListSerializer,
    SanatoriumCreateSerializer,
    SanatoriumImageSerializer,
    SanatoriumImageCreateSerializer,
    SanatoriumRoomListSerializer,
    SanatoriumRoomDetailSerializer,
    RoomCalendarDateSerializer,
    RoomCalendarDateRangeSerializer,
    SanatoriumReviewSerializer,
    SanatoriumReviewCreateSerializer,
    SanatoriumBookingCreateSerializer,
    SanatoriumBookingListSerializer,
    ClientSanatoriumBookingDetailSerializer,
)
from .services import (
    create_sanatorium_booking,
    check_room_availability,
    release_room_dates,
)


sanatorium_id_param = openapi.Parameter(
    "sanatorium_id",
    openapi.IN_PATH,
    description="Sanatorium GUID",
    type=openapi.TYPE_STRING,
    format=openapi.FORMAT_UUID,
)

room_id_param = openapi.Parameter(
    "room_id",
    openapi.IN_PATH,
    description="Room GUID",
    type=openapi.TYPE_STRING,
    format=openapi.FORMAT_UUID,
)

booking_id_param = openapi.Parameter(
    "booking_id",
    openapi.IN_PATH,
    description="Booking GUID",
    type=openapi.TYPE_STRING,
    format=openapi.FORMAT_UUID,
)


# ──────────────────────────────────────────────
# Lookup endpoints
# ──────────────────────────────────────────────


class MedicalSpecializationListView(ListAPIView):
    queryset = MedicalSpecialization.objects.all()
    serializer_class = MedicalSpecializationSerializer

    @swagger_auto_schema(
        tags=["Sanatorium"],
        operation_summary="List medical specializations",
        responses={status.HTTP_200_OK: MedicalSpecializationSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class TreatmentListView(ListAPIView):
    queryset = Treatment.objects.all()
    serializer_class = TreatmentSerializer

    @swagger_auto_schema(
        tags=["Sanatorium"],
        operation_summary="List treatments",
        responses={status.HTTP_200_OK: TreatmentSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class RoomTypeListView(ListAPIView):
    queryset = RoomType.objects.all()
    serializer_class = RoomTypeSerializer

    @swagger_auto_schema(
        tags=["Sanatorium"],
        operation_summary="List room types",
        responses={status.HTTP_200_OK: RoomTypeSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class PackageTypeListView(ListAPIView):
    queryset = PackageType.objects.all().order_by("duration_days")
    serializer_class = PackageTypeSerializer

    @swagger_auto_schema(
        tags=["Sanatorium"],
        operation_summary="List package types",
        responses={status.HTTP_200_OK: PackageTypeSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class RoomAmenityListView(ListAPIView):
    queryset = RoomAmenity.objects.all()
    serializer_class = RoomAmenitySerializer

    @swagger_auto_schema(
        tags=["Sanatorium"],
        operation_summary="List room amenities",
        responses={status.HTTP_200_OK: RoomAmenitySerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


# ──────────────────────────────────────────────
# Sanatorium list / detail (public)
# ──────────────────────────────────────────────


class SanatoriumListView(ListAPIView):
    serializer_class = SanatoriumListSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_class = SanatoriumFilter
    search_fields = ["title"]
    ordering_fields = ["title", "comment_count"]
    ordering = ["-comment_count"]

    def get_queryset(self):
        return (
            Sanatorium.objects.filter(is_verified=True)
            .select_related("location")
            .prefetch_related("images", "specializations")
        )

    @swagger_auto_schema(
        tags=["Sanatorium"],
        operation_summary="List verified sanatoriums",
        responses={status.HTTP_200_OK: SanatoriumListSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class SanatoriumDetailView(RetrieveAPIView):
    serializer_class = SanatoriumDetailSerializer
    lookup_field = "guid"
    lookup_url_kwarg = "sanatorium_id"

    def get_queryset(self):
        return (
            Sanatorium.objects.filter(is_verified=True)
            .select_related("location")
            .prefetch_related("images", "specializations", "treatments")
        )

    @swagger_auto_schema(
        tags=["Sanatorium"],
        operation_summary="Retrieve ..sanatorium details",
        manual_parameters=[sanatorium_id_param],
        responses={status.HTTP_200_OK: SanatoriumDetailSerializer},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


# ──────────────────────────────────────────────
# Rooms
# ──────────────────────────────────────────────


class SanatoriumRoomListView(ListAPIView):
    serializer_class = SanatoriumRoomListSerializer
    filter_backends = [DjangoFilterBackend]
    filterset_class = SanatoriumRoomFilter

    def get_queryset(self):
        sanatorium_id = self.kwargs["sanatorium_id"]
        return (
            SanatoriumRoom.objects.filter(sanatorium__guid=sanatorium_id)
            .select_related("room_type")
            .prefetch_related("images", "amenities", "prices__package_type")
        )

    @swagger_auto_schema(
        tags=["Sanatorium"],
        operation_summary="List rooms for a ..sanatorium",
        manual_parameters=[sanatorium_id_param],
        responses={status.HTTP_200_OK: SanatoriumRoomListSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class SanatoriumRoomDetailView(RetrieveAPIView):
    serializer_class = SanatoriumRoomDetailSerializer

    def get_object(self):
        sanatorium_id = self.kwargs["sanatorium_id"]
        room_id = self.kwargs["room_id"]
        room = (
            SanatoriumRoom.objects.filter(
                sanatorium__guid=sanatorium_id, guid=room_id
            )
            .select_related("room_type")
            .prefetch_related("images", "amenities", "prices__package_type")
            .first()
        )
        if not room:
            raise NotFound(_("Room not found"))
        return room

    @swagger_auto_schema(
        tags=["Sanatorium"],
        operation_summary="Retrieve room details",
        manual_parameters=[sanatorium_id_param, room_id_param],
        responses={status.HTTP_200_OK: SanatoriumRoomDetailSerializer},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


# ──────────────────────────────────────────────
# Room calendar
# ──────────────────────────────────────────────


class RoomCalendarDateListView(ListAPIView):
    serializer_class = RoomCalendarDateSerializer

    def get_queryset(self):
        room_id = self.kwargs["room_id"]
        return RoomCalendarDate.objects.filter(room__guid=room_id).order_by("date")

    @swagger_auto_schema(
        tags=["Sanatorium"],
        operation_summary="List room calendar dates",
        manual_parameters=[sanatorium_id_param, room_id_param],
        responses={status.HTTP_200_OK: RoomCalendarDateSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class RoomCalendarDateBlockView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    @swagger_auto_schema(
        tags=["Sanatorium"],
        operation_summary="Block room dates",
        manual_parameters=[sanatorium_id_param, room_id_param],
        request_body=RoomCalendarDateRangeSerializer,
    )
    def post(self, request, sanatorium_id, room_id):
        serializer = RoomCalendarDateRangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        room = SanatoriumRoom.objects.filter(
            guid=room_id,
            sanatorium__guid=sanatorium_id,
            sanatorium__partner=request.user,
        ).first()
        if not room:
            raise NotFound(_("Room not found"))

        from_date = serializer.validated_data["from_date"]
        to_date = serializer.validated_data["to_date"]
        current = from_date
        while current <= to_date:
            RoomCalendarDate.objects.update_or_create(
                room=room,
                date=current,
                defaults={"status": RoomCalendarDate.CalendarStatus.BLOCKED},
            )
            current += timedelta(days=1)

        return Response(
            {"detail": _("Dates blocked successfully")},
            status=status.HTTP_200_OK,
        )


class RoomCalendarDateUnblockView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    @swagger_auto_schema(
        tags=["Sanatorium"],
        operation_summary="Unblock room dates",
        manual_parameters=[sanatorium_id_param, room_id_param],
        request_body=RoomCalendarDateRangeSerializer,
    )
    def post(self, request, sanatorium_id, room_id):
        serializer = RoomCalendarDateRangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        room = SanatoriumRoom.objects.filter(
            guid=room_id,
            sanatorium__guid=sanatorium_id,
            sanatorium__partner=request.user,
        ).first()
        if not room:
            raise NotFound(_("Room not found"))

        from_date = serializer.validated_data["from_date"]
        to_date = serializer.validated_data["to_date"]

        RoomCalendarDate.objects.filter(
            room=room,
            date__gte=from_date,
            date__lte=to_date,
            status=RoomCalendarDate.CalendarStatus.BLOCKED,
        ).delete()

        return Response(
            {"detail": _("Dates unblocked successfully")},
            status=status.HTTP_200_OK,
        )


# ──────────────────────────────────────────────
# Reviews
# ──────────────────────────────────────────────


class SanatoriumReviewListCreateView(ListCreateAPIView):
    authentication_classes = [ClientJWTAuthentication]

    def get_permissions(self):
        if self.request.method == "POST":
            return [IsClient()]
        return [AllowAny()]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return SanatoriumReviewCreateSerializer
        return SanatoriumReviewSerializer

    def get_sanatorium(self):
        sanatorium = Sanatorium.objects.filter(
            guid=self.kwargs["sanatorium_id"], is_verified=True
        ).first()
        if not sanatorium:
            raise NotFound(_("Sanatorium not found"))
        return sanatorium

    def get_queryset(self):
        sanatorium = self.get_sanatorium()
        return SanatoriumReview.objects.filter(
            sanatorium=sanatorium, is_hidden=False
        ).select_related("client")

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["..sanatorium"] = self.get_sanatorium()
        return ctx

    @swagger_auto_schema(
        tags=["Sanatorium"],
        operation_summary="List ..sanatorium reviews",
        manual_parameters=[sanatorium_id_param],
        responses={status.HTTP_200_OK: SanatoriumReviewSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @swagger_auto_schema(
        tags=["Sanatorium"],
        operation_summary="Create ..sanatorium review",
        manual_parameters=[sanatorium_id_param],
        request_body=SanatoriumReviewCreateSerializer,
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


# ──────────────────────────────────────────────
# Favorites
# ──────────────────────────────────────────────


class SanatoriumFavoriteToggleView(APIView):
    authentication_classes = [ClientJWTAuthentication]
    permission_classes = [IsClient]

    @swagger_auto_schema(
        tags=["Sanatorium"],
        operation_summary="Toggle ..sanatorium favorite",
        manual_parameters=[sanatorium_id_param],
    )
    def post(self, request, sanatorium_id):
        sanatorium = Sanatorium.objects.filter(
            guid=sanatorium_id, is_verified=True
        ).first()
        if not sanatorium:
            raise NotFound(_("Sanatorium not found"))

        fav, created = SanatoriumFavorite.objects.get_or_create(
            client=request.user, sanatorium=sanatorium
        )
        if not created:
            fav.delete()
            return Response(
                {"detail": _("Removed from favorites"), "is_favorite": False},
                status=status.HTTP_200_OK,
            )
        return Response(
            {"detail": _("Added to favorites"), "is_favorite": True},
            status=status.HTTP_201_CREATED,
        )


# ──────────────────────────────────────────────
# Partner CRUD
# ──────────────────────────────────────────────


class PartnerSanatoriumListCreateView(ListCreateAPIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return SanatoriumCreateSerializer
        return PartnerSanatoriumListSerializer

    def get_queryset(self):
        return (
            Sanatorium.objects.filter(partner=self.request.user)
            .select_related("location")
            .prefetch_related("images", "specializations")
        )

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sanatorium = serializer.save()
        return Response(
            {
                "detail": _("Sanatorium created, pending verification"),
                "sanatorium_id": str(sanatorium.guid),
                "status_code": 201,
            },
            status=status.HTTP_201_CREATED,
        )

    @swagger_auto_schema(
        tags=["Sanatorium"],
        operation_summary="Partner: list own sanatoriums",
        responses={status.HTTP_200_OK: PartnerSanatoriumListSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @swagger_auto_schema(
        tags=["Sanatorium"],
        operation_summary="Partner: create ..sanatorium",
        request_body=SanatoriumCreateSerializer,
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class PartnerSanatoriumImageUploadView(APIView):
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    @swagger_auto_schema(
        tags=["Sanatorium"],
        operation_summary="Partner: upload ..sanatorium images",
        manual_parameters=[sanatorium_id_param],
    )
    def post(self, request, sanatorium_id):
        serializer = SanatoriumImageCreateSerializer(
            data=request.data,
            context={"request": request, "sanatorium_id": sanatorium_id},
        )
        serializer.is_valid(raise_exception=True)
        images = serializer.save()

        sanatorium = serializer.context["..sanatorium"]
        if not sanatorium.is_verified:
            return Response(
                {"detail": "Your image(s) are pending approval", "status": "pending"},
                status=status.HTTP_200_OK,
            )
        return Response(
            SanatoriumImageSerializer(
                images, many=True, context={"request": request}
            ).data,
            status=status.HTTP_201_CREATED,
        )


# ──────────────────────────────────────────────
# Client Booking
# ──────────────────────────────────────────────


class ClientSanatoriumBookingListCreateView(APIView):
    authentication_classes = [ClientJWTAuthentication]
    permission_classes = [IsClient]

    @swagger_auto_schema(
        tags=["Sanatorium Booking"],
        operation_summary="Client: list ..sanatorium bookings",
    )
    def get(self, request):
        bookings = (
            SanatoriumBooking.objects.filter(client=request.user)
            .exclude(
                status__in=[
                    SanatoriumBooking.BookingStatus.COMPLETED,
                    SanatoriumBooking.BookingStatus.CANCELLED,
                ]
            )
            .select_related(
                "sanatorium", "room", "room__room_type",
                "package_type", "treatment", "specialization",
            )
            .prefetch_related("sanatorium__images")
            .order_by("-created_at")
        )
        serializer = SanatoriumBookingListSerializer(
            bookings, many=True, context={"request": request}
        )
        return Response(serializer.data)

    @swagger_auto_schema(
        tags=["Sanatorium Booking"],
        operation_summary="Client: create ..sanatorium booking",
        request_body=SanatoriumBookingCreateSerializer,
    )
    def post(self, request):
        serializer = SanatoriumBookingCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        sanatorium = Sanatorium.objects.filter(
            guid=data["sanatorium_id"], is_verified=True
        ).first()
        if not sanatorium:
            raise NotFound(_("Sanatorium not found"))

        room = SanatoriumRoom.objects.filter(
            guid=data["room_id"], sanatorium=sanatorium
        ).first()
        if not room:
            raise NotFound(_("Room not found"))

        package_type = PackageType.objects.filter(
            guid=data["package_type_id"]
        ).first()
        if not package_type:
            raise NotFound(_("Package type not found"))

        treatment = None
        if data.get("treatment_id"):
            treatment = Treatment.objects.filter(guid=data["treatment_id"]).first()

        specialization = None
        if data.get("specialization_id"):
            specialization = MedicalSpecialization.objects.filter(
                guid=data["specialization_id"]
            ).first()

        booking = create_sanatorium_booking(
            client=request.user,
            sanatorium=sanatorium,
            room=room,
            package_type=package_type,
            check_in=data["check_in"],
            treatment=treatment,
            specialization=specialization,
            card_id=data.get("card_id"),
        )

        return Response(
            {
                "detail": _("Booking created successfully"),
                "booking_id": str(booking.guid),
                "booking_number": booking.booking_number,
                "status_code": 201,
            },
            status=status.HTTP_201_CREATED,
        )


class ClientSanatoriumBookingDetailView(RetrieveAPIView):
    authentication_classes = [ClientJWTAuthentication]
    permission_classes = [IsClient]
    serializer_class = ClientSanatoriumBookingDetailSerializer

    def get_object(self):
        booking = (
            SanatoriumBooking.objects.filter(
                guid=self.kwargs["booking_id"], client=self.request.user
            )
            .select_related(
                "sanatorium",
                "sanatorium__location",
                "room",
                "room__room_type",
                "package_type",
                "treatment",
                "specialization",
            )
            .prefetch_related(
                "sanatorium__images",
                "room__images",
                "room__amenities",
                "room__prices__package_type",
            )
            .first()
        )
        if not booking:
            raise NotFound(_("Booking not found"))
        return booking

    @swagger_auto_schema(
        tags=["Sanatorium Booking"],
        operation_summary="Client: booking detail",
        manual_parameters=[booking_id_param],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class ClientSanatoriumBookingCancelView(APIView):
    authentication_classes = [ClientJWTAuthentication]
    permission_classes = [IsClient]

    @swagger_auto_schema(
        tags=["Sanatorium Booking"],
        operation_summary="Client: cancel booking",
        manual_parameters=[booking_id_param],
    )
    def post(self, request, booking_id):
        booking = SanatoriumBooking.objects.filter(
            guid=booking_id,
            client=request.user,
            status__in=[
                SanatoriumBooking.BookingStatus.PENDING,
                SanatoriumBooking.BookingStatus.CONFIRMED,
            ],
        ).first()
        if not booking:
            raise NotFound(_("Booking not found or cannot be cancelled"))

        booking.status = SanatoriumBooking.BookingStatus.CANCELLED
        booking.cancellation_reason = (
            SanatoriumBooking.BookingCancellationReason.USER_CANCELLED
        )
        booking.cancelled_at = timezone.now()
        booking.save(
            update_fields=["status", "cancellation_reason", "cancelled_at"]
        )
        release_room_dates(booking.room, booking.check_in, booking.check_out)

        return Response(
            {"detail": _("Booking cancelled")}, status=status.HTTP_200_OK
        )


class ClientSanatoriumBookingHistoryView(ListAPIView):
    authentication_classes = [ClientJWTAuthentication]
    permission_classes = [IsClient]
    serializer_class = SanatoriumBookingListSerializer

    def get_queryset(self):
        return (
            SanatoriumBooking.objects.filter(
                client=self.request.user,
                status__in=[
                    SanatoriumBooking.BookingStatus.COMPLETED,
                    SanatoriumBooking.BookingStatus.CANCELLED,
                ],
            )
            .select_related(
                "sanatorium", "room", "room__room_type",
                "package_type", "treatment", "specialization",
            )
            .prefetch_related("sanatorium__images")
            .order_by("-created_at")
        )

    @swagger_auto_schema(
        tags=["Sanatorium Booking"],
        operation_summary="Client: booking history",
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


# ──────────────────────────────────────────────
# Partner Booking management
# ──────────────────────────────────────────────


class PartnerSanatoriumBookingListView(ListAPIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]
    serializer_class = SanatoriumBookingListSerializer

    def get_queryset(self):
        return (
            SanatoriumBooking.objects.filter(
                sanatorium__partner=self.request.user
            )
            .select_related(
                "sanatorium", "room", "room__room_type",
                "package_type", "treatment", "specialization",
            )
            .prefetch_related("sanatorium__images")
            .order_by("-created_at")
        )

    @swagger_auto_schema(
        tags=["Sanatorium Booking"],
        operation_summary="Partner: list all bookings",
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class PartnerSanatoriumBookingAcceptView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    @swagger_auto_schema(
        tags=["Sanatorium Booking"],
        operation_summary="Partner: accept booking",
        manual_parameters=[booking_id_param],
    )
    def post(self, request, booking_id):
        booking = SanatoriumBooking.objects.filter(
            guid=booking_id,
            sanatorium__partner=request.user,
            status=SanatoriumBooking.BookingStatus.PENDING,
        ).first()
        if not booking:
            raise NotFound(_("Booking not found"))

        booking.status = SanatoriumBooking.BookingStatus.CONFIRMED
        booking.confirmed_at = timezone.now()
        booking.save(update_fields=["status", "confirmed_at"])

        return Response(
            {"detail": _("Booking confirmed")}, status=status.HTTP_200_OK
        )


class PartnerSanatoriumBookingCancelView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    @swagger_auto_schema(
        tags=["Sanatorium Booking"],
        operation_summary="Partner: cancel booking",
        manual_parameters=[booking_id_param],
    )
    def post(self, request, booking_id):
        booking = SanatoriumBooking.objects.filter(
            guid=booking_id,
            sanatorium__partner=request.user,
            status__in=[
                SanatoriumBooking.BookingStatus.PENDING,
                SanatoriumBooking.BookingStatus.CONFIRMED,
            ],
        ).first()
        if not booking:
            raise NotFound(_("Booking not found or cannot be cancelled"))

        booking.status = SanatoriumBooking.BookingStatus.CANCELLED
        booking.cancellation_reason = (
            SanatoriumBooking.BookingCancellationReason.PARTNER_CANCELLED
        )
        booking.cancelled_at = timezone.now()
        booking.save(
            update_fields=["status", "cancellation_reason", "cancelled_at"]
        )
        release_room_dates(booking.room, booking.check_in, booking.check_out)

        return Response(
            {"detail": _("Booking cancelled")}, status=status.HTTP_200_OK
        )


class PartnerSanatoriumBookingCompleteView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    @swagger_auto_schema(
        tags=["Sanatorium Booking"],
        operation_summary="Partner: complete booking",
        manual_parameters=[booking_id_param],
    )
    def post(self, request, booking_id):
        booking = SanatoriumBooking.objects.filter(
            guid=booking_id,
            sanatorium__partner=request.user,
            status=SanatoriumBooking.BookingStatus.CONFIRMED,
        ).first()
        if not booking:
            raise NotFound(_("Booking not found"))

        booking.status = SanatoriumBooking.BookingStatus.COMPLETED
        booking.completed_at = timezone.now()
        booking.save(update_fields=["status", "completed_at"])

        return Response(
            {"detail": _("Booking completed")}, status=status.HTTP_200_OK
        )


class PartnerSanatoriumBookingNoShowView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    @swagger_auto_schema(
        tags=["Sanatorium Booking"],
        operation_summary="Partner: mark no-show",
        manual_parameters=[booking_id_param],
    )
    def post(self, request, booking_id):
        booking = SanatoriumBooking.objects.filter(
            guid=booking_id,
            sanatorium__partner=request.user,
            status=SanatoriumBooking.BookingStatus.CONFIRMED,
        ).first()
        if not booking:
            raise NotFound(_("Booking not found"))

        booking.status = SanatoriumBooking.BookingStatus.CANCELLED
        booking.cancellation_reason = (
            SanatoriumBooking.BookingCancellationReason.USER_NO_SHOW
        )
        booking.cancelled_at = timezone.now()
        booking.save(
            update_fields=["status", "cancellation_reason", "cancelled_at"]
        )

        return Response(
            {"detail": _("Marked as no-show")}, status=status.HTTP_200_OK
        )
