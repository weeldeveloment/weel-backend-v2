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
    search_hotels_page,
)
from apps.hotels.serializers import (
    HotelCalendarSerializer,
    HotelDetailSerializer,
    HotelSearchPageSerializer,
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
        responses={200: HotelSearchPageSerializer()},
    )
    def get(self, request):
        params = HotelSearchParamsSerializer(data=request.query_params)
        if not params.is_valid():
            return Response(params.errors, status=status.HTTP_400_BAD_REQUEST)

        d = params.validated_data
        page = d.pop("page")
        page_size = d.pop("page_size")
        offset = (page - 1) * page_size
        d.pop("adults", None)
        d.pop("children", None)
        d.pop("babies", None)

        # One pass: the page and its total come from the same search. Asking
        # `search_hotels` then `count_hotels` ran the whole cross-schema query
        # twice for every page view.
        hotels, count = search_hotels_page(**d, limit=page_size, offset=offset)
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

    # No manual_parameters here: this view is mounted twice, as /hotels/<guid>/
    # and as /property/hotels/<hotel_guid>/. A hardcoded "guid" parameter is
    # wrong for the second route and makes the emitted schema invalid, so let
    # drf_yasg read the name off each URL pattern.
    @swagger_auto_schema(responses={200: HotelDetailSerializer()})
    def get(self, request, guid=None, hotel_guid=None):
        resolved = _resolve_hotel(hotel_guid or guid)
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
                room_types=params.validated_data.get("room_types"),
                room_type_presets=params.validated_data.get("room_type_presets"),
                rate_plans=params.validated_data.get("rate_plans"),
                meal_plans=params.validated_data.get("meal_plans"),
                min_capacity=params.validated_data.get("min_capacity"),
                max_capacity=params.validated_data.get("max_capacity"),
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

    Daily occupancy status for every active room, so users can view booked
    and free dates in a calendar layout on the hotel page.
    """
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_summary="Hotel occupancy calendar",
        operation_description=(
            "Return the daily occupancy status for each room in the selected "
            "hotel (`guid`) over the `from_date` to `to_date` range.\n\n"
            "The result contains one row per room × date pair: `room_id`, "
            "`room_name`, `date` (YYYY-MM-DD), and `status` (`booked` or "
            "`available`)."
        ),
        manual_parameters=[
            _GUID_PARAM,
            openapi.Parameter(
                "from_date", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                format=openapi.FORMAT_DATE, required=True,
                description="Start date for the calendar range (YYYY-MM-DD).",
            ),
            openapi.Parameter(
                "to_date", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                format=openapi.FORMAT_DATE, required=True,
                description="End date for the calendar range (YYYY-MM-DD).",
            ),
            openapi.Parameter("room_types", openapi.IN_QUERY, type=openapi.TYPE_STRING, description="Comma-separated room type names."),
            openapi.Parameter("room_type_presets", openapi.IN_QUERY, type=openapi.TYPE_STRING, description="Comma-separated room type presets."),
            openapi.Parameter("include_summary", openapi.IN_QUERY, type=openapi.TYPE_BOOLEAN, default=False, description="Return a dense matrix summary grouped by date."),
        ],
        responses={
            200: HotelCalendarSerializer(many=True),
            400: openapi.Response(description="Invalid GUID or date parameters."),
            404: openapi.Response(description="Hotel not found."),
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

        room_types = [v.strip() for v in (request.query_params.get("room_types") or "").split(",") if v.strip()]
        room_type_presets = [v.strip() for v in (request.query_params.get("room_type_presets") or "").split(",") if v.strip()]
        include_summary = str(request.query_params.get("include_summary", "")).lower() in {"1", "true", "yes"}

        def _query():
            return get_hotel_calendar(
                hotel_id,
                from_date=from_date,
                to_date=to_date,
                room_types=room_types or None,
                room_type_presets=room_type_presets or None,
                include_summary=include_summary,
            )

        try:
            calendar = _run_in_schema(schema_name, _query)
        except Exception:
            return Response({"detail": "Hotel not found."}, status=status.HTTP_404_NOT_FOUND)
        if include_summary:
            return Response({
                "rows": HotelCalendarSerializer(calendar["rows"], many=True).data,
                "summary": calendar["summary"],
            })
        return Response(HotelCalendarSerializer(calendar, many=True).data)
