from __future__ import annotations

import logging
from datetime import date, timedelta

from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status

from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

from apps.platform.raw_repository import get_organization_by_schema
from apps.property.hotel_repository import (
    _execute_hotel_query,
    _find_hotel_by_guid_across_schemas,
    _run_in_schema,
    fetch_room_summaries_raw,
)
from apps.property.views import _favorite_guids_from_request
from users.authentication import OptionalClientOrPartnerJWTAuthentication

from apps.hotels.repository import (
    calculate_stay_price,
    count_hotels,
    get_available_rooms,
    get_hotel_card,
    get_hotel_calendar,
    get_hotel_reviews,
    search_hotels,
)
from apps.hotels.serializers import (
    HotelCalendarSerializer,
    HotelDetailSerializer,
    HotelSearchParamsSerializer,
    ReviewListSerializer,
    RoomAvailabilitySerializer,
    RoomSelectParamsSerializer,
    StayPriceSerializer,
)
from apps.property.hotel_serializers import HotelCardSerializer as HotelCardFullSerializer

logger = logging.getLogger(__name__)

_GUID_PARAM = openapi.Parameter(
    "guid", openapi.IN_PATH,
    type=openapi.TYPE_STRING,
    description="Property GUID (pms_property.guid).",
)


def _resolve_hotel(guid: str) -> tuple[str, int] | None:
    rows = _find_hotel_by_guid_across_schemas(str(guid))
    if not rows:
        return None
    tenant = rows[0].get("tenant_schema")
    if not tenant:
        return None
    return tenant, int(rows[0]["id"])


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
        count = count_hotels(**{k: v for k, v in d.items() if k != "sort_by"})
        ctx = {
            "request": request,
            "favorite_guids": _favorite_guids_from_request(request),
        }
        return Response({
            "count": count,
            "page": page,
            "page_size": page_size,
            "results": HotelCardFullSerializer(hotels, many=True, context=ctx).data,
        })


class HotelDetailView(APIView):
    authentication_classes = [OptionalClientOrPartnerJWTAuthentication]
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        manual_parameters=[_GUID_PARAM],
        responses={200: HotelDetailSerializer()},
    )
    def get(self, request, guid):
        resolved = _resolve_hotel(guid)
        if not resolved:
            return Response({"detail": "Invalid GUID."}, status=status.HTTP_400_BAD_REQUEST)

        schema_name, numeric_id = resolved
        organization = get_organization_by_schema(schema_name)

        def _query():
            rows = _execute_hotel_query(schema_name, hotel_id=numeric_id)
            if not rows:
                return None
            hotel = rows[0]
            hotel["reviews"] = get_hotel_reviews(numeric_id, limit=5)
            hotel["room_types"] = fetch_room_summaries_raw(numeric_id)
            if organization:
                hotel["organization_id"] = organization.get("id")
                hotel["organization_name"] = organization.get("name")
            return hotel

        result = _run_in_schema(schema_name, _query)
        if result is None:
            return Response({"detail": "Hotel not found."}, status=status.HTTP_404_NOT_FOUND)

        ctx = {
            "request": request,
            "favorite_guids": _favorite_guids_from_request(request),
        }
        return Response(HotelDetailSerializer(result, context=ctx).data)


class HotelRoomSelectView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        manual_parameters=[_GUID_PARAM],
        query_serializer=RoomSelectParamsSerializer,
        responses={200: RoomAvailabilitySerializer(many=True)},
    )
    def get(self, request, guid):
        resolved = _resolve_hotel(guid)
        if not resolved:
            return Response({"detail": "Invalid GUID."}, status=status.HTTP_400_BAD_REQUEST)

        schema_name, numeric_id = resolved

        params = RoomSelectParamsSerializer(data=request.query_params)
        if not params.is_valid():
            return Response(params.errors, status=status.HTTP_400_BAD_REQUEST)

        def _query():
            hotel = get_hotel_card(numeric_id)
            if not hotel:
                return None
            rooms = get_available_rooms(
                numeric_id,
                check_in=params.validated_data["check_in"],
                check_out=params.validated_data["check_out"],
                guests=params.validated_data["guests"],
            )
            return rooms

        result = _run_in_schema(schema_name, _query)
        if result is None:
            return Response({"detail": "Hotel not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(RoomAvailabilitySerializer(result, many=True).data)


class HotelRoomPriceView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        manual_parameters=[_GUID_PARAM],
        query_serializer=RoomSelectParamsSerializer,
        responses={200: StayPriceSerializer()},
    )
    def get(self, request, guid, room_id):
        resolved = _resolve_hotel(guid)
        if not resolved:
            return Response({"detail": "Invalid GUID."}, status=status.HTTP_400_BAD_REQUEST)

        schema_name, _ = resolved

        params = RoomSelectParamsSerializer(data=request.query_params)
        if not params.is_valid():
            return Response(params.errors, status=status.HTTP_400_BAD_REQUEST)

        def _query():
            return calculate_stay_price(
                room_id,
                params.validated_data["check_in"],
                params.validated_data["check_out"],
            )

        pricing = _run_in_schema(schema_name, _query)
        if not pricing:
            return Response(
                {"detail": "Room not found, no rate available, or invalid dates."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(StayPriceSerializer(pricing).data)


class HotelReviewsView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        manual_parameters=[
            _GUID_PARAM,
            openapi.Parameter("limit", openapi.IN_QUERY, type=openapi.TYPE_INTEGER, default=10),
            openapi.Parameter("offset", openapi.IN_QUERY, type=openapi.TYPE_INTEGER, default=0),
        ],
        responses={200: ReviewListSerializer(many=True)},
    )
    def get(self, request, guid):
        resolved = _resolve_hotel(guid)
        if not resolved:
            return Response({"detail": "Invalid GUID."}, status=status.HTTP_400_BAD_REQUEST)

        schema_name, numeric_id = resolved

        limit = int(request.query_params.get("limit", 10))
        offset = int(request.query_params.get("offset", 0))

        def _query():
            return get_hotel_reviews(numeric_id, limit=limit, offset=offset)

        reviews = _run_in_schema(schema_name, _query)
        return Response(ReviewListSerializer(reviews, many=True).data)


class HotelCalendarView(APIView):
    """GET /hotels/<guid>/calendar/

    Har bir faol xona uchun kunlik bandlik holati — foydalanuvchi mehmonxona
    sahifasida taqvim ko'rinishida band/bo'sh sanalarni ko'rishi uchun.
    """
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_summary="Mehmonxona bandlik taqvimi",
        operation_description=(
            "Tanlangan mehmonxona (``guid``) uchun ``from_date`` – "
            "``to_date`` oralig'idagi kunlik xona bandlik holatini "
            "qaytaradi.\n\n"
            "Natija har bir xona × sana juftligi uchun bitta qatordan "
            "iborat: ``room_id``, ``room_name``, ``date`` (YYYY-MM-DD), "
            "va ``status`` (``booked`` yoki ``available``)."
        ),
        manual_parameters=[
            _GUID_PARAM,
            openapi.Parameter(
                "from_date", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                format=openapi.FORMAT_DATE, required=True,
                description="Taqvim oralig'i boshlanish sanasi (YYYY-MM-DD).",
            ),
            openapi.Parameter(
                "to_date", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                format=openapi.FORMAT_DATE, required=True,
                description="Taqvim oralig'i tugash sanasi (YYYY-MM-DD).",
            ),
        ],
        responses={
            200: HotelCalendarSerializer(many=True),
            400: openapi.Response(description="Noto'g'ri GUID yoki sana parametrlari."),
            404: openapi.Response(description="Mehmonxona topilmadi."),
        },
    )
    def get(self, request, guid):
        resolved = _resolve_hotel(guid)
        if not resolved:
            return Response({"detail": "Invalid GUID."}, status=status.HTTP_400_BAD_REQUEST)

        schema_name, hotel_id = resolved

        from_date_str = request.query_params.get("from_date")
        to_date_str = request.query_params.get("to_date")
        if not from_date_str or not to_date_str:
            return Response(
                {"detail": "from_date and to_date are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            from_date = date.fromisoformat(from_date_str)
            to_date = date.fromisoformat(to_date_str)
        except ValueError:
            return Response(
                {"detail": "Invalid date format. Use YYYY-MM-DD."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        def _query():
            return get_hotel_calendar(hotel_id, from_date=from_date, to_date=to_date)

        try:
            calendar = _run_in_schema(schema_name, _query)
        except Exception:
            return Response({"detail": "Hotel not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(HotelCalendarSerializer(calendar, many=True).data)
