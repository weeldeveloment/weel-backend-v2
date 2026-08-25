"""Booking orchestration for Bookhara avia.

The views on both surfaces — consumer and B2B — call into here rather than
touching the client directly, so that the ordering the provider requires
(re-price before paying, record what came back, remember the status we saw) is
written once.
"""

from __future__ import annotations

import logging
from typing import Any

from apps.avia import raw_repository as repo
from apps.avia.client import BookharaClient, BookharaError, get_client
from apps.avia.models import AviaBookingStatus

logger = logging.getLogger(__name__)


class PriceChangedError(BookharaError):
    """The order's price moved; the caller has to show it and ask again."""


def _persist(
    payload: dict[str, Any],
    *,
    source: str,
    offer_id: str | None = None,
    previous_status: str | None = None,
    **ownership: Any,
) -> dict[str, Any]:
    """Store a provider payload and note the status if it moved."""
    booking = repo.upsert_booking(payload, offer_id=offer_id, **ownership)
    new_status = booking["status"]
    if previous_status != new_status:
        repo.record_status_event(
            booking_id=booking["id"],
            status=new_status,
            previous_status=previous_status,
            source=source,
            payload=payload,
        )
    return booking


def create_booking(
    *,
    offer_id: str,
    payer_name: str,
    payer_email: str,
    payer_tel: str,
    passengers: list[dict[str, Any]],
    order_note: str | None = None,
    additional_services: list[str] | None = None,
    language: str | None = None,
    client: BookharaClient | None = None,
    **ownership: Any,
) -> dict[str, Any]:
    """Book an offer and record the resulting order.

    Bookhara refuses duplicate bookings for the same passengers and answers
    with the id of the order it considers the duplicate. That is not an error
    the caller can do anything about by retrying, so it is surfaced as-is —
    with the existing id attached — for the UI to show.
    """
    client = client or get_client(language=language)
    payload = client.create_booking(
        offer_id,
        payer_name=payer_name,
        payer_email=payer_email,
        payer_tel=payer_tel,
        passengers=passengers,
        order_note=order_note,
        additional_services=additional_services,
        language=language,
    )
    return _persist(payload, source="api", offer_id=offer_id, **ownership)


def refresh_booking(
    booking: dict[str, Any],
    *,
    language: str | None = None,
    client: BookharaClient | None = None,
    source: str = "poll",
) -> dict[str, Any]:
    """Re-read an order from Bookhara and update the local copy.

    A booking that has auto-cancelled upstream 404s. That is a real state
    change rather than a failure, so it is written down as `cancelled` instead
    of being raised at the caller.
    """
    client = client or get_client(language=language)
    provider_id = booking["provider_booking_id"]
    try:
        payload = client.get_booking(provider_id, language=language)
    except BookharaError as exc:
        if exc.status_code == 404:
            logger.info(
                "avia: booking %s is gone upstream (auto-cancelled); marking cancelled.",
                provider_id,
            )
            if booking["status"] != AviaBookingStatus.CANCELLED:
                repo.record_status_event(
                    booking_id=booking["id"],
                    status=AviaBookingStatus.CANCELLED,
                    previous_status=booking["status"],
                    source=source,
                )
            return repo.set_booking_status(
                booking_id=booking["id"], status=AviaBookingStatus.CANCELLED
            )
        raise
    return _persist(
        payload,
        source=source,
        offer_id=booking.get("offer_id"),
        previous_status=booking["status"],
    )


def pay_booking(
    booking: dict[str, Any],
    *,
    language: str | None = None,
    client: BookharaClient | None = None,
) -> dict[str, Any]:
    """Pay for an order from the Bookhara deposit.

    The provider wants the price re-checked first: if it moved while the order
    sat unpaid, paying would charge the new amount without anyone having agreed
    to it. So a changed price stops here and the caller re-reads the booking.

    A successful payment lands on `paid` or `ticketed`. Ticket numbers only
    exist in the second case, and issuing can take ten minutes, so the order is
    re-read once here and then left to the polling task.
    """
    client = client or get_client(language=language)
    provider_id = booking["provider_booking_id"]

    price_check = client.check_booking_price(provider_id)
    if price_check.get("is_price_changed"):
        refreshed = refresh_booking(booking, language=language, client=client, source="api")
        raise PriceChangedError(
            "The price of this booking has changed. Please review it before paying.",
            error_code=100500,
            data={"amount": refreshed.get("amount"), "currency": refreshed.get("currency")},
        )

    permission = client.payment_permission(provider_id)
    if not permission.get("payment_allowed", True):
        raise BookharaError(
            "This booking cannot be paid for right now.",
            data=permission,
        )

    result = client.pay_booking(provider_id)

    fiscalization = result.get("fiscalization_v2")
    if fiscalization:
        repo.set_booking_fiscalization(booking_id=booking["id"], fiscalization=fiscalization)

    status = result.get("status")
    if status:
        repo.record_status_event(
            booking_id=booking["id"],
            status=status,
            previous_status=booking["status"],
            source="api",
            payload=result,
        )
        booking = repo.set_booking_status(booking_id=booking["id"], status=status)

    # Pull the ticket numbers if the carrier issued them immediately.
    return refresh_booking(booking, language=language, client=client, source="api")


def _terminate(
    booking: dict[str, Any],
    operation: str,
    *,
    language: str | None,
    client: BookharaClient | None,
) -> dict[str, Any]:
    """Run one of the cancel/refund calls and record the status it returns."""
    client = client or get_client(language=language)
    result = getattr(client, operation)(booking["provider_booking_id"])
    status = result.get("status")
    if status:
        repo.record_status_event(
            booking_id=booking["id"],
            status=status,
            previous_status=booking["status"],
            source="api",
            payload=result,
        )
        return repo.set_booking_status(booking_id=booking["id"], status=status)
    return refresh_booking(booking, language=language, client=client, source="api")


def cancel_unpaid(booking, *, language=None, client=None) -> dict[str, Any]:
    """Drop an unpaid `booked` order — no money has moved, so no penalty."""
    return _terminate(booking, "cancel_unpaid", language=language, client=client)


def void(booking, *, language=None, client=None) -> dict[str, Any]:
    """Full refund of a paid order, with no penalty. Not always available."""
    return _terminate(booking, "void", language=language, client=client)


def auto_cancel(booking, *, language=None, client=None) -> dict[str, Any]:
    """Refund an issued ticket, accepting the airline's penalty."""
    return _terminate(booking, "auto_cancel", language=language, client=client)


def manual_refund(booking, *, language=None, client=None) -> dict[str, Any]:
    """Hand the cancellation to Bookhara's call centre when nothing else applies."""
    return _terminate(booking, "manual_refund", language=language, client=client)


def fetch_receipts(
    booking: dict[str, Any],
    *,
    client: BookharaClient | None = None,
) -> list[dict[str, Any]]:
    """Itinerary receipts, one per passenger. Only for `ticketed` orders."""
    client = client or get_client()
    receipts = client.get_pdf_receipt(booking["provider_booking_id"])
    for receipt in receipts:
        key = receipt.get("key")
        if key:
            repo.set_passenger_receipt(
                booking_id=booking["id"],
                passenger_key=key,
                url=receipt.get("itinerary_receipt"),
            )
    return receipts


def fetch_fiscalization(
    booking: dict[str, Any],
    *,
    client: BookharaClient | None = None,
) -> dict[str, Any]:
    """Fiscal receipt data for a paid or ticketed order."""
    client = client or get_client()
    result = client.get_fiscalization(booking["provider_booking_id"])
    fiscalization = result.get("fiscalization_v2")
    if fiscalization:
        repo.set_booking_fiscalization(booking_id=booking["id"], fiscalization=fiscalization)
    return result
