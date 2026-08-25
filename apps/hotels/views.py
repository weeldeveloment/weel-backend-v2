from __future__ import annotations

import logging

from django.utils import timezone

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_yasg.utils import swagger_auto_schema

from apps.hotels import raw_repository as repo
from apps.hotels import service
from apps.hotels.client import (
    ERROR_INSUFFICIENT_BALANCE,
    ERROR_NOT_FOUND,
    HoteliosError,
    get_client,
)
from apps.hotels.models import HotelBookingStatus
from apps.hotels.raw.tables import (
    HOTELIOS_BED_TYPE_TABLE,
    HOTELIOS_COUNTRY_TABLE,
    HOTELIOS_EQUIPMENT_TABLE,
    HOTELIOS_FACILITY_TABLE,
    HOTELIOS_HOTEL_TYPE_TABLE,
    HOTELIOS_NEARBY_PLACE_TYPE_TABLE,
    HOTELIOS_SERVICE_IN_ROOM_TABLE,
    HOTELIOS_STAR_TABLE,
)
from apps.hotels.raw_serializers import (
    CitySerializer,
    CreateHotelBookingSerializer,
    HotelBookingRoomSerializer,
    HotelBookingSerializer,
    HotelSearchSerializer,
    HotelSerializer,
    MonthlySummarySerializer,
    QuoteSerializer,
    RecommendedHotelSerializer,
    RoomTypeSerializer,
    TopHotelSerializer,
)

logger = logging.getLogger(__name__)

# The reference lists the apps are allowed to read, keyed by the name they use
# in the URL. An allowlist rather than a table name straight off the path.
REFERENCE_TABLES = {
    "countries": HOTELIOS_COUNTRY_TABLE,
    "hotel-types": HOTELIOS_HOTEL_TYPE_TABLE,
    "facilities": HOTELIOS_FACILITY_TABLE,
    "equipment": HOTELIOS_EQUIPMENT_TABLE,
    "nearby-place-types": HOTELIOS_NEARBY_PLACE_TYPE_TABLE,
    "services-in-room": HOTELIOS_SERVICE_IN_ROOM_TABLE,
    "bed-types": HOTELIOS_BED_TYPE_TABLE,
    "stars": HOTELIOS_STAR_TABLE,
}


def _error_response(exc: HoteliosError) -> Response:
    body = {"detail": exc.message, "error_code": exc.error_code}

    if exc.error_code == ERROR_NOT_FOUND:
        return Response(body, status=status.HTTP_404_NOT_FOUND)

    if exc.is_price_changed:
        body["price_changed"] = True
        body["detail"] = (
            "The room price changed while you were booking. Please review it and try again."
        )
        return Response(body, status=status.HTTP_409_CONFLICT)

    if exc.is_sold_out:
        body["sold_out"] = True
        body["detail"] = "That room is no longer available. Please choose another."
        return Response(body, status=status.HTTP_409_CONFLICT)

    if exc.error_code == ERROR_INSUFFICIENT_BALANCE:
        logger.error("hotels: Hotelios credit limit reached.")
        body["detail"] = "Hotel booking is temporarily unavailable. Please try again later."
        return Response(body, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    if exc.is_retryable:
        body["retryable"] = True
        return Response(body, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    logger.warning("hotels: Hotelios call failed — %s", exc)
    return Response(body, status=status.HTTP_502_BAD_GATEWAY)


class HoteliosAPIView(APIView):
    """Turns any `HoteliosError` raised in a handler into a response."""

    permission_classes = [IsAuthenticated]

    def handle_exception(self, exc):
        if isinstance(exc, HoteliosError):
            return _error_response(exc)
        return super().handle_exception(exc)


def _ownership(request) -> dict[str, int | None]:
    user = request.user
    company_id = user.get("company_id") if isinstance(user, dict) else getattr(user, "company_id", None)
    user_id = user.get("id") if isinstance(user, dict) else getattr(user, "id", None)
    if company_id:
        return {"b2b_company_id": company_id, "b2b_user_id": user_id}
    return {"client_user_id": user_id}


def _visible_booking(request, guid: str) -> dict | None:
    booking = repo.fetch_booking_by_guid(guid)
    if booking is None:
        return None
    owner = _ownership(request)
    if "b2b_company_id" in owner:
        if booking["b2b_company_id"] != owner["b2b_company_id"]:
            return None
    elif booking["client_user_id"] != owner["client_user_id"]:
        return None
    return booking


def _with_rooms(booking: dict) -> dict:
    return {**booking, "rooms": repo.fetch_booking_rooms(booking["id"])}


# ---------------------------------------------------------------------------
# Catalogue — served from our synced copy
# ---------------------------------------------------------------------------

class HotelReferenceView(HoteliosAPIView):
    """GET /api/hotels/reference/{name}/ — a synced lookup list."""

    def get(self, request, name: str):
        table = REFERENCE_TABLES.get(name)
        if table is None:
            return Response(
                {
                    "detail": f"Unknown reference '{name}'.",
                    "available": sorted(REFERENCE_TABLES),
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response({"results": repo.fetch_reference(table)})


class HotelCityListView(HoteliosAPIView):
    """GET /api/hotels/cities/ — cities that actually have bookable hotels."""

    @swagger_auto_schema(responses={200: CitySerializer(many=True)})
    def get(self, request):
        cities = repo.fetch_cities(
            query=request.query_params.get("q") or None,
            limit=min(int(request.query_params.get("limit") or 50), 200),
        )
        return Response(CitySerializer(cities, many=True).data)


class HotelListView(HoteliosAPIView):
    """GET /api/hotels/ — browse the catalogue without checking availability."""

    @swagger_auto_schema(responses={200: HotelSerializer(many=True)})
    def get(self, request):
        params = request.query_params
        limit = min(int(params.get("limit") or 20), 100)
        offset = max(int(params.get("offset") or 0), 0)
        stars = [int(s) for s in params.getlist("stars") if s.isdigit()]
        hotels, total = repo.fetch_hotels(
            city_id=int(params["city_id"]) if params.get("city_id") else None,
            stars=stars or None,
            query=params.get("q") or None,
            limit=limit,
            offset=offset,
        )
        return Response({
            "count": total,
            "limit": limit,
            "offset": offset,
            "results": HotelSerializer(hotels, many=True).data,
        })


class HotelDetailView(HoteliosAPIView):
    """GET /api/hotels/{hotel_id}/ — the hotel card, with its room types."""

    @swagger_auto_schema(responses={200: HotelSerializer()})
    def get(self, request, hotel_id: int):
        hotel = repo.fetch_hotel(hotel_id)
        if hotel is None:
            return Response({"detail": "Hotel not found."}, status=status.HTTP_404_NOT_FOUND)
        room_types = repo.fetch_room_types(hotel_id)
        return Response({
            **HotelSerializer(hotel).data,
            "room_types": RoomTypeSerializer(room_types, many=True).data,
        })


# ---------------------------------------------------------------------------
# Booking flow — live against Hotelios
# ---------------------------------------------------------------------------

class HotelSearchView(HoteliosAPIView):
    """POST /api/hotels/search/ — live availability and prices.

    The result carries only hotel ids and room options, so the hotel cards are
    joined in from our synced copy: that is the whole point of the sync, and it
    saves the apps a second round trip per result.
    """

    @swagger_auto_schema(request_body=HotelSearchSerializer)
    def post(self, request):
        serializer = HotelSearchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        check_in, check_out = serializer.provider_dates()

        results = service.search(
            check_in=check_in,
            check_out=check_out,
            occupancies=[
                {
                    "adults": occupancy["adults"],
                    "children_ages": occupancy.get("children_ages") or [],
                }
                for occupancy in data["occupancies"]
            ],
            currency=data["currency"].lower(),
            city_id=data.get("city_id"),
            hotel_ids=data.get("hotel_ids"),
            nationality=data.get("nationality"),
            residence=data.get("residence"),
            filters=serializer.provider_filters(),
        )

        hotel_ids = [entry.get("hotel_id") for entry in results if entry.get("hotel_id")]
        cards, _ = repo.fetch_hotels(hotel_ids=hotel_ids, limit=len(hotel_ids) or 1)
        by_id = {card["id"]: HotelSerializer(card).data for card in cards}

        return Response({
            "count": len(results),
            "results": [
                {**entry, "hotel": by_id.get(entry.get("hotel_id"))}
                for entry in results
            ],
        })


class HotelQuoteView(HoteliosAPIView):
    """POST /api/hotels/quote/ — confirm price and availability, open a quote.

    Mandatory before booking, and the `quote_id` it returns is only good for
    about an hour, so this belongs immediately before the payment screen.
    """

    @swagger_auto_schema(request_body=QuoteSerializer)
    def post(self, request):
        serializer = QuoteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(service.quote(serializer.validated_data["option_ref_ids"]))


class HotelBookingListCreateView(HoteliosAPIView):
    """GET/POST /api/hotels/bookings/ — this caller's bookings, and new holds.

    POST holds the rooms; it does not send them to the hotel. That is the
    separate confirm step, which is what a completed payment triggers.
    """

    @swagger_auto_schema(responses={200: HotelBookingSerializer(many=True)})
    def get(self, request):
        owner = _ownership(request)
        if "b2b_company_id" in owner:
            bookings = repo.fetch_bookings_for_company(
                b2b_company_id=owner["b2b_company_id"],
                trip_id=request.query_params.get("trip_id") or None,
                status=request.query_params.get("status") or None,
            )
        else:
            bookings = repo.fetch_bookings_for_client(client_user_id=owner["client_user_id"])
        return Response(HotelBookingSerializer(bookings, many=True).data)

    @swagger_auto_schema(
        request_body=CreateHotelBookingSerializer,
        responses={201: HotelBookingSerializer()},
    )
    def post(self, request):
        serializer = CreateHotelBookingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        owner = _ownership(request)
        if "b2b_company_id" in owner and data.get("trip_id"):
            owner = {**owner, "b2b_trip_id": data["trip_id"]}

        delta = data.get("delta_price")
        booking = service.create_booking(
            quote_id=data["quote_id"],
            hotel_id=data["hotel_id"],
            check_in=data["check_in"],
            check_out=data["check_out"],
            booking_rooms=serializer.provider_rooms(),
            comment=data.get("comment") or None,
            delta_price={
                key: (float(value) if key != "matches" else value)
                for key, value in delta.items()
            } if delta else None,
            nationality=data.get("nationality"),
            residence=data.get("residence"),
            is_resident=data["is_resident"],
            **owner,
        )
        return Response(
            HotelBookingSerializer(_with_rooms(booking)).data,
            status=status.HTTP_201_CREATED,
        )


class HotelBookingDetailView(HoteliosAPIView):
    """GET /api/hotels/bookings/{guid}/ — with `?refresh=1` to re-read upstream."""

    @swagger_auto_schema(responses={200: HotelBookingSerializer()})
    def get(self, request, guid: str):
        booking = _visible_booking(request, guid)
        if booking is None:
            return Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        if request.query_params.get("refresh") in ("1", "true", "True"):
            booking = service.refresh_booking(booking, source="api")
        return Response(HotelBookingSerializer(_with_rooms(booking)).data)


class HotelBookingConfirmView(HoteliosAPIView):
    """POST /api/hotels/bookings/{guid}/confirm/ — send it to the hotel.

    Works exactly once, and is the step that makes the reservation real.
    """

    @swagger_auto_schema(responses={200: HotelBookingSerializer()})
    def post(self, request, guid: str):
        booking = _visible_booking(request, guid)
        if booking is None:
            return Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        if booking["status"] != HotelBookingStatus.DRAFT:
            return Response(
                {
                    "detail": f"This booking is already in status '{booking['status']}'.",
                    "status": booking["status"],
                },
                status=status.HTTP_409_CONFLICT,
            )
        booking = service.confirm_booking(booking)
        return Response(HotelBookingSerializer(_with_rooms(booking)).data)


class HotelBookingRefreshView(HoteliosAPIView):
    """POST /api/hotels/bookings/{guid}/refresh/."""

    def post(self, request, guid: str):
        booking = _visible_booking(request, guid)
        if booking is None:
            return Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        booking = service.refresh_booking(booking, source="api")
        return Response(HotelBookingSerializer(_with_rooms(booking)).data)


class HotelBookingCancelView(HoteliosAPIView):
    """DELETE /api/hotels/bookings/{guid}/cancel/ — cancel with the provider.

    Whether it costs anything is decided by the room's cancellation policy,
    which was recorded on the room line when the booking was made.
    """

    @swagger_auto_schema(responses={200: HotelBookingSerializer()})
    def delete(self, request, guid: str):
        booking = _visible_booking(request, guid)
        if booking is None:
            return Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        if booking["status"] == HotelBookingStatus.CANCELLED:
            return Response(HotelBookingSerializer(_with_rooms(booking)).data)
        booking = service.cancel_booking(booking)
        return Response(HotelBookingSerializer(_with_rooms(booking)).data)

    def post(self, request, guid: str):
        """Same operation for clients that cannot issue a DELETE."""
        return self.delete(request, guid)


class HotelBookingRoomsView(HoteliosAPIView):
    """GET /api/hotels/bookings/{guid}/rooms/ — the priced room lines."""

    @swagger_auto_schema(responses={200: HotelBookingRoomSerializer(many=True)})
    def get(self, request, guid: str):
        booking = _visible_booking(request, guid)
        if booking is None:
            return Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        rooms = repo.fetch_booking_rooms(booking["id"])
        return Response(HotelBookingRoomSerializer(rooms, many=True).data)


class HotelBookingEventsView(HoteliosAPIView):
    """GET /api/hotels/bookings/{guid}/events/ — the status history."""

    def get(self, request, guid: str):
        booking = _visible_booking(request, guid)
        if booking is None:
            return Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        events = repo.fetch_status_events(booking_id=booking["id"])
        return Response({
            "results": [
                {
                    "status": e["status"],
                    "previous_status": e["previous_status"],
                    "source": e["source"],
                    "created_at": e["created_at"],
                }
                for e in events
            ]
        })


# ---------------------------------------------------------------------------
# Analytics — company-scoped, computed from local booking history
# ---------------------------------------------------------------------------

def _company_id_or_none(request) -> int | None:
    user = request.user
    return user.get("company_id") if isinstance(user, dict) else getattr(user, "company_id", None)


class HotelMonthlySummaryView(HoteliosAPIView):
    """GET /api/hotels/monthly-summary/?year=&month= — B2B only."""

    @swagger_auto_schema(responses={200: MonthlySummarySerializer()})
    def get(self, request):
        company_id = _company_id_or_none(request)
        if not company_id:
            return Response(
                {"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST
            )
        now = timezone.now()
        year = int(request.query_params.get("year") or now.year)
        month = int(request.query_params.get("month") or now.month)
        summary = repo.fetch_monthly_summary(b2b_company_id=company_id, year=year, month=month)
        return Response(MonthlySummarySerializer(summary).data)


class HotelTopByBookingsView(HoteliosAPIView):
    """GET /api/hotels/top-by-bookings/?limit= — B2B only."""

    @swagger_auto_schema(responses={200: TopHotelSerializer(many=True)})
    def get(self, request):
        company_id = _company_id_or_none(request)
        if not company_id:
            return Response(
                {"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST
            )
        limit = min(int(request.query_params.get("limit") or 10), 50)
        hotels = repo.fetch_top_hotels_by_bookings(b2b_company_id=company_id, limit=limit)
        return Response({"results": TopHotelSerializer(hotels, many=True).data})


class HotelRecommendationsView(HoteliosAPIView):
    """GET /api/hotels/recommendations/?limit= — B2B only.

    Hotels in cities this company has booked before, top-rated first, minus
    hotels it's currently staying at. A company with no history yet gets the
    overall top-rated catalogue instead of an empty widget.
    """

    @swagger_auto_schema(responses={200: RecommendedHotelSerializer(many=True)})
    def get(self, request):
        company_id = _company_id_or_none(request)
        if not company_id:
            return Response(
                {"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST
            )
        limit = min(int(request.query_params.get("limit") or 10), 50)
        city_ids = repo.fetch_company_booking_cities(b2b_company_id=company_id)
        exclude_ids = repo.fetch_company_active_hotel_ids(b2b_company_id=company_id)
        hotels = repo.fetch_recommended_hotels(
            city_ids=city_ids or None, exclude_hotel_ids=exclude_ids, limit=limit
        )
        return Response({"results": RecommendedHotelSerializer(hotels, many=True).data})


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

class HotelBalanceView(HoteliosAPIView):
    """GET /api/hotels/balance/ — the credit bookings are drawn against.

    Hotelios refuses a booking with error 4303 once balance plus allowed credit
    runs out, so this needs to be visible before a guest finds out for us.
    """

    def get(self, request):
        return Response(get_client().get_balance())


class HotelSyncStatusView(HoteliosAPIView):
    """GET /api/hotels/sync-status/ — how the inventory imports are going."""

    def get(self, request):
        return Response({"results": repo.fetch_recent_sync_runs()})
