from __future__ import annotations

import logging
from datetime import date
from typing import Any

from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

from shared.raw.db import fetch_all, fetch_one

from .authentication import AdminJWTAuthentication
from .permissions import IsAdminUser

from apps.pms.repository import (
    get_property,
    list_properties,
    update_property,
    get_room_availability,
    list_rooms,
    list_bookings,
    list_reviews,
    respond_to_review,
    complain_review,
)
from apps.pms.serializers import (
    PropertySerializer,
    RoomSerializer,
    BookingSerializer,
    ReviewSerializer,
    ReviewRespondSerializer,
    ReviewComplainSerializer,
)
from apps.b2b.repository import list_b2b_users, get_company
from apps.b2b.serializers import B2BCompanySerializer, B2BUserSerializer

logger = logging.getLogger(__name__)


class AdminBaseView(APIView):
    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]


class ClassifyPropertySerializer(serializers.Serializer):
    star_rating = serializers.IntegerField(required=False, allow_null=True, min_value=0, max_value=5)
    weel_classification = serializers.ChoiceField(
        choices=["standard", "essential", "comfort", "comfort_plus", "business", "premium", "signature"],
        required=False,
        allow_null=True,
    )


class AdminHotelListView(AdminBaseView):
    """List all hotels across all organizations — admin view"""

    @swagger_auto_schema(responses={200: PropertySerializer(many=True)})
    def get(self, request):
        properties = fetch_all("SELECT * FROM pms_property WHERE is_active = TRUE ORDER BY name ASC")
        return Response(PropertySerializer(properties, many=True).data)


class AdminHotelDetailView(AdminBaseView):

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


class AdminHotelClassifyView(AdminBaseView):
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


class AdminHotelRoomInventoryView(AdminBaseView):
    """Mirrored room inventory — uses same PMS data source"""

    @swagger_auto_schema(responses={200: RoomSerializer(many=True)})
    def get(self, request, property_id):
        room_type_id = request.query_params.get("room_type_id")
        rooms = list_rooms(property_id, room_type_id=int(room_type_id) if room_type_id else None)
        return Response(RoomSerializer(rooms, many=True).data)


class AdminHotelCalendarView(AdminBaseView):
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


class AdminHotelBookingsView(AdminBaseView):
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


class AdminHotelReviewsView(AdminBaseView):

    @swagger_auto_schema(responses={200: ReviewSerializer(many=True)})
    def get(self, request, property_id):
        reviews = list_reviews(property_id)
        return Response(ReviewSerializer(reviews, many=True).data)


class AdminReviewRespondView(AdminBaseView):

    @swagger_auto_schema(request_body=ReviewRespondSerializer, responses={200: ReviewSerializer()})
    def post(self, request, property_id, review_id):
        serializer = ReviewRespondSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        review = respond_to_review(review_id, serializer.validated_data["response"])
        if not review:
            return Response({"detail": "Review not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(ReviewSerializer(review).data)


class AdminReviewHideView(AdminBaseView):
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
