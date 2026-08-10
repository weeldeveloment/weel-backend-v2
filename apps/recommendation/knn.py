from __future__ import annotations

from typing import Any

from shared.raw.db import fetch_all, fetch_one

from .graph import get_booked_properties


def get_personalized_recommendations(
    client_id: int,
    property_kind: str = "apartment",
    limit: int = 20,
    from_date: str | None = None,
) -> list[dict[str, Any]]:
    booked = get_booked_properties(client_id)

    exclude_clause = ""
    params: list[Any] = [client_id, property_kind, limit]

    if booked:
        placeholders = ", ".join(["%s"] * len(booked))
        exclude_clause = f"AND pe.property_guid NOT IN ({placeholders})"
        params = [client_id, property_kind] + booked + [limit]

    return fetch_all(
        f"""
        SELECT
            pe.property_guid,
            pe.property_kind,
            (1 - (ce.embedding <=> pe.embedding)) AS similarity
        FROM property_embeddings pe
        CROSS JOIN (
            SELECT embedding FROM client_embeddings WHERE client_id = %s
        ) ce
        WHERE pe.property_kind = %s
          AND pe.embedding IS NOT NULL
          {exclude_clause}
        ORDER BY ce.embedding <=> pe.embedding
        LIMIT %s
        """,
        params,
    )


def get_property_embedding(property_guid: str) -> list[float] | None:
    row = fetch_one(
        """
        SELECT embedding FROM property_embeddings WHERE property_guid = %s
        """,
        [property_guid],
    )
    if row and row.get("embedding"):
        return _parse_vector(row["embedding"])
    return None


def get_client_embedding(client_id: int) -> list[float] | None:
    row = fetch_one(
        """
        SELECT embedding FROM client_embeddings WHERE client_id = %s
        """,
        [client_id],
    )
    if row and row.get("embedding"):
        return _parse_vector(row["embedding"])
    return None


def _parse_vector(value: Any) -> list[float]:
    if isinstance(value, str):
        cleaned = value.strip("[]")
        return [float(x.strip()) for x in cleaned.split(",") if x.strip()]
    if isinstance(value, (list, tuple)):
        return [float(x) for x in value]
    return []
