"""Booking orchestration for Hotelios.

The provider's flow is `Search → Quote → Create → Confirm`, and each arrow
carries a rule worth stating once rather than at every call site:

  * Search may be served from their cache, so its prices are indicative.
  * Quote is never cached and is mandatory before Create. Its `quote_id` is
    only guaranteed for an hour.
  * Create holds nothing. A successful Create is not a reservation.
  * Confirm is what sends the booking to the hotel, and it works exactly once.

The window between Create and Confirm is where a person pays us. Keeping it
short is the provider's explicit request, which is why abandoned drafts are
swept rather than left to expire.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from apps.hotels import raw_repository as repo
from apps.hotels.client import HoteliosClient, HoteliosError, get_client
from apps.hotels.models import HotelBookingStatus

logger = logging.getLogger(__name__)


def new_external_id() -> str:
    """A booking reference that is unique on Hotelios' side and ours.

    Hotelios accepts 5–60 characters and requires uniqueness; a hex uuid tail
    is short enough to read out over the phone and wide enough never to
    collide.
    """
    return f"WEEL-{uuid.uuid4().hex[:20].upper()}"


def search(
    *,
    check_in: str,
    check_out: str,
    occupancies: list[dict[str, Any]],
    currency: str = "uzs",
    city_id: int | None = None,
    hotel_ids: list[int] | None = None,
    nationality: str | None = None,
    residence: str | None = None,
    filters: dict[str, Any] | None = None,
    client: HoteliosClient | None = None,
) -> list[dict[str, Any]]:
    """Live availability for a stay. One entry per hotel, each with its options."""
    if not (city_id or hotel_ids):
        raise HoteliosError("A search needs either a city_id or a list of hotel_ids.")

    data: dict[str, Any] = {
        "check_in": check_in,
        "check_out": check_out,
        "occupancies": occupancies,
        "currency": currency,
    }
    if city_id is not None:
        data["city_id"] = city_id
    if hotel_ids:
        data["hotel_ids"] = hotel_ids
    if nationality:
        data["nationality"] = nationality
    if residence:
        data["residence"] = residence
    data.update({k: v for k, v in (filters or {}).items() if v not in (None, [], "")})

    return (client or get_client()).search(data)


def quote(option_ref_ids: list[str], *, client: HoteliosClient | None = None) -> dict[str, Any]:
    """Re-price the chosen options and open a quote to book against."""
    return (client or get_client()).quote(option_ref_ids)


def create_booking(
    *,
    quote_id: str,
    hotel_id: int | None,
    check_in: Any,
    check_out: Any,
    booking_rooms: list[dict[str, Any]],
    comment: str | None = None,
    delta_price: dict[str, Any] | None = None,
    nationality: str | None = None,
    residence: str | None = None,
    is_resident: bool = False,
    client: HoteliosClient | None = None,
    **ownership: Any,
) -> dict[str, Any]:
    """Hold rooms against a quote, recording our side first.

    The local row is written before the provider call so the `external_id` we
    send is one we already own. If Create then times out after Hotelios
    accepted it, the booking is still findable — `booking/read` takes an
    `external_id`, and `recover_booking` below uses exactly that.
    """
    client = client or get_client()

    booking = repo.create_draft_booking(
        external_id=new_external_id(),
        quote_id=quote_id,
        hotel_id=hotel_id,
        check_in=check_in,
        check_out=check_out,
        nationality=nationality,
        residence=residence,
        is_resident=is_resident,
        comment=comment,
        **ownership,
    )
    repo.replace_booking_rooms(booking_id=booking["id"], rooms=booking_rooms)

    try:
        result = client.create_booking(
            quote_id=quote_id,
            external_id=booking["external_id"],
            booking_rooms=[
                {
                    "option_ref_id": room["option_ref_id"],
                    "guests": room["guests"],
                    "price": room["price"],
                }
                for room in booking_rooms
            ],
            comment=comment,
            delta_price=delta_price,
        )
    except HoteliosError:
        # The call failed, but that does not prove nothing was created: a
        # timeout can arrive after Hotelios accepted the booking. Ask by
        # `external_id` before writing the attempt off, or the hold sits
        # upstream against our credit with nothing here pointing at it.
        recovered = None
        try:
            recovered = recover_booking(booking, client=client)
        except HoteliosError as recovery_error:
            logger.warning(
                "hotels: could not check whether %s was created — %s",
                booking["external_id"],
                recovery_error,
            )
        if recovered:
            logger.warning(
                "hotels: create failed in transport but %s exists upstream as %s.",
                booking["external_id"],
                recovered.get("provider_booking_id"),
            )
            repo.record_status_event(
                booking_id=booking["id"],
                status=HotelBookingStatus.DRAFT,
                source="api",
            )
            return recovered

        # Nothing is held upstream, so the draft is noise. Drop the status to
        # CANCELLED rather than deleting it: a failed attempt is worth seeing.
        repo.set_booking_status(booking_id=booking["id"], status=HotelBookingStatus.CANCELLED)
        repo.record_status_event(
            booking_id=booking["id"],
            status=HotelBookingStatus.CANCELLED,
            previous_status=HotelBookingStatus.DRAFT,
            source="api",
        )
        raise

    booking = repo.attach_provider_booking(
        booking_id=booking["id"],
        provider_booking_id=str(result.get("booking_id")),
        price=result.get("price"),
        currency=(booking_rooms[0].get("currency") if booking_rooms else None),
    )
    repo.record_status_event(
        booking_id=booking["id"],
        status=HotelBookingStatus.DRAFT,
        source="api",
        payload=result,
    )
    return booking


def confirm_booking(
    booking: dict[str, Any],
    *,
    client: HoteliosClient | None = None,
) -> dict[str, Any]:
    """Send a held booking to the hotel. Only ever valid once."""
    client = client or get_client()
    if not booking.get("provider_booking_id"):
        raise HoteliosError("This booking was never created with the provider.")
    if booking["status"] != HotelBookingStatus.DRAFT:
        raise HoteliosError(
            f"A booking in status '{booking['status']}' has already been confirmed."
        )

    client.confirm_booking(booking["provider_booking_id"])
    # Confirm answers with nothing but success; the hotel's own decision
    # (PENDING, WAIT_LIST or CONFIRMED) only shows up on a read. This is the
    # one read allowed to take the booking out of DRAFT.
    return refresh_booking(booking, client=client, source="api", preserve_draft=False)


def refresh_booking(
    booking: dict[str, Any],
    *,
    client: HoteliosClient | None = None,
    source: str = "poll",
    preserve_draft: bool = True,
) -> dict[str, Any]:
    """Re-read a booking from Hotelios and fold it into the local row.

    A booking we have not confirmed yet stays DRAFT no matter what the provider
    reports — see `update_booking_from_provider`.
    """
    client = client or get_client()
    payload = client.read_booking(
        booking_id=booking.get("provider_booking_id"),
        external_id=None if booking.get("provider_booking_id") else booking["external_id"],
    )
    previous = booking["status"]
    updated = repo.update_booking_from_provider(
        booking_id=booking["id"], payload=payload, preserve_draft=preserve_draft
    )
    if updated and updated["status"] != previous:
        repo.record_status_event(
            booking_id=booking["id"],
            status=updated["status"],
            previous_status=previous,
            source=source,
            payload=payload,
        )
    return updated or booking


def cancel_booking(
    booking: dict[str, Any],
    *,
    client: HoteliosClient | None = None,
    source: str = "api",
) -> dict[str, Any]:
    """Cancel with the provider. Whether it costs anything is the room's policy."""
    client = client or get_client()
    if not booking.get("provider_booking_id"):
        # Never reached Hotelios; closing the local row is the whole job.
        repo.record_status_event(
            booking_id=booking["id"],
            status=HotelBookingStatus.CANCELLED,
            previous_status=booking["status"],
            source=source,
        )
        return repo.set_booking_status(
            booking_id=booking["id"], status=HotelBookingStatus.CANCELLED
        )

    client.cancel_booking(booking["provider_booking_id"])
    repo.record_status_event(
        booking_id=booking["id"],
        status=HotelBookingStatus.CANCELLED,
        previous_status=booking["status"],
        source=source,
    )
    return repo.set_booking_status(
        booking_id=booking["id"], status=HotelBookingStatus.CANCELLED
    )


def recover_booking(
    booking: dict[str, Any],
    *,
    client: HoteliosClient | None = None,
) -> dict[str, Any] | None:
    """Find a draft upstream by `external_id` when Create left us unsure.

    Used when a Create call failed in transport: Hotelios may still have
    accepted it, and the only handle we kept is the external id we sent.
    Returns the updated booking, or None if the provider has no such booking.
    """
    client = client or get_client()
    try:
        payload = client.read_booking(external_id=booking["external_id"])
    except HoteliosError as exc:
        if exc.is_not_found:
            return None
        raise
    return repo.update_booking_from_provider(booking_id=booking["id"], payload=payload)
