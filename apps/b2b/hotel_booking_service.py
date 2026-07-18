from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.b2b.models import HotelBookingRequestStatus, TripEmployeeStatus, TripStatus
from apps.b2b.repository import (
    add_hotel_booking_room,
    add_hotel_booking_room_employee,
    create_hotel_booking_request,
    employee_has_overlapping_hotel_booking,
    get_active_employee,
    get_hotel_booking_request,
    get_trip,
    list_hotel_booking_rooms,
    update_booking_request_employee_statuses,
    update_hotel_booking_request_status,
    update_trip_employee_status_by_pms_booking,
    upsert_trip_employee,
)
from apps.hotels.repository import (
    create_hotel_booking,
    get_available_rooms,
    get_bookings_status,
    lock_hotel_rooms,
)
from apps.pms.repository import cancel_booking as cancel_pms_booking
from apps.property.hotel_repository import (
    _run_in_schema,
    get_hotel_for_public,
    resolve_hotel_guid,
)

logger = logging.getLogger(__name__)


class HotelBookingError(Exception):
    status_code = 400

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


class HotelBookingNotFound(HotelBookingError):
    status_code = 404


class HotelBookingConflict(HotelBookingError):
    status_code = 409


class HotelBookingInternalError(HotelBookingError):
    status_code = 500


def booking_detail(booking_request: dict[str, Any]) -> dict[str, Any]:
    rooms = list_hotel_booking_rooms(booking_request["id"])
    result = dict(booking_request)
    result["rooms"] = rooms
    result["room_count"] = len(rooms)
    result["employee_count"] = sum(len(room["employees"]) for room in rooms)
    return result


def _validate_trip(trip: dict[str, Any], check_in, check_out) -> None:
    if trip.get("status") in {TripStatus.COMPLETED, TripStatus.CANCELLED}:
        raise HotelBookingError("Completed or cancelled trips cannot be booked.")
    if not trip.get("start_date") or not trip.get("end_date"):
        raise HotelBookingError(
            "The trip must have start_date and end_date before booking."
        )
    if check_in < timezone.localdate():
        raise HotelBookingError("check_in cannot be in the past.")
    if check_in < trip["start_date"] or check_out > trip["end_date"]:
        raise HotelBookingError("Hotel dates must be within the selected trip dates.")


def create_booking_request(
    *,
    company_id: int,
    requested_by: int,
    data: dict[str, Any],
) -> dict[str, Any]:
    trip = get_trip(data["trip_id"], company_id)
    if not trip:
        raise HotelBookingNotFound("Trip not found.")

    check_in = data["check_in"]
    check_out = data["check_out"]
    _validate_trip(trip, check_in, check_out)

    resolved = resolve_hotel_guid(data["hotel_guid"])
    if not resolved:
        raise HotelBookingNotFound("Hotel not found.")
    schema_name, hotel_id = resolved
    hotel = get_hotel_for_public(data["hotel_guid"])
    if not hotel:
        raise HotelBookingNotFound("Hotel not found.")

    employee_ids = [
        employee_id for room in data["rooms"] for employee_id in room["employee_ids"]
    ]
    for employee_id in employee_ids:
        if not get_active_employee(employee_id, company_id):
            raise HotelBookingNotFound(f"Employee {employee_id} not found.")

    room_ids = [room["room_id"] for room in data["rooms"]]
    nights = (check_out - check_in).days

    def _create_in_tenant() -> dict[str, Any]:
        locked_rooms = lock_hotel_rooms(hotel_id, room_ids)
        if {room["id"] for room in locked_rooms} != set(room_ids):
            raise HotelBookingNotFound("One or more rooms do not belong to this hotel.")

        available_rooms = get_available_rooms(
            hotel_id,
            check_in=check_in,
            check_out=check_out,
            guests=1,
        )
        available_by_id = {room["id"]: room for room in available_rooms}
        total_price = Decimal("0")

        for requested_room in data["rooms"]:
            room_id = requested_room["room_id"]
            available_room = available_by_id.get(room_id)
            if not available_room:
                raise HotelBookingConflict(f"Room {room_id} is no longer available.")
            capacity = available_room.get("capacity_adults")
            if capacity is not None and len(requested_room["employee_ids"]) > int(
                capacity
            ):
                raise HotelBookingError(f"Room {room_id} capacity is {capacity}.")
            total_price += (
                Decimal(str(available_room.get("price_per_night") or 0)) * nights
            )

        if trip.get("budget") is not None and total_price > Decimal(
            str(trip["budget"])
        ):
            raise HotelBookingError("Booking total exceeds the selected trip budget.")

        for employee_id in employee_ids:
            if employee_has_overlapping_hotel_booking(
                employee_id,
                check_in=check_in,
                check_out=check_out,
            ):
                raise HotelBookingConflict(
                    f"Employee {employee_id} already has an overlapping hotel booking."
                )

        booking_request = create_hotel_booking_request(
            company_id=company_id,
            trip_id=data["trip_id"],
            hotel_guid=data["hotel_guid"],
            tenant_schema=schema_name,
            hotel_property_id=hotel_id,
            hotel_name=hotel.get("title") or hotel.get("name"),
            check_in=check_in,
            check_out=check_out,
            requested_by=requested_by,
        )
        if not booking_request:
            raise HotelBookingInternalError("Failed to create booking request.")

        for requested_room in sorted(data["rooms"], key=lambda room: room["room_id"]):
            room_id = requested_room["room_id"]
            available_room = available_by_id[room_id]
            pms_booking = create_hotel_booking(
                property_id=hotel_id,
                room_id=room_id,
                client_user_id=requested_by,
                check_in=check_in,
                check_out=check_out,
                adults=len(requested_room["employee_ids"]),
                b2b_company_id=company_id,
            )
            if not pms_booking:
                raise HotelBookingConflict(f"Room {room_id} is no longer available.")

            booking_room = add_hotel_booking_room(
                booking_request_id=booking_request["id"],
                room_id=room_id,
                room_name=available_room.get("display_name")
                or available_room.get("room_type_name"),
                pms_booking_id=pms_booking["id"],
                price_per_night=available_room.get("price_per_night"),
                total_price=pms_booking.get("total_cost"),
            )
            if not booking_room:
                raise HotelBookingInternalError(
                    "Failed to attach a room to the booking request."
                )

            for employee_id in requested_room["employee_ids"]:
                if not add_hotel_booking_room_employee(
                    booking_room_id=booking_room["id"],
                    employee_id=employee_id,
                ):
                    raise HotelBookingInternalError(
                        "Failed to assign an employee to a room."
                    )
                if not upsert_trip_employee(
                    trip_id=data["trip_id"],
                    employee_id=employee_id,
                    property_id=hotel_id,
                    room_id=room_id,
                    check_in=check_in,
                    check_out=check_out,
                    pms_booking_id=pms_booking["id"],
                    status=TripEmployeeStatus.INVITED,
                ):
                    raise HotelBookingInternalError(
                        "Failed to update the trip assignment."
                    )

        return booking_detail(booking_request)

    with transaction.atomic():
        return _run_in_schema(schema_name, _create_in_tenant)


def reconcile_booking_request(booking_request: dict[str, Any]) -> dict[str, Any]:
    if booking_request.get("status") in {
        HotelBookingRequestStatus.REJECTED,
        HotelBookingRequestStatus.CANCELLED,
    }:
        return booking_request

    rooms = list_hotel_booking_rooms(booking_request["id"])
    booking_ids = [
        room["pms_booking_id"] for room in rooms if room.get("pms_booking_id")
    ]
    if not booking_ids:
        return booking_request

    def _reconcile_in_tenant() -> dict[str, Any]:
        statuses = get_bookings_status(booking_ids)
        if len(statuses) != len(booking_ids):
            return booking_request
        by_id = {row["id"]: row["status"] for row in statuses}

        if any(value in {"cancelled", "no_show"} for value in by_id.values()):
            for booking_id, current_status in by_id.items():
                if current_status in {"new", "pending", "confirmed"}:
                    cancel_pms_booking(booking_id)
            update_booking_request_employee_statuses(
                booking_request["id"], TripEmployeeStatus.CANCELLED
            )
            return (
                update_hotel_booking_request_status(
                    booking_request["id"],
                    HotelBookingRequestStatus.REJECTED,
                    reviewed_at=timezone.now(),
                )
                or booking_request
            )

        employee_statuses = {
            "confirmed": TripEmployeeStatus.CONFIRMED,
            "checked_in": TripEmployeeStatus.CHECKED_IN,
            "checked_out": TripEmployeeStatus.CHECKED_OUT,
        }
        for booking_id, current_status in by_id.items():
            employee_status = employee_statuses.get(current_status)
            if employee_status:
                update_trip_employee_status_by_pms_booking(booking_id, employee_status)

        if set(by_id.values()) <= set(employee_statuses):
            return (
                update_hotel_booking_request_status(
                    booking_request["id"],
                    HotelBookingRequestStatus.CONFIRMED,
                    reviewed_at=booking_request.get("reviewed_at") or timezone.now(),
                )
                or booking_request
            )
        return booking_request

    try:
        with transaction.atomic():
            return _run_in_schema(
                booking_request["tenant_schema"], _reconcile_in_tenant
            )
    except Exception:
        logger.exception(
            "Failed to reconcile B2B hotel booking %s", booking_request.get("id")
        )
        return booking_request


def cancel_booking_request(*, booking_id: int, company_id: int) -> dict[str, Any]:
    booking_request = get_hotel_booking_request(booking_id, company_id)
    if not booking_request:
        raise HotelBookingNotFound("Booking not found.")
    if booking_request["status"] == HotelBookingRequestStatus.CANCELLED:
        return booking_detail(booking_request)
    if booking_request["status"] == HotelBookingRequestStatus.REJECTED:
        raise HotelBookingConflict("Rejected bookings cannot be cancelled.")
    if timezone.localdate() >= booking_request["check_in"]:
        raise HotelBookingConflict("Bookings cannot be cancelled on or after check-in.")

    rooms = list_hotel_booking_rooms(booking_id)
    booking_ids = [
        room["pms_booking_id"] for room in rooms if room.get("pms_booking_id")
    ]

    def _cancel_in_tenant() -> dict[str, Any]:
        statuses = get_bookings_status(booking_ids)
        if any(row["status"] in {"checked_in", "checked_out"} for row in statuses):
            raise HotelBookingConflict("A stay that has started cannot be cancelled.")
        for row in statuses:
            if row["status"] in {"new", "pending", "confirmed"}:
                cancel_pms_booking(row["id"])
        update_booking_request_employee_statuses(
            booking_id, TripEmployeeStatus.CANCELLED
        )
        updated = update_hotel_booking_request_status(
            booking_id,
            HotelBookingRequestStatus.CANCELLED,
            reviewed_at=timezone.now(),
        )
        if not updated:
            raise HotelBookingInternalError("Failed to cancel booking.")
        return booking_detail(updated)

    with transaction.atomic():
        return _run_in_schema(booking_request["tenant_schema"], _cancel_in_tenant)
