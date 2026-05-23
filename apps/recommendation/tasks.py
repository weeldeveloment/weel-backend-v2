from __future__ import annotations

import logging

from celery import shared_task

from .embeddings import (
    extract_client_features,
    extract_property_features,
    upsert_client_embedding,
    upsert_property_embedding,
)

logger = logging.getLogger(__name__)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def rebuild_client_embedding(self, client_id: int) -> None:
    try:
        features = extract_client_features(client_id)
        upsert_client_embedding(client_id, features)
        logger.info("Rebuilt embedding for client %d", client_id)
    except Exception as exc:
        logger.exception("Failed to rebuild embedding for client %d", client_id)
        raise self.retry(exc=exc)


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def rebuild_property_embedding(
    self,
    property_guid: str,
    property_kind: str = "apartment",
) -> None:
    try:
        features = extract_property_features(property_guid, property_kind)
        upsert_property_embedding(property_guid, property_kind, features)
        logger.info("Rebuilt embedding for property %s (%s)", property_guid, property_kind)
    except Exception as exc:
        logger.exception(
            "Failed to rebuild embedding for property %s", property_guid
        )
        raise self.retry(exc=exc)


@shared_task
def rebuild_all_client_embeddings() -> None:
    from shared.raw.db import fetch_all

    rows = fetch_all(
        """
        SELECT DISTINCT client_user_id AS client_id
        FROM booking
        WHERE client_user_id IS NOT NULL
        """
    )
    for row in rows:
        rebuild_client_embedding.delay(int(row["client_id"]))
    logger.info("Queued embedding rebuild for %d clients", len(rows))


@shared_task
def rebuild_all_property_embeddings() -> None:
    from shared.raw.db import fetch_all

    apartment_rows = fetch_all(
        """
        SELECT guid FROM apartment WHERE is_verified = TRUE AND is_archived = FALSE
        """
    )
    for row in apartment_rows:
        rebuild_property_embedding.delay(str(row["guid"]), "apartment")

    cottage_rows = fetch_all(
        """
        SELECT guid FROM cottage WHERE is_verified = TRUE AND is_archived = FALSE
        """
    )
    for row in cottage_rows:
        rebuild_property_embedding.delay(str(row["guid"]), "cottage")

    logger.info(
        "Queued embedding rebuild for %d apartments and %d cottages",
        len(apartment_rows),
        len(cottage_rows),
    )
