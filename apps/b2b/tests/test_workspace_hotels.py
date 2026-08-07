"""The hotel list the mobile app renders.

The view projects a small card out of the platform's hotel row, and the row does
not use the names the card does: the hotel is called `name`, its pictures are
`photos`, and its description exists once per language. Reading `title`, `img`
and `description` straight off the row returned a list where every hotel was
nameless — which is what the app showed. These run against a mocked repository,
since the mapping is the thing under test, not the SQL.
"""
from unittest.mock import patch

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.workspace.authentication import WorkspaceUser
from apps.b2b.workspace.views import WorkspaceHotelListView

factory = APIRequestFactory()

USER = WorkspaceUser({
    "id": 1,
    "company_id": 55,
    "role": "employee",
    "full_name": "Test Person",
    "phone": "+998900000000",
})


def _row(**overrides):
    """A row shaped the way apps.hotels.repository really returns one."""
    row = {
        "id": 5,
        "guid": "fa43d173-d612-49f9-bb0a-ee61453622ee",
        "name": "Grand Tashkent Hotel",
        "city": "Tashkent",
        "address": "Chilonzor ko'chasi 58",
        "full_address": "Chilonzor ko'chasi 58, Tashkent",
        "description_uz": "Qulay joylashuv, Tashkent markazida.",
        "description_ru": "Удобное расположение в центре города.",
        "description_en": "Comfortable stay in the heart of Tashkent.",
        "photos": ["https://cdn.example/1.jpg", "https://cdn.example/2.jpg"],
        "star_rating": 5,
        "rating": 4.33,
        "review_count": 12,
        "booking_count": 0,
        "available_rooms": 7,
        "min_price": 1425000,
        "amenities": ["wifi", "pool"],
        "themes": ["luxury"],
        "legal_info": {},
        "is_recommended": True,
        "is_verified": True,
        "is_active": True,
        "is_archived": False,
        "latitude": 41.288442,
        "longitude": 69.205615,
    }
    row.update(overrides)
    return row


def _get(query: str = "", rows=None):
    request = factory.get(f"/api/b2b/workspace/hotels/{query}")
    force_authenticate(request, user=USER)
    with patch(
        "apps.b2b.workspace.views.search_hotels",
        return_value=rows if rows is not None else [_row()],
    ):
        return WorkspaceHotelListView.as_view()(request)


def test_every_hotel_comes_back_with_a_name():
    response = _get()
    results = response.data["results"]

    assert len(results) == 1
    assert results[0]["name"] == "Grand Tashkent Hotel"


def test_the_card_carries_its_pictures_and_a_description():
    hotel = _get().data["results"][0]

    assert len(hotel["images"]) == 2
    assert hotel["description"]


def test_the_rest_of_the_card_survives_the_projection():
    hotel = _get().data["results"][0]

    assert hotel["city"] == "Tashkent"
    assert hotel["stars"] == 5
    assert hotel["min_price"] == 1425000
    assert hotel["available_rooms"] == 7
    assert hotel["status"] == "active"
    assert hotel["is_recommended"] is True


@pytest.mark.parametrize("term", ["grand", "GRAND", "tashkent", "chilonzor"])
def test_search_matches_the_name_the_user_can_see(term):
    """Searching read the same absent key the list did, so it never matched."""
    assert len(_get(f"?search={term}").data["results"]) == 1


def test_search_still_rejects_what_does_not_match():
    assert _get("?search=samarqand").data["results"] == []


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({}, "active"),
        ({"is_verified": False}, "pending"),
        ({"is_archived": True}, "paused"),
        ({"is_active": False}, "paused"),
    ],
)
def test_status_collapses_the_platform_flags(overrides, expected):
    rows = [_row(**overrides)]
    assert _get(rows=rows).data["results"][0]["status"] == expected
