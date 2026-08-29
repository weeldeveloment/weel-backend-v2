"""Persistence for Bookhara avia bookings.

Bookhara is the system of record for an order — we never invent a status or a
price here. What these functions do is keep a queryable local copy so the
dashboard can list a company's tickets, a trip can find its flights, and the
polling task can find the orders still waiting on ticket numbers, without
fanning out to the provider for every screen.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from django.utils import timezone

from shared.raw.db import execute, fetch_all, fetch_one

from apps.avia.models import AviaBookingStatus
from apps.avia.raw.tables import (
    AVIA_BOOKING_EVENT_TABLE,
    AVIA_BOOKING_PASSENGER_TABLE,
    AVIA_BOOKING_TABLE,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Coercion helpers
#
# Bookhara sends naive UTC strings and JSON numbers; the columns want aware
# timestamps and Decimals. Every one of these returns None rather than raising,
# because a booking that exists upstream must still be recorded locally even if
# one optional field arrives in a shape we did not expect.
# ---------------------------------------------------------------------------

_DATETIME_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M")


def _to_json(value: Any) -> str:
    return json.dumps(value if value is not None else None, ensure_ascii=False, default=str)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if timezone.is_aware(value) else value.replace(tzinfo=dt_timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=dt_timezone.utc)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("avia: could not parse datetime %r", value)
        return None
    return parsed if timezone.is_aware(parsed) else parsed.replace(tzinfo=dt_timezone.utc)


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.strptime(value.strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        logger.warning("avia: could not parse date %r", value)
        return None


def _parse_amount(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        logger.warning("avia: could not parse amount %r", value)
        return None


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------

def upsert_booking(
    payload: dict[str, Any],
    *,
    offer_id: str | None = None,
    client_user_id: int | None = None,
    b2b_company_id: int | None = None,
    b2b_user_id: int | None = None,
    b2b_trip_id: int | None = None,
    b2b_employee_id: int | None = None,
    preserve_refund_request: bool = True,
) -> dict[str, Any]:
    """Write a Bookhara booking payload into `avia_booking`, creating or updating.

    Ownership columns are only ever *set*, never cleared: a re-read of the
    booking has no idea which of our users it belongs to, so passing None for
    them on refresh leaves whatever the create call recorded.

    `refund_request_sent` is held onto for the same reason it exists: Bookhara
    says it once, in the reply to `manual-refund`, and every read afterwards
    goes back to reporting `ticketed` until the call centre has acted. Letting
    a refresh overwrite it would put the booking back in front of the customer
    as refundable and invite a second request against a provider that rate
    limits them. Pass False only where the provider's answer really is the
    whole truth.
    """
    price = payload.get("price") or {}
    payer = payload.get("payer") or {}

    row = fetch_one(
        f"""
        INSERT INTO {AVIA_BOOKING_TABLE} (
            provider_booking_id, booking_number, offer_id, status, offer_type,
            flight_type, fare_family_type, is_charter, refund_availability,
            amount, prev_amount, currency,
            payer_name, payer_email, payer_tel,
            client_user_id, b2b_company_id, b2b_user_id, b2b_trip_id, b2b_employee_id,
            provider_created_at, expires_at,
            directions, information_for_clients, additional_services, raw
        ) VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s,
            %s, %s, %s, %s
        )
        ON CONFLICT (provider_booking_id) DO UPDATE SET
            booking_number = EXCLUDED.booking_number,
            offer_id = COALESCE(EXCLUDED.offer_id, {AVIA_BOOKING_TABLE}.offer_id),
            status = CASE
                WHEN %s
                     AND {AVIA_BOOKING_TABLE}.status = %s
                     AND EXCLUDED.status <> ALL(%s)
                THEN {AVIA_BOOKING_TABLE}.status
                ELSE EXCLUDED.status
            END,
            offer_type = EXCLUDED.offer_type,
            flight_type = EXCLUDED.flight_type,
            fare_family_type = EXCLUDED.fare_family_type,
            is_charter = EXCLUDED.is_charter,
            refund_availability = EXCLUDED.refund_availability,
            amount = EXCLUDED.amount,
            prev_amount = EXCLUDED.prev_amount,
            currency = EXCLUDED.currency,
            payer_name = EXCLUDED.payer_name,
            payer_email = EXCLUDED.payer_email,
            payer_tel = EXCLUDED.payer_tel,
            client_user_id = COALESCE({AVIA_BOOKING_TABLE}.client_user_id, EXCLUDED.client_user_id),
            b2b_company_id = COALESCE({AVIA_BOOKING_TABLE}.b2b_company_id, EXCLUDED.b2b_company_id),
            b2b_user_id = COALESCE({AVIA_BOOKING_TABLE}.b2b_user_id, EXCLUDED.b2b_user_id),
            b2b_trip_id = COALESCE({AVIA_BOOKING_TABLE}.b2b_trip_id, EXCLUDED.b2b_trip_id),
            b2b_employee_id = COALESCE({AVIA_BOOKING_TABLE}.b2b_employee_id, EXCLUDED.b2b_employee_id),
            provider_created_at = EXCLUDED.provider_created_at,
            expires_at = EXCLUDED.expires_at,
            directions = EXCLUDED.directions,
            information_for_clients = EXCLUDED.information_for_clients,
            additional_services = EXCLUDED.additional_services,
            raw = EXCLUDED.raw,
            updated_at = NOW()
        RETURNING *
        """,
        [
            payload.get("id"),
            payload.get("booking_number"),
            offer_id,
            payload.get("status") or AviaBookingStatus.BOOKED,
            payload.get("type"),
            payload.get("flight_type"),
            payload.get("fare_family_type"),
            bool(payload.get("is_charter")),
            bool(payload.get("refund_availability")),
            _parse_amount(price.get("amount")),
            _parse_amount(price.get("prev_amount")),
            price.get("currency"),
            payer.get("name"),
            payer.get("email"),
            payer.get("tel"),
            client_user_id,
            b2b_company_id,
            b2b_user_id,
            b2b_trip_id,
            b2b_employee_id,
            _parse_datetime(payload.get("created")),
            _parse_datetime(payload.get("expire")),
            _to_json(payload.get("directions") or []),
            _to_json(payload.get("information_for_clients") or []),
            _to_json(payload.get("additional_services")),
            _to_json(payload),
            # The three parameters of the status CASE above. They sit here
            # because psycopg binds by position and the ON CONFLICT clause
            # follows VALUES in the statement.
            preserve_refund_request,
            AviaBookingStatus.REFUND_REQUEST_SENT,
            sorted(AviaBookingStatus.REFUND_SETTLED),
        ],
    )

    _upsert_passengers(booking_id=row["id"], passengers=payload.get("passengers") or [])
    return row


def _upsert_passengers(*, booking_id: int, passengers: list[dict[str, Any]]) -> None:
    for passenger in passengers:
        document = passenger.get("document") or {}
        extended = passenger.get("extended_price") or {}
        execute(
            f"""
            INSERT INTO {AVIA_BOOKING_PASSENGER_TABLE} (
                booking_id, passenger_key, first_name, last_name, middle_name,
                age_group, gender, birthdate, citizenship, email, tel,
                doc_type, doc_number, doc_expire, price, tickets
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s
            )
            ON CONFLICT (booking_id, passenger_key) DO UPDATE SET
                first_name = EXCLUDED.first_name,
                last_name = EXCLUDED.last_name,
                middle_name = EXCLUDED.middle_name,
                age_group = EXCLUDED.age_group,
                gender = EXCLUDED.gender,
                birthdate = EXCLUDED.birthdate,
                citizenship = EXCLUDED.citizenship,
                email = EXCLUDED.email,
                tel = EXCLUDED.tel,
                doc_type = EXCLUDED.doc_type,
                doc_number = EXCLUDED.doc_number,
                doc_expire = EXCLUDED.doc_expire,
                price = EXCLUDED.price,
                tickets = EXCLUDED.tickets,
                updated_at = NOW()
            """,
            [
                booking_id,
                passenger.get("key"),
                passenger.get("first_name") or "",
                passenger.get("last_name") or "",
                passenger.get("middle_name"),
                passenger.get("age") or "adt",
                passenger.get("gender"),
                _parse_date(passenger.get("birthdate")),
                passenger.get("citizenship"),
                passenger.get("email"),
                passenger.get("tel"),
                document.get("type"),
                document.get("number"),
                _parse_date(document.get("expire")),
                _parse_amount(passenger.get("price") or extended.get("amount")),
                _to_json(passenger.get("tickets") or []),
            ],
        )


def set_booking_status(*, booking_id: int, status: str) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        UPDATE {AVIA_BOOKING_TABLE}
        SET status = %s, updated_at = NOW()
        WHERE id = %s
        RETURNING *
        """,
        [status, booking_id],
    )


def set_booking_fiscalization(*, booking_id: int, fiscalization: Any) -> None:
    execute(
        f"""
        UPDATE {AVIA_BOOKING_TABLE}
        SET fiscalization = %s, updated_at = NOW()
        WHERE id = %s
        """,
        [_to_json(fiscalization), booking_id],
    )


def set_passenger_receipt(*, booking_id: int, passenger_key: str, url: str | None) -> None:
    execute(
        f"""
        UPDATE {AVIA_BOOKING_PASSENGER_TABLE}
        SET itinerary_receipt_url = %s, updated_at = NOW()
        WHERE booking_id = %s AND passenger_key = %s
        """,
        [url, booking_id, passenger_key],
    )


def record_status_event(
    *,
    booking_id: int,
    status: str,
    previous_status: str | None = None,
    source: str = "api",
    payload: Any = None,
) -> None:
    execute(
        f"""
        INSERT INTO {AVIA_BOOKING_EVENT_TABLE}
            (booking_id, previous_status, status, source, payload)
        VALUES (%s, %s, %s, %s, %s)
        """,
        [booking_id, previous_status, status, source, _to_json(payload)],
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def fetch_booking(booking_id: int) -> dict[str, Any] | None:
    return fetch_one(f"SELECT * FROM {AVIA_BOOKING_TABLE} WHERE id = %s", [booking_id])


def fetch_booking_by_guid(guid: str) -> dict[str, Any] | None:
    return fetch_one(f"SELECT * FROM {AVIA_BOOKING_TABLE} WHERE guid = %s", [guid])


def fetch_booking_by_provider_id(provider_booking_id: str) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT * FROM {AVIA_BOOKING_TABLE} WHERE provider_booking_id = %s",
        [provider_booking_id],
    )


def fetch_passengers(booking_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT * FROM {AVIA_BOOKING_PASSENGER_TABLE}
        WHERE booking_id = %s
        ORDER BY id
        """,
        [booking_id],
    )


def fetch_bookings_for_client(*, client_user_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT * FROM {AVIA_BOOKING_TABLE}
        WHERE client_user_id = %s
        ORDER BY created_at DESC
        """,
        [client_user_id],
    )


def fetch_bookings_for_company(
    *,
    b2b_company_id: int,
    trip_id: int | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    conditions = ["b2b_company_id = %s"]
    params: list[Any] = [b2b_company_id]
    if trip_id is not None:
        conditions.append("b2b_trip_id = %s")
        params.append(trip_id)
    if status:
        conditions.append("status = %s")
        params.append(status)
    return fetch_all(
        f"""
        SELECT * FROM {AVIA_BOOKING_TABLE}
        WHERE {' AND '.join(conditions)}
        ORDER BY created_at DESC
        """,
        params,
    )


def fetch_bookings_awaiting_tickets(*, limit: int = 100) -> list[dict[str, Any]]:
    """Paid orders whose ticket numbers have not arrived yet.

    Issuing takes up to ten minutes, so these are re-read on a schedule until
    they reach `ticketed` (or the airline fails them out).
    """
    return fetch_all(
        f"""
        SELECT * FROM {AVIA_BOOKING_TABLE}
        WHERE status = __ANY_MARKER__(%s)
        ORDER BY updated_at ASC
        LIMIT %s
        """,
        [sorted(AviaBookingStatus.AWAITING_TICKETS), limit],
    )


def fetch_status_events(*, booking_id: int, limit: int = 50) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT * FROM {AVIA_BOOKING_EVENT_TABLE}
        WHERE booking_id = %s
        ORDER BY created_at DESC
        LIMIT %s
        """,
        [booking_id, limit],
    )
