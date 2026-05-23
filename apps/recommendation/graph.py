from __future__ import annotations

from typing import Any

from shared.raw.db import execute, fetch_all, fetch_one


def upsert_triple(
    client_id: int,
    predicate: str,
    obj: str,
    weight: float = 1.0,
) -> None:
    execute(
        """
        INSERT INTO recommendation_graph (client_id, predicate, object, weight, created_at, updated_at)
        VALUES (%s, %s, %s, %s, NOW(), NOW())
        ON CONFLICT DO NOTHING
        """,
        [client_id, predicate, obj, weight],
    )


def insert_triple(
    client_id: int,
    predicate: str,
    obj: str,
    weight: float = 1.0,
) -> None:
    execute(
        """
        INSERT INTO recommendation_graph (client_id, predicate, object, weight, created_at, updated_at)
        VALUES (%s, %s, %s, %s, NOW(), NOW())
        """,
        [client_id, predicate, obj, weight],
    )


def update_triple_weight(
    client_id: int,
    predicate: str,
    obj: str,
    weight: float,
) -> None:
    execute(
        """
        UPDATE recommendation_graph
        SET weight = %s, updated_at = NOW()
        WHERE client_id = %s AND predicate = %s AND object = %s
        """,
        [weight, client_id, predicate, obj],
    )


def delete_triple(
    client_id: int,
    predicate: str,
    obj: str,
) -> None:
    execute(
        """
        DELETE FROM recommendation_graph
        WHERE client_id = %s AND predicate = %s AND object = %s
        """,
        [client_id, predicate, obj],
    )


def get_client_triples(client_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT predicate, object, weight, created_at
        FROM recommendation_graph
        WHERE client_id = %s
        ORDER BY weight DESC
        """,
        [client_id],
    )


def get_triples_by_predicate(client_id: int, predicate: str) -> list[dict[str, Any]]:
    return fetch_all(
        """
        SELECT object, weight, created_at
        FROM recommendation_graph
        WHERE client_id = %s AND predicate = %s
        ORDER BY weight DESC
        """,
        [client_id, predicate],
    )


def get_booked_properties(client_id: int) -> list[str]:
    rows = fetch_all(
        """
        SELECT DISTINCT object
        FROM recommendation_graph
        WHERE client_id = %s AND predicate IN ('booked', 'completed')
        """,
        [client_id],
    )
    return [row["object"] for row in rows]


def record_search(
    client_id: int,
    search_params: dict[str, str],
) -> None:
    predicates = {
        "search_region": "region_id",
        "search_district": "district_id",
        "search_prefecture": "prefecture_id",
        "search_min_price": "min_price",
        "search_max_price": "max_price",
        "search_property_type": "property_type",
        "search_currency": "currency",
        "search_sort": "sort",
    }

    for predicate, param_key in predicates.items():
        value = search_params.get(param_key)
        if value:
            insert_triple(client_id, predicate, str(value), weight=0.3)
