from contextlib import nullcontext
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(
        USE_TZ=True,
        TIME_ZONE="UTC",
        REST_FRAMEWORK={},
    )

from django.utils import timezone

from apps.b2b.hotel_booking_service import (
    HotelBookingConflict,
    HotelBookingError,
    cancel_booking_request,
    create_booking_request,
    reconcile_booking_request,
)
from apps.b2b.models import HotelBookingRequestStatus
from apps.b2b.serializers import HotelBookingRequestCreateSerializer


def _dates():
    check_in = timezone.localdate() + timedelta(days=5)
    return check_in, check_in + timedelta(days=2)


def _trip(*, budget=Decimal("1000")):
    check_in, check_out = _dates()
    return {
        "id": 7,
        "status": "active",
        "start_date": check_in - timedelta(days=1),
        "end_date": check_out + timedelta(days=1),
        "budget": budget,
    }


def _payload(employee_ids=None):
    check_in, check_out = _dates()
    return {
        "trip_id": 7,
        "hotel_guid": "hotel-guid",
        "check_in": check_in,
        "check_out": check_out,
        "rooms": [{"room_id": 11, "employee_ids": employee_ids or [21]}],
    }


def _direct_schema_call(_schema, callback):
    return callback()


def test_create_serializer_rejects_duplicate_employee_across_rooms():
    check_in, check_out = _dates()
    serializer = HotelBookingRequestCreateSerializer(
        data={
            "trip_id": 7,
            "hotel_guid": "hotel-guid",
            "check_in": check_in,
            "check_out": check_out,
            "rooms": [
                {"room_id": 11, "employee_ids": [21]},
                {"room_id": 12, "employee_ids": [21]},
            ],
        }
    )

    assert not serializer.is_valid()
    assert "more than one room" in str(serializer.errors)


@patch(
    "apps.b2b.hotel_booking_service.transaction.atomic",
    side_effect=lambda: nullcontext(),
)
@patch("apps.b2b.hotel_booking_service._run_in_schema", side_effect=_direct_schema_call)
@patch("apps.b2b.hotel_booking_service.list_hotel_booking_rooms")
@patch("apps.b2b.hotel_booking_service.upsert_trip_employee")
@patch("apps.b2b.hotel_booking_service.add_hotel_booking_room_employee")
@patch("apps.b2b.hotel_booking_service.add_hotel_booking_room")
@patch("apps.b2b.hotel_booking_service.create_hotel_booking")
@patch("apps.b2b.hotel_booking_service.create_hotel_booking_request")
@patch(
    "apps.b2b.hotel_booking_service.employee_has_overlapping_hotel_booking",
    return_value=False,
)
@patch("apps.b2b.hotel_booking_service.get_available_rooms")
@patch("apps.b2b.hotel_booking_service.lock_hotel_rooms")
@patch("apps.b2b.hotel_booking_service.get_active_employee")
@patch("apps.b2b.hotel_booking_service.get_hotel_for_public")
@patch("apps.b2b.hotel_booking_service.resolve_hotel_guid")
@patch("apps.b2b.hotel_booking_service.get_trip")
def test_create_booking_builds_one_atomic_group(
    get_trip,
    resolve_hotel_guid,
    get_hotel,
    get_employee,
    lock_rooms,
    get_available_rooms,
    _overlap,
    create_group,
    create_pms,
    add_room,
    add_room_employee,
    upsert_assignment,
    list_rooms,
    _run_schema,
    _atomic,
):
    get_trip.return_value = _trip()
    resolve_hotel_guid.return_value = ("tenant_hotel", 3)
    get_hotel.return_value = {"title": "Hotel"}
    get_employee.return_value = {"id": 21, "is_active": True}
    lock_rooms.return_value = [{"id": 11, "property_id": 3}]
    get_available_rooms.return_value = [
        {
            "id": 11,
            "capacity_adults": 2,
            "price_per_night": Decimal("100"),
            "display_name": "101",
        }
    ]
    create_group.return_value = {"id": 31, "status": "pending"}
    create_pms.return_value = {"id": 41, "total_cost": Decimal("200")}
    add_room.return_value = {"id": 51}
    add_room_employee.return_value = {"id": 61}
    upsert_assignment.return_value = {"id": 71}
    list_rooms.return_value = [{"id": 51, "employees": [{"employee_id": 21}]}]

    result = create_booking_request(company_id=1, requested_by=2, data=_payload())

    assert result["room_count"] == 1
    assert result["employee_count"] == 1
    lock_rooms.assert_called_once_with(3, [11])
    create_pms.assert_called_once()
    upsert_assignment.assert_called_once()


@pytest.mark.parametrize(
    ("budget", "capacity", "employee_ids", "message"),
    [
        (Decimal("100"), 2, [21], "exceeds"),
        (Decimal("1000"), 1, [21, 22], "capacity"),
    ],
)
@patch(
    "apps.b2b.hotel_booking_service.transaction.atomic",
    side_effect=lambda: nullcontext(),
)
@patch("apps.b2b.hotel_booking_service._run_in_schema", side_effect=_direct_schema_call)
@patch(
    "apps.b2b.hotel_booking_service.employee_has_overlapping_hotel_booking",
    return_value=False,
)
@patch("apps.b2b.hotel_booking_service.lock_hotel_rooms", return_value=[{"id": 11}])
@patch("apps.b2b.hotel_booking_service.get_active_employee", return_value={"id": 21})
@patch(
    "apps.b2b.hotel_booking_service.get_hotel_for_public",
    return_value={"title": "Hotel"},
)
@patch(
    "apps.b2b.hotel_booking_service.resolve_hotel_guid",
    return_value=("tenant_hotel", 3),
)
@patch("apps.b2b.hotel_booking_service.get_trip")
def test_create_booking_enforces_budget_and_capacity(
    get_trip,
    _resolve,
    _hotel,
    _employee,
    _lock,
    _overlap,
    _run_schema,
    _atomic,
    budget,
    capacity,
    employee_ids,
    message,
):
    get_trip.return_value = _trip(budget=budget)
    available = [
        {"id": 11, "capacity_adults": capacity, "price_per_night": Decimal("100")}
    ]
    with patch(
        "apps.b2b.hotel_booking_service.get_available_rooms", return_value=available
    ):
        with pytest.raises(HotelBookingError, match=message):
            create_booking_request(
                company_id=1,
                requested_by=2,
                data=_payload(employee_ids),
            )


@patch(
    "apps.b2b.hotel_booking_service.transaction.atomic",
    side_effect=lambda: nullcontext(),
)
@patch("apps.b2b.hotel_booking_service._run_in_schema", side_effect=_direct_schema_call)
@patch("apps.b2b.hotel_booking_service.update_hotel_booking_request_status")
@patch("apps.b2b.hotel_booking_service.update_booking_request_employee_statuses")
@patch("apps.b2b.hotel_booking_service.cancel_pms_booking")
@patch("apps.b2b.hotel_booking_service.get_bookings_status")
@patch("apps.b2b.hotel_booking_service.list_hotel_booking_rooms")
def test_mixed_hotel_response_rejects_group_and_cancels_sibling(
    list_rooms,
    get_statuses,
    cancel_pms,
    update_employees,
    update_group,
    _run_schema,
    _atomic,
):
    booking = {"id": 31, "tenant_schema": "tenant_hotel", "status": "pending"}
    list_rooms.return_value = [{"pms_booking_id": 41}, {"pms_booking_id": 42}]
    get_statuses.return_value = [
        {"id": 41, "status": "confirmed"},
        {"id": 42, "status": "cancelled"},
    ]
    update_group.return_value = {**booking, "status": "rejected"}

    result = reconcile_booking_request(booking)

    assert result["status"] == HotelBookingRequestStatus.REJECTED
    cancel_pms.assert_called_once_with(41)
    update_employees.assert_called_once_with(31, "cancelled")


@patch("apps.b2b.hotel_booking_service.booking_detail")
@patch("apps.b2b.hotel_booking_service.get_hotel_booking_request")
def test_cancel_is_idempotent(get_booking, detail):
    booking = {"id": 31, "status": "cancelled"}
    get_booking.return_value = booking
    detail.return_value = {**booking, "rooms": []}

    assert cancel_booking_request(booking_id=31, company_id=1)["status"] == "cancelled"


@patch("apps.b2b.hotel_booking_service.get_hotel_booking_request")
def test_cancel_rejects_on_or_after_check_in(get_booking):
    get_booking.return_value = {
        "id": 31,
        "status": "confirmed",
        "check_in": timezone.localdate(),
    }

    with pytest.raises(HotelBookingConflict, match="on or after"):
        cancel_booking_request(booking_id=31, company_id=1)
