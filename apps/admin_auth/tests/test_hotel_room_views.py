from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.db import IntegrityError
from django.test import SimpleTestCase
from django.urls import resolve
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.admin_auth.hotel_views import AdminHotelRoomInventoryView


def _request(data):
    return SimpleNamespace(data=data)


def _room(**overrides):
    room = {
        "id": 41,
        "property_id": 7,
        "room_type_id": 3,
        "room_type_name": "Deluxe",
        "room_type_preset": "deluxe",
        "room_number": "204",
        "display_name": "Garden room",
        "floor": 2,
        "area": "32.00",
        "bedroom_count": 1,
        "beds": [],
        "amenities": [],
        "photos": [],
        "condition": "clean",
        "availability": "available",
        "capacity": 2,
        "meal_plan": "BB",
        "base_price": "450000.00",
        "currency": "UZS",
        "cover_photo_index": 0,
        "is_active": True,
        "created_at": "2026-08-03T10:00:00Z",
        "updated_at": "2026-08-03T10:00:00Z",
    }
    room.update(overrides)
    return room


class AdminHotelRoomInventoryCreateTests(SimpleTestCase):
    view = AdminHotelRoomInventoryView()

    @patch("apps.admin_auth.hotel_views.create_room")
    @patch("apps.admin_auth.hotel_views.get_room_type")
    def test_creates_room_and_derives_room_type_fields(self, get_room_type, create_room):
        get_room_type.return_value = {
            "id": 3,
            "property_id": 7,
            "name": "Deluxe",
            "preset": "deluxe",
            "is_active": True,
        }
        create_room.return_value = _room()

        response = self.view.post(
            _request(
                {
                    "room_type_id": 3,
                    "room_type_name": "Spoofed",
                    "room_type_preset": "custom",
                    "room_number": "204",
                    "display_name": "Garden room",
                    "floor": 2,
                    "area": "32.00",
                    "bedroom_count": 1,
                    "capacity": 2,
                    "meal_plan": "BB",
                    "base_price": "450000.00",
                    "currency": "UZS",
                    "condition": "clean",
                    "availability": "available",
                    "is_active": True,
                }
            ),
            property_id=7,
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["id"], 41)
        get_room_type.assert_called_once_with(3, 7)
        create_room.assert_called_once()
        kwargs = create_room.call_args.kwargs
        self.assertEqual(kwargs["property_id"], 7)
        self.assertEqual(kwargs["room_type_name"], "Deluxe")
        self.assertEqual(kwargs["room_type_preset"], "deluxe")

    @patch("apps.admin_auth.hotel_views.create_room")
    @patch("apps.admin_auth.hotel_views.get_room_type")
    def test_rejects_inactive_or_foreign_room_type(self, get_room_type, create_room):
        get_room_type.return_value = None

        response = self.view.post(
            _request({"room_type_id": 99, "room_number": "204"}),
            property_id=7,
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("room_type_id", response.data)
        create_room.assert_not_called()

    @patch("apps.admin_auth.hotel_views.create_room")
    @patch("apps.admin_auth.hotel_views.get_room_type")
    def test_returns_field_error_for_duplicate_room_number(self, get_room_type, create_room):
        get_room_type.return_value = {
            "id": 3,
            "property_id": 7,
            "name": "Deluxe",
            "preset": "deluxe",
            "is_active": True,
        }
        create_room.side_effect = IntegrityError(
            'duplicate key value violates unique constraint "pms_room_property_id_room_number_key"'
        )

        response = self.view.post(
            _request({"room_type_id": 3, "room_number": "204"}),
            property_id=7,
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("room_number", response.data)

    @patch("apps.admin_auth.hotel_views.create_room")
    def test_validates_required_and_numeric_fields(self, create_room):
        response = self.view.post(
            _request(
                {
                    "room_number": "204",
                    "area": "0",
                    "bedroom_count": -1,
                    "capacity": 0,
                    "base_price": "-1",
                }
            ),
            property_id=7,
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            set(response.data),
            {"room_type_id", "area", "bedroom_count", "capacity", "base_price"},
        )
        create_room.assert_not_called()

    @patch("apps.admin_auth.hotel_views.create_room")
    def test_rejects_unresolved_hotel(self, create_room):
        response = self.view.post(
            _request({"room_type_id": 3, "room_number": "204"}),
            property_id="missing-guid",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        create_room.assert_not_called()

    @patch("apps.admin_auth.hotel_views.create_room")
    @patch("apps.admin_auth.hotel_views.get_room_type")
    @patch("apps.admin_auth.hotel_views.pop_schema_context")
    @patch("apps.admin_auth.hotel_views._set_tenant_from_guid", return_value=7)
    @patch("apps.admin_auth.hotel_views.connection")
    def test_authenticated_guid_route_dispatches_post(
        self,
        connection,
        _set_tenant_from_guid,
        _pop_schema_context,
        get_room_type,
        create_room,
    ):
        cursor = MagicMock()
        connection.cursor.return_value.__enter__.return_value = cursor
        get_room_type.return_value = {
            "id": 3,
            "property_id": 7,
            "name": "Deluxe",
            "preset": "deluxe",
            "is_active": True,
        }
        create_room.return_value = _room()
        request = APIRequestFactory().post(
            "/api/admin-auth/hotels/hotel-guid/rooms/",
            {"room_type_id": 3, "room_number": "204"},
            format="json",
        )
        force_authenticate(
            request,
            user=SimpleNamespace(role="admin", is_active=True),
        )

        match = resolve("/api/admin-auth/hotels/hotel-guid/rooms/")
        response = match.func(request, **match.kwargs)

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        _set_tenant_from_guid.assert_called_once_with("hotel-guid")
        create_room.assert_called_once()
