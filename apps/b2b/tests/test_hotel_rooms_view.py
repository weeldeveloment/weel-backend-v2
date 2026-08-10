from decimal import Decimal
from unittest.mock import patch

from rest_framework.test import APIRequestFactory

from apps.b2b.views import B2BHotelRoomsView


def _request():
    return APIRequestFactory().get(
        "/api/b2b/hotels/hotel-guid/rooms/",
        {"check_in": "2026-07-12", "check_out": "2026-07-13", "guests": 1},
    )


def _room(**overrides):
    row = {
        "id": 7,
        "room_number": "101",
        "floor": 1,
        "display_name": "Lux",
        "room_type_id": 3,
        "bedroom_count": 1,
        "price_per_night": Decimal("150000.00"),
        "currency": "UZS",
        "beds": [],
        "amenities": [],
        "capacity_adults": 2,
        "capacity_children": 0,
        "room_type_name": "lux",
        "preset": "lux",
        "area_sqm": 25.0,
        "meal_plan": "bb",
        "images": [],
    }
    row.update(overrides)
    return row


def _call(rooms):
    with (
        patch("apps.b2b.views.resolve_hotel_guid", return_value=("tenant_acme", 42)),
        patch("apps.b2b.views._run_in_schema", side_effect=lambda schema, query: query()),
        patch("apps.b2b.views.get_available_rooms", return_value=rooms),
        patch.object(B2BHotelRoomsView, "permission_classes", []),
    ):
        return B2BHotelRoomsView.as_view()(_request(), hotel_guid="hotel-guid")


def test_room_priced_in_the_hundreds_of_millions_is_returned():
    """A nightly rate above 99,999,999.99 UZS used to overflow the serializer's
    10-digit price field and take the whole room list down with it."""
    response = _call([_room(price_per_night=Decimal("112500000.00"))])

    assert response.status_code == 200
    assert response.data[0]["price_per_night"] == "112500000.00"


def test_a_broken_room_row_is_not_reported_as_a_missing_hotel():
    """The hotel was already resolved, so a bad row is a server-side failure,
    not a 404 that sends the frontend looking for a hotel that exists."""
    response = _call([_room(id="not-an-int", price_per_night=Decimal("nan"))])

    assert response.status_code == 500
    assert "Failed to load rooms" in response.data["detail"]
