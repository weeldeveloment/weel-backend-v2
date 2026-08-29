"""How the avia endpoints behave when Bookhara says no, and whose bookings
a caller can see.

Everything here runs against a mocked client and repository. What is worth
pinning is not the SQL — it is the three decisions these views make on their
own: which HTTP status a given provider failure deserves, that a booking is
only ever visible to the account that made it, and which cancellation call to
reach for, since picking the wrong one costs the customer a penalty that did
not have to be paid.
"""
from __future__ import annotations

import base64
import hashlib
from unittest.mock import patch
from uuid import uuid4

import pytest
from django.conf import settings

if not settings.configured:  # pragma: no cover - defensive, mirrors the suite
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from rest_framework.test import APIRequestFactory, force_authenticate

from apps.avia.client import (
    BookharaError,
    BookharaExpiredError,
    BookharaRejectedError,
    BookharaUnconfirmedError,
)
from apps.avia.models import AviaBookingStatus
from apps.avia.views import (
    AviaBookingCancelView,
    AviaBookingDetailView,
    AviaOfferDetailView,
    AviaStatusCallbackView,
)

factory = APIRequestFactory()

COMPANY_ID = 42
OTHER_COMPANY_ID = 43
CLIENT_ID = 7
GUID = str(uuid4())
PROVIDER_ID = "033ad8ce-11b8-4923-b37f-c98ew7d778d8"


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
        "provider_booking_id": PROVIDER_ID,
        "booking_number": "API-62718-CE",
        "status": AviaBookingStatus.BOOKED,
        "offer_id": "offer-1",
        "client_user_id": None,
        "b2b_company_id": COMPANY_ID,
        "amount": None,
        "currency": "UZS",
    }
    booking.update(overrides)
    return booking


# ---------------------------------------------------------------------------
# Provider failures
# ---------------------------------------------------------------------------

class TestProviderFailures:
    def _get_offer(self, error):
        request = factory.get("/offers/abc/")
        force_authenticate(request, user=_ClientUser())
        with patch("apps.avia.views.get_client") as get_client:
            get_client.return_value.get_offer.side_effect = error
            return AviaOfferDetailView.as_view()(request, offer_id="abc")

    def test_an_expired_offer_is_a_404_that_says_so(self):
        # Offers live hours, not days. The app has to search again, and the
        # response has to be distinguishable from "wrong id".
        response = self._get_offer(BookharaExpiredError("Not found", status_code=404))
        assert response.status_code == 404
        assert response.data["expired"] is True

    def test_an_unconfirmed_carrier_is_marked_retryable(self):
        # HTTP 410 from Bookhara means the GDS has not answered yet, and the
        # documented remedy is to repeat the same call.
        response = self._get_offer(
            BookharaUnconfirmedError("Carrier did not confirm", status_code=410)
        )
        assert response.status_code == 503
        assert response.data["retryable"] is True

    def test_bad_passenger_data_is_a_400_even_though_it_arrives_as_a_410(self):
        # Bookhara uses HTTP 410 for two opposite things. Error 1154 is the
        # permanent one — verified against the dev API, where a 15-character
        # surname comes back this way and never succeeds however often it is
        # sent. Answering 503 "retryable" would loop the caller forever on a
        # form only the traveller can fix.
        response = self._get_offer(
            BookharaRejectedError(
                "Passenger data is invalid.", status_code=410, error_code=1154
            )
        )
        assert response.status_code == 400
        assert response.data["retryable"] is False

    def test_an_unavailable_refund_points_at_the_manual_route(self):
        # 5233 (refund) and 5234 (VOID) both mean the fare allows it but the
        # airline will not do it through the API. `manual_refund` is the way
        # on, so the response has to say so rather than read as a dead end.
        for code in (5233, 5234):
            response = self._get_offer(
                BookharaError("Refund unavailable", error_code=code)
            )
            assert response.status_code == 409, code
            assert response.data["manual_refund_available"] is True

    def test_a_price_change_is_a_conflict_not_a_gateway_error(self):
        response = self._get_offer(BookharaError("Price changed", error_code=100500))
        assert response.status_code == 409
        assert response.data["price_changed"] is True

    def test_a_duplicate_booking_hands_back_the_existing_id(self):
        response = self._get_offer(
            BookharaError(
                "Duplicate booking",
                error_code=5231,
                data={"existing_booking_id": PROVIDER_ID},
            )
        )
        assert response.status_code == 409
        assert response.data["existing_booking_id"] == PROVIDER_ID

    def test_a_validation_error_is_passed_through_as_a_400(self):
        response = self._get_offer(
            BookharaError("Invalid data", error_code=8, errors={"adults": ["required"]})
        )
        assert response.status_code == 400
        assert response.data["errors"] == {"adults": ["required"]}

    def test_an_empty_deposit_does_not_leak_to_the_customer(self):
        # Error 1048 is our problem, not theirs — the message must not tell a
        # traveller that our account has run out of money.
        response = self._get_offer(BookharaError("Not enough deposit", error_code=1048))
        assert response.status_code == 503
        assert "deposit" not in response.data["detail"].lower()

    def test_being_throttled_asks_the_caller_to_come_back(self):
        # Error 1009 / HTTP 429. The dev API produced this after three refund
        # calls in quick succession; the same call succeeded a minute later,
        # so it is a wait, not a failure.
        response = self._get_offer(
            BookharaError("Request limit exceeded.", status_code=429, error_code=1009)
        )
        assert response.status_code == 503
        assert response.data["retryable"] is True

    def test_anything_else_is_a_gateway_error(self):
        response = self._get_offer(BookharaError("Boom", error_code=7))
        assert response.status_code == 502


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------

class TestOwnership:
    def _detail(self, user, booking):
        request = factory.get(f"/bookings/{GUID}/")
        force_authenticate(request, user=user)
        with (
            patch("apps.avia.views.repo.fetch_booking_by_guid", return_value=booking),
            patch("apps.avia.views.repo.fetch_passengers", return_value=[]),
        ):
            return AviaBookingDetailView.as_view()(request, guid=GUID)

    def test_a_company_sees_its_own_booking(self):
        response = self._detail(_B2BUser(), _booking())
        assert response.status_code == 200
        assert response.data["provider_booking_id"] == PROVIDER_ID

    def test_another_companys_booking_is_not_found(self):
        # Not 403: the existence of the booking is itself none of their business.
        response = self._detail(_B2BUser(company_id=OTHER_COMPANY_ID), _booking())
        assert response.status_code == 404

    def test_a_consumer_cannot_read_a_corporate_booking(self):
        response = self._detail(_ClientUser(), _booking())
        assert response.status_code == 404

    def test_a_consumer_sees_their_own_booking(self):
        booking = _booking(b2b_company_id=None, client_user_id=CLIENT_ID)
        response = self._detail(_ClientUser(), booking)
        assert response.status_code == 200

    def test_another_consumers_booking_is_not_found(self):
        booking = _booking(b2b_company_id=None, client_user_id=CLIENT_ID + 1)
        response = self._detail(_ClientUser(), booking)
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------

class TestCancellationPath:
    """`mode=auto` has to reach for the cheapest call that can work.

    Unpaid bookings cancel for free. A paid one should be voided — a full
    refund with no penalty — and only fall back to the penalised refund when
    the airline refuses, then to a manual request when that fails too.
    """

    def _cancel(self, booking, **service_mocks):
        request = factory.delete(f"/bookings/{GUID}/cancel/")
        force_authenticate(request, user=_B2BUser())
        patches = {
            "cancel_unpaid": patch("apps.avia.views.service.cancel_unpaid"),
            "void": patch("apps.avia.views.service.void"),
            "auto_cancel": patch("apps.avia.views.service.auto_cancel"),
            "manual_refund": patch("apps.avia.views.service.manual_refund"),
        }
        started = {name: p.start() for name, p in patches.items()}
        try:
            for name, mock in started.items():
                behaviour = service_mocks.get(name, "ok")
                if behaviour == "ok":
                    mock.return_value = _booking(status=AviaBookingStatus.CANCELLED)
                else:
                    mock.side_effect = behaviour
            with (
                patch("apps.avia.views.repo.fetch_booking_by_guid", return_value=booking),
                patch("apps.avia.views.repo.fetch_passengers", return_value=[]),
            ):
                response = AviaBookingCancelView.as_view()(request, guid=GUID)
            return response, started
        finally:
            for p in patches.values():
                p.stop()

    def test_an_unpaid_booking_is_simply_dropped(self):
        response, mocks = self._cancel(_booking(status=AviaBookingStatus.BOOKED))
        assert response.status_code == 200
        mocks["cancel_unpaid"].assert_called_once()
        mocks["void"].assert_not_called()

    def test_a_ticketed_booking_is_voided_first(self):
        response, mocks = self._cancel(_booking(status=AviaBookingStatus.TICKETED))
        assert response.status_code == 200
        mocks["void"].assert_called_once()
        mocks["auto_cancel"].assert_not_called()

    def test_a_refused_void_falls_through_to_the_penalised_refund(self):
        response, mocks = self._cancel(
            _booking(status=AviaBookingStatus.TICKETED),
            void=BookharaError("Void unavailable", error_code=13),
        )
        assert response.status_code == 200
        mocks["auto_cancel"].assert_called_once()
        mocks["manual_refund"].assert_not_called()

    def test_when_nothing_automatic_works_the_call_centre_is_asked(self):
        response, mocks = self._cancel(
            _booking(status=AviaBookingStatus.TICKETED),
            void=BookharaError("Void unavailable", error_code=13),
            auto_cancel=BookharaError("Refund unavailable", error_code=12),
        )
        assert response.status_code == 200
        mocks["manual_refund"].assert_called_once()

    def test_an_already_cancelled_booking_is_a_conflict(self):
        response, _ = self._cancel(_booking(status=AviaBookingStatus.CANCELLED))
        assert response.status_code == 409

    def test_an_unknown_mode_is_rejected_before_any_provider_call(self):
        request = factory.delete(f"/bookings/{GUID}/cancel/?mode=freebie")
        force_authenticate(request, user=_B2BUser())
        with patch("apps.avia.views.repo.fetch_booking_by_guid", return_value=_booking()):
            response = AviaBookingCancelView.as_view()(request, guid=GUID)
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Status callback
# ---------------------------------------------------------------------------

class TestStatusCallback:
    """The `X-Auth` header is the only authentication this endpoint has."""

    SECRET = "RZckZrpnEX42abwR-mnM"
    EMAIL = "info@bookhara.uz"

    def _token(self, booking_id=PROVIDER_ID, secret=None, email=None):
        raw = f"{email or self.EMAIL}{booking_id}{secret or self.SECRET}"
        digest = hashlib.sha256(raw.encode()).hexdigest()
        return base64.b64encode(digest.encode()).decode()

    def _post(self, token, **setting_overrides):
        overrides = {
            "BOOKHARA_CALLBACK_SECRET": self.SECRET,
            "BOOKHARA_EMAIL": self.EMAIL,
        }
        overrides.update(setting_overrides)
        request = factory.post(
            "/callback/status/",
            {"data": {"id": PROVIDER_ID, "status": AviaBookingStatus.TICKETED}},
            format="json",
            HTTP_X_AUTH=token,
        )
        with (
            patch.multiple(settings, **overrides),
            patch(
                "apps.avia.views.repo.fetch_booking_by_provider_id",
                return_value=_booking(),
            ),
            patch(
                "apps.avia.views.repo.upsert_booking",
                return_value=_booking(status=AviaBookingStatus.TICKETED),
            ),
            patch("apps.avia.views.repo.record_status_event") as recorded,
        ):
            response = AviaStatusCallbackView.as_view()(request)
        return response, recorded

    def test_a_correctly_signed_callback_is_recorded(self):
        response, recorded = self._post(self._token())
        assert response.status_code == 200
        assert recorded.call_count == 1
        assert recorded.call_args.kwargs["source"] == "callback"

    def test_a_forged_token_is_rejected(self):
        response, recorded = self._post(self._token(secret="guessed"))
        assert response.status_code == 401
        recorded.assert_not_called()

    def test_a_token_signed_for_another_booking_is_rejected(self):
        # The booking id is part of the signature precisely so a valid token
        # for one order cannot be replayed against another.
        response, recorded = self._post(self._token(booking_id="some-other-booking"))
        assert response.status_code == 401
        recorded.assert_not_called()

    def test_a_missing_token_is_rejected(self):
        response, recorded = self._post("")
        assert response.status_code == 401
        recorded.assert_not_called()

    def test_callbacks_are_refused_outright_when_no_secret_is_configured(self):
        # Failing shut matters here: with no secret there is nothing to check,
        # and accepting the body would let anyone rewrite a booking's status.
        response, recorded = self._post(self._token(), BOOKHARA_CALLBACK_SECRET="")
        assert response.status_code == 503
        recorded.assert_not_called()
