"""
Integration tests for hotel booking endpoints and repository functions.
Uses the existing database — inserts test data and cleans up afterward.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.test import TransactionTestCase

from apps.hotels.repository import (
    _check_room_availability,
    calculate_stay_price,
    create_hotel_booking,
    create_hotel_booking_calendar_slots,
    get_available_rooms,
    get_client_hotel_booking,
    get_hotel_booking_by_id,
    get_room_with_details,
    list_client_hotel_bookings,
    release_hotel_booking_calendar_slots,
    update_hotel_booking_status,
)
from apps.booking.raw_serializers import (
    HotelBookingCreateSerializer,
    HotelBookingListSerializer,
    HotelBookingDetailSerializer,
)
from shared.raw.db import execute, fetch_all, fetch_one

TODAY = date.today()
TOMORROW = TODAY + timedelta(days=1)
DAY_AFTER = TODAY + timedelta(days=2)

# ─── DB access marker for all database tests ──────────────────────────────────

pytestmark = pytest.mark.django_db(transaction=True)


# ─── Fixture: set up test hotel + rooms ───────────────────────────────────────

@pytest.fixture(scope="module")
def test_hotel():
    execute(
        """
        INSERT INTO pms_property (organization_id, name, city, full_address, star_rating,
            weel_classification, is_active, is_verified, created_at, updated_at)
        VALUES (1, 'Test Hotel', 'Tashkent', 'Test Address 42', 5, 'Premium',
            TRUE, TRUE, NOW(), NOW())
        ON CONFLICT (id) DO NOTHING
        """
    )
    row = fetch_one(
        "SELECT id FROM pms_property WHERE name = 'Test Hotel' ORDER BY id DESC LIMIT 1"
    )
    return row


@pytest.fixture(scope="module")
def test_room(test_hotel):
    execute(
        """
        INSERT INTO pms_room (property_id, room_type_name, room_number, floor, display_name,
            capacity, base_price, is_active, created_at, updated_at)
        VALUES (%s, 'Test Suite', '101', 1, 'Test Suite 101',
            2, 250000, TRUE, NOW(), NOW())
        ON CONFLICT(id) DO NOTHING
        """,
        [test_hotel["id"]],
    )
    return fetch_one(
        "SELECT id FROM pms_room WHERE property_id = %s AND room_number = '101' ORDER BY id DESC LIMIT 1",
        [test_hotel["id"]],
    )


@pytest.fixture(scope="module")
def test_client():
    execute(
        """
        INSERT INTO public.users (phone_number, role, is_active, is_verified, first_name, last_name, created_at, updated_at)
        VALUES ('+998991234567', 'client', TRUE, TRUE, 'Test', 'Client', NOW(), NOW())
        ON CONFLICT(phone_number) DO UPDATE SET is_active = TRUE, is_verified = TRUE
        """
    )
    return fetch_one("SELECT id, guid FROM public.users WHERE phone_number = '+998991234567' ORDER BY id DESC LIMIT 1")


# ─── Repository Tests ─────────────────────────────────────────────────────────

class TestRoomAvailability:
    """Tests for _check_room_availability() and get_available_rooms()."""

    def test_room_is_available_when_no_bookings(self, test_room, test_hotel):
        available = _check_room_availability(test_room["id"], TOMORROW, DAY_AFTER)
        assert available is True

    def test_get_available_rooms_returns_room(self, test_hotel, test_room):
        rooms = get_available_rooms(test_hotel["id"], check_in=TOMORROW, check_out=DAY_AFTER, guests=1)
        assert len(rooms) >= 1
        room_ids = [r["id"] for r in rooms]
        assert test_room["id"] in room_ids

    def test_get_available_rooms_filters_by_capacity(self, test_hotel):
        rooms = get_available_rooms(test_hotel["id"], check_in=TOMORROW, check_out=DAY_AFTER, guests=99)
        assert len(rooms) == 0  # no room fits 99 people


class TestPriceCalculation:
    """Tests for calculate_stay_price()."""

    def test_price_calculation_returns_correct_values(self, test_room):
        pricing = calculate_stay_price(test_room["id"], check_in=TOMORROW, check_out=DAY_AFTER)
        assert pricing is not None
        assert pricing["nights"] == 1
        assert pricing["price_per_night"] == 250000
        assert pricing["total_price"] == Decimal("250000")
        assert pricing["hold_amount"] == Decimal("75000.00")  # 30%
        assert pricing["remaining_on_arrival"] == Decimal("175000.00")

    def test_price_calculation_multi_night(self, test_room):
        pricing = calculate_stay_price(test_room["id"], check_in=TOMORROW, check_out=TOMORROW + timedelta(days=3))
        assert pricing is not None
        assert pricing["nights"] == 3
        assert pricing["total_price"] == Decimal("750000")

    def test_price_calculation_invalid_nights(self, test_room):
        result = calculate_stay_price(test_room["id"], check_in=DAY_AFTER, check_out=TOMORROW)
        assert result is None


class TestHotelBookingCreate:
    """Tests for create_hotel_booking()."""

    def test_create_booking_success(self, test_hotel, test_room, test_client):
        booking = create_hotel_booking(
            property_id=test_hotel["id"],
            room_id=test_room["id"],
            client_user_id=test_client["id"],
            check_in=TOMORROW,
            check_out=DAY_AFTER,
            guests=2,
            card_id=None,
        )
        assert booking is not None
        assert booking["status"] == "pending"
        assert booking["booking_number"].startswith("H")
        assert booking["guests"] == 2
        assert booking["property_id"] == test_hotel["id"]
        assert booking["room_id"] == test_room["id"]

        booking_id = booking["id"]
        try:
            assert isinstance(booking_id, int)
            assert booking_id > 0
        finally:
            execute("DELETE FROM pms_booking WHERE id = %s", [booking_id])

    def test_create_booking_double_book_same_dates(self, test_hotel, test_room, test_client):
        b1 = create_hotel_booking(
            property_id=test_hotel["id"],
            room_id=test_room["id"],
            client_user_id=test_client["id"],
            check_in=TOMORROW + timedelta(days=30),
            check_out=TOMORROW + timedelta(days=32),
            guests=1,
        )
        assert b1 is not None

        b2 = create_hotel_booking(
            property_id=test_hotel["id"],
            room_id=test_room["id"],
            client_user_id=test_client["id"],
            check_in=TOMORROW + timedelta(days=30),
            check_out=TOMORROW + timedelta(days=32),
            guests=1,
        )
        assert b2 is None  # should fail — room already booked

        execute("DELETE FROM pms_booking WHERE id = %s", [b1["id"]])

    def test_create_booking_unavailable_after_calendar_slots(self, test_hotel, test_room, test_client):
        b1 = create_hotel_booking(
            property_id=test_hotel["id"],
            room_id=test_room["id"],
            client_user_id=test_client["id"],
            check_in=TOMORROW + timedelta(days=60),
            check_out=TOMORROW + timedelta(days=62),
            guests=1,
        )
        assert b1 is not None

        create_hotel_booking_calendar_slots(b1["id"], test_room["id"],
            check_in=TOMORROW + timedelta(days=60),
            check_out=TOMORROW + timedelta(days=62))

        b2 = create_hotel_booking(
            property_id=test_hotel["id"],
            room_id=test_room["id"],
            client_user_id=test_client["id"],
            check_in=TOMORROW + timedelta(days=60),
            check_out=TOMORROW + timedelta(days=62),
            guests=1,
        )
        assert b2 is None  # calendar slots block it

        execute("DELETE FROM pms_booking WHERE id = %s", [b1["id"]])
        execute("DELETE FROM pms_calendar_slot WHERE room_id = %s AND date >= %s AND date < %s",
                [test_room["id"], TOMORROW + timedelta(days=60), TOMORROW + timedelta(days=62)])


class TestCalendarSlots:
    """Tests for create and release calendar slots."""

    def test_create_and_release_calendar_slots(self, test_room):
        future = TODAY + timedelta(days=100)
        future_end = TODAY + timedelta(days=102)

        create_hotel_booking_calendar_slots(0, test_room["id"], check_in=future, check_out=future_end)
        booked = fetch_all(
            "SELECT * FROM pms_calendar_slot WHERE room_id = %s AND status = 'occupied' AND date >= %s AND date < %s",
            [test_room["id"], future, future_end],
        )
        assert len(booked) == 2

        release_hotel_booking_calendar_slots(test_room["id"], check_in=future, check_out=future_end)
        available = fetch_all(
            "SELECT * FROM pms_calendar_slot WHERE room_id = %s AND status = 'available' AND date >= %s AND date < %s",
            [test_room["id"], future, future_end],
        )
        assert len(available) == 2

    def test_availability_checks_calendar_slots(self, test_room):
        future = TODAY + timedelta(days=110)
        future_end = TODAY + timedelta(days=112)
        create_hotel_booking_calendar_slots(0, test_room["id"], check_in=future, check_out=future_end)

        available = _check_room_availability(test_room["id"], future, future_end)
        assert available is False  # blocked by calendar slots

        release_hotel_booking_calendar_slots(test_room["id"], check_in=future, check_out=future_end)
        available_after = _check_room_availability(test_room["id"], future, future_end)
        assert available_after is True


class TestHotelBookingQueries:
    """Tests for listing and detail queries."""

    def test_list_client_bookings(self, test_hotel, test_room, test_client):
        future = TODAY + timedelta(days=200)
        b = create_hotel_booking(
            property_id=test_hotel["id"],
            room_id=test_room["id"],
            client_user_id=test_client["id"],
            check_in=future,
            check_out=future + timedelta(days=1),
            guests=1,
        )
        assert b is not None
        try:
            bookings = list_client_hotel_bookings(test_client["id"])
            assert len(bookings) >= 1
            ids = [bk["id"] for bk in bookings]
            assert b["id"] in ids

            bookings_pending = list_client_hotel_bookings(test_client["id"], statuses=["pending"])
            assert b["id"] in [bk["id"] for bk in bookings_pending]

            bookings_cancelled = list_client_hotel_bookings(test_client["id"], statuses=["cancelled"])
            assert b["id"] not in [bk["id"] for bk in bookings_cancelled]
        finally:
            execute("DELETE FROM pms_booking WHERE id = %s", [b["id"]])

    def test_get_client_booking_detail(self, test_hotel, test_room, test_client):
        future = TODAY + timedelta(days=210)
        b = create_hotel_booking(
            property_id=test_hotel["id"],
            room_id=test_room["id"],
            client_user_id=test_client["id"],
            check_in=future,
            check_out=future + timedelta(days=1),
            guests=2,
        )
        assert b is not None
        try:
            detail = get_client_hotel_booking(b["id"], test_client["id"])
            assert detail is not None
            assert detail["id"] == b["id"]
            assert detail["booking_number"] == b["booking_number"]
            assert detail["hotel_name"] == "Test Hotel"
            assert detail["room_number"] == "101"
            assert detail["room_type_name"] == "Test Suite"

            not_found = get_client_hotel_booking(b["id"], 99999)
            assert not_found is None
        finally:
            execute("DELETE FROM pms_booking WHERE id = %s", [b["id"]])

    def test_get_hotel_booking_by_id(self, test_hotel, test_room, test_client):
        future = TODAY + timedelta(days=220)
        b = create_hotel_booking(
            property_id=test_hotel["id"],
            room_id=test_room["id"],
            client_user_id=test_client["id"],
            check_in=future,
            check_out=future + timedelta(days=1),
            guests=1,
        )
        assert b is not None
        try:
            found = get_hotel_booking_by_id(b["id"])
            assert found is not None
            assert found["status"] == "pending"
            assert found["room_id"] == test_room["id"]
        finally:
            execute("DELETE FROM pms_booking WHERE id = %s", [b["id"]])


class TestBookingStatusUpdate:
    """Tests for update_hotel_booking_status()."""

    def test_update_status(self, test_hotel, test_room, test_client):
        future = TODAY + timedelta(days=230)
        b = create_hotel_booking(
            property_id=test_hotel["id"],
            room_id=test_room["id"],
            client_user_id=test_client["id"],
            check_in=future,
            check_out=future + timedelta(days=1),
            guests=1,
        )
        assert b is not None
        try:
            updated = update_hotel_booking_status(b["id"], "confirmed")
            assert updated is not None
            assert updated["status"] == "confirmed"

            cancelled = update_hotel_booking_status(b["id"], "cancelled")
            assert cancelled is not None
            assert cancelled["status"] == "cancelled"
        finally:
            execute("DELETE FROM pms_booking WHERE id = %s", [b["id"]])


class TestGetRoomWithDetails:
    """Tests for get_room_with_details()."""

    def test_get_room_details(self, test_room):
        room = get_room_with_details(test_room["id"])
        assert room is not None
        assert room["price_per_night"] == 250000
        assert room["room_type_name"] == "Test Suite"
        assert room["preset"] == "SUITE"
        assert room["meal_plan"] == "BB"

    def test_get_room_not_found(self):
        room = get_room_with_details(99999999)
        assert room is None


# ─── Serializer Tests ─────────────────────────────────────────────────────────

class TestHotelBookingCreateSerializer:
    """Tests for HotelBookingCreateSerializer."""

    def test_valid_data(self):
        s = HotelBookingCreateSerializer(data={
            "hotel_guid": "schema:1",
            "room_id": 42,
            "check_in": "2026-07-10",
            "check_out": "2026-07-12",
            "guests": 2,
            "card_id": "card-abc-123",
        })
        assert s.is_valid(), s.errors
        assert s.validated_data["room_id"] == 42
        assert s.validated_data["guests"] == 2

    def test_missing_required_fields(self):
        s = HotelBookingCreateSerializer(data={"hotel_guid": "x:1"})
        assert not s.is_valid()
        assert "room_id" in s.errors or "check_in" in s.errors

    def test_invalid_date_order(self):
        s = HotelBookingCreateSerializer(data={
            "hotel_guid": "schema:1",
            "room_id": 1,
            "check_in": "2026-07-12",
            "check_out": "2026-07-10",
            "guests": 1,
        })
        assert not s.is_valid()

    def test_zero_guests(self):
        s = HotelBookingCreateSerializer(data={
            "hotel_guid": "schema:1",
            "room_id": 1,
            "check_in": "2026-07-10",
            "check_out": "2026-07-12",
            "guests": 0,
        })
        assert not s.is_valid()

    def test_card_id_optional(self):
        s = HotelBookingCreateSerializer(data={
            "hotel_guid": "schema:1",
            "room_id": 1,
            "check_in": "2026-07-10",
            "check_out": "2026-07-12",
            "guests": 3,
        })
        assert s.is_valid(), s.errors
        assert s.validated_data.get("card_id") is None


class TestHotelBookingListSerializer:
    """Tests for HotelBookingListSerializer."""

    def test_serializes_booking_list(self):
        booking = {
            "id": 1, "booking_number": "H26070123456", "status": "pending",
            "check_in": "2026-07-10", "check_out": "2026-07-12",
            "guests": 2, "total_price": "250000", "hold_amount": "75000",
            "remaining_on_arrival": "175000", "created_at": "2026-07-01T00:00:00Z",
            "hotel_name": "Test", "hotel_city": "Tashkent",
            "hotel_star_rating": 5, "room_number": "101", "room_name": "Suite",
            "room_type_name": "Suite", "room_type_preset": "SUITE",
        }
        s = HotelBookingListSerializer(booking)
        data = s.data
        assert data["id"] == 1
        assert data["booking_number"] == "H26070123456"
        assert data["total_price"] == "250000.00"
        assert data["hold_amount"] == "75000.00"
        assert data["hotel_name"] == "Test"


class TestHotelBookingDetailSerializer:
    """Tests for HotelBookingDetailSerializer."""

    def test_serializes_full_booking_detail(self):
        booking = {
            "id": 1, "booking_number": "H26070123456", "status": "pending",
            "check_in": "2026-07-10", "check_out": "2026-07-12",
            "guests": 2, "total_price": "250000", "hold_amount": "75000",
            "remaining_on_arrival": "175000", "created_at": "2026-07-01T00:00:00Z",
            "hotel_name": "Test Hotel", "hotel_city": "Tashkent",
            "hotel_address": "Street 1", "hotel_star_rating": 5,
            "hotel_check_in_time": "14:00", "hotel_check_out_time": "12:00",
            "hotel_latitude": "41.3", "hotel_longitude": "69.2",
            "hotel_images": [], "room_number": "101", "room_name": "Suite 101",
            "room_floor": 1, "room_price_per_night": "250000",
            "room_bedroom_count": 1, "room_beds": 2,
            "room_capacity_adults": 2, "room_capacity_children": 1,
            "room_type_name": "Suite", "room_type_preset": "SUITE",
            "room_type_area_sqm": "45.0", "room_meal_plan": "BB",
            "room_images": [],
        }
        s = HotelBookingDetailSerializer(booking)
        data = s.data
        assert data["id"] == 1
        assert data["booking_number"] == "H26070123456"
        assert data["total_price"] == "250000.00"
        assert data["hold_amount"] == "75000.00"
        assert data["hotel_name"] == "Test Hotel"
        assert data["room_type_name"] == "Suite"
        assert data["room_meal_plan"] == "BB"
        assert data["hotel_images"] == []
        assert data["room_images"] == []


# ─── Cleanup ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def cleanup_after_tests(request):
    yield
    # Remove all test bookings (future dates to avoid touching real data)
    execute(
        "DELETE FROM pms_booking WHERE check_in >= %s",
        [TODAY + timedelta(days=29)],
    )
    execute(
        "DELETE FROM pms_calendar_slot WHERE date >= %s",
        [TODAY + timedelta(days=29)],
    )
