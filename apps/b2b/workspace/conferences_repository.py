"""The `b2b_conference` rows — one per conference, live or finished.

Raw SQL like the rest of the workspace, and behind a module of its own for
the same reason `calls_repository` is: the service in `conferences.py` is
tested against a mock of this, so every rule it enforces can be exercised
without a database.

The one rule that lives here rather than there is **ending is conditional**.
`finish` only closes a row that is still live and says so by returning the
row or `None`, so the organiser pressing "Tugatish" twice, or two of their
devices pressing it at once, settle on one answer instead of both believing
they closed it.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence

from django.utils import timezone

from apps.b2b.raw.tables import B2B_CONFERENCE_TABLE, B2B_EMPLOYEE_TABLE
from shared.raw.db import execute, fetch_all, fetch_one


class ConferenceStatus:
    LIVE = "live"
    ENDED = "ended"


class ConferenceScope:
    """Who was invited — kept on the row because the invitation card says it
    ("Butun kompaniya", "2 bo'lim") and the membership list alone cannot: a
    department of everybody looks exactly like a company-wide one."""

    ALL = "all"
    DEPARTMENTS = "departments"
    EMPLOYEES = "employees"
    CHOICES = (ALL, DEPARTMENTS, EMPLOYEES)


def create_conference(
    *,
    company_id: int,
    room_name: str,
    title: str,
    thread_id: int,
    scope: str,
    created_by: int,
) -> dict[str, Any] | None:
    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO {B2B_CONFERENCE_TABLE}
            (company_id, room_name, title, thread_id, scope, created_by,
             status, started_at, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        [
            company_id,
            room_name,
            title,
            thread_id,
            scope,
            created_by,
            ConferenceStatus.LIVE,
            now,
            now,
            now,
        ],
    )


def set_message(conference_id: int, message_id: int) -> None:
    """Remember which message carries the invitation, so ending the
    conference can rewrite that card rather than leave a live-looking one."""
    execute(
        f"UPDATE {B2B_CONFERENCE_TABLE} SET message_id = %s, updated_at = %s WHERE id = %s",
        [message_id, timezone.now(), conference_id],
    )


def get_conference(conference_id: int, company_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT * FROM {B2B_CONFERENCE_TABLE} WHERE id = %s AND company_id = %s",
        [conference_id, company_id],
    )


def live_for_thread(thread_id: int) -> dict[str, Any] | None:
    """The conference still running in a room, if any — what the invitation
    card and the room's header both ask."""
    return fetch_one(
        f"SELECT * FROM {B2B_CONFERENCE_TABLE} "
        "WHERE thread_id = %s AND status = %s ORDER BY id DESC LIMIT 1",
        [thread_id, ConferenceStatus.LIVE],
    )


def live_for_threads(thread_ids: Sequence[int]) -> dict[int, dict[str, Any]]:
    """The same question for a whole chat list, in one query — so drawing
    fifty rows is one round trip rather than fifty."""
    ids = [int(i) for i in dict.fromkeys(thread_ids or [])]
    if not ids:
        return {}
    rows = fetch_all(
        f"SELECT DISTINCT ON (thread_id) * FROM {B2B_CONFERENCE_TABLE} "
        "WHERE thread_id = ANY(%s) AND status = %s "
        "ORDER BY thread_id, id DESC",
        [ids, ConferenceStatus.LIVE],
    )
    return {row["thread_id"]: row for row in rows}


def finish(conference_id: int, *, ended_at: datetime | None = None) -> dict[str, Any] | None:
    """Close a live conference. Returns `None` when it was already closed —
    the caller then knows somebody else got there first and should not
    announce the ending a second time."""
    now = timezone.now()
    return fetch_one(
        f"UPDATE {B2B_CONFERENCE_TABLE} SET status = %s, ended_at = %s, updated_at = %s "
        "WHERE id = %s AND status = %s RETURNING *",
        [ConferenceStatus.ENDED, ended_at or now, now, conference_id, ConferenceStatus.LIVE],
    )


def stale_live(cutoff: datetime) -> list[dict[str, Any]]:
    """Conferences nobody ever closed — started before `cutoff` and still
    live. The organiser leaving does not end a conference (the others may
    carry on), so without this sweep a room that emptied out at lunchtime is
    still advertised as running the next morning."""
    return fetch_all(
        f"SELECT * FROM {B2B_CONFERENCE_TABLE} WHERE status = %s AND started_at < %s "
        "ORDER BY started_at ASC LIMIT 500",
        [ConferenceStatus.LIVE, cutoff],
    )


def employee_ids_in_departments(company_id: int, department_ids: Sequence[int]) -> list[int]:
    """Everybody on the roster who belongs to one of these departments.

    Hidden employees are left out for the same reason the team list leaves
    them out — a conference invitation is a message, and a message to a row
    nobody is meant to see is a leak of that row's existence.
    """
    ids = [int(i) for i in dict.fromkeys(department_ids or [])]
    if not ids:
        return []
    return [
        row["id"]
        for row in fetch_all(
            f"SELECT id FROM {B2B_EMPLOYEE_TABLE} "
            "WHERE company_id = %s AND department_id = ANY(%s) "
            "AND is_active = TRUE AND is_hidden = FALSE "
            "ORDER BY full_name ASC",
            [company_id, ids],
        )
    ]


def company_employee_ids(company_id: int) -> list[int]:
    """Everybody a company-wide conference reaches. Same filter as the team
    screen, so "hamma" means the same set of people in both places."""
    return [
        row["id"]
        for row in fetch_all(
            f"SELECT id FROM {B2B_EMPLOYEE_TABLE} "
            "WHERE company_id = %s AND is_active = TRUE AND is_hidden = FALSE "
            "ORDER BY full_name ASC",
            [company_id],
        )
    ]
