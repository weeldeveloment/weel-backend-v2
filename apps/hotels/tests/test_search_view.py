"""What a live search sends to Hotelios, and what it gives back to the apps.

Two things are worth pinning here, and both cost real money to get wrong.

The first is that a search always carries a nationality or a residence.
Hotelios prices a stay against them and answers a search carrying neither
with `success: true` and an empty hotel list — which is indistinguishable
from "sold out". That is exactly what the apps saw for months: a catalogue
that synced perfectly and never once produced a price.

The second is the shape handed back. The provider sends a flat `options`
list where a room type repeats once per rate plan, and prices the whole stay
rather than a night. Both are reshaped here, and a screen quoting a total as
if it were a nightly rate is the kind of bug nobody catches by looking.
"""
from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

from django.conf import settings

if not settings.configured:  # pragma: no cover - defensive, mirrors the suite
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from rest_framework.test import APIRequestFactory, force_authenticate

from apps.hotels.views import HotelSearchView, _search_entry

factory = APIRequestFactory()

CHECK_IN = date.today() + timedelta(days=20)
CHECK_OUT = CHECK_IN + timedelta(days=2)


class _B2BUser:
    is_authenticated = True
    company_id = 42
    id = 1


def _body(**overrides):
    return {
        "hotel_ids": [130],
        "check_in": CHECK_IN.isoformat(),
        "check_out": CHECK_OUT.isoformat(),
        "occupancies": [{"adults": 2, "children_ages": []}],
        "currency": "uzs",
        **overrides,
    }


def _option(room_type_id=199, rate_plan_id=2622, price=1176470.58, meal="BB"):
    return {
        "option_ref_id": f"130|{room_type_id}|{rate_plan_id}|x",
        "room_type_id": room_type_id,
        "room_type_name": "Standard Room (DBL/TWN)",
        "rate_plan_id": rate_plan_id,
        "occupancy": {"adults": 2},
        "cancellation_policy": {"cancellation_type": "rf"},
        "meal_plan": meal,
        "currency": "uzs",
        "price": price,
        "rooms_count": 2,
    }


def _post(body):
    request = factory.post("/api/hotels/search/", body, format="json")
    force_authenticate(request, user=_B2BUser())
    return request


# --- the empty-result trap -------------------------------------------------

@patch("apps.hotels.views.repo.fetch_room_types_for", return_value=[])
@patch("apps.hotels.views.repo.fetch_hotels", return_value=([], 0))
@patch("apps.hotels.service.get_client")
def test_a_search_with_neither_country_still_sends_one(client, _hotels, _rooms):
    client.return_value.search.return_value = []

    HotelSearchView.as_view()(_post(_body()))

    sent = client.return_value.search.call_args.args[0]
    assert sent["residence"] == settings.HOTELIOS_DEFAULT_RESIDENCE


@patch("apps.hotels.views.repo.fetch_room_types_for", return_value=[])
@patch("apps.hotels.views.repo.fetch_hotels", return_value=([], 0))
@patch("apps.hotels.service.get_client")
def test_a_caller_that_names_a_nationality_keeps_it(client, _hotels, _rooms):
    client.return_value.search.return_value = []

    HotelSearchView.as_view()(_post(_body(nationality="ru")))

    sent = client.return_value.search.call_args.args[0]
    assert sent["nationality"] == "ru"
    # Not overridden with the default: the caller said what it meant.
    assert "residence" not in sent


# --- the shape handed back -------------------------------------------------

def test_rate_plans_are_grouped_under_their_room_type():
    entry = _search_entry(
        {
            "hotel_id": 130,
            "options": [
                _option(rate_plan_id=2622, price=1176470.58),
                _option(rate_plan_id=3158, price=1082352.94, meal="RO"),
                _option(room_type_id=200, rate_plan_id=2622, price=1647058.82),
            ],
        },
        hotels_by_id={},
        rooms_by_key={},
        nights=2,
    )

    assert [room["room_type_id"] for room in entry["rooms"]] == [199, 200]
    # Cheapest plan first within a room, and cheapest room first overall.
    assert [o["rate_plan_id"] for o in entry["rooms"][0]["options"]] == [3158, 2622]
    assert entry["rooms"][0]["min_price"] == 1082352.94


def test_a_stay_total_is_also_reported_per_night():
    entry = _search_entry(
        {"hotel_id": 130, "options": [_option(price=1176470.58)]},
        hotels_by_id={},
        rooms_by_key={},
        nights=2,
    )

    assert entry["min_price"] == 1176470.58
    assert entry["min_price_per_night"] == 588235.29
    assert entry["rooms"][0]["options"][0]["price_per_night"] == 588235.29


def test_a_hotel_with_no_options_reports_no_price_rather_than_zero():
    entry = _search_entry(
        {"hotel_id": 130, "options": []},
        hotels_by_id={},
        rooms_by_key={},
        nights=2,
    )

    assert entry["rooms"] == []
    assert entry["min_price"] is None
    assert entry["min_price_per_night"] is None


def test_the_synced_room_type_is_joined_in_when_we_have_one():
    synced = {"room_type_id": 199, "hotel_id": 130, "photos": [{"link": "a.jpg"}]}

    entry = _search_entry(
        {"hotel_id": 130, "options": [_option()]},
        hotels_by_id={},
        rooms_by_key={(130, 199): synced},
        nights=2,
    )

    assert entry["rooms"][0]["room_type"] == synced


def test_a_room_type_the_sync_has_not_seen_still_comes_through():
    entry = _search_entry(
        {"hotel_id": 130, "options": [_option()]},
        hotels_by_id={},
        rooms_by_key={},
        nights=2,
    )

    assert entry["rooms"][0]["room_type"] is None
    assert entry["rooms"][0]["name"] == "Standard Room (DBL/TWN)"
