from unittest.mock import patch

import pytest
from django.core.cache import cache

from apps.hotels.serializers import HotelDetailSerializer
from apps.property.hotel_serializers import HotelCardSerializer

WIFI_GUID = "9c193c41-b0b0-491a-92c1-8e55f7dda511"
PARKING_GUID = "235a12b2-2e26-4c3a-9720-4cf6a75ae9be"

SERVICES = [
    {"guid": WIFI_GUID, "title": "Wi-Fi", "icon_url": "services/wifi.svg"},
    {"guid": PARKING_GUID, "title": "Parking", "icon_url": "https://cdn.example/p.svg"},
]


@pytest.fixture
def service_titles():
    cache.clear()
    with patch(
        "apps.property.apartment_repository.list_property_services",
        return_value=SERVICES,
    ):
        yield
    cache.clear()


def _hotel_row():
    return {
        "id": 1,
        "guid": "hotel-guid",
        "name": "Dahbed City Hostel",
        # Third value is already a title: older rows store those verbatim.
        "amenities": [WIFI_GUID, PARKING_GUID, "Konditsioner"],
        "photos": [],
        "room_types": [{"id": 5, "amenities": [WIFI_GUID]}],
    }


def test_hotel_card_returns_amenity_titles(service_titles):
    data = HotelCardSerializer(_hotel_row(), context={}).data

    assert data["amenities"] == ["Wi-Fi", "Parking", "Konditsioner"]
    assert data["amenity_ids"] == [WIFI_GUID, PARKING_GUID, "Konditsioner"]


def test_hotel_card_property_detail_keeps_raw_guids(service_titles):
    """The admin edit form reads `property_detail.amenities` and PATCHes it
    back, so that list must stay the raw GUIDs it wrote."""
    data = HotelCardSerializer(_hotel_row(), context={}).data

    assert data["property_detail"]["amenities"] == [
        WIFI_GUID,
        PARKING_GUID,
        "Konditsioner",
    ]
    assert data["property_detail"]["amenity_titles"] == [
        "Wi-Fi",
        "Parking",
        "Konditsioner",
    ]


def test_hotel_detail_resolves_hotel_and_room_amenities(service_titles):
    data = HotelDetailSerializer(_hotel_row(), context={}).data

    assert data["amenities"] == ["Wi-Fi", "Parking", "Konditsioner"]
    assert data["room_types"][0]["amenities"] == ["Wi-Fi"]


def test_hotel_card_exposes_services_with_icons(service_titles):
    """Clients draw an amenity's icon from `services`; a bare title list gives
    them nothing to render but a generic placeholder."""
    data = HotelCardSerializer(_hotel_row(), context={}).data

    assert data["services"] == [
        {"guid": WIFI_GUID, "title": "Wi-Fi", "icon_url": "/media/services/wifi.svg"},
        {"guid": PARKING_GUID, "title": "Parking", "icon_url": "https://cdn.example/p.svg"},
        # No guid to look an icon up by: the row stores a title, not a service.
        {"guid": "", "title": "Konditsioner", "icon_url": ""},
    ]
    assert data["property_services"] == data["services"]


def test_hotel_detail_room_services_keep_their_guids(service_titles):
    """Detail builds room summaries and the card serializer builds them again;
    the second pass must not re-read `amenities` after it holds titles."""
    data = HotelDetailSerializer(_hotel_row(), context={}).data

    assert data["room_types"][0]["services"] == [
        {"guid": WIFI_GUID, "title": "Wi-Fi", "icon_url": "/media/services/wifi.svg"},
    ]


def test_unknown_guid_passes_through(service_titles):
    row = _hotel_row()
    row["amenities"] = ["11111111-1111-1111-1111-111111111111"]

    data = HotelCardSerializer(row, context={}).data

    assert data["amenities"] == ["11111111-1111-1111-1111-111111111111"]
