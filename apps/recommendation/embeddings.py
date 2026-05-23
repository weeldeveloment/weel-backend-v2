from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

import numpy as np

from shared.raw.db import fetch_all, fetch_one

from .models import EMBEDDING_DIM

logger = logging.getLogger(__name__)

SERVICE_LIST = [
    "wifi",
    "parking",
    "pool",
    "kitchen",
    "ac",
    "heater",
    "tv",
    "washing_machine",
    "balcony",
    "gym",
]

REGION_BUCKETS = 4
PRICE_BUCKETS = 3

FEATURE_DIM = EMBEDDING_DIM


def _normalize(vec: np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(vec)
    if norm == 0:
        return vec
    return vec / norm


def extract_client_features(client_id: int) -> np.ndarray:
    features = np.zeros(FEATURE_DIM, dtype=np.float32)

    booking_rows = fetch_all(
        """
        SELECT
            b.status,
            b.adults,
            b.children,
            b.babies,
            b.check_in,
            b.check_out,
            CASE
                WHEN b.property_apartment_id IS NOT NULL THEN 'apartment'
                WHEN b.property_cottage_id IS NOT NULL THEN 'cottage'
            END AS property_kind,
            COALESCE(a.guid, c.guid) AS property_guid,
            COALESCE(a.region_id, c.region_id) AS region_id,
            COALESCE(a.district_id, c.district_id) AS district_id,
            COALESCE(a.price, c.price_per_person) AS price,
            COALESCE(a.currency, c.currency) AS currency
        FROM booking b
        LEFT JOIN apartment a ON a.id = b.property_apartment_id
        LEFT JOIN cottage c ON c.id = b.property_cottage_id
        WHERE b.client_user_id = %s
        """,
        [client_id],
    )

    if not booking_rows:
        return _normalize(features)

    total_bookings = len(booking_rows)
    completed = sum(1 for r in booking_rows if r["status"] == "completed")
    cancelled = sum(1 for r in booking_rows if r["status"] == "cancelled")

    features[0] = sum(1 for r in booking_rows if r["property_kind"] == "apartment") / max(total_bookings, 1)
    features[1] = sum(1 for r in booking_rows if r["property_kind"] == "cottage") / max(total_bookings, 1)
    features[2] = completed / max(total_bookings, 1)
    features[3] = cancelled / max(total_bookings, 1)

    review_rows = fetch_all(
        """
        SELECT rating
        FROM review
        WHERE user_id = %s AND rating IS NOT NULL
        """,
        [client_id],
    )
    if review_rows:
        avg_rating = float(sum(float(r["rating"] or 0) for r in review_rows) / len(review_rows))
        features[4] = avg_rating / 5.0

    region_counts: dict[int, int] = {}
    price_values: list[float] = []
    weekend_bookings = 0

    for row in booking_rows:
        region_id = row.get("region_id")
        if region_id:
            region_counts[int(region_id)] = region_counts.get(int(region_id), 0) + 1

        price = row.get("price")
        if price is not None:
            price_val = float(price) if not isinstance(price, float) else price
            if row.get("currency") == "USD":
                price_val *= 12500
            price_values.append(price_val)

        check_in = row.get("check_in")
        if check_in:
            try:
                dow = int(str(check_in).split("-")[2]) if "-" in str(check_in) else 0
                if dow >= 5:
                    weekend_bookings += 1
            except (ValueError, IndexError):
                pass

    sorted_regions = sorted(region_counts.items(), key=lambda x: x[1], reverse=True)
    for i, (rid, count) in enumerate(sorted_regions[:REGION_BUCKETS]):
        features[5 + i] = count / max(total_bookings, 1)

    if price_values:
        avg_price = np.mean(price_values)
        median_price = np.median(price_values)
        price_std = np.std(price_values)
        features[9] = np.log1p(avg_price) / 20.0
        features[10] = np.log1p(median_price) / 20.0
        features[11] = min(price_std / max(avg_price, 1), 1.0)

    features[12] = weekend_bookings / max(total_bookings, 1)

    graph_rows = fetch_all(
        """
        SELECT predicate, object, weight
        FROM recommendation_graph
        WHERE client_id = %s AND predicate = 'viewed'
        ORDER BY created_at DESC
        LIMIT 50
        """,
        [client_id],
    )
    features[13] = min(len(graph_rows) / 50.0, 1.0)

    search_region = fetch_all(
        """
        SELECT object, COUNT(*) AS cnt
        FROM recommendation_graph
        WHERE client_id = %s AND predicate = 'search_region'
        GROUP BY object
        ORDER BY cnt DESC
        LIMIT 1
        """,
        [client_id],
    )
    if search_region:
        features[14] = int(search_region[0]["object"]) / 100.0

    search_min_price = fetch_all(
        """
        SELECT AVG(object::float) AS avg_min
        FROM recommendation_graph
        WHERE client_id = %s AND predicate = 'search_min_price'
        """,
        [client_id],
    )
    if search_min_price and search_min_price[0]["avg_min"]:
        features[15] = np.log1p(float(search_min_price[0]["avg_min"])) / 20.0

    search_max_price = fetch_all(
        """
        SELECT AVG(object::float) AS avg_max
        FROM recommendation_graph
        WHERE client_id = %s AND predicate = 'search_max_price'
        """,
        [client_id],
    )
    if search_max_price and search_max_price[0]["avg_max"]:
        features[16] = np.log1p(float(search_max_price[0]["avg_max"])) / 20.0

    search_type = fetch_all(
        """
        SELECT object, COUNT(*) AS cnt
        FROM recommendation_graph
        WHERE client_id = %s AND predicate = 'search_property_type'
        GROUP BY object
        ORDER BY cnt DESC
        LIMIT 1
        """,
        [client_id],
    )
    if search_type:
        stype = str(search_type[0]["object"]).lower()
        if "cottage" in stype:
            features[17] = 1.0
        elif "apartment" in stype:
            features[17] = 0.0

    return _normalize(features)


def extract_property_features(
    property_guid: str,
    property_kind: str = "apartment",
) -> np.ndarray:
    features = np.zeros(FEATURE_DIM, dtype=np.float32)

    if property_kind == "apartment":
        row = fetch_one(
            """
            SELECT
                a.id, a.region_id, a.district_id, a.price, a.currency,
                a.is_allowed_corporate,
                a.guests, a.rooms, a.beds, a.bathrooms,
                AVG(r.rating) AS avg_rating,
                COUNT(r.id) AS review_count
            FROM apartment a
            LEFT JOIN review r ON r.apartment_id = a.id
            WHERE a.guid = %s
            GROUP BY a.id
            """,
            [property_guid],
        )
    else:
        row = fetch_one(
            """
            SELECT
                c.id, c.region_id, c.district_id,
                c.price_per_person AS price, c.currency,
                c.is_allowed_corporate,
                c.guests, c.rooms, c.beds, c.bathrooms,
                AVG(r.rating) AS avg_rating,
                COUNT(r.id) AS review_count
            FROM cottage c
            LEFT JOIN review r ON r.cottage_id = c.id
            WHERE c.guid = %s
            GROUP BY c.id
            """,
            [property_guid],
        )

    if not row:
        return _normalize(features)

    features[0] = 1.0 if property_kind == "apartment" else 0.0
    features[1] = 1.0 if property_kind == "cottage" else 0.0

    region_id = row.get("region_id")
    if region_id:
        features[2] = (int(region_id) % 100) / 100.0

    district_id = row.get("district_id")
    if district_id:
        features[3] = (int(district_id) % 100) / 100.0

    price = row.get("price")
    if price is not None:
        price_val = float(price) if not isinstance(price, float) else price
        if row.get("currency") == "USD":
            price_val *= 12500
        features[4] = np.log1p(price_val) / 20.0

    features[5] = 1.0 if row.get("is_allowed_corporate") else 0.0

    guests = row.get("guests")
    if guests is not None:
        features[6] = min(int(guests) / 10.0, 1.0)

    rooms = row.get("rooms")
    if rooms is not None:
        features[7] = min(int(rooms) / 5.0, 1.0)

    beds = row.get("beds")
    if beds is not None:
        features[8] = min(int(beds) / 5.0, 1.0)

    bathrooms = row.get("bathrooms")
    if bathrooms is not None:
        features[9] = min(int(bathrooms) / 3.0, 1.0)

    avg_rating = row.get("avg_rating")
    if avg_rating is not None:
        features[10] = float(avg_rating) / 5.0

    review_count = row.get("review_count")
    if review_count is not None:
        features[11] = min(int(review_count) / 50.0, 1.0)

    if property_kind == "apartment":
        service_rows = fetch_all(
            """
            SELECT services
            FROM apartment
            WHERE guid = %s
            """,
            [property_guid],
        )
        if service_rows and service_rows[0].get("services"):
            service_guids = {str(s).lower() for s in service_rows[0]["services"]}
        else:
            service_guids = set()
    else:
        service_rows = fetch_all(
            """
            SELECT services
            FROM cottage
            WHERE guid = %s
            """,
            [property_guid],
        )
        if service_rows and service_rows[0].get("services"):
            service_guids = {str(s).lower() for s in service_rows[0]["services"]}
        else:
            service_guids = set()
    for i, svc in enumerate(SERVICE_LIST):
        if any(svc in g for g in service_guids):
            features[12 + i] = 1.0

    return _normalize(features)


def upsert_client_embedding(client_id: int, embedding: np.ndarray) -> None:
    from shared.raw.db import execute

    embedding_list = embedding.tolist()
    embedding_str = "[" + ",".join(f"{v:.6f}" for v in embedding_list) + "]"

    execute(
        """
        INSERT INTO client_embeddings (client_id, embedding, created_at, updated_at)
        VALUES (%s, %s::vector(64), NOW(), NOW())
        ON CONFLICT (client_id) DO UPDATE
        SET embedding = EXCLUDED.embedding, updated_at = NOW()
        """,
        [client_id, embedding_str],
    )


def upsert_property_embedding(
    property_guid: str,
    property_kind: str,
    embedding: np.ndarray,
) -> None:
    from shared.raw.db import execute

    embedding_list = embedding.tolist()
    embedding_str = "[" + ",".join(f"{v:.6f}" for v in embedding_list) + "]"

    execute(
        """
        INSERT INTO property_embeddings (property_guid, property_kind, embedding, created_at, updated_at)
        VALUES (%s, %s, %s::vector(64), NOW(), NOW())
        ON CONFLICT (property_guid) DO UPDATE
        SET property_kind = EXCLUDED.property_kind,
            embedding = EXCLUDED.embedding,
            updated_at = NOW()
        """,
        [property_guid, property_kind, embedding_str],
    )
