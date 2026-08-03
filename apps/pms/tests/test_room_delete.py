from unittest.mock import patch

from django.db import IntegrityError
from django.test import SimpleTestCase
from rest_framework import status
from rest_framework.test import APIRequestFactory

from apps.pms.repository import delete_room
from apps.pms.views import RoomRetrieveUpdateDestroyView


class DeleteRoomRepositoryTests(SimpleTestCase):
    @patch("apps.pms.repository.fetch_one")
    def test_returns_deleted_for_unbooked_room(self, fetch_one):
        fetch_one.return_value = {
            "found": True,
            "has_bookings": False,
            "deleted": True,
        }

        result = delete_room(room_id=7, property_id=3)

        self.assertEqual(result, "deleted")
        sql, params = fetch_one.call_args.args
        self.assertIn("DELETE FROM pms_room", sql)
        self.assertIn("FROM pms_booking", sql)
        self.assertEqual(params, [7, 3])

    @patch("apps.pms.repository.fetch_one")
    def test_returns_booking_conflict(self, fetch_one):
        fetch_one.return_value = {
            "found": True,
            "has_bookings": True,
            "deleted": False,
        }

        self.assertEqual(delete_room(room_id=7, property_id=3), "has_bookings")

    @patch("apps.pms.repository.fetch_one", return_value=None)
    def test_returns_not_found(self, _fetch_one):
        self.assertEqual(delete_room(room_id=7, property_id=3), "not_found")


class DeleteRoomViewTests(SimpleTestCase):
    def setUp(self):
        self.request = APIRequestFactory().delete(
            "/api/pms/properties/3/rooms/7/"
        )
        self.view = RoomRetrieveUpdateDestroyView()

    def _owned_room_patches(self):
        return (
            patch("apps.pms.views._require_org", return_value=11),
            patch("apps.pms.views.get_property", return_value={"id": 3}),
            patch(
                "apps.pms.views.get_room",
                return_value={"id": 7, "property_id": 3},
            ),
        )

    def test_deletes_unbooked_room_and_its_images(self):
        org, prop, room = self._owned_room_patches()
        with (
            org,
            prop,
            room,
            patch("apps.pms.views.delete_room", return_value="deleted") as remove,
            patch(
                "apps.pms.views.default_storage.listdir",
                return_value=([], ["one.jpg", "two.jpg"]),
            ),
            patch("apps.pms.views.default_storage.delete") as delete_file,
        ):
            response = self.view.delete(self.request, property_id=3, room_id=7)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        remove.assert_called_once_with(7, 3)
        self.assertEqual(
            [call.args[0] for call in delete_file.call_args_list],
            [
                "pms/properties/3/rooms/7/one.jpg",
                "pms/properties/3/rooms/7/two.jpg",
            ],
        )

    def test_rejects_room_with_any_booking_history(self):
        org, prop, room = self._owned_room_patches()
        with (
            org,
            prop,
            room,
            patch("apps.pms.views.delete_room", return_value="has_bookings"),
            patch("apps.pms.views.default_storage.listdir") as listdir,
        ):
            response = self.view.delete(self.request, property_id=3, room_id=7)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "room_has_bookings")
        listdir.assert_not_called()

    def test_maps_concurrent_booking_constraint_to_conflict(self):
        org, prop, room = self._owned_room_patches()
        with (
            org,
            prop,
            room,
            patch(
                "apps.pms.views.delete_room",
                side_effect=IntegrityError("room is referenced"),
            ),
            patch("apps.pms.views.default_storage.listdir") as listdir,
        ):
            response = self.view.delete(self.request, property_id=3, room_id=7)

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "room_has_bookings")
        listdir.assert_not_called()

    def test_hides_room_outside_owned_property(self):
        with (
            patch("apps.pms.views._require_org", return_value=11),
            patch("apps.pms.views.get_property", return_value=None) as get_property,
            patch("apps.pms.views.get_room") as get_room,
            patch("apps.pms.views.delete_room") as remove,
        ):
            response = self.view.delete(self.request, property_id=3, room_id=7)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        get_property.assert_called_once_with(3, organization_id=11)
        get_room.assert_not_called()
        remove.assert_not_called()

    def test_hides_room_id_from_another_property(self):
        with (
            patch("apps.pms.views._require_org", return_value=11),
            patch("apps.pms.views.get_property", return_value={"id": 3}),
            patch("apps.pms.views.get_room", return_value=None) as get_room,
            patch("apps.pms.views.delete_room") as remove,
        ):
            response = self.view.delete(self.request, property_id=3, room_id=7)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        get_room.assert_called_once_with(7, 3)
        remove.assert_not_called()

    def test_storage_failure_does_not_change_successful_delete(self):
        org, prop, room = self._owned_room_patches()
        with (
            org,
            prop,
            room,
            patch("apps.pms.views.delete_room", return_value="deleted"),
            patch(
                "apps.pms.views.default_storage.listdir",
                side_effect=RuntimeError("storage unavailable"),
            ),
            self.assertLogs("apps.pms.views", level="ERROR"),
        ):
            response = self.view.delete(self.request, property_id=3, room_id=7)

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
