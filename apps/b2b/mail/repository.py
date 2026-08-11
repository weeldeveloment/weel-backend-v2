"""Raw-SQL data access for corporate mail.

Same conventions as ``apps/b2b/workspace/repository.py``: plain dicts in and
out, no ORM, and every read reachable only through a mailbox the caller owns.

The tenancy rule this file enforces everywhere: a thread or message is looked
up **by id together with its mailbox id**, never by id alone. That way a
guessed id from another company returns nothing rather than someone else's
mail, and no view has to remember to check.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from django.utils import timezone

from shared.raw.db import execute, fetch_all, fetch_one

from apps.b2b.raw.tables import (
    B2B_EMPLOYEE_TABLE,
    B2B_MAIL_ATTACHMENT_TABLE,
    B2B_MAIL_DOMAIN_TABLE,
    B2B_MAIL_MESSAGE_TABLE,
    B2B_MAIL_OUTBOX_TABLE,
    B2B_MAIL_RECIPIENT_TABLE,
    B2B_MAIL_THREAD_TABLE,
    B2B_MAILBOX_TABLE,
    B2B_NOTIFICATION_TABLE,
)

# `Re:`, `Fwd:`, and their Russian and Uzbek equivalents, repeated any number
# of times — "Re: RE: Fwd: Hisobot" and "Hisobot" belong in one thread.
_REPLY_PREFIX_RE = re.compile(
    r"^(?:\s*(?:re|res|aw|sv|fw|fwd|отв|пересл|javob|ilova)\s*(?:\[\d+\])?\s*:\s*)+",
    re.IGNORECASE,
)


def normalize_subject(subject: str | None) -> str:
    """The key subject-threading groups on."""
    cleaned = _REPLY_PREFIX_RE.sub("", (subject or "").strip())
    return re.sub(r"\s+", " ", cleaned).strip().lower()[:500]


# ─── Domains ──────────────────────────────────────────────────────────────────

def list_domains(company_id: int) -> list[dict]:
    return fetch_all(
        f"SELECT * FROM {B2B_MAIL_DOMAIN_TABLE} WHERE company_id = %s ORDER BY id",
        [company_id],
    )


def get_domain(domain_id: int, company_id: int) -> dict | None:
    return fetch_one(
        f"SELECT * FROM {B2B_MAIL_DOMAIN_TABLE} WHERE id = %s AND company_id = %s",
        [domain_id, company_id],
    )


def find_domain_by_name(domain: str) -> dict | None:
    return fetch_one(
        f"SELECT * FROM {B2B_MAIL_DOMAIN_TABLE} WHERE domain = %s",
        [domain.lower()],
    )


def create_domain(company_id: int, domain: str, dkim_selector: str) -> dict | None:
    now = timezone.now()
    return fetch_one(
        f"INSERT INTO {B2B_MAIL_DOMAIN_TABLE} "
        "(company_id, domain, dkim_selector, status, created_at, updated_at) "
        "VALUES (%s, %s, %s, 'pending', %s, %s) __RETURNING_MARKER__",
        [company_id, domain.lower(), dkim_selector, now, now],
    )


def update_domain(domain_id: int, **fields: Any) -> dict | None:
    if not fields:
        return None
    fields["updated_at"] = timezone.now()
    assignments = ", ".join(f"{key} = %s" for key in fields)
    return fetch_one(
        f"UPDATE {B2B_MAIL_DOMAIN_TABLE} SET {assignments} WHERE id = %s __RETURNING_MARKER__",
        [*fields.values(), domain_id],
    )


def delete_domain(domain_id: int, company_id: int) -> int:
    return execute(
        f"DELETE FROM {B2B_MAIL_DOMAIN_TABLE} WHERE id = %s AND company_id = %s",
        [domain_id, company_id],
    )


def list_verifiable_domains() -> list[dict]:
    """Every domain the hourly recheck should look at.

    Verified ones are included too: a customer who moves DNS providers breaks
    their own mail silently otherwise, and we would rather tell them.
    """
    return fetch_all(
        f"SELECT * FROM {B2B_MAIL_DOMAIN_TABLE} WHERE status <> 'disabled' ORDER BY id"
    )


# ─── Mailboxes ────────────────────────────────────────────────────────────────

_MAILBOX_SELECT = f"""
    SELECT m.*, e.full_name AS employee_name, e.role AS employee_role,
           d.domain AS domain_name, d.status AS domain_status
      FROM {B2B_MAILBOX_TABLE} m
      JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = m.employee_id
      JOIN {B2B_MAIL_DOMAIN_TABLE} d ON d.id = m.domain_id
"""


def get_mailbox_for_employee(employee_id: int) -> dict | None:
    return fetch_one(f"{_MAILBOX_SELECT} WHERE m.employee_id = %s", [employee_id])


def get_mailbox(mailbox_id: int, company_id: int) -> dict | None:
    return fetch_one(
        f"{_MAILBOX_SELECT} WHERE m.id = %s AND m.company_id = %s",
        [mailbox_id, company_id],
    )


def get_mailbox_by_id(mailbox_id: int) -> dict | None:
    """Unscoped lookup, for background tasks that already hold a mailbox id.

    Views must use ``get_mailbox`` instead — this one performs no tenancy
    check, because a Celery task has no caller to check against.
    """
    return fetch_one(f"{_MAILBOX_SELECT} WHERE m.id = %s", [mailbox_id])


def find_mailbox_by_address(address: str) -> dict | None:
    return fetch_one(f"{_MAILBOX_SELECT} WHERE m.address = %s", [address.lower()])


def list_mailboxes(company_id: int) -> list[dict]:
    return fetch_all(f"{_MAILBOX_SELECT} WHERE m.company_id = %s ORDER BY m.address", [company_id])


def list_company_addresses(company_id: int) -> list[str]:
    """Every address inside the company — used to tell internal mail from external."""
    rows = fetch_all(
        f"SELECT address FROM {B2B_MAILBOX_TABLE} WHERE company_id = %s AND is_active = TRUE",
        [company_id],
    )
    return [row["address"] for row in rows]


def create_mailbox(
    *,
    company_id: int,
    domain_id: int,
    employee_id: int,
    address: str,
    local_part: str,
    display_name: str,
    smtp_password_enc: str,
    quota_bytes: int,
    daily_send_limit: int,
) -> dict | None:
    now = timezone.now()
    return fetch_one(
        f"INSERT INTO {B2B_MAILBOX_TABLE} "
        "(company_id, domain_id, employee_id, address, local_part, display_name, "
        " smtp_password_enc, quota_bytes, daily_send_limit, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) __RETURNING_MARKER__",
        [company_id, domain_id, employee_id, address.lower(), local_part, display_name,
         smtp_password_enc, quota_bytes, daily_send_limit, now, now],
    )


def update_mailbox(mailbox_id: int, **fields: Any) -> dict | None:
    if not fields:
        return None
    fields["updated_at"] = timezone.now()
    assignments = ", ".join(f"{key} = %s" for key in fields)
    return fetch_one(
        f"UPDATE {B2B_MAILBOX_TABLE} SET {assignments} WHERE id = %s __RETURNING_MARKER__",
        [*fields.values(), mailbox_id],
    )


def list_syncable_mailboxes(limit: int = 500) -> list[dict]:
    """Active mailboxes on active domains, least-recently-synced first.

    Ordering by `last_sync_at` is what keeps one perpetually failing mailbox
    from starving the rest when the batch is smaller than the fleet.
    """
    return fetch_all(
        f"""
        SELECT m.* FROM {B2B_MAILBOX_TABLE} m
          JOIN {B2B_MAIL_DOMAIN_TABLE} d ON d.id = m.domain_id
         WHERE m.is_active = TRUE AND d.status = 'active'
         ORDER BY m.last_sync_at NULLS FIRST
         LIMIT %s
        """,
        [limit],
    )


def count_sent_today(mailbox_id: int) -> int:
    row = fetch_one(
        f"SELECT COUNT(*) AS total FROM {B2B_MAIL_OUTBOX_TABLE} "
        "WHERE mailbox_id = %s AND created_at >= NOW() - INTERVAL '24 hours' "
        "AND status <> 'failed'",
        [mailbox_id],
    )
    return int(row["total"]) if row else 0


# ─── Threads ──────────────────────────────────────────────────────────────────

def list_threads(
    mailbox_id: int,
    *,
    folder: str = "inbox",
    query: str | None = None,
    unread_only: bool = False,
    starred_only: bool = False,
    before_id: int | None = None,
    limit: int = 30,
) -> list[dict]:
    sql = [f"SELECT * FROM {B2B_MAIL_THREAD_TABLE} WHERE mailbox_id = %s AND folder = %s"]
    params: list[Any] = [mailbox_id, folder]

    if unread_only:
        sql.append("AND unread_count > 0")
    if starred_only:
        sql.append("AND is_starred = TRUE")
    if query:
        sql.append("AND (subject ILIKE %s OR participants ILIKE %s OR snippet ILIKE %s)")
        pattern = f"%{query}%"
        params += [pattern, pattern, pattern]
    if before_id:
        sql.append("AND id < %s")
        params.append(before_id)

    sql.append("ORDER BY last_message_at DESC NULLS LAST, id DESC LIMIT %s")
    params.append(limit)
    return fetch_all(" ".join(sql), params)


def get_thread(thread_id: int, mailbox_id: int) -> dict | None:
    return fetch_one(
        f"SELECT * FROM {B2B_MAIL_THREAD_TABLE} WHERE id = %s AND mailbox_id = %s",
        [thread_id, mailbox_id],
    )


def find_thread_for_message(
    mailbox_id: int,
    *,
    folder: str,
    references: Iterable[str],
    subject_key: str,
) -> dict | None:
    """Locate the conversation an arriving message belongs to.

    Two passes, strongest evidence first. ``References``/``In-Reply-To`` name
    actual messages and are conclusive; a matching subject is only a guess, so
    it is limited to threads that are still recent — otherwise every message
    ever titled "Hisobot" collapses into one.
    """
    reference_ids = [ref for ref in references if ref]
    if reference_ids:
        row = fetch_one(
            f"""
            SELECT t.* FROM {B2B_MAIL_THREAD_TABLE} t
              JOIN {B2B_MAIL_MESSAGE_TABLE} m ON m.thread_id = t.id
             WHERE t.mailbox_id = %s AND m.message_id_header = __ANY_MARKER__(%s)
             ORDER BY t.id DESC LIMIT 1
            """,
            [mailbox_id, reference_ids],
        )
        if row:
            return row

    if subject_key:
        return fetch_one(
            f"SELECT * FROM {B2B_MAIL_THREAD_TABLE} "
            "WHERE mailbox_id = %s AND folder = %s AND subject_key = %s "
            "AND last_message_at > NOW() - INTERVAL '30 days' "
            "ORDER BY id DESC LIMIT 1",
            [mailbox_id, folder, subject_key],
        )
    return None


def create_thread(
    *,
    mailbox_id: int,
    subject: str,
    folder: str,
    participants: str,
    snippet: str,
    last_message_at,
) -> dict | None:
    now = timezone.now()
    return fetch_one(
        f"INSERT INTO {B2B_MAIL_THREAD_TABLE} "
        "(mailbox_id, subject, subject_key, folder, participants, snippet, "
        " last_message_at, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) __RETURNING_MARKER__",
        [mailbox_id, subject[:500], normalize_subject(subject), folder,
         participants[:2000], snippet[:500], last_message_at, now, now],
    )


def refresh_thread_counters(thread_id: int) -> dict | None:
    """Recompute a thread's denormalised summary from its messages.

    The list screen reads `unread_count`, `snippet` and `last_message_at`
    directly rather than aggregating per row, so they are rebuilt here after
    anything that changes a message. Recomputing beats incrementing: a sync
    that retries after a partial failure would otherwise double-count.
    """
    return fetch_one(
        f"""
        UPDATE {B2B_MAIL_THREAD_TABLE} t SET
            message_count = COALESCE(s.total, 0),
            unread_count  = COALESCE(s.unread, 0),
            last_message_at = s.last_at,
            snippet = COALESCE(s.snippet, t.snippet),
            updated_at = NOW()
          FROM (
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE is_read = FALSE AND direction = 'inbound') AS unread,
                   MAX(COALESCE(sent_at, created_at)) AS last_at,
                   (ARRAY_AGG(LEFT(body_text, 200) ORDER BY COALESCE(sent_at, created_at) DESC))[1]
                       AS snippet
              FROM {B2B_MAIL_MESSAGE_TABLE} WHERE thread_id = %s
          ) s
         WHERE t.id = %s
        __RETURNING_MARKER__
        """,
        [thread_id, thread_id],
    )


def mark_thread_read(thread_id: int, mailbox_id: int) -> None:
    execute(
        f"UPDATE {B2B_MAIL_MESSAGE_TABLE} SET is_read = TRUE, updated_at = NOW() "
        "WHERE thread_id = %s AND mailbox_id = %s AND is_read = FALSE",
        [thread_id, mailbox_id],
    )
    execute(
        f"UPDATE {B2B_MAIL_THREAD_TABLE} SET unread_count = 0, updated_at = NOW() WHERE id = %s",
        [thread_id],
    )


def set_thread_flags(
    thread_id: int,
    mailbox_id: int,
    *,
    is_starred: bool | None = None,
    folder: str | None = None,
) -> dict | None:
    fields: dict[str, Any] = {}
    if is_starred is not None:
        fields["is_starred"] = is_starred
    if folder is not None:
        fields["folder"] = folder
    if not fields:
        return get_thread(thread_id, mailbox_id)

    fields["updated_at"] = timezone.now()
    assignments = ", ".join(f"{key} = %s" for key in fields)
    return fetch_one(
        f"UPDATE {B2B_MAIL_THREAD_TABLE} SET {assignments} "
        "WHERE id = %s AND mailbox_id = %s __RETURNING_MARKER__",
        [*fields.values(), thread_id, mailbox_id],
    )


def total_unread(mailbox_id: int) -> int:
    row = fetch_one(
        f"SELECT COALESCE(SUM(unread_count), 0) AS total FROM {B2B_MAIL_THREAD_TABLE} "
        "WHERE mailbox_id = %s AND folder = 'inbox'",
        [mailbox_id],
    )
    return int(row["total"]) if row else 0


def folder_counts(mailbox_id: int) -> dict[str, int]:
    rows = fetch_all(
        f"SELECT folder, COUNT(*) AS total, COALESCE(SUM(unread_count), 0) AS unread "
        f"FROM {B2B_MAIL_THREAD_TABLE} WHERE mailbox_id = %s GROUP BY folder",
        [mailbox_id],
    )
    return {row["folder"]: {"total": int(row["total"]), "unread": int(row["unread"])}
            for row in rows}


# ─── Messages ─────────────────────────────────────────────────────────────────

def list_messages(
    thread_id: int,
    mailbox_id: int,
    *,
    before_id: int | None = None,
    limit: int = 50,
) -> list[dict]:
    """Newest-end page of a thread, returned oldest-first for display."""
    sql = [f"SELECT * FROM {B2B_MAIL_MESSAGE_TABLE} WHERE thread_id = %s AND mailbox_id = %s"]
    params: list[Any] = [thread_id, mailbox_id]
    if before_id:
        sql.append("AND id < %s")
        params.append(before_id)
    sql.append("ORDER BY id DESC LIMIT %s")
    params.append(limit)

    messages = fetch_all(" ".join(sql), params)
    messages.reverse()
    return messages


def get_message(message_id: int, mailbox_id: int) -> dict | None:
    return fetch_one(
        f"SELECT * FROM {B2B_MAIL_MESSAGE_TABLE} WHERE id = %s AND mailbox_id = %s",
        [message_id, mailbox_id],
    )


def message_exists(mailbox_id: int, message_id_header: str) -> bool:
    if not message_id_header:
        return False
    row = fetch_one(
        f"SELECT 1 AS hit FROM {B2B_MAIL_MESSAGE_TABLE} "
        "WHERE mailbox_id = %s AND message_id_header = %s",
        [mailbox_id, message_id_header],
    )
    return row is not None


def create_message(
    *,
    thread_id: int,
    mailbox_id: int,
    direction: str,
    status: str = "delivered",
    imap_uid: int | None = None,
    message_id_header: str | None = None,
    in_reply_to: str | None = None,
    references_header: str | None = None,
    from_address: str = "",
    from_name: str = "",
    subject: str = "",
    body_text: str = "",
    body_html_sanitized: str = "",
    has_attachments: bool = False,
    is_read: bool = False,
    sent_at=None,
) -> dict | None:
    now = timezone.now()
    return fetch_one(
        f"INSERT INTO {B2B_MAIL_MESSAGE_TABLE} "
        "(thread_id, mailbox_id, direction, status, imap_uid, message_id_header, "
        " in_reply_to, references_header, from_address, from_name, subject, "
        " body_text, body_html_sanitized, has_attachments, is_read, sent_at, "
        " created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
        "__RETURNING_MARKER__",
        [thread_id, mailbox_id, direction, status, imap_uid, message_id_header,
         in_reply_to, references_header, from_address[:320], from_name[:300],
         subject[:500], body_text, body_html_sanitized, has_attachments, is_read,
         sent_at or now, now, now],
    )


def update_message(message_id: int, **fields: Any) -> dict | None:
    if not fields:
        return None
    fields["updated_at"] = timezone.now()
    assignments = ", ".join(f"{key} = %s" for key in fields)
    return fetch_one(
        f"UPDATE {B2B_MAIL_MESSAGE_TABLE} SET {assignments} WHERE id = %s __RETURNING_MARKER__",
        [*fields.values(), message_id],
    )


def add_recipients(message_id: int, recipients: Iterable[tuple[str, str, str]]) -> None:
    """``recipients`` is an iterable of ``(kind, address, name)``."""
    rows = list(recipients)
    if not rows:
        return
    values = ", ".join(["(%s, %s, %s, %s)"] * len(rows))
    params: list[Any] = []
    for kind, address, name in rows:
        params += [message_id, kind, address[:320].lower(), (name or "")[:300]]
    execute(
        f"INSERT INTO {B2B_MAIL_RECIPIENT_TABLE} (message_id, kind, address, name) "
        f"VALUES {values}",
        params,
    )


def list_recipients(message_ids: list[int]) -> dict[int, list[dict]]:
    if not message_ids:
        return {}
    rows = fetch_all(
        f"SELECT * FROM {B2B_MAIL_RECIPIENT_TABLE} WHERE message_id = __ANY_MARKER__(%s)",
        [message_ids],
    )
    grouped: dict[int, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["message_id"], []).append(row)
    return grouped


# ─── Attachments ──────────────────────────────────────────────────────────────

def create_attachment(
    *,
    mailbox_id: int,
    message_id: int | None,
    filename: str,
    content_type: str,
    size_bytes: int,
    storage_key: str,
    content_id: str | None = None,
    is_inline: bool = False,
) -> dict | None:
    return fetch_one(
        f"INSERT INTO {B2B_MAIL_ATTACHMENT_TABLE} "
        "(mailbox_id, message_id, filename, content_type, size_bytes, storage_key, "
        " content_id, is_inline) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) __RETURNING_MARKER__",
        [mailbox_id, message_id, filename[:300], content_type[:200], size_bytes,
         storage_key[:500], content_id, is_inline],
    )


def list_attachments(message_ids: list[int]) -> dict[int, list[dict]]:
    if not message_ids:
        return {}
    rows = fetch_all(
        f"SELECT * FROM {B2B_MAIL_ATTACHMENT_TABLE} "
        "WHERE message_id = __ANY_MARKER__(%s) AND is_inline = FALSE",
        [message_ids],
    )
    grouped: dict[int, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["message_id"], []).append(row)
    return grouped


def claim_attachments(attachment_ids: list[int], mailbox_id: int, message_id: int) -> list[dict]:
    """Bind uploads to the message that is finally sending them.

    Scoped to the uploader's own mailbox, so passing someone else's attachment
    id attaches nothing rather than exfiltrating their file.
    """
    if not attachment_ids:
        return []
    return fetch_all(
        f"UPDATE {B2B_MAIL_ATTACHMENT_TABLE} SET message_id = %s "
        "WHERE id = __ANY_MARKER__(%s) AND mailbox_id = %s AND message_id IS NULL "
        "__RETURNING_MARKER__",
        [message_id, attachment_ids, mailbox_id],
    )


def get_attachment(attachment_id: int, mailbox_id: int) -> dict | None:
    return fetch_one(
        f"SELECT * FROM {B2B_MAIL_ATTACHMENT_TABLE} WHERE id = %s AND mailbox_id = %s",
        [attachment_id, mailbox_id],
    )


# ─── Outbox ───────────────────────────────────────────────────────────────────

def create_outbox_entry(mailbox_id: int, message_id: int, payload: dict) -> dict | None:
    import json

    now = timezone.now()
    return fetch_one(
        f"INSERT INTO {B2B_MAIL_OUTBOX_TABLE} "
        "(mailbox_id, message_id, payload, status, created_at, updated_at) "
        "VALUES (%s, %s, %s, 'pending', %s, %s) __RETURNING_MARKER__",
        [mailbox_id, message_id, json.dumps(payload), now, now],
    )


def get_outbox_entry(entry_id: int) -> dict | None:
    return fetch_one(f"SELECT * FROM {B2B_MAIL_OUTBOX_TABLE} WHERE id = %s", [entry_id])


def update_outbox_entry(entry_id: int, **fields: Any) -> dict | None:
    if not fields:
        return None
    fields["updated_at"] = timezone.now()
    assignments = ", ".join(f"{key} = %s" for key in fields)
    return fetch_one(
        f"UPDATE {B2B_MAIL_OUTBOX_TABLE} SET {assignments} WHERE id = %s __RETURNING_MARKER__",
        [*fields.values(), entry_id],
    )


# ─── Notifications ────────────────────────────────────────────────────────────

def create_notification(
    *,
    company_id: int,
    employee_id: int,
    kind: str,
    title: str,
    body: str = "",
    payload: dict | None = None,
) -> dict | None:
    import json

    return fetch_one(
        f"INSERT INTO {B2B_NOTIFICATION_TABLE} "
        "(company_id, employee_id, kind, title, body, payload) "
        "VALUES (%s, %s, %s, %s, %s, %s) __RETURNING_MARKER__",
        [company_id, employee_id, kind, title[:300], body, json.dumps(payload or {})],
    )


def list_notifications(employee_id: int, *, before_id: int | None = None, limit: int = 30):
    sql = [f"SELECT * FROM {B2B_NOTIFICATION_TABLE} WHERE employee_id = %s"]
    params: list[Any] = [employee_id]
    if before_id:
        sql.append("AND id < %s")
        params.append(before_id)
    sql.append("ORDER BY id DESC LIMIT %s")
    params.append(limit)
    return fetch_all(" ".join(sql), params)


def unread_notification_count(employee_id: int) -> int:
    row = fetch_one(
        f"SELECT COUNT(*) AS total FROM {B2B_NOTIFICATION_TABLE} "
        "WHERE employee_id = %s AND is_read = FALSE",
        [employee_id],
    )
    return int(row["total"]) if row else 0


def mark_notifications_read(employee_id: int, notification_ids: list[int] | None = None) -> int:
    if notification_ids:
        return execute(
            f"UPDATE {B2B_NOTIFICATION_TABLE} SET is_read = TRUE "
            "WHERE employee_id = %s AND id = __ANY_MARKER__(%s)",
            [employee_id, notification_ids],
        )
    return execute(
        f"UPDATE {B2B_NOTIFICATION_TABLE} SET is_read = TRUE "
        "WHERE employee_id = %s AND is_read = FALSE",
        [employee_id],
    )


def list_chat_recipients(thread_id: int, sender_id: int) -> list[dict]:
    """Everyone in a chat thread except the sender, with their push token.

    Muted members are left out: a mute is the only way someone has to say "not
    on my phone", and honouring it only in the UI would defeat the point.
    """
    from apps.b2b.raw.tables import B2B_CHAT_MEMBER_TABLE

    return fetch_all(
        f"""
        SELECT m.employee_id, e.company_id, e.fcm_token
          FROM {B2B_CHAT_MEMBER_TABLE} m
          JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = m.employee_id
         WHERE m.thread_id = %s AND m.employee_id <> %s
           AND m.is_muted = FALSE AND e.is_active = TRUE
        """,
        [thread_id, sender_id],
    )


def get_employee(employee_id: int, company_id: int) -> dict | None:
    """Company-scoped employee lookup, so a foreign id cannot be given a mailbox."""
    return fetch_one(
        f"SELECT * FROM {B2B_EMPLOYEE_TABLE} WHERE id = %s AND company_id = %s AND is_active = TRUE",
        [employee_id, company_id],
    )


def get_employee_fcm_token(employee_id: int) -> str | None:
    row = fetch_one(
        f"SELECT fcm_token FROM {B2B_EMPLOYEE_TABLE} WHERE id = %s",
        [employee_id],
    )
    return (row or {}).get("fcm_token")
