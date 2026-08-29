"""Which Bookhara failures the client says are worth retrying.

Bookhara returns HTTP 410 for two unrelated situations: a carrier that has not
answered yet, and passenger data it will never accept. The status alone cannot
tell them apart — only the `error_code` inside the body can — and getting it
wrong is expensive in both directions. Treating a permanent rejection as
retryable loops the caller on a form nobody is going to fix; treating a slow
carrier as permanent throws away a booking that would have gone through on the
second attempt.

Verified against the dev API: a passenger surname of 15 characters answers
HTTP 410 with error_code 1154, repeatably.
"""
from __future__ import annotations

import json

import pytest
from django.conf import settings

if not settings.configured:  # pragma: no cover - defensive, mirrors the suite
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from apps.avia.client import (
    BookharaClient,
    BookharaError,
    BookharaExpiredError,
    BookharaRejectedError,
    BookharaUnconfirmedError,
)


class _Response:
    """Just enough of `requests.Response` for `_error_from`."""

    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload

    @property
    def text(self):  # pragma: no cover - debugging aid
        return json.dumps(self._payload)


def _classify(status_code: int, error_code: int | None) -> BookharaError:
    payload = {
        "request_id": "abc",
        "message": "Something happened.",
        "error_code": error_code,
    }
    return BookharaClient._error_from(_Response(status_code, payload), payload)


class TestGoneIsNotAlwaysRetryable:
    @pytest.mark.parametrize(
        "error_code",
        [
            1154,  # passenger data is invalid — the one seen on the dev API
            1124,  # first/last name must be 1-25 characters
            1127,  # passenger is on the airline blacklist
            1155,  # Cyrillic not allowed for this document type
            5237,  # a third order for these passengers, refused for 24 hours
        ],
    )
    def test_a_permanent_passenger_error_is_a_rejection(self, error_code):
        error = _classify(410, error_code)
        assert isinstance(error, BookharaRejectedError)
        assert not isinstance(error, BookharaUnconfirmedError)

    @pytest.mark.parametrize(
        "error_code",
        [
            3,     # request is being processed — a real "come back shortly"
            1002,  # service temporarily unavailable
            1039,  # carrier cannot confirm right now
            None,  # no code at all: the plain unconfirmed case
        ],
    )
    def test_everything_else_still_asks_for_a_retry(self, error_code):
        error = _classify(410, error_code)
        assert isinstance(error, BookharaUnconfirmedError)
        assert not isinstance(error, BookharaRejectedError)


class TestOtherStatuses:
    def test_a_404_is_still_an_expiry(self):
        assert isinstance(_classify(404, None), BookharaExpiredError)

    def test_a_passenger_code_outside_a_410_is_not_reclassified(self):
        # The permanent codes only override the 410 meaning. A 422 carrying
        # one is already handled as a plain error and must stay that way.
        error = _classify(422, 1154)
        assert type(error) is BookharaError


class TestRefundAvailability:
    @pytest.mark.parametrize("error_code", [5233, 5234])
    def test_both_refund_refusals_are_recognised(self, error_code):
        assert BookharaError("nope", error_code=error_code).is_refund_unavailable

    def test_an_unrelated_code_is_not(self):
        assert not BookharaError("nope", error_code=8).is_refund_unavailable


class TestRefundRequestIsOurState:
    """`refund_request_sent` exists only on our side, so its boundaries matter.

    Bookhara reports it once, in the reply to `manual-refund`, and every later
    read of the booking answers `ticketed` again — confirmed on the dev API,
    where a booking still read as `ticketed` minutes after the request was
    accepted. `upsert_booking(preserve_refund_request=True)` is what keeps a
    poll from erasing it; these pin down which statuses are allowed to.

    The behaviour itself cannot be exercised here: the avia tables are raw
    PostgreSQL (BIGSERIAL, ON CONFLICT, array comparison) and this suite runs
    on in-memory SQLite. It was verified against the live dev API instead.
    """

    def test_a_settled_refund_replaces_the_request(self):
        from apps.avia.models import AviaBookingStatus

        for status in ("refunded", "partiallyrefunded", "refundauthorized", "cancelled"):
            assert status in AviaBookingStatus.REFUND_SETTLED

    def test_still_being_ticketed_does_not(self):
        from apps.avia.models import AviaBookingStatus

        # These are exactly what a read returns while the call centre works.
        for status in ("ticketed", "paid", "partiallyticketed"):
            assert status not in AviaBookingStatus.REFUND_SETTLED

    def test_the_request_state_is_not_its_own_replacement(self):
        from apps.avia.models import AviaBookingStatus

        assert AviaBookingStatus.REFUND_REQUEST_SENT not in AviaBookingStatus.REFUND_SETTLED
