"""What the hotel endpoints refuse, and how a Hotelios failure reaches the app.

Mocked client and repository throughout. The things worth pinning are the ones
the views decide by themselves: which searches are worth sending, that a
booking belongs to exactly one account, that Confirm cannot happen twice, and
that a sold-out room or a moved price arrives as something the UI can act on
rather than a generic gateway error.
"""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch
from uuid import uuid4

from django.conf import settings

if not settings.configured:  # pragma: no cover - defensive, mirrors the suite
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from rest_framework.test import APIRequestFactory, force_authenticate

from apps.hotels.client import (
    ERROR_INSUFFICIENT_BALANCE,
    ERROR_NOT_FOUND,
    ERROR_NO_ROOMS,
    ERROR_PRICE_CHANGED,
    ERROR_TOO_MANY_REQUESTS,
    HoteliosError,
)
from apps.hotels.models import HotelBookingStatus
from apps.hotels.raw_serializers import CreateHotelBookingSerializer, HotelSearchSerializer
from apps.hotels.views import (
    HotelBookingConfirmView,
    HotelBookingDetailView,
    HotelQuoteView,
)

factory = APIRequestFactory()

COMPANY_ID = 42
CLIENT_ID = 7
GUID = str(uuid4())
CHECK_IN = date.today() + timedelta(days=10)
CHECK_OUT = CHECK_IN + timedelta(days=2)


class _B2BUser:
    is_authenticated = True

    def __init__(self, company_id=COMPANY_ID, user_id=1):
        self.company_id = company_id
        self.id = user_id


class _ClientUser:
    is_authenticated = True
    company_id = None

    def __init__(self, user_id=CLIENT_ID):
        self.id = user_id


def _booking(**overrides) -> dict:
    booking = {
        "id": 1,
        "guid": GUID,
        "external_id": "WEEL-ABC",
        "provider_booking_id": "24888",
        "hotel_id": 1969,
        "status": HotelBookingStatus.DRAFT,
        "client_user_id": None,
        "b2b_company_id": COMPANY_ID,
    }
    booking.update(overrides)
    return booking


# ---------------------------------------------------------------------------
# Search validation
# ---------------------------------------------------------------------------

class TestSearchValidation:
    def _payload(self, **overrides) -> dict:
        payload = {
            "city_id": 90,
            "check_in": CHECK_IN.isoformat(),
            "check_out": CHECK_OUT.isoformat(),
            "occupancies": [{"adults": 2, "children_ages": []}],
            "currency": "uzs",
        }
        payload.update(overrides)
        return payload

    def test_a_city_search_passes(self):
        serializer = HotelSearchSerializer(data=self._payload())
        assert serializer.is_valid(), serializer.errors

    def test_a_search_without_a_city_or_hotels_is_refused(self):
        # Hotelios requires one or the other; asking for neither would be an
        # unbounded query against every hotel they have.
        payload = self._payload()
        payload.pop("city_id")
        serializer = HotelSearchSerializer(data=payload)
        assert not serializer.is_valid()

    def test_hotel_ids_alone_are_enough(self):
        payload = self._payload(hotel_ids=[1969])
        payload.pop("city_id")
        serializer = HotelSearchSerializer(data=payload)
        assert serializer.is_valid(), serializer.errors

    def test_a_stay_must_last_at_least_one_night(self):
        serializer = HotelSearchSerializer(
            data=self._payload(check_out=CHECK_IN.isoformat())
        )
        assert not serializer.is_valid()

    def test_dates_are_sent_in_the_providers_own_format(self):
        serializer = HotelSearchSerializer(data=self._payload())
        assert serializer.is_valid(), serializer.errors
        check_in, check_out = serializer.provider_dates()
        assert check_in == CHECK_IN.strftime("%Y/%m/%d 14:00")
        assert check_out == CHECK_OUT.strftime("%Y/%m/%d 12:00")

    def test_only_the_filters_actually_set_are_forwarded(self):
        serializer = HotelSearchSerializer(data=self._payload(stars=[4, 5]))
        assert serializer.is_valid(), serializer.errors
        assert serializer.provider_filters() == {"stars": [4, 5]}


# ---------------------------------------------------------------------------
# Booking validation
# ---------------------------------------------------------------------------

class TestBookingValidation:
    def _payload(self, guests=None) -> dict:
        return {
            "quote_id": "931264bd-6d0c-4abb-9a4f-a6cfe5e8eb3e",
            "hotel_id": 1969,
            "check_in": CHECK_IN.isoformat(),
            "check_out": CHECK_OUT.isoformat(),
            "booking_rooms": [{
                "option_ref_id": "130|1020|7585|x",
                "price": "100500.00",
                "currency": "uzs",
                "guests": guests if guests is not None else [
                    {"person_title": "MR", "first_name": "Bruce",
                     "last_name": "Wayne", "nationality": "uz"},
                ],
            }],
        }

    def test_a_room_with_one_adult_passes(self):
        serializer = CreateHotelBookingSerializer(data=self._payload())
        assert serializer.is_valid(), serializer.errors

    def test_a_child_without_an_age_is_refused(self):
        # Child age changes the price, so Hotelios requires it.
        serializer = CreateHotelBookingSerializer(data=self._payload(guests=[
            {"person_title": "MR", "first_name": "Bruce", "last_name": "Wayne",
             "nationality": "uz"},
            {"person_title": "CHILD", "first_name": "Dick", "last_name": "Grayson",
             "nationality": "uz"},
        ]))
        assert not serializer.is_valid()

    def test_a_room_of_only_children_is_refused(self):
        serializer = CreateHotelBookingSerializer(data=self._payload(guests=[
            {"person_title": "CHILD", "first_name": "Dick", "last_name": "Grayson",
             "nationality": "uz", "age": 10},
        ]))
        assert not serializer.is_valid()

    def test_the_room_line_keeps_the_employee_it_was_booked_for(self):
        payload = self._payload()
        payload["booking_rooms"][0]["employee_id"] = 55
        serializer = CreateHotelBookingSerializer(data=payload)
        assert serializer.is_valid(), serializer.errors
        assert serializer.provider_rooms()[0]["b2b_employee_id"] == 55


# ---------------------------------------------------------------------------
# Provider failures
# ---------------------------------------------------------------------------

class TestProviderFailures:
    def _quote(self, error):
        request = factory.post(
            "/quote/", {"option_ref_ids": ["130|1020|7585|x"]}, format="json"
        )
        force_authenticate(request, user=_ClientUser())
        with patch("apps.hotels.views.service.quote", side_effect=error):
            return HotelQuoteView.as_view()(request)

    def test_a_missing_hotel_is_a_404(self):
        response = self._quote(HoteliosError("Not found", error_code=ERROR_NOT_FOUND))
        assert response.status_code == 404

    def test_a_moved_price_is_a_conflict_the_app_can_explain(self):
        response = self._quote(HoteliosError("Price changed", error_code=ERROR_PRICE_CHANGED))
        assert response.status_code == 409
        assert response.data["price_changed"] is True

    def test_a_sold_out_room_is_a_conflict_not_a_failure(self):
        response = self._quote(HoteliosError("No rooms", error_code=ERROR_NO_ROOMS))
        assert response.status_code == 409
        assert response.data["sold_out"] is True

    def test_our_exhausted_credit_does_not_leak_to_the_guest(self):
        response = self._quote(
            HoteliosError("Insufficient balance", error_code=ERROR_INSUFFICIENT_BALANCE)
        )
        assert response.status_code == 503
        assert "balance" not in response.data["detail"].lower()

    def test_rate_limiting_is_marked_retryable(self):
        response = self._quote(
            HoteliosError("Too many requests", error_code=ERROR_TOO_MANY_REQUESTS)
        )
        assert response.status_code == 503
        assert response.data["retryable"] is True


# ---------------------------------------------------------------------------
# Ownership and the confirm step
# ---------------------------------------------------------------------------

class TestOwnership:
    def _detail(self, user, booking):
        request = factory.get(f"/bookings/{GUID}/")
        force_authenticate(request, user=user)
        with (
            patch("apps.hotels.views.repo.fetch_booking_by_guid", return_value=booking),
            patch("apps.hotels.views.repo.fetch_booking_rooms", return_value=[]),
        ):
            return HotelBookingDetailView.as_view()(request, guid=GUID)

    def test_a_company_sees_its_own_booking(self):
        assert self._detail(_B2BUser(), _booking()).status_code == 200

    def test_another_companys_booking_is_not_found(self):
        assert self._detail(_B2BUser(company_id=99), _booking()).status_code == 404

    def test_a_consumer_cannot_read_a_corporate_booking(self):
        assert self._detail(_ClientUser(), _booking()).status_code == 404


class TestConfirm:
    """Hotelios errors on a second Confirm, so the second one never leaves here."""

    def _confirm(self, booking):
        request = factory.post(f"/bookings/{GUID}/confirm/")
        force_authenticate(request, user=_B2BUser())
        with (
            patch("apps.hotels.views.repo.fetch_booking_by_guid", return_value=booking),
            patch("apps.hotels.views.repo.fetch_booking_rooms", return_value=[]),
            patch(
                "apps.hotels.views.service.confirm_booking",
                return_value=_booking(status=HotelBookingStatus.CONFIRMED),
            ) as confirmed,
        ):
            return HotelBookingConfirmView.as_view()(request, guid=GUID), confirmed

    def test_a_draft_is_confirmed(self):
        response, confirmed = self._confirm(_booking())
        assert response.status_code == 200
        confirmed.assert_called_once()

    def test_confirming_twice_is_refused_without_calling_the_provider(self):
        response, confirmed = self._confirm(_booking(status=HotelBookingStatus.CONFIRMED))
        assert response.status_code == 409
        confirmed.assert_not_called()

    def test_a_cancelled_booking_cannot_be_confirmed(self):
        response, confirmed = self._confirm(_booking(status=HotelBookingStatus.CANCELLED))
        assert response.status_code == 409
        confirmed.assert_not_called()
