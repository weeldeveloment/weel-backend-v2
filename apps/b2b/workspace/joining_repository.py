"""Getting into a workspace: invitations, join requests, and chat-only guests.

Three doors, and the TZ is careful that they are not the same door:

* an **invite link** is the workspace deciding in advance — role, modules and
  an expiry are fixed when the link is made, and whoever opens it gets exactly
  that;
* a **join request** is the other direction — somebody finds the workspace by
  its handle and asks; what they ask for is a request, and the workspace
  decides what they actually get;
* a **chat invite** is not membership at all. It puts somebody in one
  conversation and nowhere else.
"""
from __future__ import annotations

import json
import secrets
from datetime import timedelta
from typing import Any, Sequence

from django.utils import timezone

from shared.raw.db import execute, fetch_all, fetch_one

from apps.b2b.raw.tables import B2B_COMPANY_TABLE, B2B_EMPLOYEE_TABLE
from apps.b2b.workspace.access import Module, Permission, Role

B2B_INVITE_TABLE = "b2b_workspace_invite"
B2B_JOIN_REQUEST_TABLE = "b2b_join_request"

#: How long a link lives unless the sender says otherwise. Long enough to be
#: sent and read over a weekend, short enough that a link forwarded on months
#: later is no longer a way in.
DEFAULT_INVITE_DAYS = 7
MAX_INVITE_DAYS = 30


class JoinStatus:
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"

    CHOICES = [PENDING, ACCEPTED, DECLINED]


# ─── Invitations ──────────────────────────────────────────────────────────────

def create_invite(
    *,
    company_id: int,
    created_by: int,
    role: str,
    modules: Sequence[str] | None,
    permissions: Sequence[str] | None,
    days: int | None = None,
    thread_id: int | None = None,
) -> dict[str, Any] | None:
    """Mint a link.

    The token comes from `secrets` and is never derived from the row's id or
    from anything about the workspace: a link is a bearer credential, and one
    that can be guessed from a neighbouring link is not a credential at all.

    `modules` of `None` is "by role" — the first of the two answers the invite
    screen offers. A list is "configure", and it replaces the role's rather
    than adding to it.
    """
    window = min(max(days or DEFAULT_INVITE_DAYS, 1), MAX_INVITE_DAYS)
    now = timezone.now()
    token = secrets.token_urlsafe(32)
    # A link to one conversation opens nothing else, so it carries no modules
    # and no permissions whatever was asked for — the flag is what the accept
    # path reads, and leaving access on the row would be a second answer to
    # the same question.
    is_chat_only = thread_id is not None
    execute(
        f"""
        INSERT INTO {B2B_INVITE_TABLE}
            (company_id, token, role, modules, permissions, expires_at,
             created_by, thread_id, is_chat_only, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        [
            company_id,
            token,
            Role.clean(role),
            None if is_chat_only
            else (json.dumps(Module.clean(modules)) if modules is not None else None),
            None if is_chat_only
            else (
                json.dumps(Permission.clean(permissions))
                if permissions is not None
                else None
            ),
            now + timedelta(days=window),
            created_by,
            thread_id,
            is_chat_only,
            now,
            now,
        ],
    )
    return get_invite_by_token(token)


def get_invite_by_token(token: str) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        SELECT i.*, c.name AS company_name, c.slug AS company_slug,
               e.full_name AS created_by_name, t.group_name AS thread_title
          FROM {B2B_INVITE_TABLE} i
          JOIN {B2B_COMPANY_TABLE} c ON c.id = i.company_id
          LEFT JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = i.created_by
          LEFT JOIN b2b_chat_thread t ON t.id = i.thread_id
         WHERE i.token = %s
        """,
        [token],
    )


def list_invites(company_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT i.*, e.full_name AS created_by_name,
               a.username AS accepted_by_username,
               t.group_name AS thread_title
          FROM {B2B_INVITE_TABLE} i
          LEFT JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = i.created_by
          LEFT JOIN b2b_account a ON a.id = i.accepted_by
          LEFT JOIN b2b_chat_thread t ON t.id = i.thread_id
         WHERE i.company_id = %s
         ORDER BY i.created_at DESC
         LIMIT %s
        """,
        [company_id, limit],
    )


def revoke_invite(invite_id: int, company_id: int) -> int:
    """Withdraw a link. Scoped by company so an id from elsewhere cannot be
    revoked, and by `revoked_at IS NULL` so the count says whether it did
    anything."""
    return execute(
        f"UPDATE {B2B_INVITE_TABLE} SET revoked_at = %s, updated_at = %s "
        f"WHERE id = %s AND company_id = %s AND revoked_at IS NULL",
        [timezone.now(), timezone.now(), invite_id, company_id],
    )


def invite_problem(invite: dict[str, Any] | None) -> str | None:
    """Why this link cannot be used, or None if it can.

    One function so the preview and the acceptance agree — a link that
    previews as usable and then refuses is worse than one that never
    previewed.
    """
    if not invite:
        return "not_found"
    if invite.get("revoked_at"):
        return "revoked"
    if invite.get("accepted_at"):
        return "used"
    expires_at = invite.get("expires_at")
    if expires_at and expires_at < timezone.now():
        return "expired"
    return None


def mark_invite_accepted(invite_id: int, account_id: int) -> int:
    """Claim the link, once.

    Scoped to `accepted_at IS NULL` rather than checked first: two taps on a
    link a moment apart would otherwise both pass the check and both put the
    person on the roster twice.
    """
    now = timezone.now()
    return execute(
        f"UPDATE {B2B_INVITE_TABLE} SET accepted_by = %s, accepted_at = %s, updated_at = %s "
        f"WHERE id = %s AND accepted_at IS NULL AND revoked_at IS NULL",
        [account_id, now, now, invite_id],
    )


# ─── Asking to join ───────────────────────────────────────────────────────────

#: How many workspaces one search may name. Long enough that a common word
#: still shows the one being looked for, short enough that the endpoint is
#: never a way to enumerate every workspace on the platform.
SEARCH_LIMIT = 20


def search_companies(
    query: str, *, account_id: int | None = None, limit: int = SEARCH_LIMIT
) -> list[dict[str, Any]]:
    """Workspaces somebody could ask to join, by name or handle.

    Deliberately narrow. This answers a search box on a screen for people who
    belong to nothing yet, so it may not become a directory: a blank query
    returns nothing rather than everything, and the rows carry only what the
    card shows — the name, the handle, the company above it, and how many
    people are already there. No ids of members, no contact details, nothing
    that is inside the workspace.

    Workspaces the asker is already on are left out. Offering to ask for a
    seat somebody already holds is offering a button that can only answer 409.
    """
    text = (query or "").strip().lstrip("@")
    if len(text) < 2:
        # One letter matches most of the table. The screen says what to type.
        return []
    pattern = f"%{text.lower()}%"
    return fetch_all(
        f"""
        SELECT c.id,
               c.name,
               c.slug,
               c.icon,
               o.name AS org_name,
               (SELECT COUNT(*) FROM {B2B_EMPLOYEE_TABLE} e
                 WHERE e.company_id = c.id
                   AND e.is_active = TRUE
                   -- A chat-only guest is not on the roster; counting them
                   -- would tell somebody the team is bigger than it is.
                   AND e.is_chat_only = FALSE) AS member_count
          FROM {B2B_COMPANY_TABLE} c
          LEFT JOIN b2b_org o ON o.id = c.org_id
         WHERE c.is_active = TRUE
           AND (LOWER(c.name) LIKE %s OR LOWER(c.slug) LIKE %s)
           AND NOT EXISTS (
                SELECT 1 FROM {B2B_EMPLOYEE_TABLE} me
                 WHERE me.company_id = c.id
                   AND me.is_active = TRUE
                   AND me.account_id = %s
               )
         ORDER BY (LOWER(c.slug) = %s) DESC, c.name ASC
         LIMIT %s
        """,
        [pattern, pattern, account_id, text.lower(), limit],
    )


def find_company_by_slug(slug: str) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT id, name, slug FROM {B2B_COMPANY_TABLE} "
        f"WHERE LOWER(slug) = LOWER(%s) AND is_active = TRUE",
        [(slug or "").strip().lstrip("@")],
    )


def create_join_request(
    *,
    company_id: int,
    account_id: int,
    message: str,
    wanted_modules: Sequence[str] | None,
) -> dict[str, Any] | None:
    now = timezone.now()
    execute(
        f"""
        INSERT INTO {B2B_JOIN_REQUEST_TABLE}
            (company_id, account_id, message, wanted_modules, status, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT DO NOTHING
        """,
        [
            company_id,
            account_id,
            message,
            json.dumps(Module.clean(wanted_modules)) if wanted_modules is not None else None,
            JoinStatus.PENDING,
            now,
            now,
        ],
    )
    return pending_join_request(company_id, account_id)


def pending_join_request(company_id: int, account_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT * FROM {B2B_JOIN_REQUEST_TABLE} "
        f"WHERE company_id = %s AND account_id = %s AND status = %s",
        [company_id, account_id, JoinStatus.PENDING],
    )


def get_join_request(request_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT * FROM {B2B_JOIN_REQUEST_TABLE} WHERE id = %s", [request_id]
    )


def get_join_request_with_company(request_id: int) -> dict[str, Any] | None:
    """The request, plus the name of the workspace it was sent to.

    What the notification needs and [get_join_request] does not carry: a push
    that says "your request was accepted" without naming the team is unhelpful
    to anybody who asked more than one.
    """
    return fetch_one(
        f"""
        SELECT j.*, c.name AS company_name
          FROM {B2B_JOIN_REQUEST_TABLE} j
          JOIN {B2B_COMPANY_TABLE} c ON c.id = j.company_id
         WHERE j.id = %s
        """,
        [request_id],
    )


def list_join_requests(company_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT j.*, a.username, a.phone, a.first_name, a.last_name, a.photo
          FROM {B2B_JOIN_REQUEST_TABLE} j
          JOIN b2b_account a ON a.id = j.account_id
         WHERE j.company_id = %s
         ORDER BY j.created_at DESC
         LIMIT %s
        """,
        [company_id, limit],
    )


def list_account_join_requests(
    account_id: int, *, limit: int = 20
) -> list[dict[str, Any]]:
    """What this account has asked for, and what came of it.

    The other direction from [list_join_requests], which is a workspace
    looking at who wants in. This is the asker looking at their own outbox —
    the screen they land on after registering says nothing at all otherwise,
    and somebody who has asked and heard nothing cannot tell a request that
    was never sent from one nobody has answered yet.

    Answered rows are kept in the list rather than filtered to pending: being
    turned down is an answer, and a request that simply vanished reads as one
    that failed to send.
    """
    return fetch_all(
        f"""
        SELECT j.id,
               j.company_id,
               j.status,
               j.decline_reason,
               j.granted_role,
               j.created_at,
               j.decided_at,
               c.name AS company_name,
               c.slug AS company_slug,
               o.name AS org_name
          FROM {B2B_JOIN_REQUEST_TABLE} j
          JOIN {B2B_COMPANY_TABLE} c ON c.id = j.company_id
          LEFT JOIN b2b_org o ON o.id = c.org_id
         WHERE j.account_id = %s
         ORDER BY j.created_at DESC
         LIMIT %s
        """,
        [account_id, limit],
    )


def close_join_request(
    request_id: int,
    *,
    status: str,
    decided_by: int,
    granted_role: str | None = None,
    granted_modules: Sequence[str] | None = None,
    decline_reason: str | None = None,
) -> int:
    now = timezone.now()
    return execute(
        f"""
        UPDATE {B2B_JOIN_REQUEST_TABLE}
           SET status = %s, granted_role = %s, granted_modules = %s,
               decline_reason = %s, decided_by = %s, decided_at = %s, updated_at = %s
         WHERE id = %s AND status = %s
        """,
        [
            status,
            Role.clean(granted_role) if granted_role else None,
            json.dumps(Module.clean(granted_modules))
            if granted_modules is not None
            else None,
            decline_reason,
            decided_by,
            now,
            now,
            request_id,
            JoinStatus.PENDING,
        ],
    )
