"""What a booking will and will not send to Bookhara as passenger data.

Bookhara answers bad passenger data with HTTP 410 — the same status it uses
for "the carrier has not confirmed yet". Anything caught here is a field-level
400 naming the offending passenger instead of a provider round trip that comes
back looking like an outage.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.conf import settings

if not settings.configured:  # pragma: no cover - defensive, mirrors the suite
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from apps.avia.raw_serializers import CreateBookingSerializer

EXPIRY = (date.today() + timedelta(days=365 * 3)).isoformat()


def _passenger(**overrides) -> dict:
    passenger = {
        "first_name": "Anvar",
        "last_name": "Karimov",
        "age": "adt",
        "birthdate": "1990-05-15",
        "gender": "M",
        "citizenship": "UZ",
        "tel": "+998901234567",
        "doc_type": "P",
        "doc_number": "AB1234567",
        "doc_expire": EXPIRY,
    }
    passenger.update(overrides)
    return passenger


def _booking(**overrides) -> dict:
    payload = {
        "payer_name": "Anvar Karimov",
        "payer_email": "anvar@example.com",
        "payer_tel": "+998901234567",
        "passengers": [_passenger()],
    }
    payload.update(overrides)
    return payload


class TestAccepted:
    def test_a_plain_passenger_passes(self):
        serializer = CreateBookingSerializer(data=_booking())
        assert serializer.is_valid(), serializer.errors

    @pytest.mark.parametrize("name", ["O'ktam", "Abdulla-Aziz", "Van Der Berg"])
    def test_transliterated_names_are_allowed(self, name):
        # Apostrophes and hyphens are ordinary in Uzbek and Russian
        # transliterations, and the GDS takes them.
        serializer = CreateBookingSerializer(data=_booking(
            passengers=[_passenger(first_name=name)]
        ))
        assert serializer.is_valid(), serializer.errors

    def test_the_documented_order_note_is_allowed(self):
        serializer = CreateBookingSerializer(
            data=_booking(order_note="specialbuyercontacts")
        )
        assert serializer.is_valid(), serializer.errors


class TestRejected:
    def test_cyrillic_names_are_refused(self):
        # Bookhara error 1155: Cyrillic is not accepted for this document type.
        serializer = CreateBookingSerializer(data=_booking(
            passengers=[_passenger(last_name="Каримов")]
        ))
        assert not serializer.is_valid()
        assert "last_name" in serializer.errors["passengers"][0]

    def test_a_name_longer_than_the_documented_limit_is_refused(self):
        # Bookhara error 1124 caps each name part at 25 characters.
        serializer = CreateBookingSerializer(data=_booking(
            passengers=[_passenger(first_name="A" * 26)]
        ))
        assert not serializer.is_valid()
        assert "first_name" in serializer.errors["passengers"][0]

    def test_digits_in_a_name_are_refused(self):
        serializer = CreateBookingSerializer(data=_booking(
            passengers=[_passenger(first_name="Anvar2")]
        ))
        assert not serializer.is_valid()

    def test_an_undocumented_order_note_is_refused(self):
        # Bookhara documents exactly one value; anything else is dropped
        # upstream without a word, which is worse than a 400 here.
        serializer = CreateBookingSerializer(data=_booking(order_note="please hurry"))
        assert not serializer.is_valid()
        assert "order_note" in serializer.errors
