from __future__ import annotations

from functools import lru_cache
import json
import re
from collections.abc import Iterable
from typing import Any

from django.db import connection
from shared.raw.compat import is_postgresql, return_star

# Django's postgresql backend registers the jsonb/json psycopg2 typecasters
# with `loads=lambda x: x` (it decodes JSONField values itself), so raw
# cursor.execute() queries get these columns back as un-parsed JSON text
# instead of dicts/lists. Decode them ourselves using the OIDs psycopg2
# reports for the builtin json (114) and jsonb (3802) types.
_JSON_TYPE_CODES = {114, 3802}


def _row_to_dict(cursor, row: tuple[Any, ...]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for idx, desc in enumerate(cursor.description):
        value = row[idx]
        if isinstance(value, str) and desc.type_code in _JSON_TYPE_CODES:
            value = json.loads(value)
        out[desc[0]] = value
    return out


_ANY_MARKER_RE = re.compile(r"__ANY_MARKER__\(%s\)")
_TOKEN_RE = re.compile(r"=\s*__ANY_MARKER__\(%s\)|%s")


def _compile_sql(
    sql: str,
    params: list[Any] | tuple[Any, ...] | None,
) -> tuple[str, list[Any]]:
    compiled_sql = sql.replace(
        "__RETURNING_MARKER__",
        "RETURNING *" if return_star() else "",
    )
    values = list(params or [])

    if "__ANY_MARKER__(%s)" not in compiled_sql:
        return compiled_sql, values

    if is_postgresql():
        return _ANY_MARKER_RE.sub("ANY(%s)", compiled_sql), values

    compiled_params: list[Any] = []
    chunks: list[str] = []
    last_end = 0
    param_index = 0

    for match in _TOKEN_RE.finditer(compiled_sql):
        chunks.append(compiled_sql[last_end:match.start()])
        token = match.group(0)
        current_value = values[param_index] if param_index < len(values) else None
        param_index += 1

        if token == "%s":
            chunks.append("%s")
            compiled_params.append(current_value)
        else:
            if isinstance(current_value, Iterable) and not isinstance(
                current_value, (str, bytes, bytearray)
            ):
                marker_values = list(current_value)
            else:
                marker_values = [current_value]

            if marker_values:
                placeholders = ", ".join(["%s"] * len(marker_values))
                chunks.append(f"IN ({placeholders})")
                compiled_params.extend(marker_values)
            else:
                chunks.append("IN (NULL)")
        last_end = match.end()

    chunks.append(compiled_sql[last_end:])
    return "".join(chunks), compiled_params


def fetch_one(sql: str, params: list[Any] | tuple[Any, ...] | None = None) -> dict[str, Any] | None:
    with connection.cursor() as cursor:
        compiled_sql, compiled_params = _compile_sql(sql, params)
        cursor.execute(compiled_sql, compiled_params)
        row = cursor.fetchone()
        if row is None:
            return None
        return _row_to_dict(cursor, row)


def fetch_all(sql: str, params: list[Any] | tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        compiled_sql, compiled_params = _compile_sql(sql, params)
        cursor.execute(compiled_sql, compiled_params)
        rows = cursor.fetchall()
        return [_row_to_dict(cursor, row) for row in rows]


def execute(sql: str, params: list[Any] | tuple[Any, ...] | None = None) -> int:
    with connection.cursor() as cursor:
        compiled_sql, compiled_params = _compile_sql(sql, params)
        cursor.execute(compiled_sql, compiled_params)
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
