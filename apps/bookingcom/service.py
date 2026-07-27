from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any

from django.utils import timezone

from apps.bookingcom.client import BookingComClient
from apps.bookingcom.repository import (
    finish_sync_run,
    get_booking_by_external_reference,
    get_connection,
    get_latest_sync_run,
    get_room_mapping,
    list_enabled_connections,
    list_recent_sync_errors,
    list_room_mappings,
    list_rooms_by_type,
    log_sync_error,
    mark_connection_sync_state,
    start_sync_run,
)
from apps.pms.models import BookingSource, BookingStatus
from apps.pms.repository import (
    _add_booking_history,
    accept_booking,
    cancel_booking,
    create_booking,
    find_or_create_guest,
    update_booking,
    update_guest,
)
from apps.platform.raw_repository import list_organizations
from apps.property.hotel_repository import _run_in_schema

logger = logging.getLogger(__name__)

PROVIDER_NAME = "booking.com"


def _safe_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    return Decimal(str(value))


def _parse_date(value: Any):
    if value in (None, ""):
        return None
    return datetime.fromisoformat(str(value)[:10]).date()


def _reservation_status(payload: dict[str, Any]) -> str:
    status = str(payload.get("status") or payload.get("reservation_status") or "").strip().lower()
    if status in {"cancelled", "canceled", "cancel", "no_show"}:
        return BookingStatus.CANCELLED
    return BookingStatus.CONFIRMED


def normalize_reservation(payload: dict[str, Any]) -> dict[str, Any]:
    guest = payload.get("guest") if isinstance(payload.get("guest"), dict) else {}
    adults = (
        payload.get("adult_count")
        or payload.get("number_of_guests")
        or guest.get("adult_count")
        or 1
    )
    children = payload.get("child_count") or guest.get("child_count") or 0
    return {
        "external_reservation_id": str(payload.get("reservation_id") or payload.get("id") or "").strip(),
        "external_room_id": str(
            payload.get("room_id")
            or payload.get("accommodation_id")
            or payload.get("room_type_id")
            or ""
        ).strip(),
        "check_in": _parse_date(payload.get("check_in")),
        "check_out": _parse_date(payload.get("check_out")),
        "adult_count": int(adults),
        "child_count": int(children),
        "currency": str(payload.get("currency") or "USD"),
        "total_cost": _safe_decimal(
            payload.get("total_cost")
            or payload.get("total_price")
            or payload.get("price")
        ),
        "status": _reservation_status(payload),
        "notes": payload.get("notes") or payload.get("remarks") or payload.get("special_requests"),
        "guest_first_name": str(
            guest.get("first_name") or payload.get("guest_first_name") or "Guest"
        ).strip()
        or "Guest",
        "guest_last_name": str(
            guest.get("last_name") or payload.get("guest_last_name") or ""
        ).strip()
        or None,
        "guest_email": guest.get("email") or payload.get("guest_email"),
        "guest_phone": guest.get("phone") or payload.get("guest_phone"),
        "payload_ref": {
            "reservation_id": payload.get("reservation_id") or payload.get("id"),
            "status": payload.get("status") or payload.get("reservation_status"),
            "updated_at": payload.get("updated_at"),
            "raw": payload,
        },
    }


def _resolve_room_id(property_id: int, external_room_id: str) -> int | None:
    mapping = get_room_mapping(property_id, external_room_id)
    if not mapping:
        return None
    if mapping.get("room_id"):
        return int(mapping["room_id"])
    room_type_id = mapping.get("room_type_id")
    if not room_type_id:
        return None
    candidates = list_rooms_by_type(property_id, int(room_type_id))
    if len(candidates) == 1:
        return int(candidates[0]["id"])
    return None


def _sync_one_reservation(
    *,
    property_id: int,
    sync_run_id: int,
    normalized: dict[str, Any],
) -> str:
    external_reservation_id = normalized["external_reservation_id"]
    external_room_id = normalized["external_room_id"]
    if not external_reservation_id:
        log_sync_error(
            sync_run_id=sync_run_id,
            property_id=property_id,
            code="missing_reservation_id",
            message="Reservation missing external reservation id.",
            external_room_id=external_room_id or None,
            payload=normalized["payload_ref"],
        )
        return "failed"

    room_id = _resolve_room_id(property_id, external_room_id)
    if room_id is None:
        log_sync_error(
            sync_run_id=sync_run_id,
            property_id=property_id,
            code="missing_room_mapping",
            message="No active Booking.com room mapping found.",
            external_reservation_id=external_reservation_id,
            external_room_id=external_room_id or None,
            payload=normalized["payload_ref"],
        )
        return "skipped"

    existing = get_booking_by_external_reference(
        property_id,
        provider=PROVIDER_NAME,
        external_reservation_id=external_reservation_id,
    )
    guest = find_or_create_guest(
        first_name=normalized["guest_first_name"],
        last_name=normalized["guest_last_name"],
        email=normalized["guest_email"],
        phone=normalized["guest_phone"],
    )
    guest_id = guest.get("id")
    now = timezone.now()
    booking_payload = {
        "room_id": room_id,
        "guest_id": guest_id,
        "check_in": normalized["check_in"],
        "check_out": normalized["check_out"],
        "adult_count": normalized["adult_count"],
        "child_count": normalized["child_count"],
        "currency": normalized["currency"],
        "total_cost": normalized["total_cost"],
        "notes": normalized["notes"],
        "source": BookingSource.OTA,
        "external_provider": PROVIDER_NAME,
        "external_reservation_id": external_reservation_id,
        "external_room_id": external_room_id or None,
        "external_payload_ref": normalized["payload_ref"],
        "last_synced_at": now,
    }

    if existing:
        current_status = existing.get("status")
        if current_status in {BookingStatus.CHECKED_IN, BookingStatus.CHECKED_OUT}:
            log_sync_error(
                sync_run_id=sync_run_id,
                property_id=property_id,
                code="manual_conflict",
                message=f"Booking already in terminal PMS state: {current_status}.",
                external_reservation_id=external_reservation_id,
                external_room_id=external_room_id or None,
                payload=normalized["payload_ref"],
            )
            return "failed"

        if normalized["status"] == BookingStatus.CANCELLED:
            cancel_booking(int(existing["id"]))
            update_booking(
                int(existing["id"]),
                external_payload_ref=normalized["payload_ref"],
                last_synced_at=now,
            )
            _add_booking_history(
                booking_id=int(existing["id"]),
                action="bookingcom_cancelled",
                previous_value={"status": current_status},
                new_value={"status": BookingStatus.CANCELLED, "provider": PROVIDER_NAME},
                user_id=None,
            )
            return "cancelled"

        update_booking(int(existing["id"]), **booking_payload)
        if guest_id:
            update_guest(
                guest_id,
                first_name=normalized["guest_first_name"],
                last_name=normalized["guest_last_name"],
                email=normalized["guest_email"],
                phone=normalized["guest_phone"],
            )
        _add_booking_history(
            booking_id=int(existing["id"]),
            action="bookingcom_updated",
            previous_value={"provider": PROVIDER_NAME},
            new_value={"provider": PROVIDER_NAME, "status": current_status},
            user_id=None,
        )
        return "updated"

    created = create_booking(
        property_id=property_id,
        room_id=room_id,
        check_in=normalized["check_in"],
        check_out=normalized["check_out"],
        guest_id=guest_id,
        adult_count=normalized["adult_count"],
        child_count=normalized["child_count"],
        currency=normalized["currency"],
        total_cost=normalized["total_cost"],
        notes=normalized["notes"],
        source=BookingSource.OTA,
        external_provider=PROVIDER_NAME,
        external_reservation_id=external_reservation_id,
        external_room_id=external_room_id or None,
        external_payload_ref=normalized["payload_ref"],
        imported_at=now,
        last_synced_at=now,
    )
    if not created:
        log_sync_error(
            sync_run_id=sync_run_id,
            property_id=property_id,
            code="create_failed",
            message="PMS booking create returned no row.",
            external_reservation_id=external_reservation_id,
            external_room_id=external_room_id or None,
            payload=normalized["payload_ref"],
        )
        return "failed"

    accept_booking(int(created["id"]))
    _add_booking_history(
        booking_id=int(created["id"]),
        action="bookingcom_imported",
        previous_value={},
        new_value={"provider": PROVIDER_NAME, "reservation_id": external_reservation_id},
        user_id=None,
    )
    if normalized["status"] == BookingStatus.CANCELLED:
        cancel_booking(int(created["id"]))
        _add_booking_history(
            booking_id=int(created["id"]),
            action="bookingcom_cancelled",
            previous_value={"status": BookingStatus.CONFIRMED},
            new_value={"status": BookingStatus.CANCELLED, "provider": PROVIDER_NAME},
            user_id=None,
        )
        return "cancelled"
    return "created"


def sync_property_reservations(
    property_id: int,
    *,
    full_resync: bool = False,
    triggered_by: str = "manual",
    client_factory=BookingComClient,
) -> dict[str, Any]:
    connection = get_connection(property_id)
    if not connection or not connection.get("enabled"):
        raise ValueError("Booking.com connection is not enabled for this property.")

    cursor_from = None if full_resync else connection.get("last_successful_sync_at")
    sync_run = start_sync_run(
        property_id,
        connection_id=connection.get("id"),
        triggered_by=triggered_by,
        sync_cursor_from=cursor_from,
    )
    sync_run_id = int(sync_run["id"])
    stats = {"created": 0, "updated": 0, "cancelled": 0, "skipped": 0, "failed": 0}
    client = client_factory(
        api_url=connection["api_url"],
        property_id=connection["bookingcom_property_id"],
        api_token=connection.get("api_token"),
        username=connection.get("username"),
        password=connection.get("password"),
    )

    try:
        reservations = client.fetch_reservations(updated_since=cursor_from)
        for raw in reservations:
            normalized = normalize_reservation(raw)
            result = _sync_one_reservation(
                property_id=property_id,
                sync_run_id=sync_run_id,
                normalized=normalized,
            )
            stats[result] += 1

        latest_run = finish_sync_run(
            sync_run_id,
            status="success",
            stats=stats,
            sync_cursor_to=timezone.now(),
        )
        mark_connection_sync_state(
            property_id,
            last_sync_status="success",
            last_successful_sync_at=timezone.now(),
            last_error=None,
        )
        return {
            "connection": get_connection(property_id),
            "latest_run": latest_run,
            "recent_errors": list_recent_sync_errors(property_id),
        }
    except Exception as exc:
        logger.exception("Booking.com sync failed for property_id=%s", property_id)
        finish_sync_run(
            sync_run_id,
            status="failed",
            stats=stats,
            sync_cursor_to=None,
            error_message=str(exc),
        )
        mark_connection_sync_state(
            property_id,
            last_sync_status="failed",
            last_error=str(exc),
        )
        raise


def sync_all_enabled_reservations(*, client_factory=BookingComClient) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for organization in list_organizations():
        schema_name = organization.get("schema_name")
        if not schema_name:
            continue

        def _sync_in_schema():
            for connection in list_enabled_connections():
                results.append(
                    sync_property_reservations(
                        int(connection["property_id"]),
                        triggered_by="scheduler",
                        client_factory=client_factory,
                    )
                )

        _run_in_schema(schema_name, _sync_in_schema)
    return results


def get_property_status(property_id: int) -> dict[str, Any]:
    return {
        "connection": get_connection(property_id),
        "latest_run": get_latest_sync_run(property_id),
        "recent_errors": list_recent_sync_errors(property_id),
    }


def get_property_mappings(property_id: int) -> list[dict[str, Any]]:
    return list_room_mappings(property_id)
