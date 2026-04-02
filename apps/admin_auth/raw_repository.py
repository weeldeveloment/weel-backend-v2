from __future__ import annotations

import os
from typing import Any

from django.utils import timezone

from shared.raw.db import fetch_all, fetch_one
from shared.raw.entities import RawUser
from users.raw_repository import get_user_by_id


USER_TABLE = "public.users"
ALLOWED_ORDER_FIELDS = {"created_at", "first_name", "email", "username", "phone_number"}


def _row_to_user(row: dict[str, Any] | None) -> RawUser | None:
    if row is None:
        return None
    return RawUser.from_row(row)


def _parse_super_admin_ids() -> list[int]:
    raw = (os.getenv("ADMIN_SUPERUSER_IDS") or "").strip()
    if not raw:
        return []
    values: list[int] = []
    for part in raw.split(","):
        item = part.strip()
        if not item:
            continue
        if item.isdigit():
            values.append(int(item))
    return values


def is_super_admin(user_id: int) -> bool:
    configured = _parse_super_admin_ids()
    if configured:
        return user_id in configured

    first_admin = fetch_one(
        f"""
        SELECT id
        FROM {USER_TABLE}
        WHERE role = 'admin'
          AND is_active = TRUE
        ORDER BY id
        LIMIT 1
        """
    )
    if first_admin is None:
        return False
    return int(first_admin["id"]) == int(user_id)


def get_active_admin_by_email(email: str) -> RawUser | None:
    row = fetch_one(
        f"""
        SELECT *
        FROM {USER_TABLE}
        WHERE role = 'admin'
          AND is_active = TRUE
          AND LOWER(COALESCE(email, '')) = LOWER(%s)
        LIMIT 1
        """,
        [email],
    )
    return _row_to_user(row)


def get_active_admin_by_id(user_id: int) -> RawUser | None:
    return get_user_by_id(user_id, role="admin", active_only=True)


def exists_admin_email(email: str) -> bool:
    row = fetch_one(
        f"""
        SELECT EXISTS (
            SELECT 1
            FROM {USER_TABLE}
            WHERE role = 'admin'
              AND LOWER(COALESCE(email, '')) = LOWER(%s)
        ) AS exists
        """,
        [email],
    )
    return bool(row and row["exists"])


def exists_admin_username(username: str) -> bool:
    row = fetch_one(
        f"""
        SELECT EXISTS (
            SELECT 1
            FROM {USER_TABLE}
            WHERE role = 'admin'
              AND LOWER(COALESCE(username, '')) = LOWER(%s)
        ) AS exists
        """,
        [username],
    )
    return bool(row and row["exists"])


def create_admin_user(
    *,
    email: str,
    username: str,
    first_name: str = "",
    last_name: str = "",
) -> RawUser:
    now = timezone.now()
    row = fetch_one(
        f"""
        INSERT INTO {USER_TABLE} (
            role,
            email,
            phone_number,
            first_name,
            last_name,
            username,
            is_active,
            is_verified,
            created_at,
            updated_at
        ) VALUES (
            'admin',
            %s,
            NULL,
            %s,
            %s,
            %s,
            TRUE,
            TRUE,
            %s,
            %s
        )
        RETURNING *
        """,
        [email, first_name, last_name, username, now, now],
    )
    if row is None:
        raise RuntimeError("Failed to create admin user")
    return RawUser.from_row(row)


def make_unique_admin_username(base_username: str) -> str:
    normalized = (base_username or "admin").strip()
    if not normalized:
        normalized = "admin"

    candidate = normalized
    suffix = 1
    while exists_admin_username(candidate):
        suffix += 1
        candidate = f"{normalized}{suffix}"
    return candidate


def _build_search_clause(search: str | None, columns: list[str]) -> tuple[str, list[Any]]:
    term = (search or "").strip()
    if not term:
        return "", []
    like_value = f"%{term}%"
    parts = [f"COALESCE({column}::text, '') ILIKE %s" for column in columns]
    sql = " AND (" + " OR ".join(parts) + ")"
    return sql, [like_value] * len(parts)


def _normalize_ordering(ordering: str | None) -> tuple[str, str]:
    value = (ordering or "-created_at").strip()
    direction = "DESC"
    field = value
    if value.startswith("-"):
        direction = "DESC"
        field = value[1:]
    elif value.startswith("+"):
        direction = "ASC"
        field = value[1:]
    else:
        direction = "ASC"

    if field not in ALLOWED_ORDER_FIELDS:
        field = "created_at"
        direction = "DESC"
    return field, direction


def count_users_by_role(role: str, search: str | None = None, search_columns: list[str] | None = None) -> int:
    columns = search_columns or ["email", "first_name", "last_name", "phone_number", "username"]
    where = "role = %s"
    params: list[Any] = [role]
    search_sql, search_params = _build_search_clause(search, columns)
    params.extend(search_params)
    row = fetch_one(
        f"""
        SELECT COUNT(*)::int AS total
        FROM {USER_TABLE}
        WHERE {where}{search_sql}
        """,
        params,
    )
    return int(row["total"]) if row else 0


def list_users_by_role(
    *,
    role: str,
    search: str | None = None,
    ordering: str | None = None,
    limit: int = 20,
    offset: int = 0,
    search_columns: list[str] | None = None,
) -> list[RawUser]:
    columns = search_columns or ["email", "first_name", "last_name", "phone_number", "username"]
    order_field, order_direction = _normalize_ordering(ordering)

    params: list[Any] = [role]
    search_sql, search_params = _build_search_clause(search, columns)
    params.extend(search_params)
    params.extend([limit, offset])

    rows = fetch_all(
        f"""
        SELECT *
        FROM {USER_TABLE}
        WHERE role = %s{search_sql}
        ORDER BY {order_field} {order_direction}, id {order_direction}
        LIMIT %s OFFSET %s
        """,
        params,
    )
    return [RawUser.from_row(row) for row in rows]
