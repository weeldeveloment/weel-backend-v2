from __future__ import annotations

import logging

from core.celery import app

logger = logging.getLogger(__name__)


@app.task(name="bookingcom.sync_reservations")
def sync_reservations():
    from apps.bookingcom.service import sync_all_enabled_reservations

    results = sync_all_enabled_reservations()
    logger.info("bookingcom.sync_reservations processed %s property sync(s)", len(results))
    return len(results)
