"""Raw-SQL data access for secondments — the ask, and the standing it creates.

Split out of `repository.py` rather than added to it: that module is the
workspace's own data, all of it scoped by one `company_id`, and these queries
are the one place in the schema that deliberately reaches *across* that
boundary. Keeping them apart is what makes "which query can see another
workspace" a question with a short answer.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Sequence

from django.utils import timezone

from shared.raw.db import execute, fetch_all, fetch_one

from apps.b2b.raw.tables import (
    B2B_ACCOUNT_TABLE,
    B2B_COMPANY_TABLE,
    B2B_EMPLOYEE_TABLE,
    B2B_WORKSPACE_MEMBERSHIP_TABLE,
    B2B_WORKSPACE_REQUEST_TABLE,
)
from apps.b2b.workspace.people_search import people_search_clause
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
    exclude_account_id: int | None = None,
    employee_id: int | None = None,
    search: str | None = None,
    limit: int | None = 30,
) -> list[dict[str, Any]]:
    """Roster rows across the whole org — one per seat, not per person.

    By default this spans every workspace in the org, the searcher's own one
    included. Pass `exclude_company_id` to go back to *other* workspaces only,
    `exclude_employee_id` / `exclude_account_id` to drop the person doing the
    searching, and `employee_id` to look up one row (the send checks its
    target this way — reading "the first thirty" and looking for it there
    refused anybody past the thirtieth name in a bigger org).

    Guests and chat-only members are left out. Somebody already lent into a
    workspace from a third one is not that workspace's to lend on, and the row
    that represents them is a copy — inviting it would create a guest of a
    guest; a chat-only member has no seat at all.

    The picker does not read this directly: it wants one row per *person*,
    which is [search_org_persons].
    """
    if org_id is None:
        return []
    # The handle is read off the account, not off the roster row. Each row
    # keeps a copy of it so ordinary listing needs no join, but that copy is
    # written when the membership is created and is empty for everybody who
    # picked their handle after joining — which is most people, since
    # registration is `phone → OTP → name → username` and a workspace can be
    # created or joined at any point in it. Searching the copy is what made
    # "@aziz" find nobody while the picker's own placeholder invited it.
    sql = f"""
        SELECT e.id, e.full_name,
               COALESCE(a.username, e.username) AS username,
               e.position, e.phone, e.photo,
               e.role, e.company_id, c.name AS company_name,
               e.account_id
          FROM {B2B_EMPLOYEE_TABLE} e
          JOIN {B2B_COMPANY_TABLE} c ON c.id = e.company_id
          LEFT JOIN {B2B_ACCOUNT_TABLE} a ON a.id = e.account_id
         WHERE c.org_id = %s
           AND e.is_active = TRUE
           AND e.is_guest = FALSE
           AND COALESCE(e.is_chat_only, FALSE) = FALSE
    """
    params: list[Any] = [org_id]
    if exclude_company_id is not None:
        sql += " AND e.company_id <> %s"
        params.append(exclude_company_id)
    if exclude_employee_id is not None:
        sql += " AND e.id <> %s"
        params.append(exclude_employee_id)
    if exclude_account_id is not None:
        sql += " AND e.account_id IS DISTINCT FROM %s"
        params.append(exclude_account_id)
    if employee_id is not None:
        sql += " AND e.id = %s"
        params.append(employee_id)
    if search:
        clause, clause_params = people_search_clause(search)
        sql += clause
        params += clause_params
    sql += " ORDER BY e.full_name ASC, e.id ASC"
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)
    return fetch_all(sql, params)


def _person_key(seat: dict[str, Any], by_phone: dict[str, Any]) -> Any:
    """Which person a roster row belongs to.

    The account, when the row has one — one account is one human. A row with
    no account yet (imported from a roster, never signed in) is matched on its
    phone to a person already seen, because an account's phone is unique and
    the start-up link joins such rows to that account by exactly this.
    """
    digits = re.sub(r"\D", "", seat.get("phone") or "")[-9:]
    key = ("a", seat["account_id"]) if seat.get("account_id") else None
    if key is None and len(digits) == 9:
        key = by_phone.get(digits, ("p", digits))
    if key is None:
        key = ("e", seat["id"])
    if len(digits) == 9:
        by_phone.setdefault(digits, key)
    return key


def search_org_persons(
    org_id: int | None,
    *,
    here_company_id: int,
    exclude_employee_id: int | None = None,
    exclude_account_id: int | None = None,
    search: str | None = None,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """The picker on "So'rov yuborish": one row per person in the org.

    A person holds a seat in every workspace they work in, and listing seats
    showed somebody on three workspaces three times. Each row here is one
    person with every workspace they sit in (`workspaces`), and whether one of
    them is the searcher's own (`in_this_workspace`) — somebody already here
    cannot be asked in, and the app says so on the row instead of letting the
    send fail.

    `id` is the seat a request goes to: the person's own seat outside this
    workspace, the earliest one. Which seat it is does not matter to the
    person — the inbox and the answer read every seat of theirs, see
    [person_seat_ids].
    """
    seats = search_org_people(
        org_id,
        exclude_employee_id=exclude_employee_id,
        exclude_account_id=exclude_account_id,
        search=search,
        # Seats, not people: a person on three workspaces is three of them.
        limit=limit * 5,
    )
    people: dict[Any, dict[str, Any]] = {}
    by_phone: dict[str, Any] = {}
    for seat in seats:
        key = _person_key(seat, by_phone)
        person = people.get(key)
        here = seat["company_id"] == here_company_id
        if person is None:
            if len(people) >= limit:
                continue
            person = people[key] = {
                **seat,
                "workspaces": [],
                "in_this_workspace": False,
                "_elsewhere_id": None,
            }
        person["workspaces"].append(
            {"id": seat["company_id"], "name": seat["company_name"]}
        )
        person["in_this_workspace"] = person["in_this_workspace"] or here
        if not here and (
            person["_elsewhere_id"] is None or seat["id"] < person["_elsewhere_id"]
        ):
            person["_elsewhere_id"] = seat["id"]
            for field in ("id", "company_id", "company_name", "position", "role"):
                person[field] = seat[field]

    elsewhere_ids = [p["id"] for p in people.values() if not p["in_this_workspace"]]
    # A person lent here by an earlier request sits here as a guest row, which
    # the seat query leaves out — asked once more, they are already here.
    lent_here = (
        _people_lent_to(here_company_id, elsewhere_ids) if elsewhere_ids else set()
    )
    result = []
    for person in people.values():
        person.pop("_elsewhere_id")
        if person["id"] in lent_here:
            person["in_this_workspace"] = True
        person["workspaces"].sort(key=lambda w: (w["name"] or "").lower())
        result.append(person)
    return result


def _people_lent_to(company_id: int, employee_ids: Sequence[int]) -> set[int]:
    """Which of these seats' people already have a guest seat in `company_id`."""
    rows = fetch_all(
        f"""
        SELECT x.id
          FROM {B2B_EMPLOYEE_TABLE} x
         WHERE x.id = ANY(%s)
           AND EXISTS (
               SELECT 1 FROM {B2B_EMPLOYEE_TABLE} g
                WHERE g.company_id = %s
                  AND g.is_active = TRUE
                  AND (g.home_employee_id = x.id
                       OR (x.account_id IS NOT NULL AND g.account_id = x.account_id)
                       OR g.home_employee_id IN (
                           SELECT o.id FROM {B2B_EMPLOYEE_TABLE} o
                            WHERE x.account_id IS NOT NULL
                              AND o.account_id = x.account_id))
           )
        """,
        [list(employee_ids), company_id],
    )
    return {row["id"] for row in rows}


def person_seat_ids(employee_id: int) -> list[int]:
    """Every seat of the person behind this roster row, this one included.

    A request is addressed to one seat, but it is asked of a person: whichever
    workspace they have open, it is in their inbox and theirs to answer.
    """
    rows = fetch_all(
        f"""
        SELECT o.id
          FROM {B2B_EMPLOYEE_TABLE} x
          JOIN {B2B_EMPLOYEE_TABLE} o
            ON o.id = x.id
            OR (x.account_id IS NOT NULL AND o.account_id = x.account_id)
         WHERE x.id = %s
        """,
        [employee_id],
    )
    return sorted({row["id"] for row in rows} | {employee_id})


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
    # Any seat of theirs: the picker addresses a person through one of their
    # seats, and asking the same person again through another one is still
    # the second tap.
    return fetch_one(
        f"SELECT * FROM {B2B_WORKSPACE_REQUEST_TABLE} "
        f"WHERE company_id = %s AND to_employee_id = ANY(%s) AND status = %s "
        f"ORDER BY id LIMIT 1",
        [company_id, person_seat_ids(to_employee_id), RequestStatus.PENDING],
    )


def list_requests_for_employee(employee_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
    """The inbox: what other workspaces have asked of this person — through
    any of their seats, whichever workspace they are reading it from."""
    return fetch_all(
        f"""
        SELECT r.*, c.name AS company_name,
               f.full_name AS from_full_name, f.photo AS from_photo,
               f.position AS from_position
          FROM {B2B_WORKSPACE_REQUEST_TABLE} r
          JOIN {B2B_COMPANY_TABLE} c ON c.id = r.company_id
          LEFT JOIN {B2B_EMPLOYEE_TABLE} f ON f.id = r.from_employee_id
         WHERE r.to_employee_id = ANY(%s)
         ORDER BY r.created_at DESC
         LIMIT %s
        """,
        [person_seat_ids(employee_id), limit],
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
