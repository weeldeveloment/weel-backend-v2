from __future__ import annotations

import logging

from shared.raw.db import fetch_one

from .graph import insert_triple, upsert_triple
from .tasks import rebuild_client_embedding, rebuild_property_embedding

logger = logging.getLogger(__name__)


def record_booking_created(client_id: int, property_guid: str) -> None:
    try:
        insert_triple(client_id, "booked", property_guid, weight=1.0)
        rebuild_client_embedding.delay(client_id)
    except Exception:
        logger.exception("Failed to record booking triple for client %d", client_id)


def record_booking_completed(client_id: int, property_guid: str) -> None:
    try:
        upsert_triple(client_id, "completed", property_guid, weight=1.0)
        rebuild_client_embedding.delay(client_id)
    except Exception:
        logger.exception(
            "Failed to record completed booking triple for client %d", client_id
        )


def record_review(client_id: int, property_guid: str, rating: float) -> None:
    try:
        upsert_triple(client_id, "reviewed", property_guid, weight=0.8)
        upsert_triple(client_id, "rated", str(rating), weight=rating / 5.0)
        rebuild_client_embedding.delay(client_id)
    except Exception:
        logger.exception("Failed to record review triple for client %d", client_id)


def record_favorite(client_id: int, property_guid: str) -> None:
    try:
        upsert_triple(client_id, "favorited", property_guid, weight=0.6)
        rebuild_client_embedding.delay(client_id)
    except Exception:
        logger.exception("Failed to record favorite triple for client %d", client_id)


def record_property_view(client_id: int, property_guid: str) -> None:
    try:
        upsert_triple(client_id, "viewed", property_guid, weight=0.2)
    except Exception:
        logger.exception("Failed to record view triple for client %d", client_id)


def trigger_property_rebuild(property_guid: str, property_kind: str = "apartment") -> None:
    try:
        rebuild_property_embedding.delay(property_guid, property_kind)
    except Exception:
        logger.exception(
            "Failed to trigger property embedding rebuild for %s", property_guid
        )
