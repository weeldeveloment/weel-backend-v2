from __future__ import annotations

import hashlib
import secrets
from datetime import datetime
from typing import Any
from uuid import uuid4

from django.db import connection
from django.utils import timezone

from shared.raw.db import execute, fetch_all, fetch_one, table_exists
from shared.raw.tables import USER_TABLE
from apps.platform.raw.tables import (
    ORGANIZATION_TABLE,
    PLATFORM_USER_TABLE,
    ORGANIZATION_MEMBER_TABLE,
)


def _table(name: str) -> str:
    if table_exists(name):
        return name
    return name


def create_organization(
    *,
    name: str,
    slug: str,
    schema_name: str,
) -> dict[str, Any] | None:
    now = timezone.now()
    result = fetch_one(
        f"""
        INSERT INTO {_table(ORGANIZATION_TABLE)}
            (name, slug, schema_name, is_active, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        [name, slug, schema_name, True, now, now],
    )
    if result:
        from core.middleware.tenant import invalidate_org_schema_cache
        invalidate_org_schema_cache(result["id"])
    return result


def get_organization_by_slug(slug: str) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        SELECT * FROM {_table(ORGANIZATION_TABLE)}
        WHERE slug = %s AND is_active = TRUE
        LIMIT 1
        """,
        [slug],
    )


def get_organization_by_id(org_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        SELECT * FROM {_table(ORGANIZATION_TABLE)}
        WHERE id = %s AND is_active = TRUE
        LIMIT 1
        """,
        [org_id],
    )


def get_organization_by_schema(schema_name: str) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        SELECT * FROM {_table(ORGANIZATION_TABLE)}
        WHERE schema_name = %s AND is_active = TRUE
        LIMIT 1
        """,
        [schema_name],
    )


def list_organizations() -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT * FROM {_table(ORGANIZATION_TABLE)}
        WHERE is_active = TRUE
        ORDER BY created_at DESC
        """
    )


def update_organization(org_id: int, **kwargs: Any) -> dict[str, Any] | None:
    if not kwargs:
        return get_organization_by_id(org_id)

    sets = ", ".join(f"{k} = %s" for k in kwargs)
    values = list(kwargs.values())
    values.append(timezone.now())
    values.append(org_id)

    result = fetch_one(
        f"""
        UPDATE {_table(ORGANIZATION_TABLE)}
        SET {sets}, updated_at = %s
        WHERE id = %s
        RETURNING *
        """,
        values,
    )
    if result:
        from core.middleware.tenant import invalidate_org_schema_cache
        invalidate_org_schema_cache(org_id)
    return result


def deactivate_organization(org_id: int) -> bool:
    result = execute(
        f"""
        UPDATE {_table(ORGANIZATION_TABLE)}
        SET is_active = FALSE, updated_at = %s
        WHERE id = %s
        """,
        [timezone.now(), org_id],
    ) > 0
    if result:
        from core.middleware.tenant import invalidate_org_schema_cache
        invalidate_org_schema_cache(org_id)
    return result


# ─── Platform Users ──────────────────────────────────────────────────────────


def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}${h}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        salt, h = stored.split("$", 1)
    except ValueError:
        return False
    return hashlib.sha256((salt + password).encode()).hexdigest() == h


def create_platform_user(
    *,
    email: str,
    password: str,
    first_name: str | None = None,
    last_name: str | None = None,
    phone: str | None = None,
) -> dict[str, Any] | None:
    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO {_table(PLATFORM_USER_TABLE)}
            (email, phone, password_hash, first_name, last_name, is_active, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        [email, phone, _hash_password(password), first_name, last_name, True, now, now],
    )


def get_platform_user_by_email(email: str) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        SELECT * FROM {_table(PLATFORM_USER_TABLE)}
        WHERE email = %s AND is_active = TRUE
        LIMIT 1
        """,
        [email],
    )


def get_platform_user_by_id(user_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        SELECT * FROM {_table(PLATFORM_USER_TABLE)}
        WHERE id = %s AND is_active = TRUE
        LIMIT 1
        """,
        [user_id],
    )


def verify_platform_user_password(email: str, password: str) -> dict[str, Any] | None:
    user = get_platform_user_by_email(email)
    if not user or not user.get("password_hash"):
        return None
    if not _verify_password(password, user["password_hash"]):
        return None
    return user


def update_platform_user(user_id: int, **kwargs: Any) -> dict[str, Any] | None:
    if "password" in kwargs:
        kwargs["password_hash"] = _hash_password(kwargs.pop("password"))

    if not kwargs:
        return get_platform_user_by_id(user_id)

    sets = ", ".join(f"{k} = %s" for k in kwargs)
    values = list(kwargs.values())
    values.append(timezone.now())
    values.append(user_id)

    return fetch_one(
        f"""
        UPDATE {_table(PLATFORM_USER_TABLE)}
        SET {sets}, updated_at = %s
        WHERE id = %s
        RETURNING *
        """,
        values,
    )


# ─── Organization Members ────────────────────────────────────────────────────


def create_organization_member(
    *,
    organization_id: int,
    user_id: int,
    role: str = "manager",
) -> dict[str, Any] | None:
    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO {_table(ORGANIZATION_MEMBER_TABLE)}
            (organization_id, user_id, role, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING *
        """,
        [organization_id, user_id, role, now, now],
    )


def get_organization_member(org_id: int, user_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        SELECT m.*, u.email, u.first_name, u.last_name, u.phone_number as phone
        FROM {_table(ORGANIZATION_MEMBER_TABLE)} m
        JOIN {USER_TABLE} u ON u.id = m.user_id
        WHERE m.organization_id = %s AND m.user_id = %s
        LIMIT 1
        """,
        [org_id, user_id],
    )


def list_organization_members(org_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT m.*, u.email, u.first_name, u.last_name, u.phone_number as phone
        FROM {_table(ORGANIZATION_MEMBER_TABLE)} m
        JOIN {USER_TABLE} u ON u.id = m.user_id
        WHERE m.organization_id = %s
        ORDER BY m.role ASC, u.first_name ASC
        """,
        [org_id],
    )


def get_user_organizations(user_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT o.*, m.role as member_role
        FROM {_table(ORGANIZATION_MEMBER_TABLE)} m
        JOIN {_table(ORGANIZATION_TABLE)} o ON o.id = m.organization_id
        WHERE m.user_id = %s AND o.is_active = TRUE
        ORDER BY o.name ASC
        """,
        [user_id],
    )


def remove_organization_member(org_id: int, user_id: int) -> bool:
    return execute(
        f"""
        DELETE FROM {_table(ORGANIZATION_MEMBER_TABLE)}
        WHERE organization_id = %s AND user_id = %s
        """,
        [org_id, user_id],
    ) > 0


def update_member_role(org_id: int, user_id: int, role: str) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        UPDATE {_table(ORGANIZATION_MEMBER_TABLE)}
        SET role = %s, updated_at = %s
        WHERE organization_id = %s AND user_id = %s
        RETURNING *
        """,
        [role, timezone.now(), org_id, user_id],
    )
