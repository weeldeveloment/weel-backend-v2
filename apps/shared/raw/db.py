from __future__ import annotations

from functools import lru_cache
from typing import Any

from django.db import connection


def _row_to_dict(cursor, row: tuple[Any, ...]) -> dict[str, Any]:
    return {desc[0]: row[idx] for idx, desc in enumerate(cursor.description)}


def fetch_one(sql: str, params: list[Any] | tuple[Any, ...] | None = None) -> dict[str, Any] | None:
    with connection.cursor() as cursor:
        cursor.execute(sql, params or [])
        row = cursor.fetchone()
        if row is None:
            return None
        return _row_to_dict(cursor, row)


def fetch_all(sql: str, params: list[Any] | tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(sql, params or [])
        rows = cursor.fetchall()
        return [_row_to_dict(cursor, row) for row in rows]


def execute(sql: str, params: list[Any] | tuple[Any, ...] | None = None) -> int:
    with connection.cursor() as cursor:
        cursor.execute(sql, params or [])
        return cursor.rowcount


@lru_cache(maxsize=128)
def table_exists(table_name: str, schema: str = "public") -> bool:
    row = fetch_one(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_schema = %s
              AND table_name = %s
        ) AS exists
        """,
        [schema, table_name],
    )
    return bool(row and row["exists"])

