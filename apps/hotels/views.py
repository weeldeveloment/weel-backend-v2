from __future__ import annotations

import calendar
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

from django.core.cache import cache
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
    HoteliosClient,
    HoteliosError,
    get_client,
)
from apps.hotels.models import HotelBookingStatus
from apps.hotels.permissions import CanCreateTrip
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


class HotelCalendarView(HoteliosAPIView):
    """GET /api/hotels/{hotel_id}/calendar/?year=&month=&adults= — a
    free/occupied dot for every day of one month, for one hotel.

    Hotelios has no per-day availability endpoint, only `search` for a single
    date range, so this is one 1-night `search` call per day of the month —
    run several at a time, but still dozens of round trips to Hotelios for a
    30-day month. Genuinely slow on a cold call; the result is cached for
    `_CACHE_TTL_SECONDS` so the same hotel/month is instant for the next
    person (or the next open of the drawer) within that window.
    """

    _MAX_CONCURRENT_SEARCHES = 10
    # Each day's `search` gets its own short timeout — the calendar dot is
    # informational, not a booking, so a slow Hotelios response is worth
    # giving up on quickly (and retrying next load) rather than letting one
    # bad day hold up the whole month at the client's normal (60s) timeout.
    _PER_DAY_TIMEOUT_SECONDS = 8.0
    _CACHE_TTL_SECONDS = 300

    def get(self, request, hotel_id: int):
        try:
            year = int(request.query_params["year"])
            month = int(request.query_params["month"])
        except (KeyError, ValueError):
            return Response(
                {"detail": "year and month are required integers."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not 1 <= month <= 12:
            return Response(
                {"detail": "month must be between 1 and 12."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        adults = max(1, int(request.query_params.get("adults") or 1))

        cache_key = f"hotels:calendar:{hotel_id}:{year}:{month}:{adults}"
        cached = cache.get(cache_key)
        if cached is not None:
            return Response(cached)

        today = timezone.localdate()
        days_in_month = calendar.monthrange(year, month)[1]
        candidate_days = [
            day
            for day in range(1, days_in_month + 1)
            if date(year, month, day) >= today
        ]

        def _check_day(day: int) -> tuple[int, int | None]:
            check_in = date(year, month, day)
            check_out = check_in + timedelta(days=1)
            try:
                entries = service.search(
                    # Hotelios wants `YYYY/MM/DD HH:MM`, the same format
                    # HotelSearchSerializer.provider_dates() builds for the
                    # live search endpoint — not a plain ISO date, which it
                    # answers with error 2003 ("check_in value is invalid").
                    check_in=check_in.strftime("%Y/%m/%d 14:00"),
                    check_out=check_out.strftime("%Y/%m/%d 12:00"),
                    occupancies=[{"adults": adults, "children_ages": []}],
                    hotel_ids=[hotel_id],
                    # A fresh, short-timeout client per call, not the shared
                    # module-level one — `requests.Session` isn't guaranteed
                    # safe under concurrent use, and the default 60s client
                    # timeout is far too patient for an informational dot.
                    client=HoteliosClient(timeout=self._PER_DAY_TIMEOUT_SECONDS),
                )
            except HoteliosError:
                # Left out of the response entirely rather than guessed at —
                # the frontend only marks a day busy/free when it has an answer.
                return day, None
            match = next((e for e in entries if e.get("hotel_id") == hotel_id), None)
            available = len(match.get("options") or []) if match else 0
            return day, available

        outcomes: dict[int, int | None] = {}
        with ThreadPoolExecutor(max_workers=self._MAX_CONCURRENT_SEARCHES) as executor:
            futures = [executor.submit(_check_day, day) for day in candidate_days]
            for future in as_completed(futures):
                day, available = future.result()
                outcomes[day] = available

        days_payload = [
            {"date": date(year, month, day).isoformat(), "available": outcomes[day]}
            for day in candidate_days
            if outcomes.get(day) is not None
        ]

        payload = {"year": year, "month": month, "days": days_payload}
        cache.set(cache_key, payload, self._CACHE_TTL_SECONDS)
        return Response(payload)


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

        nights = (data["check_out"] - data["check_in"]).days

        hotel_ids = [entry.get("hotel_id") for entry in results if entry.get("hotel_id")]
        cards, _ = repo.fetch_hotels(hotel_ids=hotel_ids, limit=len(hotel_ids) or 1)
        by_id = {card["id"]: HotelSerializer(card).data for card in cards}

        # The provider names a room type but sends nothing else about it —
        # no photo, no area, no bed type. Those live in our synced copy, and
        # a room card that shows only a name and a price looks unfinished, so
        # the whole page's room types are joined in with one query.
        room_types = repo.fetch_room_types_for(hotel_ids)
        rooms_by_key = {
            (row["hotel_id"], row["room_type_id"]): RoomTypeSerializer(row).data
            for row in room_types
        }

        return Response({
            "count": len(results),
            "nights": nights,
            "currency": data["currency"].lower(),
            "results": [
                _search_entry(entry, by_id, rooms_by_key, nights)
                for entry in results
            ],
        })


def _search_entry(
    entry: dict,
    hotels_by_id: dict,
    rooms_by_key: dict,
    nights: int,
) -> dict:
    """One search result, shaped the way a hotel card and a room list want it.

    Hotelios sends a flat `options` list where a room type repeats once per
    rate plan. A screen wants the opposite: one entry per room type, with its
    rate plans underneath. Both shapes are returned — `options` untouched for
    anything that wants the raw list, and `rooms` grouped for the UI.

    Every `price` here is the total for the whole stay, for one room at that
    occupancy — verified against the provider by comparing 1-, 2- and 3-night
    searches. `price_per_night` is derived so a card never has to guess which
    of the two a number is.
    """
    hotel_id = entry.get("hotel_id")
    options = entry.get("options") or []

    grouped: dict[int, dict] = {}
    for option in options:
        price = option.get("price")
        priced = {
            **option,
            "price_per_night": (round(price / nights, 2) if price and nights else None),
        }
        room_type_id = option.get("room_type_id")
        room = grouped.get(room_type_id)
        if room is None:
            room = grouped[room_type_id] = {
                "room_type_id": room_type_id,
                "name": option.get("room_type_name"),
                # Null when the room type isn't in our synced copy — a hotel
                # synced before the room type was added upstream. The name and
                # price above still stand on their own.
                "room_type": rooms_by_key.get((hotel_id, room_type_id)),
                "options": [],
            }
        room["options"].append(priced)

    for room in grouped.values():
        room["options"].sort(key=lambda o: o.get("price") or 0)
        room["min_price"] = room["options"][0].get("price") if room["options"] else None

    rooms = sorted(
        grouped.values(),
        key=lambda r: (r["min_price"] is None, r["min_price"] or 0),
    )
    prices = [o["price"] for o in options if o.get("price")]

    return {
        **entry,
        "hotel": hotels_by_id.get(hotel_id),
        "rooms": rooms,
        # What the search card prints as "N so'm dan" — the cheapest option
        # in the whole hotel, for the stay as searched.
        "min_price": min(prices) if prices else None,
        "min_price_per_night": (
            round(min(prices) / nights, 2) if prices and nights else None
        ),
        "currency": next(
            (o.get("currency") for o in options if o.get("currency")), None
        ),
    }


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

    def get_permissions(self):
        permissions = super().get_permissions()
        if self.request.method == "POST":
            permissions.append(CanCreateTrip())
        return permissions

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
