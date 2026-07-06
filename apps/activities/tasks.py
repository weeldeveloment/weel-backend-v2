import logging
from datetime import timedelta

from django.utils import timezone

from core.celery import app

from .hold_service import HOLD_TTL_SECONDS, release_hold
from .raw_repository import expire_stale_pending_bookings

logger = logging.getLogger(__name__)


@app.task(
    name="activities.expire_stale_pending_bookings",
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def expire_stale_pending_bookings_task(self):
    """
    Unpaid `pending_payment` activity bookings past the Redis hold TTL never
    get an explicit cancel from the client (they just abandon checkout), so
    this sweeps them to `expired` and releases the resource's Redis hold —
    otherwise the slot would stay soft-blocked until the hold key's own TTL
    catches up (same window, but this makes the DB status authoritative too).
    """
    older_than = timezone.now() - timedelta(seconds=HOLD_TTL_SECONDS)
    expired = expire_stale_pending_bookings(older_than=older_than)

    for booking in expired:
        release_hold(
            resource_id=booking["resource_id"],
            starts_at=booking["starts_at"],
            blocked_until=booking["blocked_until"],
        )

    if expired:
        logger.info("activities: expired %d stale pending bookings", len(expired))

    return len(expired)
