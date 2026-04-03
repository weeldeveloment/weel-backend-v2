from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import resolve
from django.utils import timezone

from booking.helpers import (
    _get_cancellation_window,
    client_can_cancel,
    get_cancellation_error_message,
)
from booking.raw_serializers import (
    RawClientBookingCreateSerializer,
    RawPartnerBookingListSerializer,
    _resolve_property_average_rating,
)


class BookingCancellationRulesTests(SimpleTestCase):
    def _booking(self, *, check_in: date, created_at: datetime, status: str = "pending"):
        return SimpleNamespace(check_in=check_in, created_at=created_at, status=status)

    @patch("booking.helpers.timezone.localdate")
    def test_cancellation_window_none_when_checkin_is_today(self, mock_localdate):
        today = date(2026, 4, 3)
        mock_localdate.return_value = today
        booking = self._booking(check_in=today, created_at=timezone.now())

        self.assertIsNone(_get_cancellation_window(booking))

    @patch("booking.helpers.timezone.localdate")
    def test_cancellation_window_one_hour_when_checkin_tomorrow(self, mock_localdate):
        today = date(2026, 4, 3)
        mock_localdate.return_value = today
        booking = self._booking(check_in=today + timedelta(days=1), created_at=timezone.now())

        self.assertEqual(_get_cancellation_window(booking), timedelta(hours=1))

    @patch("booking.helpers.timezone.localdate")
    @patch("booking.helpers.timezone.now")
    def test_client_can_cancel_respects_status_and_expiry(self, mock_now, mock_localdate):
        today = date(2026, 4, 3)
        now = datetime(2026, 4, 3, 10, 0, 0, tzinfo=timezone.get_current_timezone())
        mock_localdate.return_value = today
        mock_now.return_value = now

        recent = self._booking(
            check_in=today + timedelta(days=2),
            created_at=now - timedelta(hours=2),
            status="confirmed",
        )
        expired = self._booking(
            check_in=today + timedelta(days=2),
            created_at=now - timedelta(hours=8),
            status="confirmed",
        )
        bad_status = self._booking(
            check_in=today + timedelta(days=2),
            created_at=now - timedelta(hours=1),
            status="completed",
        )

        self.assertTrue(client_can_cancel(recent))
        self.assertFalse(client_can_cancel(expired))
        self.assertFalse(client_can_cancel(bad_status))

    @patch("booking.helpers.timezone.localdate")
    def test_cancellation_error_message_for_today_checkin(self, mock_localdate):
        today = date(2026, 4, 3)
        mock_localdate.return_value = today
        booking = self._booking(check_in=today, created_at=timezone.now())
        self.assertIn("check-in is today", get_cancellation_error_message(booking))


class BookingSerializersTests(SimpleTestCase):
    def test_client_booking_create_serializer_rejects_too_many_guests(self):
        today = date.today()
        serializer = RawClientBookingCreateSerializer(
            data={
                "property_id": "11111111-1111-1111-1111-111111111111",
                "card_id": "card_1",
                "check_in": today + timedelta(days=1),
                "check_out": today + timedelta(days=2),
                "adults": 12,
                "children": 5,
                "babies": 0,
            },
            context={"property_row": {}},
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("shouldn't greater than 15", str(serializer.errors))

    def test_client_booking_create_serializer_requires_sunday_when_weekend_only_flag_enabled(self):
        today = date.today()
        # Friday -> Saturday stay does not include Sunday
        friday = today + timedelta(days=(4 - today.weekday()) % 7)
        saturday = friday + timedelta(days=1)

        serializer = RawClientBookingCreateSerializer(
            data={
                "property_id": "11111111-1111-1111-1111-111111111111",
                "card_id": "card_1",
                "check_in": friday,
                "check_out": saturday,
                "adults": 2,
                "children": 0,
                "babies": 0,
            },
            context={"property_row": {"weekend_only_sunday_inclusive": True}},
        )
        self.assertFalse(serializer.is_valid())
        self.assertIn("include Sunday", str(serializer.errors))

    @patch("booking.raw_serializers.fetch_one", return_value={"avg_rating": 4.25})
    def test_resolve_property_average_rating_returns_numeric_value(self, _mock_fetch_one):
        obj = {"property_apartment_id": 10, "property_cottage_id": None}
        self.assertEqual(_resolve_property_average_rating(obj), 4.25)

    def test_partner_booking_list_serializer_builds_nested_price_payload(self):
        row = {
            "guid": "11111111-1111-1111-1111-111111111111",
            "property_guid": "22222222-2222-2222-2222-222222222222",
            "property_title": "Cottage",
            "property_img": None,
            "client_first_name": "Ali",
            "client_last_name": "Valiyev",
            "check_in": date.today() + timedelta(days=1),
            "check_out": date.today() + timedelta(days=3),
            "adults": 2,
            "children": 1,
            "babies": 0,
            "booking_price_guid": None,
            "booking_subtotal": "300000.00",
            "booking_hold_amount": "120000.00",
            "booking_charge_amount": "180000.00",
            "booking_service_fee": "60000.00",
            "booking_service_fee_percentage": 20,
            "booking_number": "0001234",
            "status": "pending",
            "cancellation_reason": None,
            "confirmed_at": None,
            "cancelled_at": None,
            "completed_at": None,
        }
        data = RawPartnerBookingListSerializer(row).data
        self.assertEqual(data["booking_price"]["service_fee_percentage"], 20)
        self.assertEqual(data["client"]["first_name"], "Ali")


class BookingUrlsTests(SimpleTestCase):
    def test_client_booking_list_url_resolves(self):
        match = resolve("/api/booking/client/")
        self.assertEqual(match.func.view_class.__name__, "ClientBookingListCreateView")

    def test_partner_booking_list_url_resolves(self):
        match = resolve("/api/booking/partner/")
        self.assertEqual(match.func.view_class.__name__, "PartnerBookingListView")

