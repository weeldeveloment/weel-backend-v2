from __future__ import annotations

import logging

from celery import shared_task

from apps.avia import raw_repository as repo
from apps.avia import service
from apps.avia.client import BookharaError, get_client

logger = logging.getLogger(__name__)


@shared_task(name="avia.poll_ticketing_status")
def poll_ticketing_status(limit: int = 100) -> dict[str, int]:
    """Chase paid orders until their ticket numbers arrive.

    Payment answers `paid` or `ticketed`; when it answers `paid`, the carrier
    is still issuing and can take about ten minutes. Bookhara will push a
    status callback if one is registered for the account, but that is
    configured on their side and can be missing or lost — so the orders are
    polled as well. Re-reading is idempotent, so the two cannot conflict.
    """
    pending = repo.fetch_bookings_awaiting_tickets(limit=limit)
    if not pending:
        return {"checked": 0, "advanced": 0}

    client = get_client()
    advanced = 0
    for booking in pending:
        try:
            refreshed = service.refresh_booking(booking, client=client, source="poll")
        except BookharaError as exc:
            logger.warning(
                "avia: could not refresh booking %s — %s",
                booking["provider_booking_id"],
                exc,
            )
            continue
        if refreshed and refreshed["status"] != booking["status"]:
            advanced += 1

    logger.info("avia: polled %d pending orders, %d advanced.", len(pending), advanced)
    return {"checked": len(pending), "advanced": advanced}
