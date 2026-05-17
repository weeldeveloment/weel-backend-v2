from datetime import timedelta
from types import SimpleNamespace

from django.core.cache import cache
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.generics import ListAPIView, GenericAPIView, ListCreateAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.filters import SearchFilter, OrderingFilter

from django.db import transaction
from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend

from users.authentication import PartnerJWTAuthentication, ClientJWTAuthentication
from shared.permissions import IsClient, IsPartner, IsPartnerOwnerProperty
from admin_auth.authentication import AdminJWTAuthentication
from admin_auth.permissions import IsAdminUser
from .raw_calendar_service import RawCalendarDateService, RawCalendarStatus
from .raw_repository import fetch_calendar_rows, get_verified_property_by_guid
from .raw_booking_repository import (
    BOOKING_ALLOWED_STATUSES,
    count_admin_bookings,
    get_verified_property_for_booking,
    get_client_booking_by_guid,
    get_booking_for_client_action,
    get_booking_for_partner_action,
    get_client_booking_history_detail,
    list_admin_bookings,
    list_client_booking_history,
    list_client_bookings,
    list_partner_bookings,
    release_calendar_for_booking,
    update_booking_status,
)
from payment.raw_repository import (
    create_charge_transaction_from_latest,
    get_latest_transaction_history_for_booking,
    mark_latest_transaction_dismissed,
)
from .raw_serializers import (
    RawAdminBookingListSerializer,
    RawCalendarDateSerializer,
    RawPropertyCalendarDateRangeSerializer,
    RawClientBookingCreateSerializer,
    RawClientBookingDetailSerializer,
    RawClientBookingHistoryDetailSerializer,
    RawClientBookingHistoryListSerializer,
    RawClientBookingListSerializer,
    RawPartnerBookingListSerializer,
)
from .raw_create_service import RawBookingCreateService
from .helpers import client_can_cancel, get_cancellation_error_message
from payment.exchange_rate import exchange_rate
from payment.services import PlumAPIError, PlumAPIService

property_id_param = openapi.Parameter(
    "property_id",
    openapi.IN_PATH,
    description="Unique property GUID",
    type=openapi.TYPE_STRING,
    format=openapi.FORMAT_UUID,
)

booking_id_param = openapi.Parameter(
    "booking_id",
    openapi.IN_PATH,
    description="Unique booking GUID",
    type=openapi.TYPE_STRING,
    format=openapi.FORMAT_UUID,
)

status_query_param = openapi.Parameter(
    "status",
    openapi.IN_QUERY,
    description="Filter bookings by status",
    type=openapi.TYPE_STRING,
    required=False,
)

# Create your views here.


def _ensure_partner_owner(property_row: dict, partner_user) -> None:
    owner_id = int(property_row.get("partner_user_id") or 0)
    if owner_id != int(getattr(partner_user, "id", 0)):
        raise PermissionDenied(_("You don't have permission to modify this property"))


def _parse_statuses_from_query(status_param: str | None) -> list[str]:
    if not status_param:
        return []
    statuses = [value.strip().lower() for value in status_param.split(",") if value.strip()]
    invalid = [value for value in statuses if value not in BOOKING_ALLOWED_STATUSES]
    if invalid:
        raise ValidationError(
            {
                "status": _(
                    "Invalid status: {invalid_status}, allowed are: {valid_statuses}"
                ).format(
                    invalid_status=", ".join(invalid),
                    valid_statuses=", ".join(sorted(BOOKING_ALLOWED_STATUSES)),
                )
            }
        )
    return statuses


class PropertyCalendarDateListView(ListAPIView):
    permission_classes = [AllowAny]
    serializer_class = RawCalendarDateSerializer

    def get_property(self):
        property_id = self.kwargs["property_id"]
        property_row = get_verified_property_by_guid(str(property_id))
        if not property_row:
            raise NotFound(_("Property not found"))
        return property_row

    def list(self, request, *args, **kwargs):
        property = self.get_property()
        date_range_serializer = RawPropertyCalendarDateRangeSerializer(
            data={
                "from_date": request.query_params.get("from_date"),
                "to_date": request.query_params.get("to_date"),
            }
        )
        date_range_serializer.is_valid(raise_exception=True)
        from_date = date_range_serializer.validated_data["from_date"]
        to_date = date_range_serializer.validated_data["to_date"]
        if from_date >= to_date:
            raise ValidationError({"to_date": _("to_date must be greater than from_date")})

        status_filter = request.query_params.get("status")
        allowed_statuses = {
            RawCalendarStatus.AVAILABLE,
            RawCalendarStatus.BOOKED,
            RawCalendarStatus.BLOCKED,
            RawCalendarStatus.HELD,
        }
        if status_filter and status_filter not in allowed_statuses:
            raise ValidationError(
                {
                    "status": _(
                        "Invalid status: {status}, allowed are: {allowed}"
                    ).format(status=status_filter, allowed=", ".join(sorted(allowed_statuses)))
                }
            )

        calendar_dates = fetch_calendar_rows(
            property_kind=property["property_kind"],
            property_id=int(property["property_id"]),
            from_date=from_date,
            to_date=to_date,
        )
        status_by_date = {row["date"]: row["status"] for row in calendar_dates}

        calendar = []
        current_date = from_date

        while current_date <= to_date:
            if current_date in status_by_date:
                resolved_status = status_by_date[current_date]
            else:
                cache_key = f"calendar:hold:{property['guid']}:{current_date.isoformat()}"
                if cache.get(cache_key):
                    resolved_status = RawCalendarStatus.HELD
                else:
                    resolved_status = RawCalendarStatus.AVAILABLE
            if not status_filter or resolved_status == status_filter:
                calendar.append(
                    {
                        "date": current_date,
                        "status": resolved_status,
                    }
                )

            current_date += timedelta(days=1)

        return Response(
            status=status.HTTP_200_OK,
            data={
                "property_id": property["guid"],
                "range": {
                    "from_date": from_date,
                    "to_date": to_date,
                },
                "calendar": calendar,
            },
        )

    @swagger_auto_schema(
        tags=["Booking / Calendar"],
        operation_summary="Retrieve property calendar availability",
        operation_description="Returns the calendar for a property within the specified date range",
        manual_parameters=[property_id_param],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class PropertyCalendarDateBlockView(GenericAPIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]
    serializer_class = RawPropertyCalendarDateRangeSerializer

    def get_property(self, property_id):
        property_row = get_verified_property_by_guid(str(property_id))
        if not property_row:
            raise NotFound(_("Property not found"))
        _ensure_partner_owner(property_row, self.request.user)
        return property_row

    @swagger_auto_schema(
        tags=["Booking / Calendar"],
        operation_summary="Block dates in property calendar",
        operation_description="Blocks one or more dates in the property calendar",
        manual_parameters=[property_id_param],
    )
    def post(self, request, property_id):
        property = self.get_property(property_id)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        from_date = serializer.validated_data["from_date"]
        to_date = serializer.validated_data["to_date"]
        is_single_day = serializer.validated_data["is_single_day"]

        calendar_dates = RawCalendarDateService(
            property_guid=property["guid"],
            property_kind=property["property_kind"],
            property_id=int(property["property_id"]),
            from_date=from_date,
            to_date=to_date,
        )
        days = calendar_dates.block()

        data = {
            "detail": _("You have successfully blocked the booking dates"),
            "property_id": property["guid"],
        }

        if is_single_day:
            data["range"] = {"from_date": from_date.isoformat()}
            data["day"] = from_date.isoformat()
        else:
            data["range"] = {
                "from_date": from_date.isoformat(),
                "to_date": to_date.isoformat(),
            }
            data["days"] = [day.isoformat() for day in days]

        return Response(
            status=status.HTTP_200_OK,
            data=data,
        )


class PropertyCalendarDateUnblockView(GenericAPIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]
    serializer_class = RawPropertyCalendarDateRangeSerializer

    def get_property(self, property_id):
        property_row = get_verified_property_by_guid(str(property_id))
        if not property_row:
            raise NotFound(_("Property not found"))
        _ensure_partner_owner(property_row, self.request.user)
        return property_row

    @swagger_auto_schema(
        tags=["Booking / Calendar"],
        operation_summary="Unblock dates in property calendar",
        operation_description="Removes blocked dates from the property calendar",
        manual_parameters=[property_id_param],
    )
    @transaction.atomic
    def post(self, request, property_id):
        property = self.get_property(property_id)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        from_date = serializer.validated_data["from_date"]
        to_date = serializer.validated_data["to_date"]
        is_single_day = serializer.validated_data["is_single_day"]

        calendar_dates = RawCalendarDateService(
            property_guid=property["guid"],
            property_kind=property["property_kind"],
            property_id=int(property["property_id"]),
            from_date=from_date,
            to_date=to_date,
        )
        days = calendar_dates.unblock()

        data = {
            "detail": _("You have successfully unblocked the booking dates"),
            "property_id": property["guid"],
        }

        if is_single_day:
            data["range"] = {"from_date": from_date.isoformat()}
            data["day"] = from_date.isoformat()
        else:
            data["range"] = {
                "from_date": from_date.isoformat(),
                "to_date": to_date.isoformat(),
            }
            data["days"] = [day.isoformat() for day in days]

        return Response(
            status=status.HTTP_200_OK,
            data=data,
        )


class PropertyCalendarDateHoldView(GenericAPIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]
    serializer_class = RawPropertyCalendarDateRangeSerializer

    def get_property(self, property_id):
        property_row = get_verified_property_by_guid(str(property_id))
        if not property_row:
            raise NotFound(_("Property not found"))
        _ensure_partner_owner(property_row, self.request.user)
        return property_row

    @swagger_auto_schema(
        tags=["Booking / Calendar"],
        operation_summary="Temporarily hold dates for 30 minutes",
        operation_description="Temporarily holds one or more dates for a client during the booking process. The client has 30 minutes to complete payment.",
        manual_parameters=[property_id_param],
    )
    def post(self, request, property_id):
        property = self.get_property(property_id)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        from_date = serializer.validated_data["from_date"]
        to_date = serializer.validated_data["to_date"]
        is_single_day = serializer.validated_data["is_single_day"]

        calendar_dates = RawCalendarDateService(
            property_guid=property["guid"],
            property_kind=property["property_kind"],
            property_id=int(property["property_id"]),
            from_date=from_date,
            to_date=to_date,
        )
        days = calendar_dates.hold()

        data = {
            "detail": _("You have successfully held your booking dates for 30 minutes"),
            "property_id": property["guid"],
        }

        if is_single_day:
            data["range"] = {"from_date": from_date.isoformat()}
            data["day"] = from_date.isoformat()
        else:
            data["range"] = {
                "from_date": from_date.isoformat(),
                "to_date": to_date.isoformat(),
            }
            data["days"] = [day.isoformat() for day in days]

        return Response(
            status=status.HTTP_200_OK,
            data=data,
        )


class PropertyCalendarDateUnholdView(GenericAPIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]
    serializer_class = RawPropertyCalendarDateRangeSerializer

    def get_property(self, property_id):
        property_row = get_verified_property_by_guid(str(property_id))
        if not property_row:
            raise NotFound(_("Property not found"))
        _ensure_partner_owner(property_row, self.request.user)
        return property_row

    @swagger_auto_schema(
        tags=["Booking / Calendar"],
        operation_summary="Release held dates",
        operation_description="Releases previously held dates before the 30-minute hold expires",
        manual_parameters=[property_id_param],
    )
    def post(self, request, property_id):
        property = self.get_property(property_id)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        from_date = serializer.validated_data["from_date"]
        to_date = serializer.validated_data["to_date"]
        is_single_day = serializer.validated_data["is_single_day"]

        calendar_dates = RawCalendarDateService(
            property_guid=property["guid"],
            property_kind=property["property_kind"],
            property_id=int(property["property_id"]),
            from_date=from_date,
            to_date=to_date,
        )
        days = calendar_dates.unhold()

        data = {
            "detail": _("You have successfully unheld the booking dates"),
            "property_id": property["guid"],
        }

        if is_single_day:
            data["range"] = {"from_date": from_date.isoformat()}
            data["day"] = from_date.isoformat()
        else:
            data["range"] = {
                "from_date": from_date.isoformat(),
                "to_date": to_date.isoformat(),
            }
            data["days"] = [day.isoformat() for day in days]

        return Response(
            status=status.HTTP_200_OK,
            data=data,
        )


class ClientBookingListCreateView(ListCreateAPIView):
    authentication_classes = [ClientJWTAuthentication]
    permission_classes = [IsClient]

    def get_serializer_class(self):
        if self.request.method == "POST":
            return RawClientBookingCreateSerializer
        return RawClientBookingListSerializer

    def get_property(self, property_id):
        property_row = get_verified_property_for_booking(str(property_id))
        if not property_row:
            raise NotFound(
                _("Property not found or is not a verified property"))
        return property_row

    def create(self, request, *args, **kwargs):
        property_id = request.data.get("property_id")
        if not property_id:
            raise ValidationError({"property_id": _("This field is required.")})
        property_row = self.get_property(property_id)

        serializer = self.get_serializer(
            data=request.data,
            context={"property_row": property_row},
        )
        serializer.is_valid(raise_exception=True)

        booking_service = RawBookingCreateService(client=request.user)
        booking, _hold = booking_service.create_booking(
            property_row=property_row,
            check_in=serializer.validated_data["check_in"],
            check_out=serializer.validated_data["check_out"],
            card_id=serializer.validated_data["card_id"],
            adults=serializer.validated_data["adults"],
            children=serializer.validated_data["children"],
            babies=serializer.validated_data["babies"],
        )
        return Response(
            status=status.HTTP_201_CREATED,
            data={
                "booking_id": booking["guid"],
                "partner": {
                    "username": property_row.get("partner_username"),
                    "first_name": property_row.get("partner_first_name"),
                    "last_name": property_row.get("partner_last_name"),
                    "phone_number": property_row.get("partner_phone_number"),
                },
                "check_in": booking["check_in"],
                "check_out": booking["check_out"],
                "property_location": {
                    "latitude": property_row.get("latitude"),
                    "longitude": property_row.get("longitude"),
                },
                "status": booking["status"],
            },
        )

    @swagger_auto_schema(
        tags=["Client / Booking"],
        operation_summary="List client bookings",
        operation_description="Return a list of booking related to the authenticated client",
        manual_parameters=[status_query_param],
    )
    def get(self, request, *args, **kwargs):
        statuses = _parse_statuses_from_query(request.query_params.get("status"))
        rows = list_client_bookings(request.user.id, statuses=statuses)
        serializer = RawClientBookingListSerializer(
            rows,
            many=True,
            context={"request": request},
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @swagger_auto_schema(
        tags=["Client / Booking"],
        operation_summary="Create booking and payment hold",
        operation_description="Creates a **PENDING booking** and places a **payment hold (UZS)**",
        request_body=RawClientBookingCreateSerializer,
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)


class ClientBookingDetailView(APIView):
    authentication_classes = [ClientJWTAuthentication]
    permission_classes = [IsClient]

    def get_booking(self, booking_id, client):
        booking = get_client_booking_by_guid(str(booking_id), client.id)
        if not booking:
            raise NotFound(_("Booking not found"))

        return booking

    @swagger_auto_schema(
        tags=["Client / Booking"],
        operation_summary="Retrieve client booking details",
        operation_description="Retrieve information about a specific booking belonging to the authenticated client",
        manual_parameters=[booking_id_param],
    )
    def get(self, request, booking_id):
        booking = self.get_booking(booking_id, request.user)

        serializer = RawClientBookingDetailSerializer(
            booking,
            context={"request": request},
        )
        return Response(
            status=status.HTTP_200_OK,
            data=serializer.data,
        )


class ClientBookingHistoryListView(ListAPIView):
    authentication_classes = [ClientJWTAuthentication]
    permission_classes = [IsClient]
    serializer_class = RawClientBookingHistoryListSerializer

    @swagger_auto_schema(
        tags=["Client / Booking"],
        operation_summary="Retrieve booking history",
        operation_description="Returns a list of history bookings created by the authenticated client",
    )
    def get(self, request, *args, **kwargs):
        rows = list_client_booking_history(request.user.id)
        serializer = self.serializer_class(
            rows,
            many=True,
            context={"request": request},
        )
        return Response(serializer.data, status=status.HTTP_200_OK)


class ClientBookingHistoryDetailView(APIView):
    authentication_classes = [ClientJWTAuthentication]
    permission_classes = [IsClient]

    def get_booking(self, booking_id, client):
        booking = get_client_booking_history_detail(str(booking_id), client.id)
        if not booking:
            raise NotFound(_("Booking not found"))
        return booking

    @swagger_auto_schema(
        tags=["Client / Booking"],
        operation_summary="Retrieve history booking details",
        operation_description="Returns detailed information about a specific booking history belonging to the authenticated client",
        manual_parameters=[booking_id_param],
    )
    def get(self, request, booking_id):
        booking = self.get_booking(booking_id, request.user)

        serializer = RawClientBookingHistoryDetailSerializer(
            booking,
            context={"request": request},
        )
        return Response(status=status.HTTP_200_OK, data=serializer.data)


class ClientBookingCancelView(APIView):
    authentication_classes = [ClientJWTAuthentication]
    permission_classes = [IsClient]

    def get_booking(self, booking_id, client):
        booking = get_booking_for_client_action(str(booking_id), client.id)
        if not booking:
            raise NotFound(_("Booking not found"))

        return booking

    @swagger_auto_schema(
        tags=["Client / Booking"],
        operation_summary="Cancel booking",
        operation_description="Allows a client to cancel their booking",
        manual_parameters=[booking_id_param],
    )
    def post(self, request, booking_id):
        booking = self.get_booking(booking_id, request.user)

        booking_obj = SimpleNamespace(
            status=booking["status"],
            check_in=booking["check_in"],
            created_at=booking["created_at"],
        )
        if not client_can_cancel(booking_obj):
            raise ValidationError(get_cancellation_error_message(booking_obj))

        if booking["status"] == "pending":
            tx = get_latest_transaction_history_for_booking(int(booking["id"]))
            if tx and tx.get("transaction_id") and tx.get("hold_id"):
                plum_service = PlumAPIService()
                try:
                    plum_service.dismiss_hold(
                        transaction_id=tx["transaction_id"],
                        hold_id=tx["hold_id"],
                    )
                except PlumAPIError as plum_api_error:
                    if plum_api_error.status_code == 403:
                        raise PermissionDenied(plum_api_error.message)
                    raise ValidationError(plum_api_error.message)
            mark_latest_transaction_dismissed(int(booking["id"]))

        release_calendar_for_booking(booking)
        updated = update_booking_status(
            booking_id=int(booking["id"]),
            status="cancelled",
            cancellation_reason="user_cancelled",
            set_cancelled=True,
        )
        return Response(
            status=status.HTTP_200_OK,
            data={
                "booking_id": booking["guid"],
                "status": updated["status"] if updated else "cancelled",
                "cancellation_reason": updated["cancellation_reason"] if updated else "user_cancelled",
            },
        )


class PartnerBookingAcceptView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner, IsPartnerOwnerProperty]

    def get_booking(self, booking_id):
        booking = get_booking_for_partner_action(str(booking_id), self.request.user.id)
        if not booking:
            raise ValidationError(_("Booking not found"))
        return booking

    @swagger_auto_schema(
        tags=["Partner / Booking"],
        operation_summary="Accept booking",
        operation_description="Allows a partner to accept a booking request",
        manual_parameters=[booking_id_param],
    )
    def post(self, request, booking_id):
        booking = self.get_booking(booking_id)
        if booking["status"] != "pending":
            raise ValidationError(_("You can only accept bookings with **pending** statuses"))

        updated = update_booking_status(
            booking_id=int(booking["id"]),
            status="confirmed",
            set_confirmed=True,
        )
        return Response(
            status=status.HTTP_200_OK,
            data={
                "booking_id": booking["guid"],
                "status": updated["status"] if updated else "confirmed",
            },
        )


class PartnerBookingCancelView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner, IsPartnerOwnerProperty]

    def get_booking(self, booking_id):
        booking = get_booking_for_partner_action(str(booking_id), self.request.user.id)
        if not booking:
            raise ValidationError(_("Booking not found"))
        return booking

    @swagger_auto_schema(
        tags=["Partner / Booking"],
        operation_summary="Cancel booking",
        operation_description="A cancellation reason may be provided and the booking status",
        manual_parameters=[booking_id_param],
    )
    def post(self, request, booking_id):
        booking = self.get_booking(booking_id)
        if booking["status"] != "pending":
            raise ValidationError(_("Partner can cancel only bookings with status `PENDING`"))

        tx = get_latest_transaction_history_for_booking(int(booking["id"]))
        if tx and tx.get("transaction_id") and tx.get("hold_id"):
            plum_service = PlumAPIService()
            try:
                plum_service.dismiss_hold(
                    transaction_id=tx["transaction_id"],
                    hold_id=tx["hold_id"],
                )
            except PlumAPIError as plum_api_error:
                if plum_api_error.status_code == 403:
                    raise PermissionDenied(plum_api_error.message)
                raise ValidationError(plum_api_error.message)
        mark_latest_transaction_dismissed(int(booking["id"]))

        release_calendar_for_booking(booking)
        updated = update_booking_status(
            booking_id=int(booking["id"]),
            status="cancelled",
            cancellation_reason="partner_cancelled",
            set_cancelled=True,
        )
        return Response(
            status=status.HTTP_200_OK,
            data={
                "booking_id": booking["guid"],
                "status": updated["status"] if updated else "cancelled",
            },
        )


class PartnerBookingListView(ListAPIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]
    serializer_class = RawPartnerBookingListSerializer

    @swagger_auto_schema(
        tags=["Partner / Booking"],
        operation_summary="List partner bookings",
        operation_description="Return a list of booking related to the authenticated partner",
        manual_parameters=[status_query_param],
    )
    def get(self, request, *args, **kwargs):
        statuses = _parse_statuses_from_query(request.query_params.get("status"))
        rows = list_partner_bookings(request.user.id, statuses=statuses)
        serializer = self.serializer_class(
            rows,
            many=True,
            context={"request": request},
        )
        return Response(serializer.data, status=status.HTTP_200_OK)


class PartnerCompleteBookingView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner, IsPartnerOwnerProperty]

    def get_booking(self, booking_id):
        booking = get_booking_for_partner_action(str(booking_id), self.request.user.id)
        if not booking:
            raise NotFound(_("Booking not found"))
        return booking

    @swagger_auto_schema(
        tags=["Partner / Booking"],
        operation_summary="Complete booking",
        operation_description="Marks as confirmed booking as completed when the user arrives\nCharges 50% of the hold booking price",
        manual_parameters=[booking_id_param],
    )
    def post(self, request, booking_id):
        booking = self.get_booking(booking_id)
        if booking["status"] != "confirmed":
            raise ValidationError(_("Only confirmed bookings can be completed"))

        tx = get_latest_transaction_history_for_booking(int(booking["id"]))
        charge_amount = booking.get("booking_charge_amount")
        if tx and tx.get("transaction_id") and tx.get("hold_id") and charge_amount:
            plum_service = PlumAPIService()
            try:
                charge_transaction = plum_service.charge_hold(
                    transaction_id=tx["transaction_id"],
                    hold_id=tx["hold_id"],
                    charge_amount=charge_amount,
                )
                result = (charge_transaction or {}).get("result") or {}
                create_charge_transaction_from_latest(
                    booking_row=booking,
                    transaction_id=result.get("transactionId") or tx.get("transaction_id"),
                    hold_id=result.get("holdId") or tx.get("hold_id"),
                    amount=result.get("amount") or charge_amount,
                    card_id=result.get("cardId") or tx.get("card_id"),
                    extra_id=result.get("extraId"),
                )
            except PlumAPIError as plum_api_error:
                if plum_api_error.status_code == 403:
                    raise PermissionDenied(plum_api_error.message)
                raise ValidationError(plum_api_error.message)

        updated = update_booking_status(
            booking_id=int(booking["id"]),
            status="completed",
            set_completed=True,
        )
        return Response(
            status=status.HTTP_200_OK,
            data={
                "booking_id": str(booking["guid"]),
                "subtotal": str(booking.get("booking_subtotal")),
                "hold_amount": str(booking.get("booking_hold_amount")),
                "charge_amount": str(booking.get("booking_charge_amount")),
                "status": updated["status"] if updated else "completed",
            },
        )


class PartnerNoShowBookingView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner, IsPartnerOwnerProperty]

    def get_booking(self, booking_id):
        booking = get_booking_for_partner_action(str(booking_id), self.request.user.id)
        if not booking:
            raise NotFound(_("Booking not found"))
        return booking

    @swagger_auto_schema(
        tags=["Partner / Booking"],
        operation_summary="Mark booking as no-show",
        operation_description="Marks a confirmed booking as no-show when the user does not arrive",
        manual_parameters=[booking_id_param],
    )
    def post(self, request, booking_id):
        booking = self.get_booking(booking_id)
        if booking["status"] != "confirmed":
            raise ValidationError(_("Only confirmed bookings can be marked as no-show"))

        tx = get_latest_transaction_history_for_booking(int(booking["id"]))
        hold_amount = booking.get("booking_hold_amount")
        if tx and tx.get("transaction_id") and tx.get("hold_id") and hold_amount:
            plum_service = PlumAPIService()
            try:
                charge_transaction = plum_service.charge_hold(
                    transaction_id=tx["transaction_id"],
                    hold_id=tx["hold_id"],
                    charge_amount=hold_amount,
                )
                result = (charge_transaction or {}).get("result") or {}
                create_charge_transaction_from_latest(
                    booking_row=booking,
                    transaction_id=result.get("transactionId") or tx.get("transaction_id"),
                    hold_id=result.get("holdId") or tx.get("hold_id"),
                    amount=result.get("amount") or hold_amount,
                    card_id=result.get("cardId") or tx.get("card_id"),
                    extra_id=result.get("extraId"),
                )
            except PlumAPIError as plum_api_error:
                raise ValidationError(plum_api_error.message)

        updated = update_booking_status(
            booking_id=int(booking["id"]),
            status="cancelled",
            cancellation_reason="user_no_show",
            set_cancelled=True,
        )
        return Response(
            {
                "booking_id": booking["guid"],
                "status": updated["status"] if updated else "cancelled",
                "cancellation_reason": updated["cancellation_reason"] if updated else "user_no_show",
                "cancelled_at": updated["cancelled_at"] if updated else None,
            },
            status=status.HTTP_200_OK,
        )


class AdminBookingPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class AdminBookingListView(ListAPIView):
    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]
    serializer_class = RawAdminBookingListSerializer
    pagination_class = AdminBookingPagination
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["status"]
    search_fields = ["booking_number", "client__phone_number"]
    ordering_fields = ["created_at", "check_in", "status"]
    ordering = ["-created_at"]

    @swagger_auto_schema(
        tags=["Admin / Booking"],
        operation_summary="List all bookings for admin",
        operation_description="Returns a paginated list of all bookings with filtering, search, and ordering support",
        manual_parameters=[
            status_query_param,
            openapi.Parameter(
                "search",
                openapi.IN_QUERY,
                description="Search by booking number or client phone number",
                type=openapi.TYPE_STRING,
                required=False,
            ),
            openapi.Parameter(
                "ordering",
                openapi.IN_QUERY,
                description="Order by field (created_at, check_in, status). Prefix with '-' for descending",
                type=openapi.TYPE_STRING,
                required=False,
            ),
        ],
    )
    def get(self, request, *args, **kwargs):
        status_filter = request.query_params.get("status")
        search = request.query_params.get("search")
        ordering = request.query_params.get("ordering")
        usd_to_uzs_rate = None

        if status_filter and status_filter not in BOOKING_ALLOWED_STATUSES:
            raise ValidationError(
                {
                    "status": _(
                        "Invalid status: {invalid_status}, allowed are: {valid_statuses}"
                    ).format(
                        invalid_status=status_filter,
                        valid_statuses=", ".join(sorted(BOOKING_ALLOWED_STATUSES)),
                    )
                }
            )

        total = count_admin_bookings(status=status_filter, search=search)
        rows = list_admin_bookings(
            status=status_filter,
            search=search,
            ordering=ordering,
            limit=max(total, 1),
            offset=0,
        )

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(rows, request, view=self)
        try:
            usd_to_uzs_rate = str(exchange_rate())
        except Exception:
            usd_to_uzs_rate = None
        serializer = self.serializer_class(
            page,
            many=True,
            context={"request": request, "usd_to_uzs_rate": usd_to_uzs_rate},
        )
        return paginator.get_paginated_response(serializer.data)
