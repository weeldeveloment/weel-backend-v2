from __future__ import annotations

from unittest.mock import patch

from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from rest_framework.test import APIRequestFactory

from apps.bookingcom.service import normalize_reservation, sync_property_reservations
from apps.bookingcom.views import BookingComManualSyncView


class FakeClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def fetch_reservations(self, *, updated_since=None):
        return [
            {
                "reservation_id": "res-1",
                "room_id": "ext-room-1",
                "check_in": "2026-08-01",
                "check_out": "2026-08-03",
                "adult_count": 2,
                "child_count": 1,
                "currency": "USD",
                "total_price": "180.50",
                "status": "confirmed",
                "guest": {
                    "first_name": "John",
                    "last_name": "Doe",
                    "email": "john@example.com",
                    "phone": "+10000000000",
                },
            }
        ]


def test_normalize_reservation_maps_guest_and_price_fields():
    result = normalize_reservation(
        {
            "id": "abc",
            "accommodation_id": "room-77",
            "check_in": "2026-08-02",
            "check_out": "2026-08-04",
            "number_of_guests": 3,
            "price": "99.99",
            "guest": {"first_name": "Ada", "last_name": "Lovelace"},
        }
    )

    assert result["external_reservation_id"] == "abc"
    assert result["external_room_id"] == "room-77"
    assert str(result["total_cost"]) == "99.99"
    assert result["guest_first_name"] == "Ada"
    assert result["adult_count"] == 3


@patch("apps.bookingcom.service.mark_connection_sync_state")
@patch("apps.bookingcom.service.finish_sync_run", return_value={"id": 9, "status": "success"})
@patch("apps.bookingcom.service.start_sync_run", return_value={"id": 7})
@patch("apps.bookingcom.service.list_recent_sync_errors", return_value=[])
@patch("apps.bookingcom.service.get_connection")
@patch("apps.bookingcom.service._add_booking_history")
@patch("apps.bookingcom.service.accept_booking")
@patch("apps.bookingcom.service.create_booking", return_value={"id": 55, "status": "new"})
@patch("apps.bookingcom.service.find_or_create_guest", return_value={"id": 44})
@patch("apps.bookingcom.service.get_booking_by_external_reference", return_value=None)
@patch("apps.bookingcom.service.get_room_mapping", return_value={"room_id": 11})
def test_sync_property_creates_imported_booking(
    _mapping,
    _existing,
    _guest,
    create_booking,
    accept_booking,
    add_history,
    get_connection,
    _recent_errors,
    _start_sync_run,
    _finish_sync_run,
    _mark_connection,
):
    get_connection.return_value = {
        "id": 5,
        "property_id": 1,
        "enabled": True,
        "bookingcom_property_id": "hotel-1",
        "api_url": "https://example.test",
        "api_token": "secret",
        "last_successful_sync_at": None,
    }

    result = sync_property_reservations(1, client_factory=FakeClient)

    create_booking.assert_called_once()
    accept_booking.assert_called_once_with(55)
    assert result["latest_run"]["status"] == "success"
    assert add_history.call_args_list[-1].kwargs["action"] == "bookingcom_imported"


@patch("apps.bookingcom.service.mark_connection_sync_state")
@patch("apps.bookingcom.service.finish_sync_run", return_value={"id": 10, "status": "success"})
@patch("apps.bookingcom.service.start_sync_run", return_value={"id": 8})
@patch("apps.bookingcom.service.list_recent_sync_errors", return_value=[])
@patch("apps.bookingcom.service.get_connection")
@patch("apps.bookingcom.service.log_sync_error")
@patch("apps.bookingcom.service.get_room_mapping", return_value=None)
def test_sync_property_skips_missing_room_mapping(
    _mapping,
    log_sync_error,
    get_connection,
    _recent_errors,
    _start_sync_run,
    _finish_sync_run,
    _mark_connection,
):
    get_connection.return_value = {
        "id": 5,
        "property_id": 1,
        "enabled": True,
        "bookingcom_property_id": "hotel-1",
        "api_url": "https://example.test",
        "api_token": "secret",
        "last_successful_sync_at": None,
    }

    sync_property_reservations(1, client_factory=FakeClient)

    log_sync_error.assert_called_once()
    assert log_sync_error.call_args.kwargs["code"] == "missing_room_mapping"


@patch("apps.bookingcom.service.mark_connection_sync_state")
@patch("apps.bookingcom.service.finish_sync_run", return_value={"id": 11, "status": "success"})
@patch("apps.bookingcom.service.start_sync_run", return_value={"id": 12})
@patch("apps.bookingcom.service.list_recent_sync_errors", return_value=[])
@patch("apps.bookingcom.service.get_connection")
@patch("apps.bookingcom.service.log_sync_error")
@patch(
    "apps.bookingcom.service.get_booking_by_external_reference",
    return_value={"id": 77, "status": "checked_in", "guest_id": 44},
)
@patch("apps.bookingcom.service.find_or_create_guest", return_value={"id": 44})
@patch("apps.bookingcom.service.get_room_mapping", return_value={"room_id": 11})
def test_sync_property_preserves_checked_in_booking_conflict(
    _mapping,
    _guest,
    _existing,
    log_sync_error,
    get_connection,
    _recent_errors,
    _start_sync_run,
    _finish_sync_run,
    _mark_connection,
):
    get_connection.return_value = {
        "id": 5,
        "property_id": 1,
        "enabled": True,
        "bookingcom_property_id": "hotel-1",
        "api_url": "https://example.test",
        "api_token": "secret",
        "last_successful_sync_at": None,
    }

    sync_property_reservations(1, client_factory=FakeClient)

    assert log_sync_error.call_args.kwargs["code"] == "manual_conflict"


@patch("apps.bookingcom.views.sync_property_reservations", return_value={"connection": None, "latest_run": None, "recent_errors": []})
@patch("apps.bookingcom.views.get_property", return_value={"id": 1})
@patch("apps.bookingcom.views._require_org", return_value=99)
def test_manual_sync_view_calls_service(_org, _property, sync_property_reservations_mock):
    factory = APIRequestFactory()
    view = BookingComManualSyncView()
    view.authentication_classes = []
    view.permission_classes = []
    request = factory.post("/api/pms/properties/1/booking-com/sync/", {"full_resync": True}, format="json")
    request = view.initialize_request(request)
    request.user = {"id": 5, "organization_id": 99}
    request.organization = 99
    response = view.post(request, property_id=1)

    assert response.status_code == 200
    sync_property_reservations_mock.assert_called_once_with(1, full_resync=True, triggered_by="manual")
