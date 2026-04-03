from datetime import date, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.db import IntegrityError
from django.test import TestCase, RequestFactory, override_settings
from django.utils import timezone

from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from payment.choices import Currency
from users.models.clients import Client
from users.models.partners import Partner

from .filters import SanatoriumFilter, SanatoriumRoomFilter
from .models import (
    MedicalSpecialization,
    Treatment,
    RoomType,
    PackageType,
    RoomAmenity,
    Sanatorium,
    SanatoriumLocation,
    SanatoriumTreatment,
    SanatoriumImage,
    SanatoriumRoom,
    SanatoriumRoomPrice,
    RoomCalendarDate,
    SanatoriumReview,
    SanatoriumFavorite,
    SanatoriumBooking,
    SanatoriumBookingPrice,
    VerificationStatus,
)
from .serializers import (
    MedicalSpecializationSerializer,
    PackageTypeSerializer,
    RoomTypeSerializer,
    SanatoriumListSerializer,
    SanatoriumDetailSerializer,
    SanatoriumRoomListSerializer,
    SanatoriumReviewCreateSerializer,
    SanatoriumBookingCreateSerializer,
    RoomCalendarDateRangeSerializer,
)
from .services import (
    calculate_booking_price,
    check_room_availability,
    mark_room_dates_booked,
    release_room_dates,
    create_sanatorium_booking,
)


# ──────────────────────────────────────────────
# Test helpers
# ──────────────────────────────────────────────


class SanatoriumTestMixin:
    """Shared factory helpers for ..sanatorium tests."""

    @staticmethod
    def make_partner(**kwargs):
        defaults = {
            "first_name": "Test",
            "last_name": "Partner",
            "username": "testpartner",
            "phone_number": "+998901234567",
            "is_active": True,
        }
        defaults.update(kwargs)
        return Partner.objects.create(**defaults)

    @staticmethod
    def make_client(**kwargs):
        defaults = {
            "first_name": "Test",
            "last_name": "Client",
            "phone_number": "+998901111111",
            "is_active": True,
        }
        defaults.update(kwargs)
        return Client.objects.create(**defaults)

    @staticmethod
    def make_location(**kwargs):
        defaults = {
            "latitude": Decimal("41.31100000000000"),
            "longitude": Decimal("69.27900000000000"),
            "city": "Tashkent",
            "country": "Uzbekistan",
        }
        defaults.update(kwargs)
        return SanatoriumLocation.objects.create(**defaults)

    @classmethod
    def make_sanatorium(cls, partner=None, location=None, verified=True, **kwargs):
        partner = partner or cls.make_partner()
        location = location or cls.make_location()
        defaults = {
            "title": "Test Sanatorium",
            "description_en": "English description",
            "description_ru": "Описание на русском",
            "description_uz": "O'zbekcha tavsif",
            "partner": partner,
            "location": location,
            "check_in_time": time(14, 0),
            "check_out_time": time(12, 0),
        }
        if verified:
            defaults["verification_status"] = VerificationStatus.ACCEPTED
        defaults.update(kwargs)
        return Sanatorium.objects.create(**defaults)

    @staticmethod
    def make_room_type(**kwargs):
        defaults = {
            "title_en": "Standard",
            "title_ru": "Стандарт",
            "title_uz": "Standart",
        }
        defaults.update(kwargs)
        return RoomType.objects.create(**defaults)

    @staticmethod
    def make_package_type(duration_days=7, **kwargs):
        defaults = {
            "title_en": f"{duration_days}-day Package",
            "title_ru": f"{duration_days}-дневный пакет",
            "title_uz": f"{duration_days} kunlik paket",
            "duration_days": duration_days,
        }
        defaults.update(kwargs)
        return PackageType.objects.create(**defaults)

    @classmethod
    def make_room(cls, sanatorium=None, room_type=None, **kwargs):
        sanatorium = sanatorium or cls.make_sanatorium()
        room_type = room_type or cls.make_room_type()
        defaults = {
            "..sanatorium": sanatorium,
            "room_type": room_type,
            "title": "Room 101",
            "capacity": 2,
            "bed_count": 1,
        }
        defaults.update(kwargs)
        return SanatoriumRoom.objects.create(**defaults)

    @classmethod
    def make_room_price(cls, room=None, package_type=None, price=Decimal("1000000.00"), **kwargs):
        room = room or cls.make_room()
        package_type = package_type or cls.make_package_type()
        defaults = {
            "room": room,
            "package_type": package_type,
            "price": price,
            "currency": Currency.UZS,
        }
        defaults.update(kwargs)
        return SanatoriumRoomPrice.objects.create(**defaults)

    @staticmethod
    def make_specialization(**kwargs):
        defaults = {
            "title_en": "Cardiology",
            "title_ru": "Кардиология",
            "title_uz": "Kardiologiya",
        }
        defaults.update(kwargs)
        return MedicalSpecialization.objects.create(**defaults)

    @staticmethod
    def make_treatment(**kwargs):
        defaults = {
            "title_en": "Mud therapy",
            "title_ru": "Грязелечение",
            "title_uz": "Loy bilan davolash",
        }
        defaults.update(kwargs)
        return Treatment.objects.create(**defaults)

    @staticmethod
    def make_amenity(**kwargs):
        defaults = {
            "title_en": "WiFi",
            "title_ru": "WiFi",
            "title_uz": "WiFi",
        }
        defaults.update(kwargs)
        return RoomAmenity.objects.create(**defaults)


# ──────────────────────────────────────────────
# 1. Model tests
# ──────────────────────────────────────────────


class LookupModelTests(SanatoriumTestMixin, TestCase):
    """Lookup/reference table creation and __str__."""

    def test_specialization_str(self):
        spec = self.make_specialization()
        self.assertEqual(str(spec), "Cardiology")

    def test_treatment_str(self):
        treat = self.make_treatment()
        self.assertEqual(str(treat), "Mud therapy")

    def test_room_type_str(self):
        rt = self.make_room_type()
        self.assertEqual(str(rt), "Standard")

    def test_package_type_str(self):
        pt = self.make_package_type(duration_days=14)
        self.assertIn("14 days", str(pt))

    def test_amenity_str(self):
        am = self.make_amenity()
        self.assertEqual(str(am), "WiFi")

    def test_package_type_ordering(self):
        p14 = self.make_package_type(duration_days=14, title_en="14-day")
        p7 = self.make_package_type(duration_days=7, title_en="7-day")
        packages = list(PackageType.objects.all())
        self.assertEqual(packages[0], p7)
        self.assertEqual(packages[1], p14)


class SanatoriumModelTests(SanatoriumTestMixin, TestCase):
    """Sanatorium creation, constraints, and signals."""

    def test_create_sanatorium(self):
        san = self.make_sanatorium()
        self.assertIsNotNone(san.guid)
        self.assertEqual(san.title, "Test Sanatorium")

    def test_verified_status_sets_is_verified(self):
        san = self.make_sanatorium(verified=True)
        self.assertTrue(san.is_verified)

    def test_waiting_status_unsets_is_verified(self):
        san = self.make_sanatorium(verified=False)
        self.assertFalse(san.is_verified)

    def test_sanatorium_str(self):
        san = self.make_sanatorium()
        self.assertEqual(str(san), "Test Sanatorium")

    def test_location_str(self):
        loc = self.make_location()
        self.assertIn("Tashkent", str(loc))

    def test_unique_active_title_constraint(self):
        partner = self.make_partner()
        self.make_sanatorium(
            partner=partner,
            title="Unique San",
        )
        with self.assertRaises(IntegrityError):
            self.make_sanatorium(
                partner=partner,
                title="Unique San",
                location=self.make_location(city="Samarkand"),
            )

    def test_m2m_specializations(self):
        san = self.make_sanatorium()
        spec1 = self.make_specialization(title_en="Neurology")
        spec2 = self.make_specialization(title_en="Pulmonology")
        san.specializations.set([spec1, spec2])
        self.assertEqual(san.specializations.count(), 2)

    def test_m2m_treatments(self):
        san = self.make_sanatorium()
        t1 = self.make_treatment(title_en="Hydrotherapy")
        t2 = self.make_treatment(title_en="Physiotherapy")
        SanatoriumTreatment.objects.create(sanatorium=san, treatment=t1)
        SanatoriumTreatment.objects.create(sanatorium=san, treatment=t2)
        self.assertEqual(san.treatments.count(), 2)


class SanatoriumImageSignalTests(SanatoriumTestMixin, TestCase):
    """Test that pending images are approved when ..sanatorium becomes verified."""

    @staticmethod
    def _create_test_image(sanatorium, **kwargs):
        from django.core.files.uploadedfile import SimpleUploadedFile
        import io
        from PIL import Image as PILImage

        buf = io.BytesIO()
        PILImage.new("RGB", (100, 100), color="red").save(buf, format="JPEG")
        buf.seek(0)
        fake_file = SimpleUploadedFile("test.jpg", buf.read(), content_type="image/jpeg")
        defaults = {"..sanatorium": sanatorium, "image": fake_file, "order": 1, "is_pending": True}
        defaults.update(kwargs)
        return SanatoriumImage.objects.create(**defaults)

    def test_images_approved_on_verification(self):
        partner = self.make_partner()
        san = self.make_sanatorium(partner=partner, verified=False)
        img = self._create_test_image(san)
        self.assertTrue(img.is_pending)

        san.verification_status = VerificationStatus.ACCEPTED
        san.save()
        img.refresh_from_db()
        self.assertFalse(img.is_pending)

    def test_images_stay_pending_if_not_verified(self):
        san = self.make_sanatorium(verified=False)
        img = self._create_test_image(san)
        san.title = "Updated Title"
        san.save()
        img.refresh_from_db()
        self.assertTrue(img.is_pending)


class ReviewSignalTests(SanatoriumTestMixin, TestCase):
    """Test comment_count signals on review create/delete."""

    def test_comment_count_increments_on_review_create(self):
        client = self.make_client()
        san = self.make_sanatorium()
        self.assertEqual(san.comment_count, 0)

        SanatoriumReview.objects.create(
            client=client, sanatorium=san, rating=Decimal("4.5"), comment="Great"
        )
        san.refresh_from_db()
        self.assertEqual(san.comment_count, 1)

    def test_comment_count_decrements_on_review_delete(self):
        client = self.make_client()
        san = self.make_sanatorium()
        review = SanatoriumReview.objects.create(
            client=client, sanatorium=san, rating=Decimal("4.0")
        )
        san.refresh_from_db()
        self.assertEqual(san.comment_count, 1)

        review.delete()
        san.refresh_from_db()
        self.assertEqual(san.comment_count, 0)


class RoomModelTests(SanatoriumTestMixin, TestCase):
    """Room and related model tests."""

    def test_room_str(self):
        san = self.make_sanatorium()
        room = self.make_room(sanatorium=san, title="Deluxe 201")
        self.assertIn("Deluxe 201", str(room))
        self.assertIn(san.title, str(room))

    def test_room_price_unique_constraint(self):
        room = self.make_room()
        pt = self.make_package_type()
        SanatoriumRoomPrice.objects.create(
            room=room, package_type=pt, price=Decimal("500000"), currency=Currency.UZS
        )
        with self.assertRaises(IntegrityError):
            SanatoriumRoomPrice.objects.create(
                room=room, package_type=pt, price=Decimal("600000"), currency=Currency.UZS
            )

    def test_room_calendar_unique_constraint(self):
        room = self.make_room()
        today = date.today()
        RoomCalendarDate.objects.create(
            room=room, date=today, status=RoomCalendarDate.CalendarStatus.AVAILABLE
        )
        with self.assertRaises(IntegrityError):
            RoomCalendarDate.objects.create(
                room=room, date=today, status=RoomCalendarDate.CalendarStatus.BOOKED
            )

    def test_room_amenities_m2m(self):
        room = self.make_room()
        a1 = self.make_amenity(title_en="Pool")
        a2 = self.make_amenity(title_en="Gym")
        room.amenities.set([a1, a2])
        self.assertEqual(room.amenities.count(), 2)


class BookingModelTests(SanatoriumTestMixin, TestCase):
    """Booking model creation and auto-generated booking_number."""

    def test_booking_auto_generates_number(self):
        client = self.make_client()
        san = self.make_sanatorium()
        room = self.make_room(sanatorium=san)
        pt = self.make_package_type()
        booking = SanatoriumBooking.objects.create(
            client=client,
            sanatorium=san,
            room=room,
            package_type=pt,
            check_in=date.today() + timedelta(days=1),
            check_out=date.today() + timedelta(days=8),
        )
        self.assertEqual(len(booking.booking_number), 7)
        self.assertTrue(booking.booking_number.isdigit())

    def test_booking_str(self):
        client = self.make_client()
        san = self.make_sanatorium()
        room = self.make_room(sanatorium=san)
        pt = self.make_package_type()
        booking = SanatoriumBooking.objects.create(
            client=client,
            sanatorium=san,
            room=room,
            package_type=pt,
            check_in=date(2026, 3, 1),
            check_out=date(2026, 3, 8),
        )
        self.assertIn(booking.booking_number, str(booking))

    def test_booking_default_status_is_pending(self):
        client = self.make_client()
        san = self.make_sanatorium()
        room = self.make_room(sanatorium=san)
        pt = self.make_package_type()
        booking = SanatoriumBooking.objects.create(
            client=client,
            sanatorium=san,
            room=room,
            package_type=pt,
            check_in=date.today() + timedelta(days=1),
            check_out=date.today() + timedelta(days=8),
        )
        self.assertEqual(booking.status, SanatoriumBooking.BookingStatus.PENDING)


class FavoriteModelTests(SanatoriumTestMixin, TestCase):
    """Favorite unique constraint test."""

    def test_unique_favorite_constraint(self):
        client = self.make_client()
        san = self.make_sanatorium()
        SanatoriumFavorite.objects.create(client=client, sanatorium=san)
        with self.assertRaises(IntegrityError):
            SanatoriumFavorite.objects.create(client=client, sanatorium=san)


# ──────────────────────────────────────────────
# 2. Service layer tests
# ──────────────────────────────────────────────


class CalculateBookingPriceTests(SanatoriumTestMixin, TestCase):
    """Test pricing logic: 20% hold, 10% service fee."""

    def test_basic_price_calculation(self):
        room = self.make_room()
        pt = self.make_package_type()
        self.make_room_price(room=room, package_type=pt, price=Decimal("1000000.00"))

        result = calculate_booking_price(room, pt)

        self.assertEqual(result["subtotal"], Decimal("1000000.00"))
        self.assertEqual(result["hold_amount"], Decimal("200000.00"))
        self.assertEqual(result["charge_amount"], Decimal("800000.00"))
        self.assertEqual(result["service_fee"], Decimal("100000.00"))
        self.assertEqual(result["service_fee_percentage"], 20)
        self.assertEqual(result["currency"], Currency.UZS)

    def test_price_rounding(self):
        room = self.make_room()
        pt = self.make_package_type()
        self.make_room_price(room=room, package_type=pt, price=Decimal("333333.33"))

        result = calculate_booking_price(room, pt)

        self.assertEqual(result["hold_amount"], Decimal("66666.67"))
        self.assertEqual(result["service_fee"], Decimal("33333.33"))

    def test_raises_when_no_price(self):
        room = self.make_room()
        pt = self.make_package_type()

        with self.assertRaises(ValidationError):
            calculate_booking_price(room, pt)


class CheckRoomAvailabilityTests(SanatoriumTestMixin, TestCase):
    """Test room calendar availability checks."""

    def test_available_when_no_dates(self):
        room = self.make_room()
        pt = self.make_package_type(duration_days=7)
        check_in = date.today() + timedelta(days=1)
        self.assertTrue(check_room_availability(room, check_in, pt))

    def test_unavailable_when_booked(self):
        room = self.make_room()
        pt = self.make_package_type(duration_days=7)
        check_in = date.today() + timedelta(days=1)

        RoomCalendarDate.objects.create(
            room=room,
            date=check_in + timedelta(days=3),
            status=RoomCalendarDate.CalendarStatus.BOOKED,
        )
        self.assertFalse(check_room_availability(room, check_in, pt))

    def test_unavailable_when_blocked(self):
        room = self.make_room()
        pt = self.make_package_type(duration_days=7)
        check_in = date.today() + timedelta(days=1)

        RoomCalendarDate.objects.create(
            room=room,
            date=check_in,
            status=RoomCalendarDate.CalendarStatus.BLOCKED,
        )
        self.assertFalse(check_room_availability(room, check_in, pt))

    def test_available_when_dates_outside_range(self):
        room = self.make_room()
        pt = self.make_package_type(duration_days=3)
        check_in = date.today() + timedelta(days=1)

        RoomCalendarDate.objects.create(
            room=room,
            date=check_in + timedelta(days=10),
            status=RoomCalendarDate.CalendarStatus.BOOKED,
        )
        self.assertTrue(check_room_availability(room, check_in, pt))


class MarkRoomDatesTests(SanatoriumTestMixin, TestCase):
    """Test marking and releasing room calendar dates."""

    def test_mark_dates_booked(self):
        room = self.make_room()
        check_in = date(2026, 4, 1)
        check_out = date(2026, 4, 5)

        mark_room_dates_booked(room, check_in, check_out)

        dates = RoomCalendarDate.objects.filter(room=room).order_by("date")
        self.assertEqual(dates.count(), 4)
        for d in dates:
            self.assertEqual(d.status, RoomCalendarDate.CalendarStatus.BOOKED)

    def test_mark_dates_updates_existing_available(self):
        room = self.make_room()
        d = date(2026, 4, 1)
        RoomCalendarDate.objects.create(
            room=room, date=d, status=RoomCalendarDate.CalendarStatus.AVAILABLE
        )

        mark_room_dates_booked(room, d, d + timedelta(days=1))

        entry = RoomCalendarDate.objects.get(room=room, date=d)
        self.assertEqual(entry.status, RoomCalendarDate.CalendarStatus.BOOKED)

    def test_release_dates(self):
        room = self.make_room()
        check_in = date(2026, 4, 1)
        check_out = date(2026, 4, 5)

        mark_room_dates_booked(room, check_in, check_out)
        release_room_dates(room, check_in, check_out)

        booked = RoomCalendarDate.objects.filter(
            room=room, status=RoomCalendarDate.CalendarStatus.BOOKED
        ).count()
        self.assertEqual(booked, 0)

    def test_release_does_not_affect_blocked(self):
        room = self.make_room()
        d = date(2026, 4, 1)
        RoomCalendarDate.objects.create(
            room=room, date=d, status=RoomCalendarDate.CalendarStatus.BLOCKED
        )

        release_room_dates(room, d, d + timedelta(days=1))

        entry = RoomCalendarDate.objects.get(room=room, date=d)
        self.assertEqual(entry.status, RoomCalendarDate.CalendarStatus.BLOCKED)


class CreateSanatoriumBookingTests(SanatoriumTestMixin, TestCase):
    """Integration test for the full booking creation flow."""

    def setUp(self):
        self.partner = self.make_partner()
        self.client_user = self.make_client()
        self.sanatorium = self.make_sanatorium(partner=self.partner)
        self.room_type = self.make_room_type()
        self.package_type = self.make_package_type(duration_days=7)
        self.room = self.make_room(
            sanatorium=self.sanatorium, room_type=self.room_type
        )
        self.make_room_price(
            room=self.room,
            package_type=self.package_type,
            price=Decimal("2000000.00"),
        )

    def test_successful_booking_creation(self):
        check_in = date.today() + timedelta(days=2)
        booking = create_sanatorium_booking(
            client=self.client_user,
            sanatorium=self.sanatorium,
            room=self.room,
            package_type=self.package_type,
            check_in=check_in,
        )

        self.assertIsNotNone(booking.guid)
        self.assertEqual(booking.status, SanatoriumBooking.BookingStatus.PENDING)
        self.assertEqual(booking.check_out, check_in + timedelta(days=7))
        self.assertTrue(
            hasattr(booking, "booking_price") and booking.booking_price is not None
        )
        self.assertEqual(booking.booking_price.subtotal, Decimal("2000000.00"))

        booked_dates = RoomCalendarDate.objects.filter(
            room=self.room, status=RoomCalendarDate.CalendarStatus.BOOKED
        ).count()
        self.assertEqual(booked_dates, 7)

    def test_double_booking_raises_error(self):
        check_in = date.today() + timedelta(days=2)
        create_sanatorium_booking(
            client=self.client_user,
            sanatorium=self.sanatorium,
            room=self.room,
            package_type=self.package_type,
            check_in=check_in,
        )

        with self.assertRaises(ValidationError):
            create_sanatorium_booking(
                client=self.client_user,
                sanatorium=self.sanatorium,
                room=self.room,
                package_type=self.package_type,
                check_in=check_in,
            )

    def test_past_date_raises_error(self):
        check_in = date.today() - timedelta(days=1)
        with self.assertRaises(ValidationError):
            create_sanatorium_booking(
                client=self.client_user,
                sanatorium=self.sanatorium,
                room=self.room,
                package_type=self.package_type,
                check_in=check_in,
            )

    def test_too_far_advance_raises_error(self):
        check_in = date.today() + timedelta(days=60)
        with self.assertRaises(ValidationError):
            create_sanatorium_booking(
                client=self.client_user,
                sanatorium=self.sanatorium,
                room=self.room,
                package_type=self.package_type,
                check_in=check_in,
            )


# ──────────────────────────────────────────────
# 3. Serializer tests
# ──────────────────────────────────────────────


class LookupSerializerTests(SanatoriumTestMixin, TestCase):
    """Test lookup serializers produce correct fields."""

    def test_specialization_serializer(self):
        spec = self.make_specialization()
        data = MedicalSpecializationSerializer(spec).data
        self.assertIn("guid", data)
        self.assertIn("title", data)

    def test_package_type_serializer(self):
        pt = self.make_package_type(duration_days=14)
        data = PackageTypeSerializer(pt).data
        self.assertEqual(data["duration_days"], 14)
        self.assertIn("title", data)

    def test_room_type_serializer(self):
        rt = self.make_room_type()
        data = RoomTypeSerializer(rt).data
        self.assertIn("guid", data)
        self.assertIn("title", data)


class SanatoriumSerializerTests(SanatoriumTestMixin, TestCase):
    """Test Sanatorium list and detail serializers."""

    def setUp(self):
        self.factory = RequestFactory()

    def test_list_serializer_fields(self):
        san = self.make_sanatorium()
        request = self.factory.get("/")
        request.user = None
        data = SanatoriumListSerializer(san, context={"request": request}).data
        self.assertEqual(data["guid"], str(san.guid))
        self.assertEqual(data["title"], san.title)
        self.assertIn("location", data)
        self.assertIn("average_rating", data)
        self.assertIn("min_price", data)
        self.assertIn("is_favorite", data)
        self.assertFalse(data["is_favorite"])

    def test_detail_serializer_fields(self):
        san = self.make_sanatorium()
        request = self.factory.get("/")
        request.user = None
        data = SanatoriumDetailSerializer(san, context={"request": request}).data
        self.assertIn("description", data)
        self.assertIn("check_in_time", data)
        self.assertIn("check_out_time", data)
        self.assertIn("treatments", data)

    def test_is_favorite_for_client(self):
        client = self.make_client()
        san = self.make_sanatorium()
        SanatoriumFavorite.objects.create(client=client, sanatorium=san)

        request = self.factory.get("/")
        request.user = client
        data = SanatoriumListSerializer(san, context={"request": request}).data
        self.assertTrue(data["is_favorite"])

    def test_average_rating(self):
        san = self.make_sanatorium()
        client = self.make_client()
        SanatoriumReview.objects.create(
            client=client, sanatorium=san, rating=Decimal("4.0")
        )
        client2 = self.make_client(phone_number="+998902222222")
        SanatoriumReview.objects.create(
            client=client2, sanatorium=san, rating=Decimal("5.0")
        )

        request = self.factory.get("/")
        request.user = None
        data = SanatoriumListSerializer(san, context={"request": request}).data
        self.assertEqual(data["average_rating"], Decimal("4.50"))

    def test_min_price(self):
        san = self.make_sanatorium()
        room = self.make_room(sanatorium=san)
        pt1 = self.make_package_type(duration_days=7, title_en="7-day")
        pt2 = self.make_package_type(duration_days=14, title_en="14-day")
        SanatoriumRoomPrice.objects.create(
            room=room, package_type=pt1, price=Decimal("500000")
        )
        SanatoriumRoomPrice.objects.create(
            room=room, package_type=pt2, price=Decimal("900000")
        )

        request = self.factory.get("/")
        request.user = None
        data = SanatoriumListSerializer(san, context={"request": request}).data
        self.assertEqual(data["min_price"], Decimal("500000"))


class RoomSerializerTests(SanatoriumTestMixin, TestCase):
    """Test room serializer output."""

    def test_room_list_serializer(self):
        room = self.make_room()
        data = SanatoriumRoomListSerializer(room).data
        self.assertIn("guid", data)
        self.assertIn("title", data)
        self.assertIn("room_type", data)
        self.assertIn("capacity", data)
        self.assertIn("amenities", data)
        self.assertIn("prices", data)


class CalendarRangeSerializerTests(TestCase):
    """Test RoomCalendarDateRangeSerializer validation."""

    def test_valid_range(self):
        s = RoomCalendarDateRangeSerializer(
            data={"from_date": "2026-04-01", "to_date": "2026-04-05"}
        )
        self.assertTrue(s.is_valid())

    def test_to_date_defaults_to_from_date(self):
        s = RoomCalendarDateRangeSerializer(data={"from_date": "2026-04-01"})
        self.assertTrue(s.is_valid())
        self.assertEqual(
            s.validated_data["to_date"], s.validated_data["from_date"]
        )

    def test_invalid_range_to_before_from(self):
        s = RoomCalendarDateRangeSerializer(
            data={"from_date": "2026-04-05", "to_date": "2026-04-01"}
        )
        self.assertFalse(s.is_valid())


class BookingCreateSerializerTests(TestCase):
    """Test SanatoriumBookingCreateSerializer field validation."""

    def test_valid_data(self):
        import uuid

        s = SanatoriumBookingCreateSerializer(
            data={
                "sanatorium_id": str(uuid.uuid4()),
                "room_id": str(uuid.uuid4()),
                "card_id": "card_123",
                "check_in": "2026-04-01",
                "package_type_id": str(uuid.uuid4()),
            }
        )
        self.assertTrue(s.is_valid(), s.errors)

    def test_missing_required_fields(self):
        s = SanatoriumBookingCreateSerializer(data={})
        self.assertFalse(s.is_valid())
        self.assertIn("sanatorium_id", s.errors)
        self.assertIn("room_id", s.errors)
        self.assertIn("check_in", s.errors)


# ──────────────────────────────────────────────
# 4. Filter tests
# ──────────────────────────────────────────────


class SanatoriumFilterTests(SanatoriumTestMixin, TestCase):
    """Test SanatoriumFilter queryset filtering."""

    def setUp(self):
        self.partner = self.make_partner()
        self.san1 = self.make_sanatorium(
            partner=self.partner, title="Tashkent Spa", verified=True
        )
        self.san2 = self.make_sanatorium(
            partner=self.partner,
            title="Samarkand Resort",
            verified=True,
            location=self.make_location(city="Samarkand"),
        )

    def test_filter_by_city(self):
        qs = Sanatorium.objects.filter(is_verified=True)
        f = SanatoriumFilter(data={"city": "Samarkand"}, queryset=qs)
        self.assertEqual(f.qs.count(), 1)
        self.assertEqual(f.qs.first(), self.san2)

    def test_filter_by_specialization(self):
        spec = self.make_specialization()
        self.san1.specializations.add(spec)

        qs = Sanatorium.objects.filter(is_verified=True)
        f = SanatoriumFilter(
            data={"specialization": str(spec.guid)}, queryset=qs
        )
        self.assertEqual(f.qs.count(), 1)
        self.assertEqual(f.qs.first(), self.san1)

    def test_filter_by_min_price(self):
        room1 = self.make_room(sanatorium=self.san1, title="R1")
        pt = self.make_package_type()
        SanatoriumRoomPrice.objects.create(
            room=room1, package_type=pt, price=Decimal("500000")
        )

        room2 = self.make_room(sanatorium=self.san2, title="R2")
        SanatoriumRoomPrice.objects.create(
            room=room2, package_type=pt, price=Decimal("100000")
        )

        qs = Sanatorium.objects.filter(is_verified=True)
        f = SanatoriumFilter(data={"min_price": "300000"}, queryset=qs)
        results = list(f.qs)
        self.assertIn(self.san1, results)

    def test_filter_by_max_price(self):
        room1 = self.make_room(sanatorium=self.san1, title="R1")
        pt = self.make_package_type()
        SanatoriumRoomPrice.objects.create(
            room=room1, package_type=pt, price=Decimal("500000")
        )

        room2 = self.make_room(sanatorium=self.san2, title="R2")
        SanatoriumRoomPrice.objects.create(
            room=room2, package_type=pt, price=Decimal("100000")
        )

        qs = Sanatorium.objects.filter(is_verified=True)
        f = SanatoriumFilter(data={"max_price": "200000"}, queryset=qs)
        results = list(f.qs)
        self.assertIn(self.san2, results)
        self.assertNotIn(self.san1, results)


class SanatoriumRoomFilterTests(SanatoriumTestMixin, TestCase):
    """Test SanatoriumRoomFilter."""

    def test_filter_by_room_type(self):
        san = self.make_sanatorium()
        rt1 = self.make_room_type(title_en="Standard")
        rt2 = self.make_room_type(title_en="Suite")
        r1 = self.make_room(sanatorium=san, room_type=rt1, title="R1")
        r2 = self.make_room(sanatorium=san, room_type=rt2, title="R2")

        qs = SanatoriumRoom.objects.filter(sanatorium=san)
        f = SanatoriumRoomFilter(
            data={"room_type": str(rt2.guid)}, queryset=qs
        )
        self.assertEqual(f.qs.count(), 1)
        self.assertEqual(f.qs.first(), r2)

    def test_filter_by_min_capacity(self):
        san = self.make_sanatorium()
        r1 = self.make_room(sanatorium=san, title="Small", capacity=1)
        r2 = self.make_room(sanatorium=san, title="Large", capacity=4)

        qs = SanatoriumRoom.objects.filter(sanatorium=san)
        f = SanatoriumRoomFilter(data={"min_capacity": "3"}, queryset=qs)
        self.assertEqual(f.qs.count(), 1)
        self.assertEqual(f.qs.first(), r2)

    def test_filter_by_package_type(self):
        san = self.make_sanatorium()
        room = self.make_room(sanatorium=san)
        pt = self.make_package_type(duration_days=7)
        SanatoriumRoomPrice.objects.create(
            room=room, package_type=pt, price=Decimal("500000")
        )

        qs = SanatoriumRoom.objects.filter(sanatorium=san)
        f = SanatoriumRoomFilter(
            data={"package_type": str(pt.guid)}, queryset=qs
        )
        self.assertEqual(f.qs.count(), 1)


# ──────────────────────────────────────────────
# 5. API / View tests
# ──────────────────────────────────────────────


class LookupEndpointTests(SanatoriumTestMixin, TestCase):
    """Test public lookup list endpoints return 200."""

    def setUp(self):
        self.api = APIClient()

    def test_specializations_list(self):
        self.make_specialization()
        resp = self.api.get("/api/..sanatorium/specializations/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_treatments_list(self):
        self.make_treatment()
        resp = self.api.get("/api/..sanatorium/treatments/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_room_types_list(self):
        self.make_room_type()
        resp = self.api.get("/api/..sanatorium/room-types/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_package_types_list(self):
        self.make_package_type()
        resp = self.api.get("/api/..sanatorium/package-types/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_amenities_list(self):
        self.make_amenity()
        resp = self.api.get("/api/..sanatorium/amenities/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)


class SanatoriumPublicEndpointTests(SanatoriumTestMixin, TestCase):
    """Test public ..sanatorium list and detail views."""

    def setUp(self):
        self.api = APIClient()
        self.partner = self.make_partner()
        self.san = self.make_sanatorium(partner=self.partner, verified=True)

    def test_sanatorium_list(self):
        resp = self.api.get("/api/..sanatorium/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_sanatorium_list_excludes_unverified(self):
        self.make_sanatorium(
            partner=self.partner,
            title="Unverified",
            verified=False,
            location=self.make_location(city="Bukhara"),
        )
        resp = self.api.get("/api/..sanatorium/")
        data = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        titles = [s["title"] for s in data]
        self.assertNotIn("Unverified", titles)

    def test_sanatorium_detail(self):
        resp = self.api.get(f"/api/..sanatorium/{self.san.guid}/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["guid"], str(self.san.guid))

    def test_sanatorium_detail_not_found(self):
        import uuid

        resp = self.api.get(f"/api/..sanatorium/{uuid.uuid4()}/")
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_search_by_title(self):
        resp = self.api.get("/api/..sanatorium/", {"search": "Test"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)


class RoomEndpointTests(SanatoriumTestMixin, TestCase):
    """Test room list and detail endpoints."""

    def setUp(self):
        self.api = APIClient()
        self.san = self.make_sanatorium()
        self.room = self.make_room(sanatorium=self.san)

    def test_room_list(self):
        resp = self.api.get(f"/api/..sanatorium/{self.san.guid}/rooms/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_room_detail(self):
        resp = self.api.get(
            f"/api/..sanatorium/{self.san.guid}/rooms/{self.room.guid}/"
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["guid"], str(self.room.guid))

    def test_room_detail_not_found(self):
        import uuid

        resp = self.api.get(
            f"/api/..sanatorium/{self.san.guid}/rooms/{uuid.uuid4()}/"
        )
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


class CalendarEndpointTests(SanatoriumTestMixin, TestCase):
    """Test room calendar list endpoint."""

    def setUp(self):
        self.api = APIClient()
        self.san = self.make_sanatorium()
        self.room = self.make_room(sanatorium=self.san)

    def test_calendar_list(self):
        RoomCalendarDate.objects.create(
            room=self.room,
            date=date.today(),
            status=RoomCalendarDate.CalendarStatus.AVAILABLE,
        )
        resp = self.api.get(
            f"/api/..sanatorium/{self.san.guid}/rooms/{self.room.guid}/calendar/"
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)


class ReviewEndpointTests(SanatoriumTestMixin, TestCase):
    """Test review list (public) endpoint."""

    def setUp(self):
        self.api = APIClient()
        self.san = self.make_sanatorium()

    def test_review_list_public(self):
        client = self.make_client()
        SanatoriumReview.objects.create(
            client=client, sanatorium=self.san, rating=Decimal("4.0"), comment="Nice"
        )
        resp = self.api.get(f"/api/..sanatorium/{self.san.guid}/reviews/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_hidden_reviews_excluded(self):
        client = self.make_client()
        SanatoriumReview.objects.create(
            client=client,
            sanatorium=self.san,
            rating=Decimal("1.0"),
            comment="Bad",
            is_hidden=True,
        )
        resp = self.api.get(f"/api/..sanatorium/{self.san.guid}/reviews/")
        results = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        self.assertEqual(len(results), 0)


class FavoriteEndpointTests(SanatoriumTestMixin, TestCase):
    """Test favorite toggle requires authentication."""

    def setUp(self):
        self.api = APIClient()
        self.san = self.make_sanatorium()

    def test_favorite_toggle_unauthenticated(self):
        resp = self.api.post(f"/api/..sanatorium/{self.san.guid}/favorite/")
        self.assertIn(resp.status_code, [401, 403])


class PartnerEndpointTests(SanatoriumTestMixin, TestCase):
    """Test partner CRUD endpoints require authentication."""

    def setUp(self):
        self.api = APIClient()

    def test_partner_list_unauthenticated(self):
        resp = self.api.get("/api/..sanatorium/partner/")
        self.assertIn(resp.status_code, [401, 403])

    def test_partner_create_unauthenticated(self):
        resp = self.api.post("/api/..sanatorium/partner/", data={})
        self.assertIn(resp.status_code, [401, 403])

    def test_partner_create_duplicate_title_returns_400(self):
        partner = self.make_partner(phone_number="+998901234568")
        self.make_sanatorium(partner=partner, title="Test Sanatoriya")

        self.api.force_authenticate(user=partner)
        payload = {
            "title": "Test Sanatoriya",
            "description_en": "En",
            "description_ru": "Ru",
            "description_uz": "Uz",
            "location": {
                "latitude": "41.31108100000000",
                "longitude": "69.24056200000000",
                "country": "Uzbekistan",
                "city": "Tashkent",
            },
            "specializations": [],
            "treatments": [],
            "check_in_time": "12:00:00",
            "check_out_time": "10:00:00",
        }

        resp = self.api.post("/api/..sanatorium/partner/", data=payload, format="json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("errors", resp.data)
        self.assertIn("already exists", str(resp.data["errors"][0]["detail"]).lower())
        self.assertEqual(resp.data["errors"][0]["field"], "title")


class ClientBookingEndpointTests(SanatoriumTestMixin, TestCase):
    """Test client booking endpoints require authentication."""

    def setUp(self):
        self.api = APIClient()

    def test_client_booking_list_unauthenticated(self):
        resp = self.api.get("/api/..sanatorium/booking/client/")
        self.assertIn(resp.status_code, [401, 403])

    def test_client_booking_create_unauthenticated(self):
        resp = self.api.post("/api/..sanatorium/booking/client/", data={})
        self.assertIn(resp.status_code, [401, 403])

    def test_client_booking_history_unauthenticated(self):
        resp = self.api.get("/api/..sanatorium/booking/client/history/")
        self.assertIn(resp.status_code, [401, 403])


class PartnerBookingEndpointTests(SanatoriumTestMixin, TestCase):
    """Test partner booking management endpoints require authentication."""

    def setUp(self):
        self.api = APIClient()

    def test_partner_booking_list_unauthenticated(self):
        resp = self.api.get("/api/..sanatorium/booking/partner/")
        self.assertIn(resp.status_code, [401, 403])
