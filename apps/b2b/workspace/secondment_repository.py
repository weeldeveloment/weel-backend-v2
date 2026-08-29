"""Raw-SQL data access for secondments — the ask, and the standing it creates.

Split out of `repository.py` rather than added to it: that module is the
workspace's own data, all of it scoped by one `company_id`, and these queries
are the one place in the schema that deliberately reaches *across* that
boundary. Keeping them apart is what makes "which query can see another
workspace" a question with a short answer.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Sequence

from django.utils import timezone

from shared.raw.db import execute, fetch_all, fetch_one

from apps.b2b.raw.tables import (
    B2B_COMPANY_TABLE,
    B2B_EMPLOYEE_TABLE,
    B2B_WORKSPACE_MEMBERSHIP_TABLE,
    B2B_WORKSPACE_REQUEST_TABLE,
)
from apps.b2b.workspace.secondment import Module, RequestRole, RequestStatus


# ─── Orgs and the workspaces under them ───────────────────────────────────────

def org_id_for_company(company_id: int) -> int | None:
    row = fetch_one(
        f"SELECT org_id FROM {B2B_COMPANY_TABLE} WHERE id = %s", [company_id]
    )
    return row["org_id"] if row else None


def list_org_workspaces(org_id: int | None) -> list[dict[str, Any]]:
    """Every workspace in an organisation. Empty for an org that has none —
    and for `None`, which is a company whose backfill has not run."""
    if org_id is None:
        return []
    return fetch_all(
        f"SELECT id, name FROM {B2B_COMPANY_TABLE} "
        f"WHERE org_id = %s AND is_active = TRUE ORDER BY name ASC",
        [org_id],
    )


def search_org_people(
    org_id: int | None,
    *,
    exclude_company_id: int | None = None,
    exclude_employee_id: int | None = None,
    search: str | None = None,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """People across the whole org, for the picker on "So'rov yuborish".

    By default this spans every workspace in the org, the searcher's own one
    included — the picker is a "find anybody in the company by name, handle or
    phone" box, and leaving out the roster you already stand on made it read as
    broken for an org that has only one workspace. Pass `exclude_company_id` to
    go back to *other* workspaces only, and `exclude_employee_id` to drop the
    person doing the searching.

    Guests are left out. Somebody already lent into a workspace from a third
    one is not that workspace's to lend on, and the row that represents them is
    a copy — inviting it would create a guest of a guest.

    Bounded by `limit` because this is the one roster query that is not
    naturally small: an org with twenty workspaces has twenty rosters behind
    it, and the screen it feeds shows search results rather than a list.
    """
    if org_id is None:
        return []
    sql = f"""
        SELECT e.id, e.full_name, e.username, e.position, e.phone, e.photo,
               e.role, e.company_id, c.name AS company_name
          FROM {B2B_EMPLOYEE_TABLE} e
          JOIN {B2B_COMPANY_TABLE} c ON c.id = e.company_id
         WHERE c.org_id = %s
           AND e.is_active = TRUE
           AND e.is_guest = FALSE
    """
    params: list[Any] = [org_id]
    if exclude_company_id is not None:
        sql += " AND e.company_id <> %s"
        params.append(exclude_company_id)
    if exclude_employee_id is not None:
        sql += " AND e.id <> %s"
        params.append(exclude_employee_id)
    if search:
        needle = f"%{search.lstrip('@')}%"
        sql += (
            " AND (e.full_name ILIKE %s OR e.position ILIKE %s"
            " OR e.phone ILIKE %s OR e.username ILIKE %s)"
        )
        params += [needle, needle, needle, needle]
    sql += " ORDER BY e.full_name ASC LIMIT %s"
    params.append(limit)
    return fetch_all(sql, params)


# ─── Requests ─────────────────────────────────────────────────────────────────

def create_request(
    *,
    company_id: int,
    from_employee_id: int,
    to_employee_id: int,
    message: str,
    role: str,
    modules: Sequence[str],
    starts_at: datetime | None,
    ends_at: datetime | None,
) -> dict[str, Any] | None:
    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO {B2B_WORKSPACE_REQUEST_TABLE}
            (company_id, from_employee_id, to_employee_id, message, role, modules,
             starts_at, ends_at, status, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        __RETURNING_MARKER__
        """,
        [
            company_id,
            from_employee_id,
            to_employee_id,
            message,
            role,
            json.dumps(Module.clean(modules)),
            starts_at,
            ends_at,
            RequestStatus.PENDING,
            now,
            now,
        ],
    )


def get_request(request_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT * FROM {B2B_WORKSPACE_REQUEST_TABLE} WHERE id = %s", [request_id]
    )


def pending_request_between(company_id: int, to_employee_id: int) -> dict[str, Any] | None:
    """The live ask this workspace already has out to this person, if any.

    Checked before writing so a second tap of "So'rov yuborish" is answered
    with the request that already exists rather than with a unique-index
    error — the index is the backstop, this is the manners.
    """
    return fetch_one(
        f"SELECT * FROM {B2B_WORKSPACE_REQUEST_TABLE} "
        f"WHERE company_id = %s AND to_employee_id = %s AND status = %s",
        [company_id, to_employee_id, RequestStatus.PENDING],
    )


def list_requests_for_employee(employee_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
    """The inbox: what other workspaces have asked of this person."""
    return fetch_all(
        f"""
        SELECT r.*, c.name AS company_name,
               f.full_name AS from_full_name, f.photo AS from_photo,
               f.position AS from_position
          FROM {B2B_WORKSPACE_REQUEST_TABLE} r
          JOIN {B2B_COMPANY_TABLE} c ON c.id = r.company_id
          LEFT JOIN {B2B_EMPLOYEE_TABLE} f ON f.id = r.from_employee_id
         WHERE r.to_employee_id = %s
         ORDER BY r.created_at DESC
         LIMIT %s
        """,
        [employee_id, limit],
    )


def list_requests_from_company(company_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
    """The "Jo'natgan" tab: what this workspace has asked of other people."""
    return fetch_all(
        f"""
        SELECT r.*, c.name AS company_name,
               t.full_name AS to_full_name, t.photo AS to_photo,
               t.position AS to_position,
               tc.name AS to_company_name
          FROM {B2B_WORKSPACE_REQUEST_TABLE} r
          JOIN {B2B_COMPANY_TABLE} c ON c.id = r.company_id
          LEFT JOIN {B2B_EMPLOYEE_TABLE} t ON t.id = r.to_employee_id
          LEFT JOIN {B2B_COMPANY_TABLE} tc ON tc.id = t.company_id
         WHERE r.company_id = %s
         ORDER BY r.created_at DESC
         LIMIT %s
        """,
        [company_id, limit],
    )


def close_request(
    request_id: int, *, status: str, decline_reason: str | None = None
) -> int:
    """Move a pending request to one of its endings.

    Scoped to `status = 'pending'` in the WHERE rather than checked first: two
    taps on "Qabul qilish" a moment apart would otherwise both pass the check
    and both create a guest row. The row count is how the caller learns it
    lost that race.
    """
    now = timezone.now()
    return execute(
        f"""
        UPDATE {B2B_WORKSPACE_REQUEST_TABLE}
           SET status = %s, decline_reason = %s, responded_at = %s, updated_at = %s
         WHERE id = %s AND status = %s
        """,
        [status, decline_reason, now, now, request_id, RequestStatus.PENDING],
    )


# ─── Memberships, and the guest rows behind them ──────────────────────────────

def create_guest_employee(
    *, company_id: int, home: dict[str, Any], role: str
) -> dict[str, Any] | None:
    """The employee row a guest works through in the host workspace.

    A copy of the parts of them the host needs to render a row — name, photo,
    handle — and nothing else. Their passport, their limits and their
    attendance stay in the workspace that hired them.
    """
    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO {B2B_EMPLOYEE_TABLE}
            (company_id, full_name, username, position, phone, email, photo, role,
             is_active, is_guest, is_hidden, home_employee_id, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, TRUE, %s, %s, %s, %s)
        __RETURNING_MARKER__
        """,
        [
            company_id,
            home.get("full_name"),
            home.get("username"),
            home.get("position"),
            home.get("phone"),
            home.get("email"),
            home.get("photo"),
            RequestRole.to_employee_role(role),
            RequestRole.is_hidden(role),
            home["id"],
            now,
            now,
        ],
    )


def create_membership(
    *,
    company_id: int,
    employee_id: int,
    home_employee_id: int,
    request_id: int | None,
    role: str,
    modules: Sequence[str],
    starts_at: datetime | None,
    ends_at: datetime | None,
) -> dict[str, Any] | None:
    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO {B2B_WORKSPACE_MEMBERSHIP_TABLE}
            (company_id, employee_id, home_employee_id, request_id, role, modules,
             starts_at, ends_at, is_active, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s)
        __RETURNING_MARKER__
        """,
        [
            company_id,
            employee_id,
            home_employee_id,
            request_id,
            role,
            json.dumps(Module.clean(modules)),
            starts_at,
            ends_at,
            now,
            now,
        ],
    )


def membership_for_employee(employee_id: int) -> dict[str, Any] | None:
    """The secondment an employee row *is*, or None for a permanent hire.

    Read on every authenticated request, which is why it is a single indexed
    lookup on a unique column and not a join.
    """
    return fetch_one(
        f"SELECT * FROM {B2B_WORKSPACE_MEMBERSHIP_TABLE} WHERE employee_id = %s",
        [employee_id],
    )


def list_memberships_for_person(home_employee_id: int) -> list[dict[str, Any]]:
    """Every workspace this person is currently lent to, newest first.

    Feeds the workspace switcher: their home workspace plus one row per live
    secondment.
    """
    return fetch_all(
        f"""
        SELECT m.*, c.name AS company_name
          FROM {B2B_WORKSPACE_MEMBERSHIP_TABLE} m
          JOIN {B2B_COMPANY_TABLE} c ON c.id = m.company_id
         WHERE m.home_employee_id = %s AND m.is_active = TRUE
         ORDER BY m.created_at DESC
        """,
        [home_employee_id],
    )


def end_membership(membership_id: int) -> None:
    """Close a secondment and retire the guest row it created.

    Both halves matter. Clearing `is_active` is what the permission layer
    reads; deactivating the employee row is what takes the guest out of the
    roster, the assignee pickers and the chat member lists — leaving it active
    would keep offering a person who can no longer sign in.
    """
    now = timezone.now()
    row = fetch_one(
        f"SELECT employee_id FROM {B2B_WORKSPACE_MEMBERSHIP_TABLE} WHERE id = %s",
        [membership_id],
    )
    execute(
        f"UPDATE {B2B_WORKSPACE_MEMBERSHIP_TABLE} "
        f"SET is_active = FALSE, ended_at = %s, updated_at = %s WHERE id = %s",
        [now, now, membership_id],
    )
    if row:
        execute(
            f"UPDATE {B2B_EMPLOYEE_TABLE} SET is_active = FALSE, updated_at = %s "
            f"WHERE id = %s AND is_guest = TRUE",
            [now, row["employee_id"]],
        )


def list_expired_memberships(now: datetime | None = None) -> list[dict[str, Any]]:
    """Secondments whose end has passed but which nobody has closed yet."""
    return fetch_all(
        f"SELECT id, company_id, employee_id, home_employee_id FROM {B2B_WORKSPACE_MEMBERSHIP_TABLE} "
        f"WHERE is_active = TRUE AND ends_at IS NOT NULL AND ends_at < %s",
        [now or timezone.now()],
    )
