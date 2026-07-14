from unittest.mock import patch

from rest_framework.test import APIRequestFactory

from apps.booking.views import HotelRoomListView


def test_hotel_room_list_queries_the_resolved_tenant_schema():
    """Public hotel rooms must not query the public schema for PMS tables."""
    request = APIRequestFactory().get(
        "/api/booking/hotels/hotel-guid/rooms/",
        {"check_in": "2026-07-12", "check_out": "2026-07-13", "guests": 1},
    )
    seen_schemas = []

    def run_in_schema(schema_name, query):
        seen_schemas.append(schema_name)
        return query()

    with (
        patch(
            "property.hotel_repository.resolve_hotel_guid",
            return_value=("tenant_acme", 42),
        ),
        patch(
            "apps.property.hotel_repository._run_in_schema",
            side_effect=run_in_schema,
        ),
        patch(
            "apps.hotels.repository.get_available_rooms",
            return_value=[{"id": 7, "room_number": "101"}],
        ) as get_available_rooms,
    ):
        response = HotelRoomListView.as_view()(request, hotel_guid="hotel-guid")

    assert response.status_code == 200
    assert seen_schemas == ["tenant_acme"]
    get_available_rooms.assert_called_once()
