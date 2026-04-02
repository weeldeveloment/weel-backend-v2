"""
Tests for the booking app: models, helpers, serializers, filters,
CalendarDateService, BookingPriceService, BookingService (mocked), and API views.
"""

import warnings

# Suppress urllib3 InsecureRequestWarning when tests hit media.weel.uz (e.g. FileField URLs)
try:
    import urllib3
    warnings.filterwarnings("ignore", category=urllib3.exceptions.InsecureRequestWarning)
except Exception:
    pass

from datetime import date, time, timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from rest_framework import status
from rest_framework.test import APIClient

from property.models import (
    Property,
    PropertyType,
    PropertyLocation,
    PropertyDetail,
    PropertyRoom,
    PropertyPrice,
)
from users.models.clients import Client
from users.models.partners import Partner

from .models import Booking, BookingPrice, BookingTransaction, CalendarDate
from .helpers import (
    client_can_cancel,
    get_cancellation_error_message,
)
from .serializers import (
    PropertyCalendarDateRangeSerializer,
    ClientBookingCreateSerializer,
    CalendarDateSerializer,
    BookingPriceSerializer,
)
from .filters import PropertyCalenderDateFilter
from .services import CalendarDateService, BookingPriceService
from .mixins import DateRangeValidationMixin


# ──────────────────────────────────────────────
# Test helpers
# ──────────────────────────────────────────────


class BookingTestMixin:
    """Factory helpers for booking tests."""

    @staticmethod
    def make_client(**kwargs):
        defaults = {
            "first_name": "Test",
            "last_name": "Client",
            "phone_number": "+998901234567",
            "is_active": True,
        }
        defaults.update(kwargs)
        return Client.objects.create(**defaults)

    @staticmethod
    def make_partner(**kwargs):
        defaults = {
            "first_name": "Test",
            "last_name": "Partner",
            "username": "testpartner",
            "phone_number": "+998909876543",
            "is_active": True,
        }
        defaults.update(kwargs)
        return Partner.objects.create(**defaults)

    @staticmethod
    def make_location(**kwargs):
        defaults = {
            "latitude": Decimal("41.311"),
            "longitude": Decimal("69.279"),
            "city": "Tashkent",
            "country": "Uzbekistan",
        }
        defaults.update(kwargs)
        return PropertyLocation.objects.create(**defaults)

    @classmethod
    def make_property_type(cls, **kwargs):
        from django.core.files.base import ContentFile
        defaults = {"title_en": "Apartment", "title_ru": "Квартира", "title_uz": "Kvartira"}
        defaults.update(kwargs)
        # PropertyType has icon FileField; create with simple content to avoid missing file
        pt = PropertyType(
            title_en=defaults["title_en"],
            title_ru=defaults["title_ru"],
            title_uz=defaults["title_uz"],
        )
        pt.icon.save("icon.svg", ContentFile(b"<svg/>"), save=False)
        pt.save()
        return pt

    @classmethod
    def make_property(cls, partner=None, location=None, verified=True, **kwargs):
        partner = partner or cls.make_partner()
        location = location or cls.make_location()
        pt = cls.make_property_type()
        defaults = {
            "title": "Test Property",
            "partner": partner,
            "property_type": pt,
            "property_location": location,
            "currency": "UZS",
        }
        if verified:
            defaults["verification_status"] = "accepted"
        defaults.update(kwargs)
        prop = Property.objects.create(**defaults)
        PropertyDetail.objects.create(
            property=prop,
            description_en="Desc",
            description_ru="Описание",
            description_uz="Tavsif",
            check_in=time(14, 0),
            check_out=time(12, 0),
        )
        PropertyRoom.objects.create(property=prop, guests=4, rooms=2, beds=2, bathrooms=1)
        return prop

    @classmethod
    def make_property_price(cls, property_obj, month_start_date, price_work=Decimal("500000"), price_weekend=Decimal("600000"), price_per_person=Decimal("50000")):
        from dateutil.relativedelta import relativedelta
        from shared.date import month_end
        month_end_date = month_end(month_start_date)
        return PropertyPrice.objects.create(
            property=property_obj,
            month_from=month_start_date,
            month_to=month_end_date,
            price_on_working_days=price_work,
            price_on_weekends=price_weekend,
            price_per_person=price_per_person,
        )

    @classmethod
    def make_booking(cls, client=None, property_obj=None, status=Booking.BookingStatus.PENDING, **kwargs):
        client = client or cls.make_client()
        property_obj = property_obj or cls.make_property()
        today = date.today()
        defaults = {
            "client": client,
            "property": property_obj,
            "check_in": today + timedelta(days=2),
            "check_out": today + timedelta(days=5),
            "adults": 2,
            "children": 0,
            "babies": 0,
            "status": status,
        }
        defaults.update(kwargs)
        booking = Booking.objects.create(**defaults)
        return booking

    @classmethod
    def make_booking_with_price(cls, **kwargs):
        booking = cls.make_booking(**kwargs)
        BookingPrice.objects.create(
            booking=booking,
            subtotal=Decimal("1000000"),
            hold_amount=Decimal("200000"),
            charge_amount=Decimal("100000"),
            service_fee=Decimal("200000"),
            service_fee_percentage=20,
        )
        return booking


# ──────────────────────────────────────────────
# 1. Model tests
# ──────────────────────────────────────────────


class BookingModelTests(BookingTestMixin, TestCase):
    def test_booking_auto_generates_booking_number(self):
        booking = self.make_booking()
        self.assertEqual(len(booking.booking_number), 7)
        self.assertTrue(booking.booking_number.isdigit())

    def test_booking_default_status_is_pending(self):
        booking = self.make_booking()
        self.assertEqual(booking.status, Booking.BookingStatus.PENDING)

    def test_booking_str(self):
        booking = self.make_booking()
        self.assertIn(str(booking.check_in), str(booking))
        self.assertIn(str(booking.check_out), str(booking))

    def test_check_out_must_be_after_check_in_constraint(self):
        from django.db import IntegrityError
        client = self.make_client()
        prop = self.make_property()
        with self.assertRaises(IntegrityError):
            Booking.objects.create(
                client=client,
                property=prop,
                check_in=date.today() + timedelta(days=5),
                check_out=date.today() + timedelta(days=2),
            )


class BookingPriceModelTests(BookingTestMixin, TestCase):
    def test_booking_price_one_to_one_booking(self):
        booking = self.make_booking()
        price = BookingPrice.objects.create(
            booking=booking,
            subtotal=Decimal("500000"),
            hold_amount=Decimal("100000"),
            charge_amount=Decimal("50000"),
            service_fee=Decimal("100000"),
            service_fee_percentage=20,
        )
        self.assertEqual(booking.booking_price, price)


class CalendarDateModelTests(BookingTestMixin, TestCase):
    def test_unique_property_date_constraint(self):
        prop = self.make_property()
        d = date.today()
        CalendarDate.objects.create(
            property=prop,
            date=d,
            status=CalendarDate.CalendarStatus.AVAILABLE,
        )
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            CalendarDate.objects.create(
                property=prop,
                date=d,
                status=CalendarDate.CalendarStatus.BLOCKED,
            )

    def test_calendar_date_clean_duplicate_raises(self):
        prop = self.make_property()
        d = date.today()
        CalendarDate.objects.create(property=prop, date=d, status=CalendarDate.CalendarStatus.AVAILABLE)
        duplicate = CalendarDate(property=prop, date=d, status=CalendarDate.CalendarStatus.BLOCKED)
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            duplicate.clean()


# ──────────────────────────────────────────────
# 2. Helper tests
# ──────────────────────────────────────────────


class HelpersTests(BookingTestMixin, TestCase):
    def test_client_can_cancel_pending_booking_within_window(self):
        booking = self.make_booking_with_price(status=Booking.BookingStatus.PENDING)
        self.assertTrue(client_can_cancel(booking))

    def test_client_can_cancel_confirmed_booking_within_window(self):
        booking = self.make_booking_with_price(status=Booking.BookingStatus.CONFIRMED)
        self.assertTrue(client_can_cancel(booking))

    def test_client_cannot_cancel_completed_booking(self):
        booking = self.make_booking_with_price(status=Booking.BookingStatus.COMPLETED)
        self.assertFalse(client_can_cancel(booking))

    def test_client_cannot_cancel_cancelled_booking(self):
        booking = self.make_booking_with_price(status=Booking.BookingStatus.CANCELLED)
        self.assertFalse(client_can_cancel(booking))

    def test_get_cancellation_error_message_returns_string(self):
        booking = self.make_booking_with_price()
        msg = get_cancellation_error_message(booking)
        self.assertIsInstance(msg, str)
        self.assertTrue(len(msg) > 0)


# ──────────────────────────────────────────────
# 3. Serializer tests
# ──────────────────────────────────────────────


class PropertyCalendarDateRangeSerializerTests(TestCase):
    def test_to_date_defaults_to_from_date(self):
        today = date.today()
        s = PropertyCalendarDateRangeSerializer(data={"from_date": today})
        self.assertTrue(s.is_valid())
        self.assertEqual(s.validated_data["to_date"], today)

    def test_invalid_past_date(self):
        past = date.today() - timedelta(days=1)
        s = PropertyCalendarDateRangeSerializer(data={"from_date": past})
        self.assertFalse(s.is_valid())


class ClientBookingCreateSerializerTests(BookingTestMixin, TestCase):
    def test_valid_data_with_property_context(self):
        prop = self.make_property()
        check_in = date.today() + timedelta(days=1)
        check_out = check_in + timedelta(days=2)
        s = ClientBookingCreateSerializer(
            data={
                "property_id": str(prop.guid),
                "card_id": "card_123",
                "check_in": check_in,
                "check_out": check_out,
                "adults": 2,
                "children": 0,
                "babies": 0,
            },
            context={"property": prop},
        )
        self.assertTrue(s.is_valid(), s.errors)

    def test_guests_over_15_invalid(self):
        prop = self.make_property()
        check_in = date.today() + timedelta(days=1)
        check_out = check_in + timedelta(days=2)
        s = ClientBookingCreateSerializer(
            data={
                "property_id": str(prop.guid),
                "card_id": "card_123",
                "check_in": check_in,
                "check_out": check_out,
                "adults": 10,
                "children": 10,
                "babies": 0,
            },
            context={"property": prop},
        )
        self.assertFalse(s.is_valid())
        self.assertIn("non_field_errors", s.errors)


class CalendarDateSerializerTests(BookingTestMixin, TestCase):
    def test_serializer_fields(self):
        prop = self.make_property()
        cal = CalendarDate.objects.create(
            property=prop,
            date=date.today(),
            status=CalendarDate.CalendarStatus.AVAILABLE,
        )
        data = CalendarDateSerializer(cal).data
        self.assertIn("date", data)
        self.assertIn("status", data)


# ──────────────────────────────────────────────
# 4. Filter tests
# ──────────────────────────────────────────────


class PropertyCalenderDateFilterTests(BookingTestMixin, TestCase):
    def test_from_date_to_date_required(self):
        prop = self.make_property()
        qs = CalendarDate.objects.filter(property=prop)
        f = PropertyCalenderDateFilter(data={}, queryset=qs)
        self.assertFalse(f.is_valid())
        self.assertIn("from_date", f.errors)

    def test_to_date_must_be_greater_than_from_date(self):
        today = date.today()
        prop = self.make_property()
        qs = CalendarDate.objects.filter(property=prop)
        f = PropertyCalenderDateFilter(
            data={"from_date": today, "to_date": today},
            queryset=qs,
        )
        f.is_valid()
        from rest_framework.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            list(f.qs)


# ──────────────────────────────────────────────
# 5. CalendarDateService tests
# ──────────────────────────────────────────────


class CalendarDateServiceTests(BookingTestMixin, TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_block_creates_blocked_dates(self):
        prop = self.make_property()
        from_date = date.today() + timedelta(days=1)
        to_date = from_date + timedelta(days=3)
        service = CalendarDateService(property=prop, from_date=from_date, to_date=to_date)
        days = service.block()
        self.assertEqual(len(days), 4)
        blocked = CalendarDate.objects.filter(
            property=prop,
            status=CalendarDate.CalendarStatus.BLOCKED,
        )
        self.assertEqual(blocked.count(), 4)

    def test_block_raises_if_dates_already_booked(self):
        prop = self.make_property()
        from_date = date.today() + timedelta(days=1)
        to_date = from_date + timedelta(days=2)
        CalendarDate.objects.create(
            property=prop,
            date=from_date,
            status=CalendarDate.CalendarStatus.BOOKED,
        )
        service = CalendarDateService(property=prop, from_date=from_date, to_date=to_date)
        from rest_framework.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            service.block()

    def test_unblock_removes_blocked_dates(self):
        prop = self.make_property()
        from_date = date.today() + timedelta(days=1)
        to_date = from_date + timedelta(days=2)
        for d in (from_date, from_date + timedelta(days=1), to_date):
            CalendarDate.objects.create(
                property=prop,
                date=d,
                status=CalendarDate.CalendarStatus.BLOCKED,
            )
        service = CalendarDateService(property=prop, from_date=from_date, to_date=to_date)
        days = service.unblock()
        self.assertEqual(len(days), 3)
        self.assertEqual(
            CalendarDate.objects.filter(property=prop, status=CalendarDate.CalendarStatus.BLOCKED).count(),
            0,
        )

    def test_unblock_raises_if_no_blocked_dates(self):
        prop = self.make_property()
        from_date = date.today() + timedelta(days=1)
        to_date = from_date + timedelta(days=2)
        service = CalendarDateService(property=prop, from_date=from_date, to_date=to_date)
        from rest_framework.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            service.unblock()

    def test_hold_sets_cache(self):
        prop = self.make_property()
        from_date = date.today() + timedelta(days=1)
        to_date = from_date + timedelta(days=1)
        service = CalendarDateService(property=prop, from_date=from_date, to_date=to_date)
        days = service.hold()
        self.assertEqual(len(days), 2)
        key = f"calendar:hold:{prop.guid}:{from_date.isoformat()}"
        self.assertTrue(cache.get(key))

    def test_unhold_removes_cache(self):
        prop = self.make_property()
        from_date = date.today() + timedelta(days=1)
        to_date = from_date + timedelta(days=1)
        service = CalendarDateService(property=prop, from_date=from_date, to_date=to_date)
        service.hold()
        days = service.unhold()
        self.assertEqual(len(days), 2)
        key = f"calendar:hold:{prop.guid}:{from_date.isoformat()}"
        self.assertIsNone(cache.get(key))


# ──────────────────────────────────────────────
# 6. BookingPriceService tests
# ──────────────────────────────────────────────


class BookingPriceServiceTests(BookingTestMixin, TestCase):
    def test_calculate_returns_expected_keys(self):
        prop = self.make_property()
        from shared.date import month_start
        today = date.today()
        month_start_date = month_start(today)
        self.make_property_price(prop, month_start_date)
        # Keep test dates inside the same month to avoid month-boundary flakiness.
        check_in = month_start_date + timedelta(days=10)
        check_out = check_in + timedelta(days=2)
        service = BookingPriceService()
        result = service.calculate(
            adults=2,
            children=0,
            check_in=check_in,
            check_out=check_out,
            property=prop,
        )
        self.assertIn("subtotal", result)
        self.assertIn("hold_amount", result)
        self.assertIn("charge_amount", result)
        self.assertIn("service_fee", result)
        self.assertIn("nights", result)
        self.assertIn("guests", result)


# ──────────────────────────────────────────────
# 7. BookingService tests (mocked Plum & notifications)
# ──────────────────────────────────────────────


class BookingServiceTests(BookingTestMixin, TestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    @patch("booking.services.NotificationService.send_to_partner")
    @patch("booking.services.NotificationService.send_to_client")
    @patch("booking.services.send_partner_telegram_msg")
    @patch("booking.tasks.auto_cancel_booking")
    def test_partner_accept_changes_status_to_confirmed(
        self, mock_auto_cancel, mock_telegram, mock_notif_client, mock_notif_partner
    ):
        from .services import BookingService
        from payment.models import (
            PlumTransaction,
            PlumTransactionStatus,
            PlumTransactionType,
        )

        client = self.make_client()
        prop = self.make_property()
        booking = self.make_booking_with_price(
            client=client,
            property_obj=prop,
            status=Booking.BookingStatus.PENDING,
        )
        PlumTransaction.objects.create(
            transaction_id="txn1",
            hold_id="hold1",
            amount=Decimal("200000"),
            card_id="card1",
            extra_id="extra1",
            type=PlumTransactionType.HOLD,
            status=PlumTransactionStatus.PENDING,
        )
        BookingTransaction.objects.create(
            booking=booking,
            plum_transaction=PlumTransaction.objects.first(),
        )

        service = BookingService(client=client, property=prop)
        updated = service.partner_accept(booking, notify_partner=False)
        self.assertEqual(updated.status, Booking.BookingStatus.CONFIRMED)
        self.assertIsNotNone(updated.confirmed_at)
        mock_notif_partner.assert_not_called()

    def test_partner_accept_raises_if_not_pending(self):
        from .services import BookingService
        from rest_framework.exceptions import ValidationError
        booking = self.make_booking_with_price(status=Booking.BookingStatus.CONFIRMED)
        service = BookingService(client=booking.client, property=booking.property)
        with self.assertRaises(ValidationError):
            service.partner_accept(booking)

    @patch("booking.services.NotificationService.send_to_partner")
    def test_cancel_booking_releases_calendar_and_sets_cancelled(
        self, mock_notif_partner
    ):
        from .services import BookingService
        client = self.make_client()
        prop = self.make_property()
        check_in = date.today() + timedelta(days=3)
        check_out = check_in + timedelta(days=2)
        booking = self.make_booking_with_price(
            client=client,
            property_obj=prop,
            check_in=check_in,
            check_out=check_out,
        )
        CalendarDate.objects.create(
            property=prop,
            date=check_in,
            status=CalendarDate.CalendarStatus.BOOKED,
        )
        CalendarDate.objects.create(
            property=prop,
            date=check_in + timedelta(days=1),
            status=CalendarDate.CalendarStatus.BOOKED,
        )
        service = BookingService(client=client, property=prop)
        with patch.object(service.plum_service, "dismiss_hold"):
            cancelled = service.cancel_booking(booking)
        self.assertEqual(cancelled.status, Booking.BookingStatus.CANCELLED)
        self.assertEqual(cancelled.cancellation_reason, Booking.BookingCancellationReason.USER_CANCELLED)
        mock_notif_partner.assert_called_once()


# ──────────────────────────────────────────────
# 8. API / View tests
# ──────────────────────────────────────────────


class CalendarEndpointTests(BookingTestMixin, TestCase):
    def setUp(self):
        self.api = APIClient()
        self.prop = self.make_property()

    def test_calendar_list_requires_from_date_to_date(self):
        resp = self.api.get(
            f"/api/booking/properties/{self.prop.guid}/calendar/",
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_calendar_list_returns_200_with_valid_range(self):
        today = date.today()
        from_date = today
        to_date = today + timedelta(days=5)
        resp = self.api.get(
            f"/api/booking/properties/{self.prop.guid}/calendar/",
            {"from_date": from_date, "to_date": to_date},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("calendar", resp.data)
        self.assertIn("property_id", resp.data)


class ClientBookingEndpointTests(BookingTestMixin, TestCase):
    def setUp(self):
        self.api = APIClient()

    def test_client_booking_list_unauthenticated(self):
        resp = self.api.get("/api/booking/client/")
        self.assertIn(resp.status_code, (401, 403))

    def test_client_booking_create_unauthenticated(self):
        resp = self.api.post("/api/booking/client/", data={})
        self.assertIn(resp.status_code, (401, 403))

    def test_client_booking_history_unauthenticated(self):
        resp = self.api.get("/api/booking/client/history/")
        self.assertIn(resp.status_code, (401, 403))


class PartnerBookingEndpointTests(BookingTestMixin, TestCase):
    def setUp(self):
        self.api = APIClient()

    def test_partner_booking_list_unauthenticated(self):
        resp = self.api.get("/api/booking/partner/")
        self.assertIn(resp.status_code, (401, 403))

    def test_partner_accept_unauthenticated(self):
        import uuid
        resp = self.api.post(f"/api/booking/partner/{uuid.uuid4()}/accept/")
        self.assertIn(resp.status_code, (401, 403))


class PartnerCalendarEndpointTests(BookingTestMixin, TestCase):
    def setUp(self):
        self.api = APIClient()

    def test_calendar_block_unauthenticated(self):
        prop = self.make_property()
        resp = self.api.post(
            f"/api/booking/properties/{prop.guid}/calendar/block/",
            data={"from_date": date.today(), "to_date": date.today()},
        )
        self.assertIn(resp.status_code, (401, 403))
