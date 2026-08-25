"""What a flight search will and will not send to Bookhara.

Bookhara answers a bad search with a numeric error code and a Russian message.
Rejecting the impossible ones here means the app gets a field-level 400 naming
the actual problem, and the provider is not asked a question it cannot answer.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from django.conf import settings

if not settings.configured:  # pragma: no cover - defensive, mirrors the suite
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from apps.avia.raw_serializers import OfferSearchSerializer

TOMORROW = date.today() + timedelta(days=1)
NEXT_WEEK = date.today() + timedelta(days=8)


def _payload(**overrides) -> dict:
    payload = {
        "directions": [
            {"departure_airport": "TAS", "arrival_airport": "IST", "date": TOMORROW.isoformat()}
        ],
        "service_class": "E",
        "adults": 1,
        "children": 0,
        "infants": 0,
        "infants_with_seat": 0,
    }
    payload.update(overrides)
    return payload


class TestAccepted:
    def test_a_one_way_search_passes(self):
        serializer = OfferSearchSerializer(data=_payload())
        assert serializer.is_valid(), serializer.errors

    def test_a_return_trip_passes(self):
        serializer = OfferSearchSerializer(data=_payload(directions=[
            {"departure_airport": "TAS", "arrival_airport": "IST", "date": TOMORROW.isoformat()},
            {"departure_airport": "IST", "arrival_airport": "TAS", "date": NEXT_WEEK.isoformat()},
        ]))
        assert serializer.is_valid(), serializer.errors

    def test_airports_are_upper_cased_for_the_provider(self):
        serializer = OfferSearchSerializer(data=_payload(directions=[
            {"departure_airport": "tas", "arrival_airport": "ist", "date": TOMORROW.isoformat()}
        ]))
        assert serializer.is_valid(), serializer.errors
        params = serializer.to_provider_params()
        assert params["directions"][0] == {
            "departure_airport": "TAS",
            "arrival_airport": "IST",
            "date": TOMORROW.isoformat(),
        }


class TestRejected:
    def test_the_same_airport_twice_is_not_a_route(self):
        serializer = OfferSearchSerializer(data=_payload(directions=[
            {"departure_airport": "TAS", "arrival_airport": "TAS", "date": TOMORROW.isoformat()}
        ]))
        assert not serializer.is_valid()

    def test_more_lap_infants_than_adults_is_refused(self):
        # Every lap infant has to sit on somebody.
        serializer = OfferSearchSerializer(data=_payload(adults=1, infants=2))
        assert not serializer.is_valid()

    def test_return_legs_must_run_forwards_in_time(self):
        serializer = OfferSearchSerializer(data=_payload(directions=[
            {"departure_airport": "TAS", "arrival_airport": "IST", "date": NEXT_WEEK.isoformat()},
            {"departure_airport": "IST", "arrival_airport": "TAS", "date": TOMORROW.isoformat()},
        ]))
        assert not serializer.is_valid()

    def test_more_than_nine_seated_passengers_is_refused(self):
        serializer = OfferSearchSerializer(data=_payload(adults=9, children=1))
        assert not serializer.is_valid()

    @pytest.mark.parametrize("adults", [0, -1])
    def test_a_flight_needs_at_least_one_adult(self, adults):
        serializer = OfferSearchSerializer(data=_payload(adults=adults))
        assert not serializer.is_valid()
