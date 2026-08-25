from __future__ import annotations

import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

from apps.hotels import raw_repository as repo
from apps.hotels import service, sync
from apps.hotels.client import HoteliosError, get_client

logger = logging.getLogger(__name__)

# Hotelios guarantees a quote for an hour and asks that Create and Confirm stay
# close together. Two hours is comfortably past any real payment attempt, so a
# draft still open by then has been abandoned.
DRAFT_ABANDONED_AFTER = timedelta(hours=2)


@shared_task(name="hotels.sync_inventory")
def sync_inventory() -> list[dict]:
    """The nightly catalogue import: references, geography, hotels, room types."""
    return sync.sync_all()


@shared_task(name="hotels.sync_references")
def sync_references() -> dict:
    return sync.sync_references()


@shared_task(name="hotels.sync_hotels")
def sync_hotels() -> dict:
    return sync.sync_hotels()


@shared_task(name="hotels.sync_room_types")
def sync_room_types() -> dict:
    return sync.sync_room_types()


@shared_task(name="hotels.poll_booking_statuses")
def poll_booking_statuses(limit: int = 200) -> dict[str, int]:
    """Chase bookings the hotel has not settled yet.

    A confirmed booking goes to the hotel as PENDING and becomes CONFIRMED —
    or WAIT_LIST, or CANCELLED — on the hotel's own schedule. Hotelios has no
    callback for this, so the only way to know is to ask.
    """
    pending = repo.fetch_live_bookings(limit=limit)
    if not pending:
        return {"checked": 0, "advanced": 0}

    client = get_client()
    advanced = 0
    for booking in pending:
        try:
            refreshed = service.refresh_booking(booking, client=client, source="poll")
        except HoteliosError as exc:
            logger.warning(
                "hotels: could not refresh booking %s — %s",
                booking["provider_booking_id"],
                exc,
            )
            continue
        if refreshed and refreshed["status"] != booking["status"]:
            advanced += 1

    logger.info("hotels: polled %d live bookings, %d advanced.", len(pending), advanced)
    return {"checked": len(pending), "advanced": advanced}


@shared_task(name="hotels.release_abandoned_drafts")
def release_abandoned_drafts(limit: int = 200) -> dict[str, int]:
    """Cancel holds that were created upstream but never paid for.

    Left alone these sit against our credit limit and, depending on the hotel,
    against its availability. Cancelling an unconfirmed booking costs nothing.
    """
    cutoff = timezone.now() - DRAFT_ABANDONED_AFTER
    drafts = repo.fetch_stale_drafts(older_than=cutoff, limit=limit)
    if not drafts:
        return {"released": 0}

    client = get_client()
    released = 0
    for draft in drafts:
        try:
            service.cancel_booking(draft, client=client, source="poll")
            released += 1
        except HoteliosError as exc:
            logger.warning(
                "hotels: could not release draft %s — %s", draft["external_id"], exc
            )

    logger.info("hotels: released %d abandoned drafts.", released)
    return {"released": released}
