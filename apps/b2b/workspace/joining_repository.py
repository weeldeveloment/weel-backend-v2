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
    execute(
        f"""
        INSERT INTO {B2B_INVITE_TABLE}
            (company_id, token, role, modules, permissions, expires_at,
             created_by, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        [
            company_id,
            token,
            Role.clean(role),
            json.dumps(Module.clean(modules)) if modules is not None else None,
            json.dumps(Permission.clean(permissions)) if permissions is not None else None,
            now + timedelta(days=window),
            created_by,
            now,
            now,
        ],
    )
    return get_invite_by_token(token)


def get_invite_by_token(token: str) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        SELECT i.*, c.name AS company_name, c.slug AS company_slug,
               e.full_name AS created_by_name
          FROM {B2B_INVITE_TABLE} i
          JOIN {B2B_COMPANY_TABLE} c ON c.id = i.company_id
          LEFT JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = i.created_by
         WHERE i.token = %s
        """,
        [token],
    )


def list_invites(company_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT i.*, e.full_name AS created_by_name,
               a.username AS accepted_by_username
          FROM {B2B_INVITE_TABLE} i
          LEFT JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = i.created_by
          LEFT JOIN b2b_account a ON a.id = i.accepted_by
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
