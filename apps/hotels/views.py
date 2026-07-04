from __future__ import annotations

import logging

from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status

from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

from apps.hotels.repository import (
    calculate_stay_price,
    count_hotels,
    get_available_rooms,
    get_hotel_card,
    get_hotel_reviews,
    search_hotels,
)
from apps.hotels.serializers import (
    HotelCardSerializer,
    HotelDetailSerializer,
    HotelSearchParamsSerializer,
    ReviewListSerializer,
    RoomAvailabilitySerializer,
    RoomSelectParamsSerializer,
    StayPriceSerializer,
)

logger = logging.getLogger(__name__)


class HotelSearchView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        query_serializer=HotelSearchParamsSerializer,
        responses={200: openapi.Response(
            "Paginated hotel list",
            openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    "count": openapi.Schema(type=openapi.TYPE_INTEGER),
                    "results": openapi.Schema(type=openapi.TYPE_ARRAY, items=openapi.Schema(type=openapi.TYPE_OBJECT)),
                },
            ),
        )},
    )
    def get(self, request):
        params = HotelSearchParamsSerializer(data=request.query_params)
        if not params.is_valid():
            return Response(params.errors, status=status.HTTP_400_BAD_REQUEST)

        d = params.validated_data
        page = d.pop("page")
        page_size = d.pop("page_size")
        offset = (page - 1) * page_size

        hotels = search_hotels(**d, limit=page_size, offset=offset)
        count = count_hotels(
            city=d.get("city"),
            star_rating=d.get("star_rating"),
            weel_classification=d.get("weel_classification"),
            themes=d.get("themes"),
        )
        return Response({
            "count": count,
            "page": page,
            "page_size": page_size,
            "results": HotelCardSerializer(hotels, many=True).data,
        })


class HotelDetailView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(responses={200: HotelDetailSerializer()})
    def get(self, request, hotel_id):
        hotel = get_hotel_card(hotel_id)
        if not hotel:
            return Response({"detail": "Hotel not found."}, status=status.HTTP_404_NOT_FOUND)

        reviews = get_hotel_reviews(hotel_id, limit=5)
        hotel["images"] = hotel.get("photos") or []
        hotel["reviews"] = reviews
        return Response(HotelDetailSerializer(hotel).data)


class HotelRoomSelectView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        query_serializer=RoomSelectParamsSerializer,
        responses={200: RoomAvailabilitySerializer(many=True)},
    )
    def get(self, request, hotel_id):
        params = RoomSelectParamsSerializer(data=request.query_params)
        if not params.is_valid():
            return Response(params.errors, status=status.HTTP_400_BAD_REQUEST)

        hotel = get_hotel_card(hotel_id)
        if not hotel:
            return Response({"detail": "Hotel not found."}, status=status.HTTP_404_NOT_FOUND)

        rooms = get_available_rooms(
            hotel_id,
            check_in=params.validated_data["check_in"],
            check_out=params.validated_data["check_out"],
            guests=params.validated_data["guests"],
        )
        return Response(RoomAvailabilitySerializer(rooms, many=True).data)


class HotelRoomPriceView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        query_serializer=RoomSelectParamsSerializer,
        responses={200: StayPriceSerializer()},
    )
    def get(self, request, hotel_id, room_id):
        params = RoomSelectParamsSerializer(data=request.query_params)
        if not params.is_valid():
            return Response(params.errors, status=status.HTTP_400_BAD_REQUEST)

        pricing = calculate_stay_price(
            room_id,
            params.validated_data["check_in"],
            params.validated_data["check_out"],
        )
        if not pricing:
            return Response({"detail": "Room not found or invalid dates."}, status=status.HTTP_400_BAD_REQUEST)
        return Response(StayPriceSerializer(pricing).data)


class HotelReviewsView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter("limit", openapi.IN_QUERY, type=openapi.TYPE_INTEGER, default=10),
            openapi.Parameter("offset", openapi.IN_QUERY, type=openapi.TYPE_INTEGER, default=0),
        ],
        responses={200: ReviewListSerializer(many=True)},
    )
    def get(self, request, hotel_id):
        limit = int(request.query_params.get("limit", 10))
        offset = int(request.query_params.get("offset", 0))
        reviews = get_hotel_reviews(hotel_id, limit=limit, offset=offset)
        return Response(ReviewListSerializer(reviews, many=True).data)
