from unittest.mock import patch

from rest_framework.test import APIRequestFactory

from apps.hotels.views import HotelDetailView


def test_hotel_detail_accepts_property_route_keyword():
    request = APIRequestFactory().get("/api/property/hotels/hotel-guid/")

    with patch("apps.hotels.views._resolve_hotel", return_value=None) as resolve_hotel:
        response = HotelDetailView.as_view()(request, hotel_guid="hotel-guid")

    assert response.status_code == 400
    resolve_hotel.assert_called_once_with("hotel-guid")


def test_hotel_detail_still_accepts_hotels_route_keyword():
    request = APIRequestFactory().get("/api/hotels/hotel-guid/")

    with patch("apps.hotels.views._resolve_hotel", return_value=None) as resolve_hotel:
        response = HotelDetailView.as_view()(request, guid="hotel-guid")

    assert response.status_code == 400
    resolve_hotel.assert_called_once_with("hotel-guid")
