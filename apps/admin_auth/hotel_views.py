from __future__ import annotations

import logging
from datetime import date
from typing import Any

from django.db import IntegrityError, connection

from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

from shared.raw.db import fetch_all

from .authentication import AdminJWTAuthentication
from .permissions import IsAdminUser

from apps.pms.repository import (
    accept_booking,
    cancel_booking,
    check_in_booking,
    check_out_booking,
    complain_review,
    create_booking,
    find_or_create_guest,
    get_analytics,
    get_booking,
    get_property,
    get_room_availability,
    list_bookings,
    list_properties,
    list_reviews,
    list_rooms,
    move_booking,
    respond_to_review,
    update_booking_with_guest,
    update_property,
)
from apps.pms.serializers import (
    AnalyticsQuerySerializer,
    AnalyticsResponseSerializer,
    BookingSerializer,
    MoveBookingSerializer,
    PropertySerializer,
    ReviewComplainSerializer,
    ReviewRespondSerializer,
    ReviewSerializer,
    RoomSerializer,
)
from apps.b2b.repository import list_b2b_users, get_company
from apps.b2b.serializers import B2BCompanySerializer, B2BUserSerializer
from property.hotel_repository import resolve_hotel_guid
from apps.platform.raw_repository import list_organizations

logger = logging.getLogger(__name__)


def _set_tenant_from_guid(hotel_guid: str) -> int | None:
    resolved = resolve_hotel_guid(hotel_guid, include_inactive=True)
    if not resolved:
        return None
    schema_name, hotel_id = resolved
    with connection.cursor() as cursor:
        cursor.execute("SET search_path TO %s, public", [schema_name])
    return hotel_id


class AdminBaseView(APIView):
    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]


class AdminHotelBaseView(AdminBaseView):
    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        raw = self.kwargs.get("property_id")
        if raw is not None:
            numeric_id = _set_tenant_from_guid(str(raw))
            if numeric_id is not None:
                self.kwargs["property_id"] = numeric_id

    def dispatch(self, request, *args, **kwargs):
        try:
            return super().dispatch(request, *args, **kwargs)
        finally:
            if request.method in ("GET", "HEAD", "OPTIONS"):
                with connection.cursor() as cursor:
                    cursor.execute("SET search_path TO public")


class ClassifyPropertySerializer(serializers.Serializer):
    star_rating = serializers.IntegerField(required=False, allow_null=True, min_value=0, max_value=5)
    weel_classification = serializers.ChoiceField(
        choices=["standard", "essential", "comfort", "comfort_plus", "business", "premium", "signature"],
        required=False,
        allow_null=True,
    )


class AdminHotelListView(AdminHotelBaseView):
    """List all hotels across all organizations — admin view"""

    @swagger_auto_schema(responses={200: PropertySerializer(many=True)})
    def get(self, request):
        orgs = list_organizations()
        all_properties: list[dict[str, Any]] = []
        for org in orgs:
            schema = org.get("schema_name")
            if not schema:
                continue
            rows = fetch_all(
                f"SELECT * FROM {schema}.pms_property WHERE is_active = TRUE ORDER BY name ASC"
            )
            for row in rows:
                row["guid"] = str(row["guid"])
            all_properties.extend(rows)
        return Response(PropertySerializer(all_properties, many=True).data)


class AdminHotelDetailView(AdminHotelBaseView):

    @swagger_auto_schema(responses={200: PropertySerializer()})
    def get(self, request, property_id):
        prop = get_property(property_id)
        if not prop:
            return Response({"detail": "Property not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(PropertySerializer(prop).data)

    @swagger_auto_schema(request_body=PropertySerializer, responses={200: PropertySerializer()})
    def patch(self, request, property_id):
        serializer = PropertySerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        prop = update_property(property_id, **serializer.validated_data)
        if not prop:
            return Response({"detail": "Property not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(PropertySerializer(prop).data)


class AdminHotelClassifyView(AdminHotelBaseView):
    """Assign star rating and Weel classification to a hotel"""

    @swagger_auto_schema(request_body=ClassifyPropertySerializer, responses={200: PropertySerializer()})
    def patch(self, request, property_id):
        serializer = ClassifyPropertySerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        if not serializer.validated_data:
            return Response({"detail": "No data to update."}, status=status.HTTP_400_BAD_REQUEST)

        prop = update_property(property_id, **serializer.validated_data)
        if not prop:
            return Response({"detail": "Property not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(PropertySerializer(prop).data)


class AdminHotelRoomInventoryView(AdminHotelBaseView):
    """Mirrored room inventory — uses same PMS data source"""

    @swagger_auto_schema(responses={200: RoomSerializer(many=True)})
    def get(self, request, property_id):
        room_type_name = request.query_params.get("room_type_name")
        rooms = list_rooms(property_id, room_type_name=room_type_name if room_type_name else None)
        return Response(RoomSerializer(rooms, many=True).data)


class AdminHotelCalendarView(AdminHotelBaseView):
    """Mirrored calendar — uses same PMS availability data"""

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter("from_date", openapi.IN_QUERY, type=openapi.TYPE_STRING, format="date"),
            openapi.Parameter("to_date", openapi.IN_QUERY, type=openapi.TYPE_STRING, format="date"),
        ],
        responses={200: openapi.Response("Availability slots", openapi.Schema(type=openapi.TYPE_ARRAY, items=openapi.Schema(type=openapi.TYPE_OBJECT)))}
    )
    def get(self, request, property_id):
        from_date = request.query_params.get("from_date")
        to_date = request.query_params.get("to_date")
        if not from_date or not to_date:
            return Response({"detail": "from_date and to_date required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            from_d = date.fromisoformat(from_date)
            to_d = date.fromisoformat(to_date)
        except ValueError:
            return Response({"detail": "Invalid date format. Use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)

        slots = get_room_availability(property_id=property_id, from_date=from_d, to_date=to_d)
        return Response(slots)


class AdminHotelBookingsView(AdminHotelBaseView):
    """Admin view of hotel bookings"""

    @swagger_auto_schema(responses={200: BookingSerializer(many=True)})
    def get(self, request, property_id):
        status_filter = request.query_params.get("status")
        from_date = request.query_params.get("from_date")
        to_date = request.query_params.get("to_date")
        bookings = list_bookings(
            property_id=property_id,
            status=status_filter,
            from_date=from_date,
            to_date=to_date,
        )
        return Response(BookingSerializer(bookings, many=True).data)


class AdminHotelBookingDetailView(AdminHotelBaseView):
    """Get or update a specific booking"""

    @swagger_auto_schema(responses={200: BookingSerializer()})
    def get(self, request, property_id, booking_id):
        booking = get_booking(booking_id, property_id)
        if not booking:
            return Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(BookingSerializer(booking).data)

    @swagger_auto_schema(responses={200: BookingSerializer()})
    def patch(self, request, property_id, booking_id):
        booking = get_booking(booking_id, property_id)
        if not booking:
            return Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = BookingSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        updated = update_booking_with_guest(booking_id, **serializer.validated_data)
        if not updated:
            return Response({"detail": "Failed to update booking."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(BookingSerializer(updated).data)


class AdminHotelBookingCreateView(AdminHotelBaseView):
    """Quick-create a booking"""

    @swagger_auto_schema(responses={201: BookingSerializer()})
    def post(self, request, property_id):
        serializer = BookingSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated = serializer.validated_data
        guest_id = validated.pop("guest_id", None)

        if not guest_id:
            guest_data = request.data.get("guest", {})
            if guest_data:
                guest = find_or_create_guest(
                    first_name=guest_data.get("first_name", "Guest"),
                    last_name=guest_data.get("last_name"),
                    email=guest_data.get("email"),
                    phone=guest_data.get("phone"),
                )
                guest_id = guest.get("id")

        try:
            booking = create_booking(
                property_id=property_id,
                check_in=validated["check_in"],
                check_out=validated["check_out"],
                room_id=validated["room_id"],
                guest_id=guest_id,
                **{k: v for k, v in validated.items() if k not in ("check_in", "check_out", "room_id")},
            )
        except IntegrityError as e:
            return Response({"detail": f"Database error: {e}"}, status=status.HTTP_400_BAD_REQUEST)

        if not booking:
            return Response({"detail": "Failed to create booking."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(BookingSerializer(booking).data, status=status.HTTP_201_CREATED)


class AdminHotelBookingMoveView(AdminHotelBaseView):
    """Move booking to a different room / date range (drag on calendar)"""

    @swagger_auto_schema(responses={200: BookingSerializer()})
    def post(self, request, property_id, booking_id):
        serializer = MoveBookingSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        booking = move_booking(
            booking_id,
            new_room_id=serializer.validated_data["new_room_id"],
            new_check_in=serializer.validated_data.get("new_check_in"),
            new_check_out=serializer.validated_data.get("new_check_out"),
        )
        if not booking:
            return Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(BookingSerializer(booking).data)


class AdminHotelBookingAcceptView(AdminHotelBaseView):
    """Accept a booking"""

    @swagger_auto_schema(responses={200: BookingSerializer()})
    def post(self, request, property_id, booking_id):
        booking = accept_booking(booking_id)
        if not booking:
            return Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(BookingSerializer(booking).data)


class AdminHotelBookingCancelView(AdminHotelBaseView):
    """Cancel a booking"""

    @swagger_auto_schema(responses={200: BookingSerializer()})
    def post(self, request, property_id, booking_id):
        try:
            booking = cancel_booking(booking_id)
        except IntegrityError as e:
            return Response({"detail": f"Database error: {e}"}, status=status.HTTP_400_BAD_REQUEST)
        if not booking:
            return Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(BookingSerializer(booking).data)


class AdminHotelBookingCheckInView(AdminHotelBaseView):
    """Check in a booking"""

    @swagger_auto_schema(responses={200: BookingSerializer()})
    def post(self, request, property_id, booking_id):
        booking = check_in_booking(booking_id)
        if not booking:
            return Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(BookingSerializer(booking).data)


class AdminHotelBookingCheckOutView(AdminHotelBaseView):
    """Check out a booking"""

    @swagger_auto_schema(responses={200: BookingSerializer()})
    def post(self, request, property_id, booking_id):
        booking = check_out_booking(booking_id)
        if not booking:
            return Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(BookingSerializer(booking).data)


class AdminHotelReviewsView(AdminHotelBaseView):

    @swagger_auto_schema(responses={200: ReviewSerializer(many=True)})
    def get(self, request, property_id):
        reviews = list_reviews(property_id)
        return Response(ReviewSerializer(reviews, many=True).data)


class AdminReviewRespondView(AdminHotelBaseView):

    @swagger_auto_schema(request_body=ReviewRespondSerializer, responses={200: ReviewSerializer()})
    def post(self, request, property_id, review_id):
        serializer = ReviewRespondSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        review = respond_to_review(review_id, serializer.validated_data["response"])
        if not review:
            return Response({"detail": "Review not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(ReviewSerializer(review).data)


class AdminReviewHideView(AdminHotelBaseView):
    """Admin can hide/complain a review"""

    @swagger_auto_schema(request_body=ReviewComplainSerializer, responses={200: ReviewSerializer()})
    def post(self, request, property_id, review_id):
        serializer = ReviewComplainSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        review = complain_review(review_id, serializer.validated_data["reason"])
        if not review:
            return Response({"detail": "Review not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(ReviewSerializer(review).data)


class AdminB2BCompaniesView(AdminBaseView):
    """List all B2B companies — admin view"""

    @swagger_auto_schema(responses={200: B2BCompanySerializer(many=True)})
    def get(self, request):
        companies = fetch_all("SELECT * FROM b2b_company WHERE is_active = TRUE ORDER BY name ASC")
        return Response(B2BCompanySerializer(companies, many=True).data)


class AdminB2BCompanyDetailView(AdminBaseView):

    @swagger_auto_schema(responses={200: B2BCompanySerializer()})
    def get(self, request, company_id):
        company = get_company(company_id)
        if not company:
            return Response({"detail": "Company not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(B2BCompanySerializer(company).data)


class AdminB2BUsersView(AdminBaseView):

    @swagger_auto_schema(responses={200: B2BUserSerializer(many=True)})
    def get(self, request, company_id):
        users = list_b2b_users(company_id)
        return Response(B2BUserSerializer(users, many=True).data)


class AdminHotelAnalyticsView(AdminHotelBaseView):
    """Analytics data for a property"""

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter("date_from", openapi.IN_QUERY, description="Start date", type=openapi.TYPE_STRING, format="date", required=True),
            openapi.Parameter("date_to", openapi.IN_QUERY, description="End date", type=openapi.TYPE_STRING, format="date", required=True),
            openapi.Parameter("metric", openapi.IN_QUERY, description="Chart metric", type=openapi.TYPE_STRING, enum=["check_ins", "revenue", "bookings", "occupancy"]),
            openapi.Parameter("category", openapi.IN_QUERY, description="Room category filter", type=openapi.TYPE_STRING),
            openapi.Parameter("floor", openapi.IN_QUERY, description="Floor filter", type=openapi.TYPE_STRING),
            openapi.Parameter("search", openapi.IN_QUERY, description="Room number search", type=openapi.TYPE_STRING),
        ],
        responses={200: AnalyticsResponseSerializer()},
    )
    def get(self, request, property_id):
        serializer = AnalyticsQuerySerializer(data=request.query_params)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = get_analytics(
            property_id=property_id,
            date_from=serializer.validated_data["date_from"],
            date_to=serializer.validated_data["date_to"],
            metric=serializer.validated_data.get("metric", "revenue"),
            category=serializer.validated_data.get("category"),
            floor=serializer.validated_data.get("floor"),
            search=serializer.validated_data.get("search"),
        )

        return Response(AnalyticsResponseSerializer(data).data)
