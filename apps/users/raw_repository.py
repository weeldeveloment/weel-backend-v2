from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from django.utils import timezone

from shared.raw.db import execute, fetch_all, fetch_one, table_exists
from shared.raw.compat import get_table_name
from shared.raw.entities import RawUser
from shared.raw.tables import USER_TABLE


def _row_to_user(row: dict[str, Any] | None) -> RawUser | None:
    if row is None:
        return None
    return RawUser.from_row(row)


def normalized_phone_candidates(phone_number: str) -> list[str]:
    raw = (phone_number or "").strip()
    if not raw:
        return []

    candidates = [raw]
    if raw.startswith("+"):
        candidates.append(raw[1:])
    else:
        candidates.append(f"+{raw}")

    uniq: list[str] = []
    for value in candidates:
        if value not in uniq:
            uniq.append(value)
    return uniq


def get_active_user_by_phone(phone_number: str, role: str) -> RawUser | None:
    candidates = normalized_phone_candidates(phone_number)
    if not candidates:
        return None

    # Database-agnostic array membership
    from shared.raw.compat import is_postgresql
    if is_postgresql():
        row = fetch_one(
            f"""
            SELECT *
            FROM {USER_TABLE}
            WHERE role = %s
              AND is_active = TRUE
              AND phone_number = __ANY_MARKER__(%s)
            ORDER BY id
            LIMIT 1
            """,
            [role, candidates],
        )
    else:
        # SQLite: expand array into IN clause
        placeholders = ','.join(['%s'] * len(candidates))
        row = fetch_one(
            f"""
            SELECT *
            FROM {USER_TABLE}
            WHERE role = %s
              AND is_active = TRUE
              AND phone_number IN ({placeholders})
            ORDER BY id
            LIMIT 1
            """,
            [role] + candidates,
        )
    return _row_to_user(row)


def get_user_by_id(user_id: int, role: str | None = None, active_only: bool = False) -> RawUser | None:
    where = ["id = %s"]
    params: list[Any] = [user_id]

    if role:
        where.append("role = %s")
        params.append(role)
    if active_only:
        where.append("is_active = TRUE")

    row = fetch_one(
        f"""
        SELECT *
        FROM {USER_TABLE}
        WHERE {' AND '.join(where)}
        LIMIT 1
        """,
        params,
    )
    return _row_to_user(row)


def _subject_to_user_id(subject: str | None) -> int | None:
    if not subject:
        return None
    normalized = str(subject).strip()
    if not normalized:
        return None

    if normalized.isdigit():
        return int(normalized)

    try:
        return int(UUID(normalized))
    except (ValueError, TypeError):
        return None


def get_active_user_by_subject(subject: str | None, role: str) -> RawUser | None:
    user_id = _subject_to_user_id(subject)
    if user_id is None:
        return None
    return get_user_by_id(user_id, role=role, active_only=True)


def exists_user_by_phone(phone_number: str, role: str) -> bool:
    candidates = normalized_phone_candidates(phone_number)
    if not candidates:
        return False
    
    from shared.raw.compat import is_postgresql
    if is_postgresql():
        row = fetch_one(
            f"""
            SELECT EXISTS (
                SELECT 1
                FROM {USER_TABLE}
                WHERE role = %s
                  AND phone_number = __ANY_MARKER__(%s)
            ) AS exists_flag
            """,
            [role, candidates],
        )
    else:
        # SQLite: expand array into IN clause
        placeholders = ','.join(['%s'] * len(candidates))
        row = fetch_one(
            f"""
            SELECT EXISTS (
                SELECT 1
                FROM {USER_TABLE}
                WHERE role = %s
                  AND phone_number IN ({placeholders})
            ) AS exists_flag
            """,
            [role] + candidates,
        )
    return bool(row and row["exists_flag"])


def exists_partner_username(username: str) -> bool:
    row = fetch_one(
        f"""
        SELECT EXISTS (
            SELECT 1
            FROM {USER_TABLE}
            WHERE role = 'partner'
              AND LOWER(COALESCE(username, '')) = LOWER(%s)
        ) AS exists_flag
        """,
        [username],
    )
    return bool(row and row["exists_flag"])


def exists_partner_email(email: str) -> bool:
    row = fetch_one(
        f"""
        SELECT EXISTS (
            SELECT 1
            FROM {USER_TABLE}
            WHERE role = 'partner'
              AND LOWER(COALESCE(email, '')) = LOWER(%s)
        ) AS exists_flag
        """,
        [email],
    )
    return bool(row and row["exists_flag"])


def _insert_user(
    *,
    role: str,
    first_name: str | None,
    last_name: str | None,
    phone_number: str | None,
    username: str | None = None,
    email: str | None = None,
    is_active: bool = True,
) -> RawUser:
    now: datetime = timezone.now()
    
    from shared.raw.compat import is_postgresql, return_star
    returning_clause = "__RETURNING_MARKER__" if return_star() else ""
    
    row = fetch_one(
        f"""
        INSERT INTO {USER_TABLE} (
            guid,
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
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, FALSE, %s, %s)
        {returning_clause}
        """,
        [uuid4(), role, email, phone_number, first_name, last_name, username, is_active, now, now],
    )
    
    # If RETURNING not supported, fetch the inserted row
    if row is None and not return_star():
        row = fetch_one(
            f"SELECT * FROM {USER_TABLE} WHERE role = %s AND phone_number = %s ORDER BY id DESC LIMIT 1",
            [role, phone_number],
        )
    
    if row is None:
        raise RuntimeError("Failed to insert user")
    return RawUser.from_row(row)


def create_client(phone_number: str, first_name: str = "", last_name: str = "") -> RawUser:
    normalized = normalized_phone_candidates(phone_number)
    stored_phone = normalized[0] if normalized else phone_number
    return _insert_user(
        role="client",
        first_name=first_name or "",
        last_name=last_name or "",
        phone_number=stored_phone,
        is_active=True,
    )


def create_partner(
    *,
    phone_number: str,
    username: str,
    first_name: str,
    last_name: str,
    email: str | None = None,
) -> RawUser:
    normalized = normalized_phone_candidates(phone_number)
    stored_phone = normalized[0] if normalized else phone_number
    return _insert_user(
        role="partner",
        first_name=first_name,
        last_name=last_name,
        phone_number=stored_phone,
        username=username,
        email=email,
        is_active=True,
    )


def create_pms_user(
    *,
    phone_number: str,
    first_name: str = "",
    last_name: str = "",
) -> RawUser:
    normalized = normalized_phone_candidates(phone_number)
    stored_phone = normalized[0] if normalized else phone_number
    return _insert_user(
        role="pms",
        first_name=first_name,
        last_name=last_name,
        phone_number=stored_phone,
        username="",
        email=None,
        is_active=True,
    )


def create_sms_log(phone_number: str, purpose: str | Any, is_sent: bool) -> None:
    if not table_exists("users_smslog"):
        return

    now = timezone.now()
    purpose_value = getattr(purpose, "value", purpose)
    execute(
        f"""
        INSERT INTO {get_table_name("users_smslog")} (
            guid,
            created_at,
            updated_at,
            phone_number,
            purpose,
            is_sent
        ) VALUES (%s, %s, %s, %s, %s, %s)
        """,
        [uuid4(), now, now, phone_number, purpose_value, bool(is_sent)],
    )


def get_latest_active_partner_telegram_user(partner_id: int) -> dict[str, Any] | None:
    if not table_exists("users_partnertelegramuser"):
        return None
    return fetch_one(
        f"""
        SELECT id, telegram_user_id, is_active
        FROM {get_table_name("users_partnertelegramuser")}
        WHERE partner_id = %s
          AND is_active = TRUE
        ORDER BY id DESC
        LIMIT 1
        """,
        [partner_id],
    )


def deactivate_partner_telegram_user(telegram_row_id: int) -> None:
    if not table_exists("users_partnertelegramuser"):
        return
    execute(
        f"""
        UPDATE {get_table_name("users_partnertelegramuser")}
        SET is_active = FALSE,
            updated_at = %s
        WHERE id = %s
        """,
        [timezone.now(), telegram_row_id],
    )


def ensure_test_partner(phone_number: str) -> RawUser:
    existing = get_active_user_by_phone(phone_number, role="partner")
    if existing:
        return existing

    normalized = normalized_phone_candidates(phone_number)
    canonical = normalized[1] if len(normalized) > 1 and normalized[1].startswith("+") else normalized[0]
    digits = canonical.replace("+", "")
    if not digits.startswith("998"):
        digits = f"998{digits}"
    canonical = f"+{digits}"

    username = f"test_partner_{digits}"
    if exists_partner_username(username):
        suffix = digits[-6:]
        username = f"test_partner_{suffix}_{int(timezone.now().timestamp())}"

    return create_partner(
        phone_number=canonical,
        username=username,
        first_name="Test",
        last_name="Partner",
    )


def update_user_profile(
    user_id: int,
    role: str,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    username: str | None = None,
    avatar: str | None = None,
) -> RawUser | None:
    allowed: dict[str, Any] = {}
    if first_name is not None:
        allowed["first_name"] = first_name
    if last_name is not None:
        allowed["last_name"] = last_name
    if username is not None:
        allowed["username"] = username
    if avatar is not None:
        allowed["avatar"] = avatar

    if not allowed:
        return get_user_by_id(user_id, role=role)

    allowed["updated_at"] = timezone.now()
    assignments = ", ".join(f"{col} = %s" for col in allowed.keys())
    params = list(allowed.values()) + [user_id, role]
    row = fetch_one(
        f"""
        UPDATE {USER_TABLE}
        SET {assignments}
        WHERE id = %s
          AND role = %s
        __RETURNING_MARKER__
        """,
        params,
    )
    return _row_to_user(row)


def soft_deactivate_user(user: RawUser) -> None:
    now = timezone.now()
    user_id = user.id

    if user.is_partner:
        execute(
            f"""
            UPDATE {USER_TABLE}
            SET is_active = FALSE,
                phone_number = %s,
                username = %s,
                email = NULL,
                updated_at = %s
            WHERE id = %s
            """,
            [f"d{user_id}", f"deleted_{user_id}", now, user_id],
        )

        execute(
            f"""
            UPDATE property
            SET is_archived = TRUE,
                updated_at = %s
            WHERE partner_user_id = %s AND is_archived = FALSE
            """,
            [now, user_id],
        )
    else:
        execute(
            f"""
            UPDATE {USER_TABLE}
            SET is_active = FALSE,
                phone_number = %s,
                updated_at = %s
            WHERE id = %s
            """,
            [f"d{user_id}", now, user_id],
        )

    execute(
        f"""
        UPDATE booking
        SET status = 'cancelled',
            updated_at = %s
        WHERE client_user_id = %s AND status NOT IN ('cancelled', 'completed')
        """,
        [now, user_id],
    )

    if table_exists("user_map"):
        execute(
            "DELETE FROM user_map WHERE user_id = %s",
            [user_id],
        )

    if table_exists("notification"):
        execute(
            "DELETE FROM notification WHERE recipient_user_id = %s",
            [user_id],
        )

    if table_exists("chat_conversation"):
        execute(
            f"""
            UPDATE chat_conversation
            SET client_user_id = NULL, partner_user_id = NULL
            WHERE client_user_id = %s OR partner_user_id = %s
            """,
            [user_id, user_id],
        )

    if table_exists("chat_message"):
        execute(
            f"""
            UPDATE chat_message
            SET receiver_user_id = NULL
            WHERE sender_user_id = %s OR receiver_user_id = %s
            """,
            [user_id, user_id],
        )

    log_account_deletion(user_id, user_id, user.role)


def log_account_deletion(user_id: int, deleted_by: int, role: str) -> None:
    now = timezone.now()
    if table_exists("account_deletion_log"):
        execute(
            f"""
            INSERT INTO account_deletion_log
                (user_id, deleted_by, role, deleted_at)
            VALUES (%s, %s, %s, %s)
            """,
            [user_id, deleted_by, role, now],
        )


def table_capability_snapshot() -> dict[str, bool]:
    return {
        "users": table_exists("users"),
        "user_map": table_exists("user_map"),
        "cottage": table_exists("cottage"),
        "users_smslog": table_exists("users_smslog"),
        "client_devices": table_exists("client_devices"),
        "partner_devices": table_exists("partner_devices"),
        "users_clientsession": table_exists("users_clientsession"),
        "users_partnersession": table_exists("users_partnersession"),
        "users_partnertelegramuser": table_exists("users_partnertelegramuser"),
    }


def list_active_admin_ids(limit: int = 1) -> list[int]:
    rows = fetch_all(
        f"""
        SELECT id
        FROM {USER_TABLE}
        WHERE role = 'admin'
          AND is_active = TRUE
        ORDER BY id
        LIMIT %s
        """,
        [limit],
    )
    return [int(row["id"]) for row in rows]


def fetch_users_by_ids(ids: Iterable[int]) -> dict[int, RawUser]:
    values = [int(v) for v in ids]
    if not values:
        return {}
    
    from shared.raw.compat import is_postgresql
    if is_postgresql():
        rows = fetch_all(
            f"""
            SELECT *
            FROM {USER_TABLE}
            WHERE id = __ANY_MARKER__(%s)
            """,
            [values],
        )
    else:
        # SQLite: expand array into IN clause
        placeholders = ','.join(['%s'] * len(values))
        rows = fetch_all(
            f"""
            SELECT *
            FROM {USER_TABLE}
            WHERE id IN ({placeholders})
            """,
            values,
        )
    
    result: dict[int, RawUser] = {}
    for row in rows:
        user = RawUser.from_row(row)
        result[user.id] = user
    return result
