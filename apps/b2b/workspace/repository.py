"""Raw-SQL data access for the B2B mobile workspace.

Follows the same conventions as ``apps/b2b/repository.py``: plain dicts in and
out, every query scoped by ``company_id``, no ORM.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Iterable, Sequence

from django.utils import timezone

from shared.raw.db import execute, fetch_all, fetch_one
from apps.b2b.workspace.storage import photo_url as _photo_url

from apps.b2b.models import (
    EmployeeRole,
    LeadActivityKind,
    LeadKind,
    LeadLostReason,
    LeadQuality,
    LeadSource,
    LeadStage,
    LeadStatus,
    PaymentMethod,
    TaskActivityKind,
)
from apps.b2b.raw.tables import (
    B2B_CALENDAR_EVENT_TABLE,
    B2B_CALENDAR_PARTICIPANT_TABLE,
    B2B_CALENDAR_REMINDER_TABLE,
    B2B_CHAT_MEMBER_TABLE,
    B2B_CHAT_MESSAGE_TABLE,
    B2B_CHAT_REACTION_TABLE,
    B2B_CHAT_THREAD_TABLE,
    B2B_COMPANY_TABLE,
    B2B_DEPARTMENT_TABLE,
    B2B_ATTENDANCE_TABLE,
    B2B_ATTENDANCE_LOCATION_TABLE,
    B2B_SUPPORT_MESSAGE_TABLE,
    B2B_EMPLOYEE_OF_MONTH_TABLE,
    B2B_EMPLOYEE_TABLE,
    B2B_NOTIFICATION_TABLE,
    B2B_TASK_ACTIVITY_TABLE,
    B2B_TASK_ASSIGNEE_TABLE,
    B2B_TASK_COMMENT_TABLE,
    B2B_TASK_SUBTASK_TABLE,
    B2B_TASK_TABLE,
    B2B_USER_TABLE,
    B2B_WORKSPACE_FILE_TABLE,
    B2B_WORKSPACE_FOLDER_TABLE,
    B2B_WORKSPACE_LEAD_TABLE,
    B2B_WORKSPACE_CUSTOMER_TABLE,
    B2B_WORKSPACE_LEAD_ACTIVITY_TABLE,
    B2B_WORKSPACE_LEAD_ITEM_TABLE,
    B2B_WORKSPACE_NOTE_TABLE,
)

# ─── Identity ─────────────────────────────────────────────────────────────────

_DIGITS_RE = re.compile(r"\D")


def normalize_phone(phone: str | None) -> str:
    """Digits only, so ``+998 90 123 45 67`` and ``998901234567`` compare equal.

    Phone numbers reach this table from three directions — typed into the web
    dashboard, parsed off a passport scan, and typed into the mobile login box —
    and each formats them differently.
    """
    return _DIGITS_RE.sub("", phone or "")


def _phone_suffix(phone: str | None) -> str | None:
    """The national part (last 9 digits for UZ) used to match numbers whose
    country code may or may not have been typed."""
    digits = normalize_phone(phone)
    return digits[-9:] if len(digits) >= 9 else (digits or None)


def find_employee_by_phone(phone: str) -> dict[str, Any] | None:
    """The row somebody signs in as, found by their number.

    Guest rows are excluded, and that exclusion is what makes secondments
    safe. A person lent to another workspace has a second employee row there
    carrying the same phone number, and this lookup is unscoped by design —
    it is how a login with nothing but a phone number finds anybody at all.
    Without the filter, which workspace somebody landed in on sign-in would be
    decided by an `ORDER BY id`, and a guest row created after a home row was
    deactivated would win outright.

    Signing in always lands on the workspace that hired you. Getting to one
    you were lent to is a deliberate switch afterwards — see
    `WorkspaceSwitchView`.
    """
    suffix = _phone_suffix(phone)
    if not suffix:
        return None
    return fetch_one(
        f"""
        SELECT * FROM {B2B_EMPLOYEE_TABLE}
        WHERE is_active = TRUE
          AND is_guest = FALSE
          AND phone IS NOT NULL
          AND regexp_replace(phone, '[^0-9]', '', 'g') LIKE %s
        ORDER BY id ASC
        LIMIT 1
        """,
        [f"%{suffix}"],
    )


def find_b2b_user_by_phone(phone: str) -> dict[str, Any] | None:
    suffix = _phone_suffix(phone)
    if not suffix:
        return None
    return fetch_one(
        f"""
        SELECT * FROM {B2B_USER_TABLE}
        WHERE is_active = TRUE
          AND regexp_replace(phone, '[^0-9]', '', 'g') LIKE %s
        ORDER BY id ASC
        LIMIT 1
        """,
        [f"%{suffix}"],
    )


def ensure_workspace_employee(b2b_user: dict[str, Any]) -> dict[str, Any] | None:
    """Return the employee row that represents ``b2b_user`` in the workspace,
    creating it if the owner was never added to the roster.

    The mobile app's identity is *always* an employee row — tasks, events and
    chat all reference ``b2b_employee(id)``. Owners are created by
    ``create_b2b_owner`` as a ``b2b_user`` with no matching employee, so
    without this they could log in and then be invisible to every feature.
    """
    existing = find_employee_by_phone(b2b_user["phone"])
    if existing:
        # An owner promoted after the fact still carries the roster's default
        # 'employee' role; the login table is the authority on who owns the
        # company, so bring the employee row in line with it.
        if b2b_user.get("role") == EmployeeRole.OWNER and existing.get("role") != EmployeeRole.OWNER:
            return update_employee_role(existing["id"], EmployeeRole.OWNER) or existing
        return existing

    full_name = " ".join(
        part for part in [b2b_user.get("first_name"), b2b_user.get("last_name")] if part
    ).strip() or b2b_user["phone"]

    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO {B2B_EMPLOYEE_TABLE}
            (company_id, full_name, phone, email, role, is_active, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, TRUE, %s, %s)
        RETURNING *
        """,
        [
            b2b_user["company_id"],
            full_name,
            b2b_user["phone"],
            b2b_user.get("email"),
            b2b_user.get("role") or EmployeeRole.EMPLOYEE,
            now,
            now,
        ],
    )


def set_employee_fcm_token(employee_id: int, token: str | None) -> None:
    execute(
        f"UPDATE {B2B_EMPLOYEE_TABLE} SET fcm_token = %s, updated_at = %s WHERE id = %s",
        [token, timezone.now(), employee_id],
    )


def clear_employee_fcm_tokens(tokens: list[str]) -> None:
    """Drop the workspace tokens Firebase has just reported as dead.

    Handed to `FCMService.send_to_tokens` as `deactivate_invalid` by every B2B
    sender. Its default clears `public.users` instead, which holds the consumer
    and partner tokens and never a workspace one — so without this a token from
    an uninstalled app stayed in `b2b_employee` and was re-sent to on every
    chat message, every new lead and every mail, forever.

    Scoped to this table on purpose: a consumer token is somebody else's row in
    somebody else's Firebase project, and nothing here has any business
    touching it.
    """
    if not tokens:
        return
    execute(
        f"UPDATE {B2B_EMPLOYEE_TABLE} SET fcm_token = NULL, updated_at = %s "
        f"WHERE fcm_token = __ANY_MARKER__(%s)",
        [timezone.now(), list(tokens)],
    )


def unread_badges_for_tokens(tokens: list[str]) -> dict[str, int]:
    """How many unread feed rows the owner of each of these phones has.

    Handed to `FCMService.send_to_tokens` as `badge_for` by every workspace
    sender, and read *after* the sender has written its own feed rows, so the
    number already includes the push it is riding on. That is the number the
    app icon shows — iOS draws exactly what the payload says, and a launcher
    on Android that shows a count reads the same figure — and it is cleared by
    `POST /notifications/read/`, which the app calls whenever it comes to the
    foreground.

    Counted from `b2b_notification` rather than kept as a counter of its own
    because the feed is already the record of what each employee has and has
    not seen; a second number would only ever drift from it.
    """
    if not tokens:
        return {}
    rows = fetch_all(
        f"SELECT e.fcm_token AS token, COUNT(n.id) AS unread "
        f"FROM {B2B_EMPLOYEE_TABLE} e "
        f"LEFT JOIN {B2B_NOTIFICATION_TABLE} n "
        f"ON n.employee_id = e.id AND n.is_read = FALSE "
        f"WHERE e.fcm_token = __ANY_MARKER__(%s) "
        f"GROUP BY e.fcm_token",
        [list(tokens)],
    )
    return {row["token"]: int(row["unread"] or 0) for row in rows}


def list_task_assignee_recipients(
    task_id: int,
    *,
    exclude_employee_id: int | None = None,
    only_employee_ids: Sequence[int] | None = None,
) -> list[dict[str, Any]]:
    """Who to tell about a task, with their push token.

    The author is excluded by the caller rather than here: a manager who puts
    a task on their own plate along with two colleagues should still see it in
    their feed, but not be pushed about something they just typed.

    `only_employee_ids` narrows it to the people who were *just* added. An
    edit that puts a fourth person on a task must not push it at the three who
    have had it since Monday — to them that reads as a second task.
    """
    sql = (
        f"SELECT a.employee_id, e.company_id, e.fcm_token "
        f"FROM {B2B_TASK_ASSIGNEE_TABLE} a "
        f"JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = a.employee_id "
        f"WHERE a.task_id = %s AND e.is_active = TRUE"
    )
    params: list[Any] = [task_id]
    if exclude_employee_id is not None:
        sql += " AND a.employee_id <> %s"
        params.append(exclude_employee_id)
    if only_employee_ids is not None:
        if not only_employee_ids:
            return []
        sql += " AND a.employee_id = __ANY_MARKER__(%s)"
        params.append(list(only_employee_ids))
    return fetch_all(sql, params)


def list_event_participant_recipients(
    event_id: int,
    *,
    exclude_employee_id: int | None = None,
    only_employee_ids: Sequence[int] | None = None,
) -> list[dict[str, Any]]:
    """The same, for everyone invited to a calendar event."""
    sql = (
        f"SELECT p.employee_id, e.company_id, e.fcm_token "
        f"FROM {B2B_CALENDAR_PARTICIPANT_TABLE} p "
        f"JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = p.employee_id "
        f"WHERE p.event_id = %s AND e.is_active = TRUE"
    )
    params: list[Any] = [event_id]
    if exclude_employee_id is not None:
        sql += " AND p.employee_id <> %s"
        params.append(exclude_employee_id)
    if only_employee_ids is not None:
        if not only_employee_ids:
            return []
        sql += " AND p.employee_id = __ANY_MARKER__(%s)"
        params.append(list(only_employee_ids))
    return fetch_all(sql, params)


def list_events_starting_between(start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Events whose start falls in a window, across every company.

    The reminder pass is the one caller, and it runs for the whole deployment
    rather than per company — so unlike everything else in this module it is
    deliberately not scoped by `company_id`. All-day entries are left out:
    their `starts_at` is midnight, and a "30 minutes to go" at 23:30 for
    something that is really just a date on the calendar wakes people up for
    nothing.
    """
    return fetch_all(
        f"SELECT id, company_id, title, event_type, starts_at, location "
        f"FROM {B2B_CALENDAR_EVENT_TABLE} "
        f"WHERE all_day = FALSE AND starts_at >= %s AND starts_at <= %s "
        f"ORDER BY starts_at",
        [start, end],
    )


def claim_event_reminder(event_id: int, minutes_before: int) -> bool:
    """Take the right to send one reminder, exactly once.

    True means this call won the row and should send; False means it was
    already sent — by the previous minute's pass catching up, or by a second
    worker running the same beat tick. The unique constraint is what decides,
    so two workers racing cannot both win.
    """
    inserted = execute(
        f"INSERT INTO {B2B_CALENDAR_REMINDER_TABLE} (event_id, minutes_before, sent_at) "
        f"VALUES (%s, %s, %s) ON CONFLICT (event_id, minutes_before) DO NOTHING",
        [event_id, minutes_before, timezone.now()],
    )
    return inserted > 0


def clear_event_reminders(event_id: int) -> None:
    """Forget what was sent for an event, so its reminders are due again.

    Called when an event is moved. Without it, pushing a meeting from 10:00 to
    16:00 leaves the 30-minute row already claimed and the attendees get no
    warning at all for the time it actually happens.
    """
    execute(
        f"DELETE FROM {B2B_CALENDAR_REMINDER_TABLE} WHERE event_id = %s",
        [event_id],
    )


def list_company_recipients(
    company_id: int, *, exclude_employee_id: int | None = None
) -> list[dict[str, Any]]:
    """The whole active roster, with whatever push token each one has.

    Unlike [list_employee_fcm_tokens] this keeps the people who have no token:
    a lead posted to the board belongs in everybody's notification list
    whether or not their phone can be reached. Somebody who never granted the
    permission, or who is reading on the dashboard, still has to see that
    there is a lead waiting to be claimed.
    """
    sql = (
        f"SELECT id AS employee_id, company_id, fcm_token FROM {B2B_EMPLOYEE_TABLE} "
        f"WHERE company_id = %s AND is_active = TRUE"
    )
    params: list[Any] = [company_id]
    if exclude_employee_id is not None:
        sql += " AND id <> %s"
        params.append(exclude_employee_id)
    return fetch_all(sql, params)


def list_employee_fcm_tokens(company_id: int, *, exclude_employee_id: int | None = None) -> list[str]:
    """Push targets for 'notify the whole roster' events (e.g. a new lead)."""
    sql = (
        f"SELECT fcm_token FROM {B2B_EMPLOYEE_TABLE} "
        f"WHERE company_id = %s AND is_active = TRUE AND fcm_token IS NOT NULL"
    )
    params: list[Any] = [company_id]
    if exclude_employee_id is not None:
        sql += " AND id <> %s"
        params.append(exclude_employee_id)
    return [row["fcm_token"] for row in fetch_all(sql, params)]


def update_employee_role(employee_id: int, role: str) -> dict[str, Any] | None:
    return fetch_one(
        f"UPDATE {B2B_EMPLOYEE_TABLE} SET role = %s, updated_at = %s WHERE id = %s RETURNING *",
        [role, timezone.now(), employee_id],
    )


def get_workspace_employee(employee_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        SELECT e.*, d.name AS department_name, d.color AS department_color
        FROM {B2B_EMPLOYEE_TABLE} e
        LEFT JOIN {B2B_DEPARTMENT_TABLE} d ON d.id = e.department_id
        WHERE e.id = %s AND e.is_active = TRUE
        """,
        [employee_id],
    )


def list_team(company_id: int, *, search: str | None = None) -> list[dict[str, Any]]:
    sql = f"""
        SELECT e.*, d.name AS department_name, d.color AS department_color
        FROM {B2B_EMPLOYEE_TABLE} e
        LEFT JOIN {B2B_DEPARTMENT_TABLE} d ON d.id = e.department_id
        WHERE e.company_id = %s AND e.is_active = TRUE AND e.is_hidden = FALSE
    """
    params: list[Any] = [company_id]
    if search:
        # Name, position, phone and handle. The phone and the handle are what
        # the picker on "So'rov yuborish" promises in its own placeholder, and
        # a search that quietly ignores two of the three things it offers to
        # match on is worse than not offering them.
        #
        # A leading "@" is dropped: it is how a handle is written and read
        # everywhere in the app, and it is not part of the stored value.
        needle = f"%{search.lstrip('@')}%"
        sql += (
            " AND (e.full_name ILIKE %s OR e.position ILIKE %s"
            " OR e.phone ILIKE %s OR e.username ILIKE %s)"
        )
        params += [needle, needle, needle, needle]
    sql += " ORDER BY e.full_name ASC"
    return fetch_all(sql, params)


def company_employee_ids(company_id: int) -> list[int]:
    """Just the ids on a workspace's roster.

    Presence is answered against this rather than against a set kept in the
    cache: the roster is the authority on who could be online, it is tens of
    rows, and a cached set would need every hire and departure to remember to
    update it.
    """
    return [
        row["id"]
        for row in fetch_all(
            f"SELECT id FROM {B2B_EMPLOYEE_TABLE} "
            "WHERE company_id = %s AND is_active = TRUE",
            [company_id],
        )
    ]


def employee_ids_in_company(company_id: int, employee_ids: Iterable[int]) -> set[int]:
    """The subset of ``employee_ids`` that really belongs to this company —
    the guard that stops one company from assigning work to another's staff."""
    ids = [int(i) for i in employee_ids]
    if not ids:
        return set()
    rows = fetch_all(
        f"""
        SELECT id FROM {B2B_EMPLOYEE_TABLE}
        WHERE company_id = %s AND is_active = TRUE AND id = __ANY_MARKER__(%s)
        """,
        [company_id, ids],
    )
    return {row["id"] for row in rows}


# ─── Tasks ────────────────────────────────────────────────────────────────────

# Three, and only three: a task is new, being worked on, or finished. There
# used to be a fourth, "review", which no screen ever offered as a place to put
# something — the dashboard folded it into "in progress" for display and the
# app had no tab for it at all, so work could land in a status nobody could see
# or move. Old rows are normalised to "in_progress" by `create_b2b_tables`.
TASK_STATUSES = ("todo", "in_progress", "done")
TASK_PRIORITIES = ("low", "medium", "high", "urgent")

# What a task's uploads are stored as in `b2b_workspace_file.kind`. Two kinds
# rather than one flag: the drive lists `kind = 'file'`, the storage breakdown
# groups by the same column, and both stay right without knowing that a task
# can carry two different sorts of thing.
TASK_VOICE_KIND = "task"
TASK_FILE_KIND = "task_file"


def _attach_task_children(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Load assignees, subtasks and comments for a page of tasks in three
    queries instead of three per task."""
    if not tasks:
        return tasks

    ids = [t["id"] for t in tasks]
    by_id = {t["id"]: t for t in tasks}
    for task in tasks:
        task["assignee_ids"] = []
        task["subtasks"] = []
        task["comments"] = []
        task["voice"] = None
        task["files"] = []

    for row in fetch_all(
        f"SELECT task_id, employee_id FROM {B2B_TASK_ASSIGNEE_TABLE} "
        f"WHERE task_id = __ANY_MARKER__(%s)",
        [ids],
    ):
        by_id[row["task_id"]]["assignee_ids"].append(row["employee_id"])

    for row in fetch_all(
        f"SELECT id, task_id, title, is_done, position FROM {B2B_TASK_SUBTASK_TABLE} "
        f"WHERE task_id = __ANY_MARKER__(%s) ORDER BY position ASC, id ASC",
        [ids],
    ):
        by_id[row["task_id"]]["subtasks"].append(row)

    for row in fetch_all(
        f"SELECT id, task_id, author_id, text, created_at FROM {B2B_TASK_COMMENT_TABLE} "
        f"WHERE task_id = __ANY_MARKER__(%s) ORDER BY created_at ASC, id ASC",
        [ids],
    ):
        by_id[row["task_id"]]["comments"].append(row)

    # Everything a task carries, in one query: the clip recorded while it was
    # written and the documents attached to it. `kind` is what tells them
    # apart — both are rows in the one storage table, because the quota is a
    # SUM over that table and a second table would have to be added to it.
    #
    # At most one clip per task: a second upload replaces the first, so the
    # newest row wins here rather than the list growing a recording nobody
    # meant to keep. Documents accumulate, oldest first — the order they were
    # attached in is the order they are read in.
    for row in fetch_all(
        f"SELECT * FROM {B2B_WORKSPACE_FILE_TABLE} "
        f"WHERE task_id = __ANY_MARKER__(%s) AND kind = __ANY_MARKER__(%s) "
        "ORDER BY created_at ASC, id ASC",
        [ids, [TASK_VOICE_KIND, TASK_FILE_KIND]],
    ):
        if row["kind"] == TASK_VOICE_KIND:
            by_id[row["task_id"]]["voice"] = row
        else:
            by_id[row["task_id"]]["files"].append(row)

    return tasks


def list_tasks(
    company_id: int,
    *,
    visible_to: int | None = None,
    status: str | None = None,
    search: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Company tasks, newest first.

    ``visible_to`` narrows the list to what one employee may see: the tasks
    they were assigned plus the ones they wrote. Managers pass ``None`` and
    get everything.
    """
    # Deleted tasks are gone from lists, search and the counters — the row
    # survives so it can be restored and so its history still reads, which is
    # the whole of "delete is not destroy". See `list_deleted_tasks`.
    sql = (
        f"SELECT t.* FROM {B2B_TASK_TABLE} t "
        f"WHERE t.company_id = %s AND t.deleted_at IS NULL"
    )
    params: list[Any] = [company_id]

    if visible_to is not None:
        sql += f"""
          AND (
            t.author_id = %s
            OR EXISTS (
                SELECT 1 FROM {B2B_TASK_ASSIGNEE_TABLE} a
                WHERE a.task_id = t.id AND a.employee_id = %s
            )
          )
        """
        params += [visible_to, visible_to]

    if status:
        sql += " AND t.status = %s"
        params.append(status)

    if search:
        sql += " AND (t.title ILIKE %s OR t.description ILIKE %s OR t.project ILIKE %s)"
        needle = f"%{search}%"
        params += [needle, needle, needle]

    sql += " ORDER BY t.created_at DESC, t.id DESC LIMIT %s"
    params.append(limit)

    return _attach_task_children(fetch_all(sql, params))


def get_task(
    task_id: int, company_id: int, *, include_deleted: bool = False
) -> dict[str, Any] | None:
    """One task. Deleted ones are invisible unless asked for by name — which
    is what the trash screen and `restore_task` do."""
    sql = f"SELECT * FROM {B2B_TASK_TABLE} WHERE id = %s AND company_id = %s"
    if not include_deleted:
        sql += " AND deleted_at IS NULL"
    task = fetch_one(sql, [task_id, company_id])
    if not task:
        return None
    return _attach_task_children([task])[0]


def is_task_assignee(task_id: int, employee_id: int) -> bool:
    return bool(
        fetch_one(
            f"SELECT 1 AS ok FROM {B2B_TASK_ASSIGNEE_TABLE} "
            f"WHERE task_id = %s AND employee_id = %s",
            [task_id, employee_id],
        )
    )


def create_task(
    *,
    company_id: int,
    author_id: int,
    title: str,
    description: str = "",
    status: str = "todo",
    priority: str = "medium",
    project: str | None = None,
    due_date: datetime | None = None,
    assignee_ids: Sequence[int] = (),
    subtasks: Sequence[str] = (),
    lead_id: int | None = None,
) -> dict[str, Any] | None:
    now = timezone.now()
    task = fetch_one(
        f"""
        INSERT INTO {B2B_TASK_TABLE}
            (company_id, title, description, status, priority, project, due_date,
             author_id, lead_id, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        [
            company_id, title, description, status, priority, project, due_date,
            author_id, lead_id, now, now,
        ],
    )
    if not task:
        return None

    set_task_assignees(task["id"], assignee_ids)
    replace_subtasks(task["id"], subtasks)
    add_task_activity(
        task["id"], company_id, task_title=title,
        kind=TaskActivityKind.CREATED, author_id=author_id,
    )
    return get_task(task["id"], company_id)


def update_task(
    task_id: int, company_id: int, *, actor_id: int | None = None, **fields: Any
) -> dict[str, Any] | None:
    if fields:
        # Read before the UPDATE so a status change can say what it moved
        # from, and every field-set can log against the title even when the
        # title itself is one of the fields being changed.
        current = fetch_one(
            f"SELECT title, status FROM {B2B_TASK_TABLE} WHERE id = %s AND company_id = %s",
            [task_id, company_id],
        )
        sets = ", ".join(f"{key} = %s" for key in fields)
        params = list(fields.values()) + [timezone.now(), task_id, company_id]
        fetch_one(
            f"UPDATE {B2B_TASK_TABLE} SET {sets}, updated_at = %s "
            f"WHERE id = %s AND company_id = %s RETURNING *",
            params,
        )
        if current:
            title = fields.get("title") or current["title"]
            if "status" in fields and fields["status"] != current["status"]:
                add_task_activity(
                    task_id, company_id, task_title=title,
                    kind=TaskActivityKind.STATUS, author_id=actor_id,
                    text=f"{current['status']}>{fields['status']}",
                )
            other_fields = [key for key in fields if key not in {"status", "completed_at"}]
            if other_fields:
                add_task_activity(
                    task_id, company_id, task_title=title,
                    kind=TaskActivityKind.UPDATED, author_id=actor_id,
                    text=",".join(other_fields),
                )
    return get_task(task_id, company_id)


def task_voice(task_id: int) -> dict[str, Any] | None:
    """The clip attached to a task, or None.

    Filtered by kind: documents attached to the same task live in this table
    with the same `task_id`, and without the filter the newest of *those*
    would be handed back as the task's voice note — and then deleted by the
    endpoint that replaces a recording.
    """
    return fetch_one(
        f"SELECT * FROM {B2B_WORKSPACE_FILE_TABLE} WHERE task_id = %s AND kind = %s "
        "ORDER BY created_at DESC, id DESC",
        [task_id, TASK_VOICE_KIND],
    )


def task_files(task_id: int) -> list[dict[str, Any]]:
    """The documents attached to a task, oldest first."""
    return fetch_all(
        f"SELECT * FROM {B2B_WORKSPACE_FILE_TABLE} WHERE task_id = %s AND kind = %s "
        "ORDER BY created_at ASC, id ASC",
        [task_id, TASK_FILE_KIND],
    )


def delete_task_file(task_id: int, file_id: int) -> dict[str, Any] | None:
    """Detaches one document and hands back the row that owned its bytes.

    Scoped by `task_id` as well as by id: the id comes off a request, and
    without it a caller who may write to their own task could name any file
    row in the database. Returned rather than dropped for the same reason
    [delete_task_voice] returns one — the row is the only record of where the
    bytes are.
    """
    existing = fetch_one(
        f"SELECT * FROM {B2B_WORKSPACE_FILE_TABLE} "
        "WHERE id = %s AND task_id = %s AND kind = %s",
        [file_id, task_id, TASK_FILE_KIND],
    )
    if existing:
        execute(
            f"DELETE FROM {B2B_WORKSPACE_FILE_TABLE} WHERE id = %s",
            [existing["id"]],
        )
    return existing


def delete_task_voice(task_id: int) -> dict[str, Any] | None:
    """Removes the clip a task carries and hands back the row that owned it.

    Returned rather than dropped so the caller can delete the object too — the
    row is the only record of where the bytes are, and a DELETE that forgets
    them leaves the company paying quota for a file nothing can reach.
    """
    existing = task_voice(task_id)
    if existing:
        execute(
            f"DELETE FROM {B2B_WORKSPACE_FILE_TABLE} WHERE id = %s",
            [existing["id"]],
        )
    return existing


def delete_task(task_id: int, company_id: int, *, actor_id: int | None = None) -> bool:
    """Remove a task from the workspace without destroying it.

    The row stays, with `deleted_at` and `deleted_by` set: its id, its links,
    its author and its whole history survive, and an authorised person can put
    it back. That is what the TZ means by delete ≠ destroy, and it is why the
    activity row below still has a title to read.
    """
    existing = fetch_one(
        f"SELECT title FROM {B2B_TASK_TABLE} "
        f"WHERE id = %s AND company_id = %s AND deleted_at IS NULL",
        [task_id, company_id],
    )
    deleted = execute(
        f"UPDATE {B2B_TASK_TABLE} SET deleted_at = %s, deleted_by = %s, updated_at = %s "
        f"WHERE id = %s AND company_id = %s AND deleted_at IS NULL",
        [timezone.now(), actor_id, timezone.now(), task_id, company_id],
    ) > 0
    if deleted and existing:
        add_task_activity(
            None, company_id, task_title=existing["title"],
            kind=TaskActivityKind.DELETED, author_id=actor_id,
        )
        from apps.b2b.workspace.access_repository import record_audit

        record_audit(
            company_id,
            actor_employee_id=actor_id,
            action="task.deleted",
            target_type="task",
            target_id=task_id,
        )
    return deleted


def set_task_assignees(
    task_id: int,
    employee_ids: Sequence[int],
    *,
    company_id: int | None = None,
    actor_id: int | None = None,
    task_title: str = "",
) -> None:
    """Full replace. When ``company_id`` is passed, the before/after sets are
    diffed and logged as ASSIGNED/UNASSIGNED — left off during task creation,
    where the CREATED entry already covers who the task started with."""
    previous = {
        row["employee_id"]
        for row in fetch_all(
            f"SELECT employee_id FROM {B2B_TASK_ASSIGNEE_TABLE} WHERE task_id = %s",
            [task_id],
        )
    }
    new_ids = list(dict.fromkeys(employee_ids))  # de-duplicate, keep order

    execute(f"DELETE FROM {B2B_TASK_ASSIGNEE_TABLE} WHERE task_id = %s", [task_id])
    for employee_id in new_ids:
        execute(
            f"INSERT INTO {B2B_TASK_ASSIGNEE_TABLE} (task_id, employee_id, created_at) "
            f"VALUES (%s, %s, %s) ON CONFLICT (task_id, employee_id) DO NOTHING",
            [task_id, employee_id, timezone.now()],
        )

    if company_id is not None:
        new_set = set(new_ids)
        for added in new_set - previous:
            add_task_activity(
                task_id, company_id, task_title=task_title,
                kind=TaskActivityKind.ASSIGNED, author_id=actor_id, text=str(added),
            )
        for removed in previous - new_set:
            add_task_activity(
                task_id, company_id, task_title=task_title,
                kind=TaskActivityKind.UNASSIGNED, author_id=actor_id, text=str(removed),
            )


def replace_subtasks(task_id: int, titles: Sequence[str]) -> None:
    """Rewrite the checklist from a list of titles.

    Existing rows are matched by title so an edit that only adds a step does
    not wipe the done-flags on the steps that were already ticked.
    """
    existing = fetch_all(
        f"SELECT id, title, is_done FROM {B2B_TASK_SUBTASK_TABLE} WHERE task_id = %s",
        [task_id],
    )
    done_by_title = {row["title"]: row["is_done"] for row in existing}

    execute(f"DELETE FROM {B2B_TASK_SUBTASK_TABLE} WHERE task_id = %s", [task_id])
    now = timezone.now()
    for position, title in enumerate(titles):
        execute(
            f"INSERT INTO {B2B_TASK_SUBTASK_TABLE} "
            f"(task_id, title, is_done, position, created_at, updated_at) "
            f"VALUES (%s, %s, %s, %s, %s, %s)",
            [task_id, title, done_by_title.get(title, False), position, now, now],
        )


def toggle_subtask(task_id: int, subtask_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"UPDATE {B2B_TASK_SUBTASK_TABLE} SET is_done = NOT is_done, updated_at = %s "
        f"WHERE id = %s AND task_id = %s RETURNING *",
        [timezone.now(), subtask_id, task_id],
    )


def add_task_comment(task_id: int, author_id: int, text: str) -> dict[str, Any] | None:
    return fetch_one(
        f"INSERT INTO {B2B_TASK_COMMENT_TABLE} (task_id, author_id, text, created_at) "
        f"VALUES (%s, %s, %s, %s) RETURNING *",
        [task_id, author_id, text, timezone.now()],
    )


# ─── Task activity ────────────────────────────────────────────────────────────

def add_task_activity(
    task_id: int | None,
    company_id: int,
    *,
    task_title: str,
    kind: str,
    author_id: int | None = None,
    text: str = "",
) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        INSERT INTO {B2B_TASK_ACTIVITY_TABLE}
            (company_id, task_id, task_title, author_id, kind, text, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        [company_id, task_id, task_title, author_id, kind, text, timezone.now()],
    )


def _with_author_photo(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resolves the ``author_photo`` an activity feed joins on.

    The column holds a storage path, and a feed that ships it bare leaves the
    phone building ``<host>/b2b/avatars/...`` — no ``/media/`` in it — so every
    comment and every history line drew initials while the same person's face
    loaded fine on the roster next to it. Rows are copied rather than mutated:
    `fetch_all` hands back what the query returned and nothing here owns it.
    """
    return [{**row, "author_photo": _photo_url(row.get("author_photo"))} for row in rows]


def list_task_activity(task_id: int, *, limit: int = 100) -> list[dict[str, Any]]:
    """Newest first, with the author's name joined on so the feed does not
    need the roster to render."""
    return _with_author_photo(fetch_all(
        f"""
        SELECT a.*,
               e.full_name AS author_name,
               e.photo AS author_photo
        FROM {B2B_TASK_ACTIVITY_TABLE} a
        LEFT JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = a.author_id
        WHERE a.task_id = %s
        ORDER BY a.created_at DESC, a.id DESC
        LIMIT %s
        """,
        [task_id, limit],
    ))


def list_company_task_activity(
    company_id: int, *, actor_id: int | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    """Company-wide feed for the tasks page: every task's history, newest
    first — including tasks since deleted, since ``task_id`` is nullable and
    ``task_title`` is a snapshot taken at write time.

    ``actor_id`` narrows the feed to one employee's own actions — used for
    non-manager callers, who may only see their own tasks and must not read
    what the rest of the company did on theirs."""
    sql = f"""
        SELECT a.*,
               e.full_name AS author_name,
               e.photo AS author_photo
        FROM {B2B_TASK_ACTIVITY_TABLE} a
        LEFT JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = a.author_id
        WHERE a.company_id = %s
    """
    params: list[Any] = [company_id]
    if actor_id is not None:
        sql += " AND a.author_id = %s"
        params.append(actor_id)
    sql += " ORDER BY a.created_at DESC, a.id DESC LIMIT %s"
    params.append(limit)
    return _with_author_photo(fetch_all(sql, params))


def employee_task_counters(company_id: int, employee_id: int) -> dict[str, int]:
    """What one colleague is carrying, for the card their profile shows.

    Counted over the tasks *assigned* to them, not the ones they wrote: the
    card answers "what is this person working on", and a manager who raises
    everybody's tasks would otherwise read as the busiest person in the
    company.

    Deleted tasks are left out, like everywhere else — a task in the trash is
    not work anybody is doing.
    """
    row = fetch_one(
        f"""
        SELECT
            COUNT(*) FILTER (WHERE t.status = 'done')          AS done_count,
            COUNT(*) FILTER (WHERE t.status = 'in_progress')   AS in_progress_count,
            COUNT(*) FILTER (WHERE t.status = 'todo')          AS todo_count,
            COUNT(*) FILTER (WHERE t.status <> 'done'
                             AND t.due_date IS NOT NULL
                             AND t.due_date::date < CURRENT_DATE) AS overdue_count
        FROM {B2B_TASK_ASSIGNEE_TABLE} a
        JOIN {B2B_TASK_TABLE} t ON t.id = a.task_id
        WHERE a.employee_id = %s
          AND t.company_id = %s
          AND t.deleted_at IS NULL
        """,
        [employee_id, company_id],
    )
    return {key: int(value or 0) for key, value in (row or {}).items()}


def task_counters(company_id: int, visible_to: int | None = None) -> dict[str, int]:
    """Counts for the stat tiles and the app's three status tabs, computed in
    the database rather than by pulling every task into the app just to count
    them.

    One per status, plus the two derived buckets the tiles show — the list
    endpoint is capped at a page, so counting the rows that came back would
    quietly under-report a company with more tasks than fit in one.
    """
    where = "company_id = %s"
    params: list[Any] = [company_id]
    if visible_to is not None:
        where += f"""
          AND (
            author_id = %s
            OR EXISTS (
                SELECT 1 FROM {B2B_TASK_ASSIGNEE_TABLE} a
                WHERE a.task_id = {B2B_TASK_TABLE}.id AND a.employee_id = %s
            )
          )
        """
        params += [visible_to, visible_to]

    row = fetch_one(
        f"""
        SELECT
            COUNT(*) FILTER (WHERE status <> 'done')                       AS open_count,
            COUNT(*) FILTER (WHERE status = 'todo')                        AS todo_count,
            COUNT(*) FILTER (WHERE status = 'in_progress')                 AS in_progress_count,
            COUNT(*) FILTER (WHERE status = 'done')                        AS done_count,
            COUNT(*) FILTER (WHERE status <> 'done' AND due_date IS NOT NULL
                             AND due_date::date < CURRENT_DATE)            AS overdue_count,
            COUNT(*) FILTER (WHERE status <> 'done' AND due_date IS NOT NULL
                             AND due_date::date = CURRENT_DATE)            AS due_today_count
        FROM {B2B_TASK_TABLE}
        WHERE {where} AND deleted_at IS NULL
        """,
        params,
    )
    return {key: int(value or 0) for key, value in (row or {}).items()}


# ─── Calendar ─────────────────────────────────────────────────────────────────

EVENT_TYPES = ("meeting", "call", "task", "deadline", "personal")


def _attach_event_participants(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not events:
        return events
    by_id = {e["id"]: e for e in events}
    for event in events:
        event["participant_ids"] = []
    for row in fetch_all(
        f"SELECT event_id, employee_id FROM {B2B_CALENDAR_PARTICIPANT_TABLE} "
        f"WHERE event_id = __ANY_MARKER__(%s)",
        [list(by_id)],
    ):
        by_id[row["event_id"]]["participant_ids"].append(row["employee_id"])
    return events


def list_events(
    company_id: int,
    *,
    visible_to: int | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    sql = f"SELECT e.* FROM {B2B_CALENDAR_EVENT_TABLE} e WHERE e.company_id = %s"
    params: list[Any] = [company_id]

    if visible_to is not None:
        sql += f"""
          AND (
            e.author_id = %s
            OR EXISTS (
                SELECT 1 FROM {B2B_CALENDAR_PARTICIPANT_TABLE} p
                WHERE p.event_id = e.id AND p.employee_id = %s
            )
          )
        """
        params += [visible_to, visible_to]

    if start:
        sql += " AND e.ends_at >= %s"
        params.append(start)
    if end:
        sql += " AND e.starts_at <= %s"
        params.append(end)

    sql += " ORDER BY e.starts_at ASC LIMIT %s"
    params.append(limit)

    return _attach_event_participants(fetch_all(sql, params))


def get_event(event_id: int, company_id: int) -> dict[str, Any] | None:
    event = fetch_one(
        f"SELECT * FROM {B2B_CALENDAR_EVENT_TABLE} WHERE id = %s AND company_id = %s",
        [event_id, company_id],
    )
    if not event:
        return None
    return _attach_event_participants([event])[0]


def create_event(
    *,
    company_id: int,
    author_id: int,
    title: str,
    event_type: str,
    starts_at: datetime,
    ends_at: datetime,
    all_day: bool = False,
    location: str | None = None,
    notes: str | None = None,
    participant_ids: Sequence[int] = (),
) -> dict[str, Any] | None:
    now = timezone.now()
    event = fetch_one(
        f"""
        INSERT INTO {B2B_CALENDAR_EVENT_TABLE}
            (company_id, title, event_type, starts_at, ends_at, all_day, location,
             notes, author_id, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        [company_id, title, event_type, starts_at, ends_at, all_day, location,
         notes, author_id, now, now],
    )
    if not event:
        return None
    set_event_participants(event["id"], participant_ids)
    return get_event(event["id"], company_id)


def update_event(event_id: int, company_id: int, **fields: Any) -> dict[str, Any] | None:
    if fields:
        sets = ", ".join(f"{key} = %s" for key in fields)
        params = list(fields.values()) + [timezone.now(), event_id, company_id]
        fetch_one(
            f"UPDATE {B2B_CALENDAR_EVENT_TABLE} SET {sets}, updated_at = %s "
            f"WHERE id = %s AND company_id = %s RETURNING *",
            params,
        )
    return get_event(event_id, company_id)


def delete_event(event_id: int, company_id: int) -> bool:
    return execute(
        f"DELETE FROM {B2B_CALENDAR_EVENT_TABLE} WHERE id = %s AND company_id = %s",
        [event_id, company_id],
    ) > 0


def set_event_participants(event_id: int, employee_ids: Sequence[int]) -> None:
    execute(f"DELETE FROM {B2B_CALENDAR_PARTICIPANT_TABLE} WHERE event_id = %s", [event_id])
    for employee_id in dict.fromkeys(employee_ids):
        execute(
            f"INSERT INTO {B2B_CALENDAR_PARTICIPANT_TABLE} (event_id, employee_id, created_at) "
            f"VALUES (%s, %s, %s) ON CONFLICT (event_id, employee_id) DO NOTHING",
            [event_id, employee_id, timezone.now()],
        )


# ─── Quick notes ──────────────────────────────────────────────────────────────
#
# The strip above the month card. A note is either typed or recorded, and the
# recording is a `b2b_workspace_file` row like every other upload — see
# [note_voice] for why it is not a column on the note itself.

NOTE_KINDS = ("text", "voice")
NOTE_COLORS = ("green", "violet", "blue", "orange", "pink", "red")

#: What a note's clip is filed under in `b2b_workspace_file.kind`.
NOTE_VOICE_KIND = "note"


def _attach_note_voice(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fills in each voice note's clip, in one query rather than per row.

    Text notes are left with `voice = None`. Done here rather than in the view
    because every path that hands a note back needs it — the list, the create,
    the patch — and a note whose recording is missing from one of them reads
    as the upload having failed.
    """
    if not notes:
        return notes
    by_id = {note["id"]: note for note in notes}
    for note in notes:
        note["voice"] = None
    for row in fetch_all(
        f"SELECT * FROM {B2B_WORKSPACE_FILE_TABLE} "
        f"WHERE note_id = __ANY_MARKER__(%s) AND kind = %s "
        "ORDER BY created_at ASC, id ASC",
        [list(by_id), NOTE_VOICE_KIND],
    ):
        by_id[row["note_id"]]["voice"] = row
    return notes


def list_notes(
    company_id: int,
    *,
    employee_id: int,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """One employee's notes plus everything the workspace has shared.

    Not scoped by ``visible_to`` the way events are: a note is private by
    default whatever your role is. A manager who could read every note in the
    company would make the feature unusable for the thing people actually reach
    for it for, and sharing is one tap away for the notes that are not private.
    """
    return _attach_note_voice(fetch_all(
        f"SELECT * FROM {B2B_WORKSPACE_NOTE_TABLE} "
        "WHERE company_id = %s AND (author_id = %s OR is_shared) "
        "ORDER BY is_pinned DESC, updated_at DESC, id DESC LIMIT %s",
        [company_id, employee_id, limit],
    ))


def get_note(note_id: int, company_id: int) -> dict[str, Any] | None:
    note = fetch_one(
        f"SELECT * FROM {B2B_WORKSPACE_NOTE_TABLE} WHERE id = %s AND company_id = %s",
        [note_id, company_id],
    )
    if not note:
        return None
    return _attach_note_voice([note])[0]


def create_note(
    *,
    company_id: int,
    author_id: int,
    kind: str = "text",
    title: str = "",
    body: str = "",
    color: str = "green",
    is_shared: bool = False,
) -> dict[str, Any] | None:
    now = timezone.now()
    note = fetch_one(
        f"""
        INSERT INTO {B2B_WORKSPACE_NOTE_TABLE}
            (company_id, author_id, kind, title, body, color, is_pinned,
             is_shared, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, FALSE, %s, %s, %s)
        RETURNING *
        """,
        [company_id, author_id, kind, title, body, color, is_shared, now, now],
    )
    if not note:
        return None
    return get_note(note["id"], company_id)


def update_note(note_id: int, company_id: int, **fields: Any) -> dict[str, Any] | None:
    if fields:
        sets = ", ".join(f"{key} = %s" for key in fields)
        execute(
            f"UPDATE {B2B_WORKSPACE_NOTE_TABLE} SET {sets}, updated_at = %s "
            "WHERE id = %s AND company_id = %s",
            list(fields.values()) + [timezone.now(), note_id, company_id],
        )
    return get_note(note_id, company_id)


def delete_note(note_id: int, company_id: int) -> dict[str, Any] | None:
    """Removes a note and hands back what it was, clip included.

    Returned rather than answering a bare bool so the caller can delete the
    recording's object too: the row cascades away with the note, and once it is
    gone nothing knows where the bytes were.
    """
    note = get_note(note_id, company_id)
    if not note:
        return None
    execute(
        f"DELETE FROM {B2B_WORKSPACE_NOTE_TABLE} WHERE id = %s AND company_id = %s",
        [note_id, company_id],
    )
    return note


def note_voice(note_id: int) -> dict[str, Any] | None:
    """The clip a voice note carries, or None.

    Filtered by kind for the same reason [task_voice] is: this table holds
    every kind of upload, and only rows filed as a note's recording are one.
    """
    return fetch_one(
        f"SELECT * FROM {B2B_WORKSPACE_FILE_TABLE} WHERE note_id = %s AND kind = %s "
        "ORDER BY created_at DESC, id DESC",
        [note_id, NOTE_VOICE_KIND],
    )


def delete_note_voice(note_id: int) -> dict[str, Any] | None:
    """Drops the clip a note carries and hands back the row that owned it."""
    existing = note_voice(note_id)
    if existing:
        execute(
            f"DELETE FROM {B2B_WORKSPACE_FILE_TABLE} WHERE id = %s",
            [existing["id"]],
        )
    return existing


# ─── Chat ─────────────────────────────────────────────────────────────────────

def list_threads(company_id: int, employee_id: int) -> list[dict[str, Any]]:
    """Every thread the employee belongs to, with their per-member flags, the
    unread count and the last message — one query, so the list screen does not
    fan out per row."""
    threads = fetch_all(
        f"""
        SELECT
            t.id,
            t.kind,
            t.group_name,
            t.photo,
            t.created_by,
            t.last_message_at,
            m.is_pinned,
            m.is_muted,
            m.last_read_at,
            (
                SELECT COUNT(*) FROM {B2B_CHAT_MESSAGE_TABLE} msg
                WHERE msg.thread_id = t.id
                  AND msg.sender_id <> %s
                  AND (m.last_read_at IS NULL OR msg.created_at > m.last_read_at)
            ) AS unread,
            last_msg.id         AS last_message_id,
            last_msg.sender_id  AS last_message_sender_id,
            last_msg.text       AS last_message_text,
            last_msg.created_at AS last_message_created_at
        FROM {B2B_CHAT_THREAD_TABLE} t
        JOIN {B2B_CHAT_MEMBER_TABLE} m
          ON m.thread_id = t.id AND m.employee_id = %s
        LEFT JOIN LATERAL (
            SELECT id, sender_id, text, created_at
            FROM {B2B_CHAT_MESSAGE_TABLE}
            WHERE thread_id = t.id
            ORDER BY id DESC
            LIMIT 1
        ) last_msg ON TRUE
        WHERE t.company_id = %s
        -- The saved room is the first row whatever else is going on, the way
        -- Telegram keeps Saved Messages at the top: it is the one thread the
        -- reader can rely on finding without scrolling.
        ORDER BY (t.kind = %s) DESC,
                 m.is_pinned DESC,
                 COALESCE(t.last_message_at, t.created_at) DESC
        """,
        [employee_id, employee_id, company_id, THREAD_KIND_SAVED],
    )
    return _attach_thread_members(threads, employee_id)


def _attach_thread_members(threads: list[dict[str, Any]], viewer_id: int) -> list[dict[str, Any]]:
    if not threads:
        return threads
    by_id = {t["id"]: t for t in threads}
    for thread in threads:
        thread["participant_ids"] = []
    for row in fetch_all(
        f"SELECT thread_id, employee_id FROM {B2B_CHAT_MEMBER_TABLE} "
        f"WHERE thread_id = __ANY_MARKER__(%s)",
        [list(by_id)],
    ):
        # The client's model holds "everyone except me", matching how a direct
        # chat is labelled with the other person's name.
        if row["employee_id"] != viewer_id:
            by_id[row["thread_id"]]["participant_ids"].append(row["employee_id"])
    return threads


def get_thread_for_member(thread_id: int, company_id: int, employee_id: int) -> dict[str, Any] | None:
    thread = fetch_one(
        f"""
        SELECT
            t.*,
            m.is_pinned,
            m.is_muted,
            m.last_read_at,
            (
                SELECT COUNT(*) FROM {B2B_CHAT_MESSAGE_TABLE} msg
                WHERE msg.thread_id = t.id
                  AND msg.sender_id <> %s
                  AND (m.last_read_at IS NULL OR msg.created_at > m.last_read_at)
            ) AS unread,
            last_msg.id         AS last_message_id,
            last_msg.sender_id  AS last_message_sender_id,
            last_msg.text       AS last_message_text,
            last_msg.created_at AS last_message_created_at
        FROM {B2B_CHAT_THREAD_TABLE} t
        JOIN {B2B_CHAT_MEMBER_TABLE} m ON m.thread_id = t.id AND m.employee_id = %s
        LEFT JOIN LATERAL (
            SELECT id, sender_id, text, created_at
            FROM {B2B_CHAT_MESSAGE_TABLE}
            WHERE thread_id = t.id
            ORDER BY id DESC
            LIMIT 1
        ) last_msg ON TRUE
        WHERE t.id = %s AND t.company_id = %s
        """,
        [employee_id, employee_id, thread_id, company_id],
    )
    if not thread:
        return None
    return _attach_thread_members([thread], employee_id)[0]


#: What a `b2b_chat_thread` row is — see the column's note in
#: `create_b2b_tables`. Only these two so far; an assistant chat is not a
#: thread at all (it lives in `b2b_ai_conversation`, see `assistant.py`).
THREAD_KIND_CHAT = "chat"
THREAD_KIND_SAVED = "saved"


def ensure_saved_thread(company_id: int, employee_id: int) -> dict[str, Any]:
    """The employee's own "Saqlangan xabarlar" room, made on first ask.

    A one-member thread with the person as its only member. Idempotent — the
    partial unique index on (company_id, created_by) WHERE kind = 'saved' is
    what makes two concurrent first opens of the chat list come out with one
    room rather than two.
    """
    now = timezone.now()
    thread = fetch_one(
        f"""
        INSERT INTO {B2B_CHAT_THREAD_TABLE}
            (company_id, kind, group_name, created_by, created_at, updated_at)
        VALUES (%s, %s, NULL, %s, %s, %s)
        ON CONFLICT (company_id, created_by) WHERE kind = 'saved'
        DO UPDATE SET updated_at = {B2B_CHAT_THREAD_TABLE}.updated_at
        RETURNING *
        """,
        [company_id, THREAD_KIND_SAVED, employee_id, now, now],
    )
    execute(
        f"INSERT INTO {B2B_CHAT_MEMBER_TABLE} "
        f"(thread_id, employee_id, role, created_at, updated_at) "
        f"VALUES (%s, %s, 'member', %s, %s) ON CONFLICT (thread_id, employee_id) DO NOTHING",
        [thread["id"], employee_id, now, now],
    )
    return thread


def find_direct_thread(company_id: int, a: int, b: int) -> dict[str, Any] | None:
    """The existing one-to-one thread between two people, if any.

    Matched by membership rather than by a stored pair key: a direct thread is
    simply a group-less thread whose members are exactly these two.
    """
    return fetch_one(
        f"""
        SELECT t.*
        FROM {B2B_CHAT_THREAD_TABLE} t
        JOIN {B2B_CHAT_MEMBER_TABLE} m ON m.thread_id = t.id
        WHERE t.company_id = %s AND t.group_name IS NULL AND t.kind = %s
        GROUP BY t.id
        HAVING COUNT(*) = 2
           AND BOOL_OR(m.employee_id = %s)
           AND BOOL_OR(m.employee_id = %s)
        LIMIT 1
        """,
        [company_id, THREAD_KIND_CHAT, a, b],
    )


def create_thread(
    *,
    company_id: int,
    created_by: int,
    member_ids: Sequence[int],
    group_name: str | None = None,
) -> dict[str, Any] | None:
    now = timezone.now()
    thread = fetch_one(
        f"INSERT INTO {B2B_CHAT_THREAD_TABLE} "
        f"(company_id, kind, group_name, created_by, created_at, updated_at) "
        f"VALUES (%s, %s, %s, %s, %s, %s) RETURNING *",
        [company_id, THREAD_KIND_CHAT, group_name, created_by, now, now],
    )
    if not thread:
        return None
    for employee_id in dict.fromkeys([created_by, *member_ids]):
        # Whoever opened a group runs it. A direct chat has no admin at all —
        # there is nothing in it to administer, and calling one of two people
        # its owner would only ever mislead.
        role = "admin" if group_name and employee_id == created_by else "member"
        execute(
            f"INSERT INTO {B2B_CHAT_MEMBER_TABLE} "
            f"(thread_id, employee_id, role, created_at, updated_at) "
            f"VALUES (%s, %s, %s, %s, %s) ON CONFLICT (thread_id, employee_id) DO NOTHING",
            [thread["id"], employee_id, role, now, now],
        )
    return get_thread_for_member(thread["id"], company_id, created_by)


def add_thread_member(thread_id: int, employee_id: int, *, role: str = "member") -> None:
    """Put somebody in a conversation.

    Idempotent: a link can be followed twice, and the second time should be a
    no-op rather than a duplicate row or an error. The conflict clause also
    means re-adding somebody never quietly resets the role they already hold.
    """
    now = timezone.now()
    execute(
        f"INSERT INTO {B2B_CHAT_MEMBER_TABLE} "
        f"(thread_id, employee_id, role, created_at, updated_at) "
        f"VALUES (%s, %s, %s, %s, %s) ON CONFLICT (thread_id, employee_id) DO NOTHING",
        [thread_id, employee_id, role, now, now],
    )


def get_thread(thread_id: int, company_id: int) -> dict[str, Any] | None:
    """The room itself, without asking who is looking at it.

    [get_thread_for_member] answers "may this person see it, and what are their
    own flags"; this one answers "does it exist here at all", which is what a
    membership change needs before it can decide anything else.
    """
    return fetch_one(
        f"SELECT * FROM {B2B_CHAT_THREAD_TABLE} WHERE id = %s AND company_id = %s",
        [thread_id, company_id],
    )


def update_thread(thread_id: int, company_id: int, **fields: Any) -> dict[str, Any] | None:
    """Rename a group, or hang a new picture on it."""
    allowed = {"group_name", "photo"}
    changes = {name: value for name, value in fields.items() if name in allowed}
    if not changes:
        return get_thread(thread_id, company_id)

    assignments = ", ".join(f"{name} = %s" for name in changes)
    return fetch_one(
        f"UPDATE {B2B_CHAT_THREAD_TABLE} SET {assignments}, updated_at = %s "
        f"WHERE id = %s AND company_id = %s RETURNING *",
        [*changes.values(), timezone.now(), thread_id, company_id],
    )


def list_thread_members(thread_id: int) -> list[dict[str, Any]]:
    """Everyone in the room, as the roster describes them plus what they are
    here — the same employee shape the team endpoint returns, so one row
    renders in both places.

    Admins first, then by name: the group screen's whole job is to show who
    runs the room, and burying them in an alphabetical list makes the reader
    hunt for it.
    """
    return fetch_all(
        f"""
        SELECT
            e.*,
            d.name  AS department_name,
            d.color AS department_color,
            m.role  AS member_role,
            m.created_at AS joined_at
        FROM {B2B_CHAT_MEMBER_TABLE} m
        JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = m.employee_id
        LEFT JOIN {B2B_DEPARTMENT_TABLE} d ON d.id = e.department_id
        WHERE m.thread_id = %s
        ORDER BY (m.role = 'admin') DESC, e.full_name ASC
        """,
        [thread_id],
    )


def thread_member(thread_id: int, employee_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT * FROM {B2B_CHAT_MEMBER_TABLE} "
        "WHERE thread_id = %s AND employee_id = %s",
        [thread_id, employee_id],
    )


def thread_admin_ids(thread_id: int) -> list[int]:
    return [
        row["employee_id"]
        for row in fetch_all(
            f"SELECT employee_id FROM {B2B_CHAT_MEMBER_TABLE} "
            "WHERE thread_id = %s AND role = 'admin'",
            [thread_id],
        )
    ]


def set_thread_member_role(thread_id: int, employee_id: int, role: str) -> bool:
    return bool(
        fetch_one(
            f"UPDATE {B2B_CHAT_MEMBER_TABLE} SET role = %s, updated_at = %s "
            "WHERE thread_id = %s AND employee_id = %s RETURNING id",
            [role, timezone.now(), thread_id, employee_id],
        )
    )


def remove_thread_member(thread_id: int, employee_id: int) -> bool:
    return bool(
        fetch_one(
            f"DELETE FROM {B2B_CHAT_MEMBER_TABLE} "
            "WHERE thread_id = %s AND employee_id = %s RETURNING id",
            [thread_id, employee_id],
        )
    )


def promote_longest_standing_member(thread_id: int) -> int | None:
    """Hands the room to whoever has been in it longest.

    Called when the last admin walks out. The alternative — a group nobody can
    rename, add to or remove from — is a room that quietly breaks the day its
    creator leaves the company, and there is no way back into it from inside
    the app.
    """
    promoted = fetch_one(
        f"""
        UPDATE {B2B_CHAT_MEMBER_TABLE} SET role = 'admin', updated_at = %s
        WHERE id = (
            SELECT id FROM {B2B_CHAT_MEMBER_TABLE}
            WHERE thread_id = %s
            ORDER BY created_at ASC, id ASC
            LIMIT 1
        )
        RETURNING employee_id
        """,
        [timezone.now(), thread_id],
    )
    return promoted["employee_id"] if promoted else None


def list_messages(
    thread_id: int,
    *,
    before_id: int | None = None,
    limit: int = 50,
    search: str | None = None,
) -> list[dict[str, Any]]:
    """A page of history, oldest-first for rendering but paged from the newest
    end so opening a room does not read years of backlog.

    ``search`` narrows it to messages containing a phrase — the room's own
    search, which is a different question from the thread list's and cannot be
    answered by it: the list matches names, and what somebody is looking for
    inside a room is what was said.
    """
    sql = f"SELECT * FROM {B2B_CHAT_MESSAGE_TABLE} WHERE thread_id = %s"
    params: list[Any] = [thread_id]
    if before_id:
        sql += " AND id < %s"
        params.append(before_id)
    if search:
        sql += " AND text ILIKE %s"
        params.append(f"%{search}%")
    sql += " ORDER BY id DESC LIMIT %s"
    params.append(limit)

    return list(reversed(fetch_all(sql, params)))


def list_pinned_messages(thread_id: int) -> list[dict[str, Any]]:
    """What is pinned in a room, newest pin first.

    Its own query rather than a flag read off the history page: a pin is
    supposed to be reachable from the top of the room however far back it was
    written, which is precisely when it is not on the page.
    """
    return fetch_all(
        f"SELECT * FROM {B2B_CHAT_MESSAGE_TABLE} "
        "WHERE thread_id = %s AND pinned_at IS NOT NULL "
        "ORDER BY pinned_at DESC",
        [thread_id],
    )


def edit_message(message_id: int, thread_id: int, text: str) -> dict[str, Any] | None:
    """Rewrites a message, and records that it was rewritten.

    ``edited_at`` is the point: a message whose text changes with nothing to
    show for it is a worse thing to have in a room than one that cannot be
    changed at all.
    """
    now = timezone.now()
    return fetch_one(
        f"UPDATE {B2B_CHAT_MESSAGE_TABLE} SET text = %s, edited_at = %s "
        "WHERE id = %s AND thread_id = %s RETURNING *",
        [text, now, message_id, thread_id],
    )


def set_message_pinned(
    message_id: int, thread_id: int, *, pinned_by: int | None
) -> dict[str, Any] | None:
    """Pins or unpins. ``pinned_by`` of None is the unpin."""
    now = timezone.now() if pinned_by is not None else None
    return fetch_one(
        f"UPDATE {B2B_CHAT_MESSAGE_TABLE} SET pinned_at = %s, pinned_by = %s "
        "WHERE id = %s AND thread_id = %s RETURNING *",
        [now, pinned_by, message_id, thread_id],
    )


def toggle_reaction(message_id: int, employee_id: int, emoji: str) -> bool:
    """Adds somebody's reaction, or takes it back if it was already theirs.

    One call for both directions, because the app has one gesture for both:
    tapping a reaction you already left is how you remove it. Returns whether
    the reaction is now on.
    """
    removed = execute(
        f"DELETE FROM {B2B_CHAT_REACTION_TABLE} "
        "WHERE message_id = %s AND employee_id = %s AND emoji = %s",
        [message_id, employee_id, emoji],
    )
    if removed:
        return False
    execute(
        f"INSERT INTO {B2B_CHAT_REACTION_TABLE} "
        "(message_id, employee_id, emoji, created_at) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (message_id, employee_id, emoji) DO NOTHING",
        [message_id, employee_id, emoji, timezone.now()],
    )
    return True


def reactions_for_messages(
    message_ids: Sequence[int],
) -> dict[int, list[dict[str, Any]]]:
    """Every reaction on a page of history, in one query.

    Grouped per message here rather than counted in SQL because the bubble
    needs both the count and whether *this* reader is among them, and the
    second is not something a GROUP BY can answer without the viewer in it.
    """
    if not message_ids:
        return {}
    rows = fetch_all(
        f"SELECT message_id, employee_id, emoji FROM {B2B_CHAT_REACTION_TABLE} "
        "WHERE message_id = __ANY_MARKER__(%s)",
        [list(message_ids)],
    )
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["message_id"], []).append(row)
    return grouped


def send_message(
    thread_id: int,
    sender_id: int,
    text: str,
    reply_to_id: int | None = None,
    forwarded_from_id: int | None = None,
) -> dict[str, Any] | None:
    now = timezone.now()
    message = fetch_one(
        f"INSERT INTO {B2B_CHAT_MESSAGE_TABLE} "
        "(thread_id, sender_id, text, reply_to_id, forwarded_from_id, created_at) "
        f"VALUES (%s, %s, %s, %s, %s, %s) RETURNING *",
        [thread_id, sender_id, text, reply_to_id, forwarded_from_id, now],
    )
    if message:
        execute(
            f"UPDATE {B2B_CHAT_THREAD_TABLE} SET last_message_at = %s, updated_at = %s WHERE id = %s",
            [now, now, thread_id],
        )
        # Sending is also reading: otherwise your own message counts as unread.
        mark_thread_read(thread_id, sender_id)
    return message


def get_message(message_id: int, thread_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT * FROM {B2B_CHAT_MESSAGE_TABLE} WHERE id = %s AND thread_id = %s",
        [message_id, thread_id],
    )


def message_visible_to(
    message_id: int, company_id: int, employee_id: int
) -> dict[str, Any] | None:
    """A message, if the caller is in the room it was written in.

    The guard on forwarding, and it has to be membership rather than the
    company: a forward copies the *text* into a room the sender chooses, so
    anything looser would turn a message id into a way to read what colleagues
    said in a chat nobody invited you to.
    """
    return fetch_one(
        f"""
        SELECT m.* FROM {B2B_CHAT_MESSAGE_TABLE} m
        JOIN {B2B_CHAT_THREAD_TABLE} t ON t.id = m.thread_id
        JOIN {B2B_CHAT_MEMBER_TABLE} mem
          ON mem.thread_id = t.id AND mem.employee_id = %s
        WHERE m.id = %s AND t.company_id = %s
        """,
        [employee_id, message_id, company_id],
    )


def employee_names(employee_ids: Sequence[int]) -> dict[int, str]:
    """Names for a handful of ids, in one query.

    What a forward label is drawn from. The name travels with the message
    rather than being looked up on the phone, because the roster the app
    holds is the *current* one: somebody who has left, been hidden, or was
    lent by another workspace is not on it, and a forward of their message
    would then say "Yuborilgan xabar" and name nobody.
    """
    ids = list({int(i) for i in employee_ids if i})
    if not ids:
        return {}
    rows = fetch_all(
        f"SELECT id, full_name FROM {B2B_EMPLOYEE_TABLE} "
        "WHERE id = __ANY_MARKER__(%s)",
        [ids],
    )
    return {row["id"]: row["full_name"] or "" for row in rows}


def messages_by_ids(message_ids: Sequence[int]) -> dict[int, dict[str, Any]]:
    """The messages a page of history quotes, fetched in one query.

    A room page is up to 200 bubbles and any of them may be a reply; asking
    per reply would be a query per bubble to render one screen.
    """
    if not message_ids:
        return {}
    rows = fetch_all(
        f"SELECT * FROM {B2B_CHAT_MESSAGE_TABLE} WHERE id = __ANY_MARKER__(%s)",
        [list(message_ids)],
    )
    return {row["id"]: row for row in rows}


def delete_message(message_id: int, thread_id: int) -> bool:
    """Removes a message. Its attachment row cascades with it, which is what
    hands the bytes back to the quota."""
    return execute(
        f"DELETE FROM {B2B_CHAT_MESSAGE_TABLE} WHERE id = %s AND thread_id = %s",
        [message_id, thread_id],
    ) > 0


def mark_thread_read(thread_id: int, employee_id: int) -> str | None:
    """Marks everything in the room read for one member, and says when.

    The timestamp goes back to the caller because the other side needs it: a
    read receipt is only useful to the person who *sent* the messages, and
    "everything up to this moment" is what turns their single ticks into
    double ones without either client having to guess which bubbles it covers.
    """
    now = timezone.now()
    execute(
        f"UPDATE {B2B_CHAT_MEMBER_TABLE} SET last_read_at = %s, updated_at = %s "
        f"WHERE thread_id = %s AND employee_id = %s",
        [now, now, thread_id, employee_id],
    )
    return now.isoformat()


def last_message_id(thread_id: int) -> int | None:
    row = fetch_one(
        f"SELECT id FROM {B2B_CHAT_MESSAGE_TABLE} WHERE thread_id = %s "
        "ORDER BY id DESC LIMIT 1",
        [thread_id],
    )
    return row["id"] if row else None


def thread_read_state(thread_id: int, viewer_id: int) -> dict[str, Any]:
    """When each *other* member last read this room.

    The viewer's own row is left out: their ticks are about whether anybody
    else has seen what they wrote, and including themselves would mark every
    message read the moment they opened the thread.
    """
    rows = fetch_all(
        f"SELECT employee_id, last_read_at FROM {B2B_CHAT_MEMBER_TABLE} "
        "WHERE thread_id = %s AND employee_id <> %s",
        [thread_id, viewer_id],
    )
    return {
        row["employee_id"]: row["last_read_at"]
        for row in rows
        if row["last_read_at"] is not None
    }


def set_thread_flags(
    thread_id: int,
    employee_id: int,
    *,
    is_pinned: bool | None = None,
    is_muted: bool | None = None,
) -> None:
    fields: dict[str, Any] = {}
    if is_pinned is not None:
        fields["is_pinned"] = is_pinned
    if is_muted is not None:
        fields["is_muted"] = is_muted
    if not fields:
        return
    sets = ", ".join(f"{key} = %s" for key in fields)
    execute(
        f"UPDATE {B2B_CHAT_MEMBER_TABLE} SET {sets}, updated_at = %s "
        f"WHERE thread_id = %s AND employee_id = %s",
        list(fields.values()) + [timezone.now(), thread_id, employee_id],
    )


def total_unread(company_id: int, employee_id: int) -> int:
    row = fetch_one(
        f"""
        SELECT COUNT(*) AS unread
        FROM {B2B_CHAT_MESSAGE_TABLE} msg
        JOIN {B2B_CHAT_MEMBER_TABLE} m
          ON m.thread_id = msg.thread_id AND m.employee_id = %s
        JOIN {B2B_CHAT_THREAD_TABLE} t ON t.id = msg.thread_id
        WHERE t.company_id = %s
          AND msg.sender_id <> %s
          AND (m.last_read_at IS NULL OR msg.created_at > m.last_read_at)
        """,
        [employee_id, company_id, employee_id],
    )
    return int((row or {}).get("unread") or 0)


# ─── Customers ────────────────────────────────────────────────────────────────

def search_customers(
    company_id: int, *, query: str = "", limit: int = 20
) -> list[dict[str, Any]]:
    """The directory as the "new lead" sheet searches it: by name or by number.

    Digits in the query are matched against the phone with every separator
    stripped from both sides, because nobody types a number the way it was
    stored — "90 123 45 67" has to find "+998901234567".

    Each row carries ``deal_count``, which is the whole point of searching
    before creating: seeing "2 ta bitim" beside a name is how a salesperson
    knows this is the same buyer and not a namesake.
    """
    sql = f"""
        SELECT c.*,
               (SELECT COUNT(*) FROM {B2B_WORKSPACE_LEAD_TABLE} l
                 WHERE l.customer_id = c.id AND l.deleted_at IS NULL) AS deal_count
        FROM {B2B_WORKSPACE_CUSTOMER_TABLE} c
        WHERE c.company_id = %s
    """
    params: list[Any] = [company_id]

    query = (query or "").strip()
    if query:
        digits = re.sub(r"\D", "", query)
        # A stray digit or two in a name ("Aziz 2") is not a phone search, and
        # matching on it would return the whole directory.
        if len(digits) >= 3:
            sql += (
                " AND (c.full_name ILIKE %s OR c.company_name ILIKE %s"
                " OR regexp_replace(c.phone, '\\D', '', 'g') LIKE %s)"
            )
            params += [f"%{query}%", f"%{query}%", f"%{digits}%"]
        else:
            sql += " AND (c.full_name ILIKE %s OR c.company_name ILIKE %s)"
            params += [f"%{query}%", f"%{query}%"]

    sql += " ORDER BY c.updated_at DESC, c.id DESC LIMIT %s"
    params.append(limit)
    return fetch_all(sql, params)


def get_customer(customer_id: int, company_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT * FROM {B2B_WORKSPACE_CUSTOMER_TABLE} WHERE id = %s AND company_id = %s",
        [customer_id, company_id],
    )


def upsert_customer(
    *,
    company_id: int,
    full_name: str,
    phone: str,
    company_name: str | None = None,
    position: str | None = None,
    email: str | None = None,
    address: str | None = None,
) -> dict[str, Any] | None:
    """Files a customer under their number, or updates the card already there.

    ON CONFLICT rather than a SELECT-then-INSERT: two salespeople creating a
    lead for the same new customer at the same moment is not a race anybody
    should have to think about, and the unique index settles it.

    The update is COALESCE'd on the optional columns so a lead created without
    a company name cannot blank one somebody else already filled in — the sheet
    sends the fields it has, not the fields it knows to be empty.
    """
    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO {B2B_WORKSPACE_CUSTOMER_TABLE}
            (company_id, full_name, phone, company_name, position, email, address,
             created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (company_id, phone) DO UPDATE
        SET full_name = EXCLUDED.full_name,
            company_name = COALESCE(EXCLUDED.company_name, {B2B_WORKSPACE_CUSTOMER_TABLE}.company_name),
            position = COALESCE(EXCLUDED.position, {B2B_WORKSPACE_CUSTOMER_TABLE}.position),
            email = COALESCE(EXCLUDED.email, {B2B_WORKSPACE_CUSTOMER_TABLE}.email),
            address = COALESCE(EXCLUDED.address, {B2B_WORKSPACE_CUSTOMER_TABLE}.address),
            updated_at = EXCLUDED.updated_at
        RETURNING *
        """,
        [
            company_id, full_name, phone, company_name or None, position or None,
            email or None, address or None, now, now,
        ],
    )


def count_customer_deals(customer_id: int) -> int:
    row = fetch_one(
        f"SELECT COUNT(*) AS total FROM {B2B_WORKSPACE_LEAD_TABLE} "
        f"WHERE customer_id = %s AND deleted_at IS NULL",
        [customer_id],
    )
    return int((row or {}).get("total") or 0)


def list_crm_customers(
    company_id: int, *, query: str = "", active: bool | None = None
) -> list[dict[str, Any]]:
    """The CRM directory screen: every customer with their deal footprint.

    Unlike ``search_customers`` (a typeahead capped at 20, used while raising a
    lead) this is the whole list, sortable by how recently the customer moved
    and filterable to who is still an open deal versus who is not.
    """
    sql = f"""
        SELECT c.*,
               COUNT(l.id) AS deal_count,
               COALESCE(SUM(l.amount), 0) AS total_amount,
               MAX(COALESCE(l.completed_at, l.claimed_at, l.created_at)) AS last_activity_at,
               COALESCE(BOOL_OR(l.status IN ('new', 'in_progress')), FALSE) AS is_active
        FROM {B2B_WORKSPACE_CUSTOMER_TABLE} c
        LEFT JOIN {B2B_WORKSPACE_LEAD_TABLE} l ON l.customer_id = c.id
        WHERE c.company_id = %s
    """
    params: list[Any] = [company_id]

    query = (query or "").strip()
    if query:
        digits = re.sub(r"\D", "", query)
        if len(digits) >= 3:
            sql += (
                " AND (c.full_name ILIKE %s OR c.company_name ILIKE %s"
                " OR regexp_replace(c.phone, '\\D', '', 'g') LIKE %s)"
            )
            params += [f"%{query}%", f"%{query}%", f"%{digits}%"]
        else:
            sql += " AND (c.full_name ILIKE %s OR c.company_name ILIKE %s)"
            params += [f"%{query}%", f"%{query}%"]

    sql += " GROUP BY c.id"
    if active is True:
        sql += " HAVING COALESCE(BOOL_OR(l.status IN ('new', 'in_progress')), FALSE)"
    elif active is False:
        sql += " HAVING NOT COALESCE(BOOL_OR(l.status IN ('new', 'in_progress')), FALSE)"
    sql += " ORDER BY last_activity_at DESC NULLS LAST, c.id DESC"

    return fetch_all(sql, params)


def get_customer_detail(customer_id: int, company_id: int) -> dict[str, Any] | None:
    """The CRM detail card: the customer plus their whole deal history.

    ``top_manager_name`` is whoever has claimed the most of this customer's
    leads — "Eng faol menejer" on the card — and ``monthly_amounts`` sums deal
    value by month over the trailing six, which is what the bar chart draws.
    """
    customer = get_customer(customer_id, company_id)
    if not customer:
        return None

    deals = fetch_all(
        f"""
        SELECT id, amount, stage, status, kind, payment_method,
               created_at, completed_at
        FROM {B2B_WORKSPACE_LEAD_TABLE}
        WHERE customer_id = %s AND company_id = %s AND deleted_at IS NULL
        ORDER BY created_at DESC, id DESC
        """,
        [customer_id, company_id],
    )

    top_manager = fetch_one(
        f"""
        SELECT e.full_name AS name, COUNT(*) AS n
        FROM {B2B_WORKSPACE_LEAD_TABLE} l
        JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = l.claimed_by_id
        WHERE l.customer_id = %s AND l.company_id = %s AND l.claimed_by_id IS NOT NULL
          AND l.deleted_at IS NULL
        GROUP BY e.id, e.full_name
        ORDER BY n DESC, e.id ASC
        LIMIT 1
        """,
        [customer_id, company_id],
    )

    monthly_amounts = fetch_all(
        f"""
        SELECT to_char(date_trunc('month', created_at), 'YYYY-MM') AS month,
               COALESCE(SUM(amount), 0) AS amount
        FROM {B2B_WORKSPACE_LEAD_TABLE}
        WHERE customer_id = %s AND company_id = %s AND deleted_at IS NULL
          AND created_at >= date_trunc('month', NOW()) - INTERVAL '5 months'
        GROUP BY 1
        ORDER BY 1
        """,
        [customer_id, company_id],
    )

    return {
        **customer,
        "deal_count": len(deals),
        "total_amount": sum(float(deal["amount"] or 0) for deal in deals),
        "top_manager_name": (top_manager or {}).get("name"),
        "monthly_amounts": monthly_amounts,
        "deals": deals,
    }


# ─── Leads ────────────────────────────────────────────────────────────────────

LEAD_STATUSES = tuple(LeadStatus.CHOICES)
LEAD_STAGES = tuple(LeadStage.CHOICES)
LEAD_SOURCES = tuple(LeadSource.CHOICES)
#: What a person may pick when raising a lead by hand — everything but `meta`,
#: which only the ingest path writes.
LEAD_MANUAL_SOURCES = tuple(LeadSource.MANUAL_CHOICES)
LEAD_LOST_REASONS = tuple(LeadLostReason.CHOICES)
LEAD_QUALITIES = tuple(LeadQuality.CHOICES)
LEAD_KINDS = tuple(LeadKind.CHOICES)
PAYMENT_METHODS = tuple(PaymentMethod.CHOICES)

#: What ``list_leads(kind=...)`` takes for "both kinds at once" — the CRM's
#: view of a customer, where a deal is a deal however it was recorded. Its own
#: word rather than ``None``, which the default already means: leaving the
#: filter off asks for the funnel, not for everything.
LEAD_KIND_ANY = "any"

LEAD_KIND_FILTERS = (*LEAD_KINDS, LEAD_KIND_ANY)


#: What ``list_leads(quality=...)`` takes for "the ones nobody has judged".
#: Its own word rather than an empty string, which the view cannot tell from a
#: filter that was not asked for at all.
LEAD_QUALITY_UNMARKED = "unmarked"

LEAD_QUALITY_FILTERS = (*LEAD_QUALITIES, LEAD_QUALITY_UNMARKED)


def list_leads(
    company_id: int,
    *,
    status: str | None = None,
    stage: str | None = None,
    quality: str | None = None,
    kind: str | None = LeadKind.LEAD,
) -> list[dict[str, Any]]:
    """The board, and — with ``kind`` — the two other lists cut from the same
    table.

    ``kind`` defaults to ``LeadKind.LEAD`` rather than to "everything", which
    is the whole mechanism behind the quick sale: a sale recorded after the
    fact must never appear on the funnel as something to work, and the way to
    guarantee that is for the funnel's own query to ask for leads by name.
    ``LeadKind.QUICK_SALE`` lists the sales instead, and ``LEAD_KIND_ANY``
    lifts the filter for the readers that count deals rather than work them.
    """
    sql = (
        f"SELECT * FROM {B2B_WORKSPACE_LEAD_TABLE} "
        f"WHERE company_id = %s AND deleted_at IS NULL"
    )
    params: list[Any] = [company_id]
    if kind and kind != LEAD_KIND_ANY:
        sql += " AND kind = %s"
        params.append(kind)
    if status:
        sql += " AND status = %s"
        params.append(status)
    if stage:
        sql += " AND stage = %s"
        params.append(stage)
    if quality == LEAD_QUALITY_UNMARKED:
        sql += " AND quality IS NULL"
    elif quality:
        sql += " AND quality = %s"
        params.append(quality)
    sql += " ORDER BY created_at DESC, id DESC"
    return fetch_all(sql, params)


def find_lead_by_external_id(
    company_id: int, source: str, external_id: str
) -> dict[str, Any] | None:
    """The lead a connected service's own id already produced.

    Deleted leads included: a Meta delivery whose lead somebody has since
    binned must not come back as a second card the next time Meta retries.
    """
    return fetch_one(
        f"SELECT * FROM {B2B_WORKSPACE_LEAD_TABLE} "
        f"WHERE company_id = %s AND source = %s AND external_id = %s",
        [company_id, source, external_id],
    )


def get_lead(lead_id: int, company_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT * FROM {B2B_WORKSPACE_LEAD_TABLE} "
        f"WHERE id = %s AND company_id = %s AND deleted_at IS NULL",
        [lead_id, company_id],
    )


def delete_lead(lead_id: int, company_id: int, *, actor_id: int | None = None) -> bool:
    """Take the lead off the board without destroying it.

    Its items, its activity and any task raised off it are all untouched — the
    row is still there, and restoring it brings back a deal with its whole
    history rather than a shell. The TZ requires exactly this for leads and
    tasks; everything else in the schema still deletes outright.
    """
    deleted = bool(
        execute(
            f"UPDATE {B2B_WORKSPACE_LEAD_TABLE} "
            f"SET deleted_at = %s, deleted_by = %s, updated_at = %s "
            f"WHERE id = %s AND company_id = %s AND deleted_at IS NULL",
            [timezone.now(), actor_id, timezone.now(), lead_id, company_id],
        )
    )
    if deleted:
        from apps.b2b.workspace.access_repository import record_audit

        record_audit(
            company_id,
            actor_employee_id=actor_id,
            action="lead.deleted",
            target_type="lead",
            target_id=lead_id,
        )
    return deleted


def list_deleted_tasks(company_id: int, *, limit: int = 100) -> list[dict[str, Any]]:
    """The bin. Only reachable with the permission to see it — the TZ says an
    ordinary user does not see deleted objects at all."""
    return fetch_all(
        f"""
        SELECT t.*, e.full_name AS deleted_by_name
          FROM {B2B_TASK_TABLE} t
          LEFT JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = t.deleted_by
         WHERE t.company_id = %s AND t.deleted_at IS NOT NULL
         ORDER BY t.deleted_at DESC
         LIMIT %s
        """,
        [company_id, limit],
    )


def list_deleted_leads(company_id: int, *, limit: int = 100) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT l.*, e.full_name AS deleted_by_name
          FROM {B2B_WORKSPACE_LEAD_TABLE} l
          LEFT JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = l.deleted_by
         WHERE l.company_id = %s AND l.deleted_at IS NOT NULL
         ORDER BY l.deleted_at DESC
         LIMIT %s
        """,
        [company_id, limit],
    )


def restore_task(task_id: int, company_id: int) -> bool:
    """Put a task back in the working set.

    Scoped to `deleted_at IS NOT NULL` so the row count says whether anything
    was actually restored — restoring something that was never deleted should
    not read as success.
    """
    return bool(
        execute(
            f"UPDATE {B2B_TASK_TABLE} SET deleted_at = NULL, deleted_by = NULL, "
            f"updated_at = %s WHERE id = %s AND company_id = %s AND deleted_at IS NOT NULL",
            [timezone.now(), task_id, company_id],
        )
    )


def restore_lead(lead_id: int, company_id: int) -> bool:
    return bool(
        execute(
            f"UPDATE {B2B_WORKSPACE_LEAD_TABLE} SET deleted_at = NULL, deleted_by = NULL, "
            f"updated_at = %s WHERE id = %s AND company_id = %s AND deleted_at IS NOT NULL",
            [timezone.now(), lead_id, company_id],
        )
    )


def purge_task(task_id: int, company_id: int) -> bool:
    """Take a task out of the bin for good.

    `deleted_at IS NOT NULL` is the guard that matters: only something already
    in the bin may be purged, so a stray id can never destroy a live task, and
    the row count still says whether anything was there to destroy. Everything
    hanging off a task — its comments, its checklist, its attachments — is
    declared `ON DELETE CASCADE`, so this is one statement rather than a
    sweep, and the calendar rows that merely point at one are `SET NULL`.
    """
    return bool(
        execute(
            f"DELETE FROM {B2B_TASK_TABLE} "
            f"WHERE id = %s AND company_id = %s AND deleted_at IS NOT NULL",
            [task_id, company_id],
        )
    )


def purge_lead(lead_id: int, company_id: int) -> bool:
    return bool(
        execute(
            f"DELETE FROM {B2B_WORKSPACE_LEAD_TABLE} "
            f"WHERE id = %s AND company_id = %s AND deleted_at IS NOT NULL",
            [lead_id, company_id],
        )
    )


def create_lead(
    *,
    company_id: int,
    author_id: int,
    company_name: str,
    contact_full_name: str,
    contact_phone: str,
    product_name: str,
    quantity,
    contact_position: str | None = None,
    contact_email: str | None = None,
    contact_address: str | None = None,
    source: str = LeadSource.MANUAL,
    items: Sequence[dict[str, Any]] = (),
    customer_id: int | None = None,
    amount=None,
    due_date=None,
    note: str | None = None,
    claim_for_author: bool = False,
    kind: str = LeadKind.LEAD,
    payment_method: str | None = None,
    integration_id: int | None = None,
    external_id: str | None = None,
    external_form_name: str | None = None,
    external_data: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Raises a lead, and files its customer in the directory on the way past.

    The contact fields are the lead's own copy rather than a join through
    ``customer_id``: a card can be corrected months later, and a deal should
    keep the name and number it was actually worked against.

    ``amount`` is only honoured when there are no items — with items the total
    is theirs, and ``replace_lead_items`` mirrors it onto the row. A deal priced
    as one round number and a deal broken into lines are both real, and this is
    what lets the sheet accept either without the two disagreeing.

    ``kind=LeadKind.QUICK_SALE`` records a sale that has already happened
    rather than a deal to be worked: the row is written closed — won, claimed
    by its author, completed now — so it lands in the CRM card and every sales
    total the moment it exists, and never on the funnel, which asks for
    ``kind = 'lead'``. ``payment_method`` belongs to that case and is ignored
    on an ordinary lead, which has nothing to pay with yet.

    The four ``external_*``/``integration_*`` arguments are for a lead nobody
    typed: one that arrived from a connected service (see
    ``apps/b2b/integrations``). ``external_id`` is the other side's own id for
    it and carries a unique index per company and source, so an ingest path
    that runs twice on the same delivery raises the deal once — the insert
    below is the guard, not a check the caller has to remember.
    """
    now = timezone.now()
    customer = None
    if customer_id is not None:
        customer = get_customer(customer_id, company_id)
    if customer is None and contact_phone:
        customer = upsert_customer(
            company_id=company_id,
            full_name=contact_full_name,
            phone=contact_phone,
            company_name=company_name,
            position=contact_position,
            email=contact_email,
            address=contact_address,
        )

    # The customer card wins over what was typed: for a buyer already in the
    # directory the sheet shows their details locked, so anything different
    # arriving here is stale, not an edit.
    if customer:
        contact_full_name = customer.get("full_name") or contact_full_name
        contact_phone = customer.get("phone") or contact_phone
        company_name = customer.get("company_name") or company_name
        contact_position = customer.get("position") or contact_position
        # An existing card is missing an email or address more often than not
        # — the search step never asked for either — so a fresh deal is also a
        # chance to fill them in, without overwriting what is already there.
        if contact_email or contact_address:
            customer = upsert_customer(
                company_id=company_id,
                full_name=contact_full_name,
                phone=contact_phone,
                company_name=company_name,
                position=contact_position,
                email=contact_email,
                address=contact_address,
            ) or customer

    # A quick sale is a sale, not an offer: it is born won and held by whoever
    # recorded it, because there is no step left for anyone to take on it.
    # That is also what puts it in the totals — every figure downstream counts
    # completed/won rows, and this row is one from the moment it is written.
    is_quick_sale = kind == LeadKind.QUICK_SALE
    claim_for_author = claim_for_author or is_quick_sale
    claimed_by = author_id if claim_for_author else None
    lead = fetch_one(
        f"""
        INSERT INTO {B2B_WORKSPACE_LEAD_TABLE}
            (company_id, author_id, customer_id, company_name, contact_full_name,
             contact_phone, contact_position, contact_email, contact_address,
             product_name, quantity, amount, status, stage, source, kind,
             payment_method, claimed_by_id, claimed_at, completed_at, due_date,
             integration_id, external_id,
             external_form_name, external_data, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
        ON CONFLICT (company_id, source, external_id)
            WHERE external_id IS NOT NULL DO NOTHING
        RETURNING *
        """,
        [
            company_id, author_id, (customer or {}).get("id"), company_name,
            contact_full_name, contact_phone, contact_position, contact_email,
            contact_address, product_name, quantity, amount or 0,
            LeadStatus.COMPLETED if is_quick_sale
            else (LeadStatus.IN_PROGRESS if claim_for_author else LeadStatus.NEW),
            LeadStage.WON if is_quick_sale else LeadStage.NEW, source, kind,
            payment_method if is_quick_sale else None,
            claimed_by, now if claim_for_author else None,
            now if is_quick_sale else None, due_date,
            integration_id, external_id, external_form_name,
            json.dumps(external_data) if external_data is not None else None,
            now, now,
        ],
    )
    # `ON CONFLICT DO NOTHING` only ever fires on the external-id index — no
    # hand-raised lead has one — so an empty result here means this delivery
    # has already been turned into a lead. The caller wants that lead, not a
    # failure: a webhook Meta retried is a success the second time too.
    if not lead:
        if external_id:
            return find_lead_by_external_id(company_id, source, external_id)
        return None

    if items:
        replace_lead_items(lead["id"], items)
    add_lead_activity(lead["id"], kind=LeadActivityKind.CREATED, author_id=author_id)
    if claim_for_author:
        add_lead_activity(lead["id"], kind=LeadActivityKind.CLAIMED, author_id=author_id)
    # The history of a quick sale is one line long, and it should say what
    # actually happened: the deal was closed won the second it was entered.
    # Without this the card would claim it was created and never finished.
    if is_quick_sale:
        add_lead_activity(
            lead["id"],
            kind=LeadActivityKind.COMPLETED,
            author_id=author_id,
            # The same "from>to" the funnel's own moves write, so the feed
            # reads a quick sale exactly as it reads a deal that was walked to
            # the same place the long way.
            text=f"{LeadStage.NEW}>{LeadStage.WON}",
        )
    # The sheet's "Izoh" is the first thing said about the deal, so it lands in
    # the history as a comment rather than in a column nothing ever reads.
    if note and note.strip():
        add_lead_activity(
            lead["id"], kind=LeadActivityKind.COMMENT, author_id=author_id,
            text=note.strip(),
        )
    return get_lead(lead["id"], company_id)


def claim_lead(lead_id: int, company_id: int, employee_id: int) -> dict[str, Any] | None:
    """Atomically hand the lead to the first employee to ask for it.

    The ``WHERE status = 'new'`` on the UPDATE is the whole guard: two
    employees racing to claim the same lead only ever leaves one row updated,
    so the loser's request simply matches zero rows instead of overwriting
    the winner.
    """
    now = timezone.now()
    lead = fetch_one(
        f"""
        UPDATE {B2B_WORKSPACE_LEAD_TABLE}
        SET status = %s, claimed_by_id = %s, claimed_at = %s, updated_at = %s
        WHERE id = %s AND company_id = %s AND status = %s
        RETURNING *
        """,
        [LeadStatus.IN_PROGRESS, employee_id, now, now, lead_id, company_id, LeadStatus.NEW],
    )
    # Only the winner logs — the loser updated nothing and has nothing to say.
    if lead:
        add_lead_activity(lead_id, kind=LeadActivityKind.CLAIMED, author_id=employee_id)
    return lead


def assign_lead(
    lead_id: int, company_id: int, *, employee_id: int, actor_id: int
) -> dict[str, Any] | None:
    """Hands a lead to a named employee. A manager's move, not a claim.

    Unlike [claim_lead] this does not care what the current status is: the point
    of it is to take a lead off somebody and give it to somebody else. A lead
    that was still ``new`` becomes ``in_progress``, because it now has an owner;
    a completed one keeps its status.
    """
    now = timezone.now()
    lead = fetch_one(
        f"""
        UPDATE {B2B_WORKSPACE_LEAD_TABLE}
        SET claimed_by_id = %s,
            claimed_at = COALESCE(claimed_at, %s),
            status = CASE WHEN status = %s THEN %s ELSE status END,
            updated_at = %s
        WHERE id = %s AND company_id = %s
        RETURNING *
        """,
        [
            employee_id, now, LeadStatus.NEW, LeadStatus.IN_PROGRESS, now,
            lead_id, company_id,
        ],
    )
    if lead:
        add_lead_activity(
            lead_id, kind=LeadActivityKind.ASSIGNED, author_id=actor_id,
            text=str(employee_id),
        )
    return lead


def set_lead_stage(
    lead_id: int,
    company_id: int,
    *,
    stage: str,
    employee_id: int,
    lost_reason: str | None = None,
    note: str | None = None,
    attachment_file_id: int | None = None,
) -> dict[str, Any] | None:
    """Moves a lead along the funnel, and closes it if the stage closes it.

    The status follows the stage here and nowhere else, so "which stages mean
    done" is a single rule rather than one the callers each re-derive.

    ``lost_reason`` is stored only on the move to ``lost`` and cleared on any
    other move: a lead re-opened out of ``lost`` and closed again for a
    different reason must not keep the old one. ``note`` is the salesperson's
    own words and goes to the history as a comment, where the rest of what was
    said about this deal already lives.

    ``attachment_file_id`` is a document already stored by the view — a signed
    contract, the offer that went out — which is hung on the stage row this
    writes. It is linked here rather than by the caller because only this
    function knows the id of the history row the move produced, and a file
    left pointing at nothing is bytes the drive cannot show and nobody can
    reclaim.
    """
    current = get_lead(lead_id, company_id)
    if not current or current.get("stage") == stage:
        return current

    now = timezone.now()
    closing = stage in LeadStage.CLOSED
    losing = stage == LeadStage.LOST
    lead = fetch_one(
        f"""
        UPDATE {B2B_WORKSPACE_LEAD_TABLE}
        SET stage = %s,
            status = %s,
            completed_at = %s,
            lost_reason = %s,
            lost_note = %s,
            updated_at = %s
        WHERE id = %s AND company_id = %s
        RETURNING *
        """,
        [
            stage,
            LeadStatus.COMPLETED if closing else current.get("status"),
            now if closing else None,
            lost_reason if losing else None,
            (note or "").strip() or None if losing else None,
            now,
            lead_id,
            company_id,
        ],
    )
    if lead:
        moved = add_lead_activity(
            lead_id,
            kind=LeadActivityKind.COMPLETED if closing else LeadActivityKind.STAGE,
            author_id=employee_id,
            # The two stage names, so the feed can read "Yangi → Taklif
            # yuborildi" without having to guess what it moved from.
            text=f"{current.get('stage') or LeadStage.NEW}>{stage}",
        )
        if attachment_file_id and moved:
            link_file_to_lead_activity(attachment_file_id, moved["id"])
        if note and note.strip():
            add_lead_activity(
                lead_id, kind=LeadActivityKind.COMMENT, author_id=employee_id,
                text=note.strip(),
            )
    return lead


def set_lead_due_date(
    lead_id: int,
    company_id: int,
    *,
    due_date,
    employee_id: int,
) -> dict[str, Any] | None:
    """Sets, moves or clears the deal's deadline.

    A ``None`` clears it, which is a real answer rather than a no-op: a
    salesperson who decides a deal is not on a clock after all has to be able
    to say so, and leaving a stale date on the card would keep it going red for
    a reason nobody believes any more.

    Logged either way — a deadline that moves is exactly the kind of thing a
    manager reading the history wants to see, and a date that quietly slipped
    twice is the deal worth asking about.
    """
    now = timezone.now()
    lead = fetch_one(
        f"""
        UPDATE {B2B_WORKSPACE_LEAD_TABLE}
        SET due_date = %s, updated_at = %s
        WHERE id = %s AND company_id = %s
        RETURNING *
        """,
        [due_date, now, lead_id, company_id],
    )
    if lead:
        add_lead_activity(
            lead_id, kind=LeadActivityKind.DUE_DATE, author_id=employee_id,
            # The date itself, not a sentence: the app writes the words, in
            # whichever language the reader has the app set to.
            text=due_date.isoformat() if due_date else "",
        )
    return lead


def set_lead_quality(
    lead_id: int,
    company_id: int,
    *,
    quality: str | None,
    employee_id: int,
) -> dict[str, Any] | None:
    """Marks the enquiry good or bad, or takes the mark off.

    A ``None`` clears it, and that is a real answer rather than a no-op: a lead
    written off as noise on the strength of one unanswered call is exactly the
    kind of judgement that gets revised when the customer rings back, and a
    mark that could only be changed and never withdrawn would leave the board
    counting a bad lead that nobody believes in any more.

    Logged like the deadline is. Which leads a company throws away is a number
    a manager reads, and the row saying who called this one noise is the
    difference between a statistic and an argument.
    """
    now = timezone.now()
    lead = fetch_one(
        f"""
        UPDATE {B2B_WORKSPACE_LEAD_TABLE}
        SET quality = %s, updated_at = %s
        WHERE id = %s AND company_id = %s AND deleted_at IS NULL
        RETURNING *
        """,
        [quality, now, lead_id, company_id],
    )
    if lead:
        add_lead_activity(
            lead_id, kind=LeadActivityKind.QUALITY, author_id=employee_id,
            # The value, not a sentence — the app writes the words, in
            # whichever language the reader has the app set to.
            text=quality or "",
        )
    return lead


def complete_lead(lead_id: int, company_id: int, employee_id: int) -> dict[str, Any] | None:
    """The claiming employee marks the lead won.

    Kept as its own call rather than folded into [set_lead_stage] because it
    carries a guard that one does not: only the employee holding the lead may
    finish it, and only from ``in_progress``.
    """
    now = timezone.now()
    previous = (get_lead(lead_id, company_id) or {}).get("stage")
    lead = fetch_one(
        f"""
        UPDATE {B2B_WORKSPACE_LEAD_TABLE}
        SET status = %s, stage = %s, completed_at = %s, updated_at = %s
        WHERE id = %s AND company_id = %s AND status = %s AND claimed_by_id = %s
        RETURNING *
        """,
        [
            LeadStatus.COMPLETED, LeadStage.WON, now, now,
            lead_id, company_id, LeadStatus.IN_PROGRESS, employee_id,
        ],
    )
    if lead:
        add_lead_activity(
            lead_id, kind=LeadActivityKind.COMPLETED, author_id=employee_id,
            # Where it actually came from, read off the row before the UPDATE
            # rewrote it — the funnel gained a stage after this was written and
            # a hardcoded "from" would have started lying the day it did.
            text=f"{previous or LeadStage.NEW}>{LeadStage.WON}",
        )
    return lead


# ─── Lead line items ──────────────────────────────────────────────────────────

def list_lead_items(lead_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        f"SELECT * FROM {B2B_WORKSPACE_LEAD_ITEM_TABLE} WHERE lead_id = %s "
        "ORDER BY position, id",
        [lead_id],
    )


def replace_lead_items(lead_id: int, items: Sequence[dict[str, Any]]) -> None:
    """Swaps a lead's whole item list, then re-totals it.

    Delete-and-insert rather than a diff: the list is short, the client sends it
    whole, and matching rows up by name would break the moment somebody renamed
    one.
    """
    execute(f"DELETE FROM {B2B_WORKSPACE_LEAD_ITEM_TABLE} WHERE lead_id = %s", [lead_id])
    for position, item in enumerate(items):
        name = (item.get("name") or "").strip()
        if not name:
            continue
        execute(
            f"""
            INSERT INTO {B2B_WORKSPACE_LEAD_ITEM_TABLE}
                (lead_id, name, unit, amount, position, created_at,
                 product_id, qty, warehouse_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                lead_id, name, (item.get("unit") or "").strip(),
                item.get("amount") or 0, position, timezone.now(),
                item.get("product_id"), item.get("qty") or 1, item.get("warehouse_id"),
            ],
        )
    recalc_lead_amount(lead_id)


def add_lead_item(
    lead_id: int,
    *,
    name: str,
    unit: str = "",
    amount=0,
    product_id: int | None = None,
    qty=1,
    warehouse_id: int | None = None,
) -> dict[str, Any] | None:
    """One priced line. ``product_id``/``qty``/``warehouse_id`` are set when
    the line was picked off the catalogue — that is what lets the won lead
    come off the shelf (see ``inventory_repository.record_sale_for_lead``)."""
    row = fetch_one(
        f"SELECT COALESCE(MAX(position), -1) + 1 AS next FROM "
        f"{B2B_WORKSPACE_LEAD_ITEM_TABLE} WHERE lead_id = %s",
        [lead_id],
    )
    item = fetch_one(
        f"""
        INSERT INTO {B2B_WORKSPACE_LEAD_ITEM_TABLE}
            (lead_id, name, unit, amount, position, created_at,
             product_id, qty, warehouse_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        [
            lead_id, name, unit, amount, int((row or {}).get("next") or 0), timezone.now(),
            product_id, qty or 1, warehouse_id,
        ],
    )
    recalc_lead_amount(lead_id)
    return item


def delete_lead_item(lead_id: int, item_id: int) -> bool:
    deleted = execute(
        f"DELETE FROM {B2B_WORKSPACE_LEAD_ITEM_TABLE} WHERE id = %s AND lead_id = %s",
        [item_id, lead_id],
    )
    recalc_lead_amount(lead_id)
    return bool(deleted)


def recalc_lead_amount(lead_id: int) -> None:
    """Mirrors SUM(items.amount) onto the lead.

    Denormalised on purpose: the board lists every lead in the company and the
    card shows the money, so the alternative is a join and a GROUP BY on every
    list call to recompute a number that only changes when somebody edits an
    item.
    """
    execute(
        f"""
        UPDATE {B2B_WORKSPACE_LEAD_TABLE}
        SET amount = COALESCE((
                SELECT SUM(amount) FROM {B2B_WORKSPACE_LEAD_ITEM_TABLE}
                WHERE lead_id = %s
            ), 0),
            updated_at = %s
        WHERE id = %s
        """,
        [lead_id, timezone.now(), lead_id],
    )


# ─── Lead activity ────────────────────────────────────────────────────────────

def list_lead_activity(lead_id: int, *, limit: int = 100) -> list[dict[str, Any]]:
    """Newest first, with the author's name joined on so the feed does not need
    the roster to render, and the document filed with the move where there is
    one — a row carries at most one, so this stays a single query rather than a
    second pass over the page."""
    return _with_author_photo(fetch_all(
        f"""
        SELECT a.*,
               e.full_name AS author_name,
               e.photo AS author_photo,
               f.id AS attachment_id,
               f.name AS attachment_name,
               f.path AS attachment_path,
               f.size AS attachment_size,
               f.content_type AS attachment_content_type
        FROM {B2B_WORKSPACE_LEAD_ACTIVITY_TABLE} a
        LEFT JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = a.author_id
        LEFT JOIN {B2B_WORKSPACE_FILE_TABLE} f ON f.lead_activity_id = a.id
        WHERE a.lead_id = %s
        ORDER BY a.created_at DESC, a.id DESC
        LIMIT %s
        """,
        [lead_id, limit],
    ))


def add_lead_activity(
    lead_id: int,
    *,
    kind: str,
    author_id: int | None = None,
    text: str = "",
) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        INSERT INTO {B2B_WORKSPACE_LEAD_ACTIVITY_TABLE}
            (lead_id, author_id, kind, text, created_at)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING *
        """,
        [lead_id, author_id, kind, text, timezone.now()],
    )


def link_file_to_lead_activity(file_id: int, activity_id: int) -> bool:
    """Points a stored file at the history row it documents.

    The file is written first — the bytes and the quota row have to exist
    before anything can reference them — and the history row only exists once
    the move has been made, so the two are joined here rather than at either
    end.
    """
    return execute(
        f"UPDATE {B2B_WORKSPACE_FILE_TABLE} SET lead_activity_id = %s WHERE id = %s",
        [activity_id, file_id],
    ) > 0


def add_lead_comment(lead_id: int, *, author_id: int, text: str) -> dict[str, Any] | None:
    """A typed note. The only activity kind the client may create directly."""
    activity = add_lead_activity(
        lead_id, kind=LeadActivityKind.COMMENT, author_id=author_id, text=text
    )
    if not activity:
        return None
    # Re-read through the list query so a comment comes back with the same
    # author fields every other row has.
    rows = list_lead_activity(lead_id, limit=1)
    return rows[0] if rows else activity


# ─── Tasks raised off a lead ──────────────────────────────────────────────────

def list_lead_tasks(lead_id: int, company_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        f"SELECT * FROM {B2B_TASK_TABLE} WHERE lead_id = %s AND company_id = %s "
        "AND deleted_at IS NULL ORDER BY created_at DESC, id DESC",
        [lead_id, company_id],
    )


# ─── Board counters ───────────────────────────────────────────────────────────
#
# Both take the whole page of lead ids and answer in one query. The funnel lists
# every lead in the company, so asking per lead is the difference between one
# round trip and one per card.

def count_lead_items(lead_ids: Sequence[int]) -> dict[int, int]:
    if not lead_ids:
        return {}
    rows = fetch_all(
        f"SELECT lead_id, COUNT(*) AS total FROM {B2B_WORKSPACE_LEAD_ITEM_TABLE} "
        "WHERE lead_id = __ANY_MARKER__(%s) GROUP BY lead_id",
        [list(lead_ids)],
    )
    return {int(row["lead_id"]): int(row["total"]) for row in rows}


def count_lead_tasks(company_id: int, lead_ids: Sequence[int]) -> dict[int, int]:
    if not lead_ids:
        return {}
    rows = fetch_all(
        f"SELECT lead_id, COUNT(*) AS total FROM {B2B_TASK_TABLE} "
        "WHERE company_id = %s AND lead_id = __ANY_MARKER__(%s) "
        "AND deleted_at IS NULL GROUP BY lead_id",
        [company_id, list(lead_ids)],
    )
    return {int(row["lead_id"]): int(row["total"]) for row in rows}


# ─── Files ────────────────────────────────────────────────────────────────────

def list_files(
    company_id: int,
    kind: str | None = "file",
    folder_id: int | None = None,
) -> list[dict[str, Any]]:
    """The shared drive.

    Defaults to ``kind='file'``: chat attachments and vouchers live in the same
    table because that is what makes the quota one SUM, but they are not drive
    documents and listing them here would fill the Fayllar tab with every photo
    anyone ever sent. Pass ``kind=None`` to get everything.

    ``folder_id`` narrows to one folder's contents, and then the kind stops
    mattering: a folder is something a person put files into, so what it holds
    is whatever they put there.
    """
    where = "company_id = %s"
    params: list[Any] = [company_id]
    if folder_id is not None:
        where += " AND folder_id = %s"
        params.append(folder_id)
    elif kind is not None:
        where += " AND kind = %s"
        params.append(kind)

    return fetch_all(
        f"SELECT * FROM {B2B_WORKSPACE_FILE_TABLE} WHERE {where} "
        "ORDER BY created_at DESC, id DESC",
        params,
    )


# ─── Folders ──────────────────────────────────────────────────────────────────

def list_folders(company_id: int) -> list[dict[str, Any]]:
    """The company's folders, each with what it holds.

    The count and the size come back with the row rather than from a query per
    folder: the drive screen draws every folder at once, and a card that had to
    ask for its own numbers would turn one screen into N+1 round trips.
    """
    return fetch_all(
        f"""
        SELECT f.*,
               COUNT(w.id)                  AS file_count,
               COALESCE(SUM(w.size), 0)     AS size_bytes
        FROM {B2B_WORKSPACE_FOLDER_TABLE} f
        LEFT JOIN {B2B_WORKSPACE_FILE_TABLE} w ON w.folder_id = f.id
        WHERE f.company_id = %s
        GROUP BY f.id
        ORDER BY f.created_at DESC, f.id DESC
        """,
        [company_id],
    )


def get_folder(folder_id: int, company_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT * FROM {B2B_WORKSPACE_FOLDER_TABLE} WHERE id = %s AND company_id = %s",
        [folder_id, company_id],
    )


def create_folder(*, company_id: int, author_id: int, name: str) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        INSERT INTO {B2B_WORKSPACE_FOLDER_TABLE} (company_id, author_id, name, created_at)
        VALUES (%s, %s, %s, %s)
        RETURNING *
        """,
        [company_id, author_id, name, timezone.now()],
    )


def delete_folder(folder_id: int, company_id: int) -> bool:
    """Removes the folder. Its files stay.

    ``folder_id`` on the file rows is ON DELETE SET NULL, so they fall back to
    the drive itself — emptying a shelf is not the same act as throwing out
    what was on it, and the bytes are still the company's either way.
    """
    return execute(
        f"DELETE FROM {B2B_WORKSPACE_FOLDER_TABLE} WHERE id = %s AND company_id = %s",
        [folder_id, company_id],
    ) > 0


def get_file(file_id: int, company_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT * FROM {B2B_WORKSPACE_FILE_TABLE} WHERE id = %s AND company_id = %s",
        [file_id, company_id],
    )


def update_file(file_id: int, company_id: int, **fields: Any) -> dict[str, Any] | None:
    """Renames a file, moves it into a folder, or both.

    Only the two columns a person can change from the drive screen. The path,
    the size and the kind are facts about the stored bytes, not something a
    rename is allowed to touch — a "rename" that could rewrite the path would
    be a way to point a row at somebody else's object.
    """
    allowed = {key: value for key, value in fields.items() if key in {"name", "folder_id"}}
    if not allowed:
        return get_file(file_id, company_id)

    assignments = ", ".join(f"{key} = %s" for key in allowed)
    return fetch_one(
        f"UPDATE {B2B_WORKSPACE_FILE_TABLE} SET {assignments} "
        "WHERE id = %s AND company_id = %s RETURNING *",
        [*allowed.values(), file_id, company_id],
    )


def create_file(
    *,
    company_id: int,
    author_id: int,
    name: str,
    path: str,
    size: int,
    kind: str = "file",
    content_type: str | None = None,
    message_id: int | None = None,
    trip_id: int | None = None,
    task_id: int | None = None,
    folder_id: int | None = None,
    lead_activity_id: int | None = None,
    note_id: int | None = None,
    duration_ms: int | None = None,
) -> dict[str, Any] | None:
    """Records stored bytes.

    Every upload path goes through here — the drive, a chat attachment, a
    generated voucher — because this row *is* the company's storage accounting.
    Writing an object to storage without one makes those bytes invisible to the
    quota and impossible to reclaim.
    """
    return fetch_one(
        f"""
        INSERT INTO {B2B_WORKSPACE_FILE_TABLE}
            (company_id, author_id, name, path, size, kind, content_type,
             message_id, trip_id, task_id, folder_id, lead_activity_id,
             note_id, duration_ms, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        [
            company_id, author_id, name, path, size, kind, content_type,
            message_id, trip_id, task_id, folder_id, lead_activity_id,
            note_id, duration_ms, timezone.now(),
        ],
    )


def attachments_for_messages(message_ids: Sequence[int]) -> dict[int, dict[str, Any]]:
    """Attachment per message id, fetched in one query.

    A room page is up to 200 messages; asking per message would be 200 round
    trips to render one screen.
    """
    if not message_ids:
        return {}
    rows = fetch_all(
        f"SELECT * FROM {B2B_WORKSPACE_FILE_TABLE} WHERE message_id = __ANY_MARKER__(%s)",
        [list(message_ids)],
    )
    return {row["message_id"]: row for row in rows}


def files_for_company_paths(company_id: int, kind: str) -> list[str]:
    """Storage paths for one kind — used when reclaiming space."""
    rows = fetch_all(
        f"SELECT path FROM {B2B_WORKSPACE_FILE_TABLE} "
        "WHERE company_id = %s AND kind = %s",
        [company_id, kind],
    )
    return [row["path"] for row in rows]


def delete_file(file_id: int, company_id: int) -> bool:
    return execute(
        f"DELETE FROM {B2B_WORKSPACE_FILE_TABLE} WHERE id = %s AND company_id = %s",
        [file_id, company_id],
    ) > 0


# ─── Employee of the month ──────────────────────────────────────────────────

def _month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    tz = timezone.get_current_timezone()
    start = datetime(year, month, 1, tzinfo=tz)
    end = datetime(year + 1, 1, 1, tzinfo=tz) if month == 12 else datetime(year, month + 1, 1, tzinfo=tz)
    return start, end


def monthly_employee_stats(company_id: int, year: int, month: int) -> list[dict[str, Any]]:
    """Every active employee's month, as the owner judges it from.

    ``on_time_count`` is out of ``due_count`` — tasks that had a due date —
    not out of every completed task: one with no deadline was never late by
    definition, and counting it either way would just dilute the rate.

    ``deals_count`` is deals they closed as won in the same window. Counted in
    its own subquery rather than a third LEFT JOIN: joining both the task
    assignees and the leads off one employee row multiplies them together, and
    every task count would come back scaled by however many deals that person
    closed.

    ``present_days`` / ``absent_days`` are the same month's attendance, and
    for the same reason they are a subquery too. ``unexcused_days`` counts
    only the absences nobody wrote a reason against — the ones the owner
    actually holds against a month, as opposed to sick leave.

    The job title and the department ride along because the screen that reads
    this lists people by name and has nowhere else to get them from — without
    it the app would fetch the whole roster again just to label six rows.
    """
    start, end = _month_bounds(year, month)
    return fetch_all(
        f"""
        SELECT
            e.id                                                 AS employee_id,
            e.full_name,
            e.photo,
            e.position,
            d.name                                                AS department_name,
            COUNT(t.id)                                           AS completed_count,
            COUNT(t.id) FILTER (WHERE t.due_date IS NOT NULL)     AS due_count,
            COUNT(t.id) FILTER (WHERE t.due_date IS NOT NULL
                                 AND t.completed_at <= t.due_date) AS on_time_count,
            COALESCE(deals.deals_count, 0)                        AS deals_count,
            COALESCE(att.present_days, 0)                         AS present_days,
            COALESCE(att.absent_days, 0)                          AS absent_days,
            COALESCE(att.unexcused_days, 0)                       AS unexcused_days
        FROM {B2B_EMPLOYEE_TABLE} e
        LEFT JOIN {B2B_DEPARTMENT_TABLE} d ON d.id = e.department_id
        LEFT JOIN {B2B_TASK_ASSIGNEE_TABLE} ta ON ta.employee_id = e.id
        LEFT JOIN {B2B_TASK_TABLE} t
            ON t.id = ta.task_id
            AND t.status = 'done'
            AND t.completed_at >= %s AND t.completed_at < %s
        LEFT JOIN (
            SELECT claimed_by_id AS employee_id, COUNT(*) AS deals_count
            FROM {B2B_WORKSPACE_LEAD_TABLE}
            WHERE company_id = %s
              AND stage = 'won'
              AND deleted_at IS NULL
              AND claimed_by_id IS NOT NULL
              AND completed_at >= %s AND completed_at < %s
            GROUP BY claimed_by_id
        ) deals ON deals.employee_id = e.id
        LEFT JOIN (
            SELECT
                employee_id,
                COUNT(*) FILTER (
                    WHERE status IN ('present', 'late', 'remote')
                )                                                 AS present_days,
                COUNT(*) FILTER (WHERE status = 'absent')          AS absent_days,
                COUNT(*) FILTER (
                    WHERE status = 'absent'
                      AND COALESCE(BTRIM(reason), '') = ''
                )                                                 AS unexcused_days
            FROM {B2B_ATTENDANCE_TABLE}
            WHERE company_id = %s
              AND work_date >= %s AND work_date < %s
            GROUP BY employee_id
        ) att ON att.employee_id = e.id
        WHERE e.company_id = %s AND e.is_active = TRUE
        GROUP BY e.id, e.full_name, e.photo, e.position, d.name, deals.deals_count,
                 att.present_days, att.absent_days, att.unexcused_days
        -- Absences break the tie rather than decide the order: the owner still
        -- makes the pick, but two people with the same month of work should
        -- not be listed as if one of them had not missed a fortnight of it.
        ORDER BY completed_count DESC, deals_count DESC,
                 unexcused_days ASC, present_days DESC, e.full_name ASC
        """,
        [start, end, company_id, start, end,
         company_id, start.date(), end.date(), company_id],
    )


def completed_tasks_this_month(employee_id: int, year: int, month: int) -> int:
    """The one number the profile screen prints — "Bu oy: 24 ta vazifa
    bajarildi". Same window and same definition of "done" as
    :func:`monthly_employee_stats`, so the two can never disagree."""
    start, end = _month_bounds(year, month)
    return fetch_one(
        f"""
        SELECT COUNT(*) AS n
        FROM {B2B_TASK_ASSIGNEE_TABLE} ta
        JOIN {B2B_TASK_TABLE} t ON t.id = ta.task_id
        WHERE ta.employee_id = %s
          AND t.status = 'done'
          AND t.completed_at >= %s AND t.completed_at < %s
        """,
        [employee_id, start, end],
    )["n"]


def list_employees_of_month(
    company_id: int, year: int, month: int
) -> list[dict[str, Any]]:
    """Everyone the owner named this month, in the order they were named.

    A month used to have exactly one winner. It has as many as the owner
    wants: a good month is rarely one person's, and a badge that can only go
    to one of four people who all had it is a badge that annoys three of them.

    The job title and the department ride along because the card that reads
    this prints the department under the name and has nowhere else to get it
    — the alternative is fetching the whole roster to label two cards.
    """
    return fetch_all(
        f"""
        SELECT eom.year, eom.month, eom.selected_at,
               e.id AS employee_id, e.full_name, e.photo, e.position,
               d.name AS department_name
        FROM {B2B_EMPLOYEE_OF_MONTH_TABLE} eom
        JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = eom.employee_id
        LEFT JOIN {B2B_DEPARTMENT_TABLE} d ON d.id = e.department_id
        WHERE eom.company_id = %s AND eom.year = %s AND eom.month = %s
        ORDER BY eom.selected_at, eom.id
        """,
        [company_id, year, month],
    )


def set_employees_of_month(
    *,
    company_id: int,
    year: int,
    month: int,
    employee_ids: Sequence[int],
    selected_by_id: int,
) -> list[dict[str, Any]]:
    """Replaces the month's whole list with the one given.

    A replace, not a merge, because that is what the screen sending it means:
    the owner ticks the people they want and saves, and somebody taken off the
    list has to actually come off it. An empty list therefore clears the month,
    which is the only way to take a badge back after it was given by mistake.
    """
    now = timezone.now()
    execute(
        f"DELETE FROM {B2B_EMPLOYEE_OF_MONTH_TABLE} "
        "WHERE company_id = %s AND year = %s AND month = %s",
        [company_id, year, month],
    )
    for employee_id in dict.fromkeys(employee_ids):
        execute(
            f"""
            INSERT INTO {B2B_EMPLOYEE_OF_MONTH_TABLE}
                (company_id, year, month, employee_id, selected_by_id, selected_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (company_id, year, month, employee_id) DO NOTHING
            """,
            [company_id, year, month, employee_id, selected_by_id, now],
        )
    return list_employees_of_month(company_id, year, month)


# ─── Reports and analytics ──────────────────────────────────────────────────
#
# The "Hisobot va analitika" screen: one read of the three things a workspace
# actually runs on — the sales funnel, the task board, the calendar — over a
# window the reader picks.
#
# Everything here counts in the database. The screen shows six months of a
# company's work at once, and the alternative is pulling every lead, task and
# event into the app to add them up — which is both the slowest thing the API
# could do and quietly wrong, since the list endpoints are paged.
#
# Two scopes, decided by the caller rather than here: a manager reads the
# company, and a plain employee reads their own work. `employee_id` is what
# narrows it, and each section defines "theirs" the way that section's screens
# already do — a lead they raised or claimed, a task they wrote or were given,
# an event they called or were invited to.

#: Windows the screen offers, and the bucket a trend line is drawn in for
#: each. A year in days would be 365 points on a phone-width chart, and a week
#: in months would be one.
REPORT_PERIODS: dict[str, tuple[int, str]] = {
    "week": (7, "1 day"),
    "month": (30, "1 day"),
    "quarter": (90, "1 week"),
    "year": (365, "1 month"),
}

DEFAULT_REPORT_PERIOD = "month"


def report_window(period: str, *, now: datetime | None = None) -> dict[str, Any]:
    """The window a period name stands for, resolved against one clock.

    Read once per request and passed down, so the three sections of a report
    cannot each take their own `NOW()` and disagree about where the month
    ended by a few milliseconds.

    An unknown period is the default rather than an error: the parameter comes
    off a query string, and a report is a page to read, not a form to fail.
    """
    period = period if period in REPORT_PERIODS else DEFAULT_REPORT_PERIOD
    days, bucket = REPORT_PERIODS[period]
    end = now or timezone.now()
    return {
        "period": period,
        "start": end - timedelta(days=days),
        "end": end,
        "bucket": bucket,
    }


def _lead_scope(employee_id: int | None) -> tuple[str, list[Any]]:
    """"Mine" on the sales board: raised by me, or claimed by me.

    Both, not just the claim: a salesperson who logs an enquiry somebody else
    then takes on has still done the work of logging it, and a report that
    showed them nothing for it would be read as broken.
    """
    if employee_id is None:
        return "", []
    return " AND (l.author_id = %s OR l.claimed_by_id = %s)", [employee_id, employee_id]


def _task_scope(employee_id: int | None, alias: str = "t") -> tuple[str, list[Any]]:
    """"Mine" on the task board — written by me, or handed to me. The same
    pair [list_tasks] filters its own board by."""
    if employee_id is None:
        return "", []
    return (
        f"""
          AND ({alias}.author_id = %s
               OR EXISTS (
                   SELECT 1 FROM {B2B_TASK_ASSIGNEE_TABLE} a
                   WHERE a.task_id = {alias}.id AND a.employee_id = %s
               ))
        """,
        [employee_id, employee_id],
    )


def _event_scope(employee_id: int | None) -> tuple[str, list[Any]]:
    """"Mine" on the calendar — called by me, or I am on it."""
    if employee_id is None:
        return "", []
    return (
        f"""
          AND (e.author_id = %s
               OR EXISTS (
                   SELECT 1 FROM {B2B_CALENDAR_PARTICIPANT_TABLE} p
                   WHERE p.event_id = e.id AND p.employee_id = %s
               ))
        """,
        [employee_id, employee_id],
    )


def _money(value: Any) -> str:
    """Amounts leave this module as strings.

    They are NUMERIC(14,2) in the database and a deal can run to eleven
    figures in so'm — through a float that is no longer the number the
    salesperson typed, and the client formats it for display anyway.
    """
    return str(value or 0)


def sales_report(
    company_id: int,
    *,
    start: datetime,
    end: datetime,
    bucket: str,
    employee_id: int | None = None,
) -> dict[str, Any]:
    """The funnel over one window.

    Two clocks are in play and the difference matters: leads are *created*
    (``created_at``) and deals are *closed* (``completed_at``). Counting both
    off the same column would report a deal in the month it was first enquired
    about rather than the month the money came in, which is not the question
    the screen is asking.

    ``open_*`` are deliberately as-of-now rather than windowed: a pipeline is
    a present-tense fact — what is still out there to win — and one clipped to
    last month's dates would answer a question nobody asked.

    Quick sales are counted, unlike on the funnel board itself. They are money
    the company took, and a sales report that left them out would disagree
    with the till.
    """
    scope, scope_params = _lead_scope(employee_id)

    # `LeadStage.CLOSED` rather than a literal `(%s, %s)`: a lead archived
    # without a verdict is off the board exactly like a won or a lost one, so
    # it has to leave `open_count`/`open_amount`/`by_stage` the same way those
    # two do — see the note on `LeadStage.ARCHIVED`.
    closed_stages = ", ".join(["%s"] * len(LeadStage.CLOSED))

    totals = fetch_one(
        f"""
        SELECT
            COUNT(*) FILTER (
                WHERE l.created_at >= %s AND l.created_at < %s
            )                                                    AS created_count,
            COUNT(*) FILTER (
                WHERE l.stage = %s
                  AND l.completed_at >= %s AND l.completed_at < %s
            )                                                    AS won_count,
            COUNT(*) FILTER (
                WHERE l.stage = %s
                  AND l.completed_at >= %s AND l.completed_at < %s
            )                                                    AS lost_count,
            COALESCE(SUM(l.amount) FILTER (
                WHERE l.stage = %s
                  AND l.completed_at >= %s AND l.completed_at < %s
            ), 0)                                                AS won_amount,
            COUNT(*) FILTER (
                WHERE l.stage NOT IN ({closed_stages})
            )                                                    AS open_count,
            COALESCE(SUM(l.amount) FILTER (
                WHERE l.stage NOT IN ({closed_stages})
            ), 0)                                                AS open_amount
        FROM {B2B_WORKSPACE_LEAD_TABLE} l
        WHERE l.company_id = %s AND l.deleted_at IS NULL{scope}
        """,
        [
            start, end,
            LeadStage.WON, start, end,
            LeadStage.LOST, start, end,
            LeadStage.WON, start, end,
            *LeadStage.CLOSED,
            *LeadStage.CLOSED,
            company_id, *scope_params,
        ],
    ) or {}

    by_stage = fetch_all(
        f"""
        SELECT l.stage, COUNT(*) AS count, COALESCE(SUM(l.amount), 0) AS amount
        FROM {B2B_WORKSPACE_LEAD_TABLE} l
        WHERE l.company_id = %s AND l.deleted_at IS NULL
          AND l.stage NOT IN ({closed_stages}){scope}
        GROUP BY l.stage
        """,
        [company_id, *LeadStage.CLOSED, *scope_params],
    )
    # Ordered here rather than in SQL: the funnel's order is `LeadStage.ORDER`
    # and not alphabetical, and a stage nobody is sitting in still has to
    # appear — a funnel with a step missing reads as a bug in the funnel.
    stage_counts = {row["stage"]: row for row in by_stage}
    stages = [
        {
            "stage": stage,
            "count": int((stage_counts.get(stage) or {}).get("count") or 0),
            "amount": _money((stage_counts.get(stage) or {}).get("amount")),
        }
        for stage in LeadStage.ORDER
        if stage not in LeadStage.CLOSED
    ]

    by_source = fetch_all(
        f"""
        SELECT
            l.source,
            COUNT(*)                                   AS count,
            COUNT(*) FILTER (WHERE l.stage = %s)       AS won_count,
            COALESCE(SUM(l.amount) FILTER (WHERE l.stage = %s), 0) AS won_amount
        FROM {B2B_WORKSPACE_LEAD_TABLE} l
        WHERE l.company_id = %s AND l.deleted_at IS NULL
          AND l.created_at >= %s AND l.created_at < %s{scope}
        GROUP BY l.source
        ORDER BY count DESC, l.source ASC
        """,
        [LeadStage.WON, LeadStage.WON, company_id, start, end, *scope_params],
    )

    lost_reasons = fetch_all(
        f"""
        SELECT COALESCE(l.lost_reason, %s) AS reason, COUNT(*) AS count
        FROM {B2B_WORKSPACE_LEAD_TABLE} l
        WHERE l.company_id = %s AND l.deleted_at IS NULL
          AND l.stage = %s
          AND l.completed_at >= %s AND l.completed_at < %s{scope}
        GROUP BY 1
        ORDER BY count DESC, reason ASC
        """,
        [LeadLostReason.OTHER, company_id, LeadStage.LOST, start, end, *scope_params],
    )

    # Buckets come from `generate_series` rather than from the rows, so a week
    # in which nothing was sold is a zero on the chart instead of a gap the
    # line is drawn straight through.
    trend = fetch_all(
        f"""
        SELECT
            d.bucket,
            (
                SELECT COUNT(*) FROM {B2B_WORKSPACE_LEAD_TABLE} l
                WHERE l.company_id = %s AND l.deleted_at IS NULL
                  AND l.created_at >= d.bucket
                  AND l.created_at < d.bucket + %s::interval{scope}
            ) AS created_count,
            (
                SELECT COUNT(*) FROM {B2B_WORKSPACE_LEAD_TABLE} l
                WHERE l.company_id = %s AND l.deleted_at IS NULL
                  AND l.stage = %s
                  AND l.completed_at >= d.bucket
                  AND l.completed_at < d.bucket + %s::interval{scope}
            ) AS won_count,
            (
                SELECT COALESCE(SUM(l.amount), 0) FROM {B2B_WORKSPACE_LEAD_TABLE} l
                WHERE l.company_id = %s AND l.deleted_at IS NULL
                  AND l.stage = %s
                  AND l.completed_at >= d.bucket
                  AND l.completed_at < d.bucket + %s::interval{scope}
            ) AS won_amount
        FROM generate_series(%s::timestamptz, %s::timestamptz, %s::interval) AS d(bucket)
        ORDER BY d.bucket
        """,
        [
            company_id, bucket, *scope_params,
            company_id, LeadStage.WON, bucket, *scope_params,
            company_id, LeadStage.WON, bucket, *scope_params,
            start, end, bucket,
        ],
    )

    # Never narrowed by `employee_id`: a leaderboard of one person is not a
    # leaderboard. An employee's own row is theirs to find in it.
    leaders = fetch_all(
        f"""
        SELECT
            e.id AS employee_id, e.full_name, e.photo,
            COUNT(*)                            AS won_count,
            COALESCE(SUM(l.amount), 0)          AS won_amount
        FROM {B2B_WORKSPACE_LEAD_TABLE} l
        JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = l.claimed_by_id
        WHERE l.company_id = %s AND l.deleted_at IS NULL
          AND l.stage = %s
          AND l.completed_at >= %s AND l.completed_at < %s
        GROUP BY e.id, e.full_name, e.photo
        ORDER BY won_amount DESC, won_count DESC, e.full_name ASC
        LIMIT 5
        """,
        [company_id, LeadStage.WON, start, end],
    )

    won = int(totals.get("won_count") or 0)
    lost = int(totals.get("lost_count") or 0)
    closed = won + lost

    return {
        "created_count": int(totals.get("created_count") or 0),
        "won_count": won,
        "lost_count": lost,
        "open_count": int(totals.get("open_count") or 0),
        "won_amount": _money(totals.get("won_amount")),
        "open_amount": _money(totals.get("open_amount")),
        # Out of the deals that were *decided* in the window, not out of every
        # lead created in it: a deal still being worked has not failed to
        # convert, it simply has not been answered yet, and counting it as a
        # miss makes every healthy pipeline look like a bad month.
        "conversion_rate": round(won / closed, 4) if closed else 0.0,
        # Through `Decimal`, not a float: the sum arrives as NUMERIC and an
        # average taken in binary floating point comes back with a tail of
        # digits nobody sold.
        "average_deal": _money(
            Decimal(str(totals.get("won_amount") or 0)) / won if won else 0
        ),
        "by_stage": stages,
        "by_source": [
            {
                "source": row["source"],
                "count": int(row["count"] or 0),
                "won_count": int(row["won_count"] or 0),
                "won_amount": _money(row["won_amount"]),
            }
            for row in by_source
        ],
        "lost_reasons": [
            {"reason": row["reason"], "count": int(row["count"] or 0)}
            for row in lost_reasons
        ],
        "trend": [
            {
                "date": row["bucket"].isoformat(),
                "created_count": int(row["created_count"] or 0),
                "won_count": int(row["won_count"] or 0),
                "won_amount": _money(row["won_amount"]),
            }
            for row in trend
        ],
        "leaders": [
            {
                "employee_id": int(row["employee_id"]),
                "full_name": row["full_name"],
                # Through the resolver, like every other payload that
                # carries this column: shipped bare, the phone builds
                # `<host>/b2b/...` with no `/media/` in it and the leader
                # board falls back to initials.
                "photo": _photo_url(row["photo"]),
                "won_count": int(row["won_count"] or 0),
                "won_amount": _money(row["won_amount"]),
            }
            for row in leaders
        ],
    }


def task_report(
    company_id: int,
    *,
    start: datetime,
    end: datetime,
    bucket: str,
    employee_id: int | None = None,
) -> dict[str, Any]:
    """The board over one window.

    ``on_time_rate`` is out of the tasks that had a deadline, for the same
    reason [monthly_employee_stats] gives: one with no due date was never late
    by definition, and counting it either way only dilutes the number.

    ``open_count`` / ``overdue_count`` / ``due_today_count`` are as-of-now.
    What is late is late today, regardless of which window is on screen.
    """
    scope, scope_params = _task_scope(employee_id)

    totals = fetch_one(
        f"""
        SELECT
            COUNT(*) FILTER (
                WHERE t.created_at >= %s AND t.created_at < %s
            )                                                       AS created_count,
            COUNT(*) FILTER (
                WHERE t.status = 'done'
                  AND t.completed_at >= %s AND t.completed_at < %s
            )                                                       AS completed_count,
            COUNT(*) FILTER (
                WHERE t.status = 'done' AND t.due_date IS NOT NULL
                  AND t.completed_at >= %s AND t.completed_at < %s
            )                                                       AS due_count,
            COUNT(*) FILTER (
                WHERE t.status = 'done' AND t.due_date IS NOT NULL
                  AND t.completed_at >= %s AND t.completed_at < %s
                  AND t.completed_at <= t.due_date
            )                                                       AS on_time_count,
            COUNT(*) FILTER (WHERE t.status <> 'done')               AS open_count,
            COUNT(*) FILTER (
                WHERE t.status <> 'done' AND t.due_date IS NOT NULL
                  AND t.due_date::date < CURRENT_DATE
            )                                                       AS overdue_count,
            COUNT(*) FILTER (
                WHERE t.status <> 'done' AND t.due_date IS NOT NULL
                  AND t.due_date::date = CURRENT_DATE
            )                                                       AS due_today_count,
            COUNT(*) FILTER (WHERE t.status = 'todo')                AS todo_count,
            COUNT(*) FILTER (WHERE t.status = 'in_progress')         AS in_progress_count
        FROM {B2B_TASK_TABLE} t
        WHERE t.company_id = %s AND t.deleted_at IS NULL{scope}
        """,
        [
            start, end,
            start, end,
            start, end,
            start, end,
            company_id, *scope_params,
        ],
    ) or {}

    by_priority = fetch_all(
        f"""
        SELECT t.priority, COUNT(*) AS count
        FROM {B2B_TASK_TABLE} t
        WHERE t.company_id = %s AND t.deleted_at IS NULL
          AND t.status <> 'done'{scope}
        GROUP BY t.priority
        """,
        [company_id, *scope_params],
    )
    priority_counts = {row["priority"]: int(row["count"] or 0) for row in by_priority}

    trend = fetch_all(
        f"""
        SELECT
            d.bucket,
            (
                SELECT COUNT(*) FROM {B2B_TASK_TABLE} t
                WHERE t.company_id = %s AND t.deleted_at IS NULL
                  AND t.created_at >= d.bucket
                  AND t.created_at < d.bucket + %s::interval{scope}
            ) AS created_count,
            (
                SELECT COUNT(*) FROM {B2B_TASK_TABLE} t
                WHERE t.company_id = %s AND t.deleted_at IS NULL
                  AND t.status = 'done'
                  AND t.completed_at >= d.bucket
                  AND t.completed_at < d.bucket + %s::interval{scope}
            ) AS completed_count
        FROM generate_series(%s::timestamptz, %s::timestamptz, %s::interval) AS d(bucket)
        ORDER BY d.bucket
        """,
        [
            company_id, bucket, *scope_params,
            company_id, bucket, *scope_params,
            start, end, bucket,
        ],
    )

    # Counted over the tasks people were *given*, like the employee card and
    # the month's stats — a manager who raises everybody's work would
    # otherwise top a chart of work done.
    leaders = fetch_all(
        f"""
        SELECT
            e.id AS employee_id, e.full_name, e.photo,
            COUNT(*)                                              AS completed_count,
            COUNT(*) FILTER (WHERE t.due_date IS NOT NULL)        AS due_count,
            COUNT(*) FILTER (
                WHERE t.due_date IS NOT NULL AND t.completed_at <= t.due_date
            )                                                     AS on_time_count
        FROM {B2B_TASK_ASSIGNEE_TABLE} ta
        JOIN {B2B_TASK_TABLE} t ON t.id = ta.task_id
        JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = ta.employee_id
        WHERE t.company_id = %s AND t.deleted_at IS NULL
          AND t.status = 'done'
          AND t.completed_at >= %s AND t.completed_at < %s
        GROUP BY e.id, e.full_name, e.photo
        ORDER BY completed_count DESC, on_time_count DESC, e.full_name ASC
        LIMIT 5
        """,
        [company_id, start, end],
    )

    due = int(totals.get("due_count") or 0)
    on_time = int(totals.get("on_time_count") or 0)

    return {
        "created_count": int(totals.get("created_count") or 0),
        "completed_count": int(totals.get("completed_count") or 0),
        "open_count": int(totals.get("open_count") or 0),
        "todo_count": int(totals.get("todo_count") or 0),
        "in_progress_count": int(totals.get("in_progress_count") or 0),
        "overdue_count": int(totals.get("overdue_count") or 0),
        "due_today_count": int(totals.get("due_today_count") or 0),
        "on_time_rate": round(on_time / due, 4) if due else 0.0,
        "by_priority": [
            {"priority": priority, "count": priority_counts.get(priority, 0)}
            for priority in TASK_PRIORITIES
        ],
        "trend": [
            {
                "date": row["bucket"].isoformat(),
                "created_count": int(row["created_count"] or 0),
                "completed_count": int(row["completed_count"] or 0),
            }
            for row in trend
        ],
        "leaders": [
            {
                "employee_id": int(row["employee_id"]),
                "full_name": row["full_name"],
                # Through the resolver, like every other payload that
                # carries this column: shipped bare, the phone builds
                # `<host>/b2b/...` with no `/media/` in it and the leader
                # board falls back to initials.
                "photo": _photo_url(row["photo"]),
                "completed_count": int(row["completed_count"] or 0),
                "on_time_rate": (
                    round(int(row["on_time_count"] or 0) / int(row["due_count"]), 4)
                    if int(row["due_count"] or 0)
                    else 0.0
                ),
            }
            for row in leaders
        ],
    }


def calendar_report(
    company_id: int,
    *,
    start: datetime,
    end: datetime,
    bucket: str,
    employee_id: int | None = None,
) -> dict[str, Any]:
    """The calendar over one window.

    Counted by when an event *starts*, which is the only reading of "meetings
    in October" anybody means. All-day entries are counted like any other but
    contribute no hours: a day blocked out for a trip is not eight hours of
    meetings, and letting it claim twenty-four would swamp every real figure
    beside it.
    """
    scope, scope_params = _event_scope(employee_id)

    totals = fetch_one(
        f"""
        SELECT
            COUNT(*)                                       AS total_count,
            COUNT(*) FILTER (WHERE e.all_day)              AS all_day_count,
            COALESCE(SUM(
                EXTRACT(EPOCH FROM (e.ends_at - e.starts_at)) / 3600.0
            ) FILTER (WHERE NOT e.all_day), 0)             AS hours
        FROM {B2B_CALENDAR_EVENT_TABLE} e
        WHERE e.company_id = %s
          AND e.starts_at >= %s AND e.starts_at < %s{scope}
        """,
        [company_id, start, end, *scope_params],
    ) or {}

    upcoming = fetch_one(
        f"""
        SELECT COUNT(*) AS count
        FROM {B2B_CALENDAR_EVENT_TABLE} e
        WHERE e.company_id = %s AND e.starts_at >= %s{scope}
        """,
        [company_id, end, *scope_params],
    ) or {}

    by_type = fetch_all(
        f"""
        SELECT e.event_type, COUNT(*) AS count
        FROM {B2B_CALENDAR_EVENT_TABLE} e
        WHERE e.company_id = %s
          AND e.starts_at >= %s AND e.starts_at < %s{scope}
        GROUP BY e.event_type
        """,
        [company_id, start, end, *scope_params],
    )
    type_counts = {row["event_type"]: int(row["count"] or 0) for row in by_type}

    # ISODOW, so the week reads Monday-first the way the calendar screen draws
    # it, rather than Postgres's Sunday-first `DOW`.
    by_weekday = fetch_all(
        f"""
        SELECT EXTRACT(ISODOW FROM e.starts_at)::int AS weekday, COUNT(*) AS count
        FROM {B2B_CALENDAR_EVENT_TABLE} e
        WHERE e.company_id = %s
          AND e.starts_at >= %s AND e.starts_at < %s{scope}
        GROUP BY 1
        """,
        [company_id, start, end, *scope_params],
    )
    weekday_counts = {int(row["weekday"]): int(row["count"] or 0) for row in by_weekday}

    trend = fetch_all(
        f"""
        SELECT
            d.bucket,
            (
                SELECT COUNT(*) FROM {B2B_CALENDAR_EVENT_TABLE} e
                WHERE e.company_id = %s
                  AND e.starts_at >= d.bucket
                  AND e.starts_at < d.bucket + %s::interval{scope}
            ) AS count
        FROM generate_series(%s::timestamptz, %s::timestamptz, %s::interval) AS d(bucket)
        ORDER BY d.bucket
        """,
        [company_id, bucket, *scope_params, start, end, bucket],
    )

    return {
        "total_count": int(totals.get("total_count") or 0),
        "all_day_count": int(totals.get("all_day_count") or 0),
        "upcoming_count": int(upcoming.get("count") or 0),
        "hours": round(float(totals.get("hours") or 0), 1),
        "by_type": [
            {"event_type": event_type, "count": type_counts.get(event_type, 0)}
            for event_type in EVENT_TYPES
        ],
        "by_weekday": [
            {"weekday": weekday, "count": weekday_counts.get(weekday, 0)}
            for weekday in range(1, 8)
        ],
        "trend": [
            {"date": row["bucket"].isoformat(), "count": int(row["count"] or 0)}
            for row in trend
        ],
    }


# ─── Attendance ─────────────────────────────────────────────────────────────

def attendance_for_date(company_id: int, work_date) -> list[dict[str, Any]]:
    """The roll call for one day.

    Left-joined off the roster rather than read straight from the attendance
    table: someone with no row for the day has not been accounted for, and a
    query over the attendance table alone would silently leave them out of a
    list whose whole purpose is to show who is missing. They come back with a
    null status, which the view reads as "unmarked".
    """
    return fetch_all(
        f"""
        SELECT
            e.id            AS employee_id,
            e.full_name,
            e.position,
            e.photo,
            d.name          AS department_name,
            a.status,
            a.checked_in_at,
            a.checked_out_at,
            a.reason,
            a.marked_by_id
        FROM {B2B_EMPLOYEE_TABLE} e
        LEFT JOIN {B2B_DEPARTMENT_TABLE} d ON d.id = e.department_id
        LEFT JOIN {B2B_ATTENDANCE_TABLE} a
               ON a.employee_id = e.id AND a.work_date = %s
        WHERE e.company_id = %s AND e.is_active = TRUE
        ORDER BY e.full_name
        """,
        [work_date, company_id],
    )


def upsert_attendance(
    *,
    company_id: int,
    employee_id: int,
    work_date,
    status: str,
    checked_in_at=None,
    checked_out_at=None,
    reason: str | None = None,
    marked_by_id: int | None = None,
    check_in_latitude: float | None = None,
    check_in_longitude: float | None = None,
    check_out_latitude: float | None = None,
    check_out_longitude: float | None = None,
) -> dict[str, Any] | None:
    """Records one employee's day.

    ON CONFLICT rather than a read-then-write: two managers marking the same
    person at once, or an employee double-tapping check-in, would otherwise
    race into two rows the UNIQUE key then rejects outright.

    `checked_in_at` is only overwritten when a new one is given — correcting a
    status from a manager's screen must not erase the time the employee
    actually arrived. The check-in coordinates follow the same rule: a
    manager's own mark carries none, and must not blank out where the
    employee's own check-in happened. `checked_out_at` and its coordinates are
    the same again — a manager marking the day must not wipe a "Ketdim" the
    employee filed.
    """
    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO {B2B_ATTENDANCE_TABLE}
            (company_id, employee_id, work_date, status, checked_in_at,
             checked_out_at, reason, marked_by_id,
             check_in_latitude, check_in_longitude,
             check_out_latitude, check_out_longitude,
             created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (employee_id, work_date) DO UPDATE SET
            status              = EXCLUDED.status,
            checked_in_at       = COALESCE(EXCLUDED.checked_in_at, {B2B_ATTENDANCE_TABLE}.checked_in_at),
            checked_out_at      = COALESCE(EXCLUDED.checked_out_at, {B2B_ATTENDANCE_TABLE}.checked_out_at),
            reason              = EXCLUDED.reason,
            marked_by_id        = EXCLUDED.marked_by_id,
            check_in_latitude   = COALESCE(EXCLUDED.check_in_latitude, {B2B_ATTENDANCE_TABLE}.check_in_latitude),
            check_in_longitude  = COALESCE(EXCLUDED.check_in_longitude, {B2B_ATTENDANCE_TABLE}.check_in_longitude),
            check_out_latitude  = COALESCE(EXCLUDED.check_out_latitude, {B2B_ATTENDANCE_TABLE}.check_out_latitude),
            check_out_longitude = COALESCE(EXCLUDED.check_out_longitude, {B2B_ATTENDANCE_TABLE}.check_out_longitude),
            updated_at          = EXCLUDED.updated_at
        RETURNING *
        """,
        [
            company_id, employee_id, work_date, status, checked_in_at,
            checked_out_at, reason, marked_by_id,
            check_in_latitude, check_in_longitude,
            check_out_latitude, check_out_longitude, now, now,
        ],
    )


def attendance_row(employee_id: int, work_date) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT * FROM {B2B_ATTENDANCE_TABLE} "
        "WHERE employee_id = %s AND work_date = %s",
        [employee_id, work_date],
    )


# ─── Attendance location (geofence) ────────────────────────────────────────

def get_attendance_location(company_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT * FROM {B2B_ATTENDANCE_LOCATION_TABLE} WHERE company_id = %s",
        [company_id],
    )


def upsert_attendance_location(
    *,
    company_id: int,
    is_enabled: bool,
    latitude: float | None,
    longitude: float | None,
    radius_meters: int,
    updated_by_id: int,
) -> dict[str, Any] | None:
    """One row per company — the point and radius check-ins are measured
    against. ON CONFLICT so switching it on and off never fights the UNIQUE
    constraint the way two managers marking the same employee would."""
    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO {B2B_ATTENDANCE_LOCATION_TABLE}
            (company_id, is_enabled, latitude, longitude, radius_meters,
             updated_by_id, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (company_id) DO UPDATE SET
            is_enabled     = EXCLUDED.is_enabled,
            latitude       = EXCLUDED.latitude,
            longitude      = EXCLUDED.longitude,
            radius_meters  = EXCLUDED.radius_meters,
            updated_by_id  = EXCLUDED.updated_by_id,
            updated_at     = EXCLUDED.updated_at
        RETURNING *
        """,
        [
            company_id, is_enabled, latitude, longitude, radius_meters,
            updated_by_id, now, now,
        ],
    )


# ─── Yordam markazi ─────────────────────────────────────────────────────────
#
# One flat log per employee. See the table's own comment in
# ``create_b2b_tables`` for why there is no thread row.

def list_support_messages(employee_id: int, limit: int = 200) -> list[dict[str, Any]]:
    """One employee's conversation, oldest first — reading order.

    The limit takes the *newest* rows and then flips them, so a long-running
    conversation shows its recent end rather than its opening lines.
    """
    rows = fetch_all(
        f"""
        SELECT id, text, is_staff, created_at
        FROM {B2B_SUPPORT_MESSAGE_TABLE}
        WHERE employee_id = %s
        ORDER BY created_at DESC, id DESC
        LIMIT %s
        """,
        [employee_id, limit],
    )
    return list(reversed(rows))


def create_support_message(
    *,
    company_id: int,
    employee_id: int,
    text: str,
    is_staff: bool = False,
    author_user_id: int | None = None,
) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        INSERT INTO {B2B_SUPPORT_MESSAGE_TABLE}
            (company_id, employee_id, text, is_staff, author_user_id, created_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id, text, is_staff, created_at
        """,
        [company_id, employee_id, text, is_staff, author_user_id, timezone.now()],
    )


def mark_support_read(employee_id: int) -> None:
    """The employee has seen support's replies. Only the staff side is marked:
    what the employee wrote was never unread to them."""
    execute(
        f"""
        UPDATE {B2B_SUPPORT_MESSAGE_TABLE}
        SET read_at = %s
        WHERE employee_id = %s AND is_staff = TRUE AND read_at IS NULL
        """,
        [timezone.now(), employee_id],
    )


def list_support_threads(search: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """The admin inbox: one row per employee who has ever written in, newest
    conversation first.

    A "thread" is derived rather than stored — see ``create_b2b_tables`` for
    why there is no thread table. ``unread_count`` counts the employee's own
    unanswered lines, not support's: the inbox is a queue of people waiting,
    and a reply that has been sent is off it.
    """
    where = ""
    params: list[Any] = []
    if search:
        where = "WHERE e.full_name ILIKE %s OR c.name ILIKE %s OR e.phone ILIKE %s"
        needle = f"%{search}%"
        params = [needle, needle, needle]

    params.append(limit)
    return fetch_all(
        f"""
        SELECT
            e.id                                          AS employee_id,
            e.full_name,
            e.phone,
            e.photo,
            c.id                                          AS company_id,
            c.name                                        AS company_name,
            COUNT(m.id)                                   AS message_count,
            COUNT(m.id) FILTER (
                WHERE m.is_staff = FALSE AND m.read_at IS NULL
            )                                             AS unread_count,
            MAX(m.created_at)                             AS last_message_at,
            (
                SELECT text FROM {B2B_SUPPORT_MESSAGE_TABLE} latest
                WHERE latest.employee_id = e.id
                ORDER BY latest.created_at DESC, latest.id DESC
                LIMIT 1
            )                                             AS last_message
        FROM {B2B_SUPPORT_MESSAGE_TABLE} m
        JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = m.employee_id
        JOIN {B2B_COMPANY_TABLE} c ON c.id = m.company_id
        {where}
        GROUP BY e.id, e.full_name, e.phone, e.photo, c.id, c.name
        ORDER BY last_message_at DESC
        LIMIT %s
        """,
        params,
    )


def mark_support_answered(employee_id: int) -> None:
    """Support has read what this employee wrote. The mirror of
    :func:`mark_support_read`, which is the employee reading support."""
    execute(
        f"""
        UPDATE {B2B_SUPPORT_MESSAGE_TABLE}
        SET read_at = %s
        WHERE employee_id = %s AND is_staff = FALSE AND read_at IS NULL
        """,
        [timezone.now(), employee_id],
    )


def support_employee(employee_id: int) -> dict[str, Any] | None:
    """Who a support thread belongs to — needed before replying into it, so a
    reply cannot be addressed to an id that is not an employee."""
    return fetch_one(
        f"""
        SELECT e.id, e.full_name, e.phone, e.photo, e.company_id, c.name AS company_name
        FROM {B2B_EMPLOYEE_TABLE} e
        JOIN {B2B_COMPANY_TABLE} c ON c.id = e.company_id
        WHERE e.id = %s
        """,
        [employee_id],
    )


def set_own_profile(
    employee_id: int, *, full_name: str, email: str | None
) -> dict[str, Any] | None:
    """What somebody may change about their own entry in the roster.

    Narrow on purpose. The name and a way of being reached are theirs. The
    position, the department and the role are the workspace's answer to "what
    do you do here", and stay with whoever runs it — see [WorkspaceProfileView]
    for why the app draws those as read-only rather than hiding them.

    The name is written to every workspace this account works in, not only the
    one being edited. The employee row keeps its own copy of the name to save a
    join on every roster query (see `accounts.create_membership`), not because
    each workspace authors it separately — somebody correcting their surname
    means it everywhere, and leaving the other copies behind is how one person
    ends up under two names inside one company.

    The email deliberately does not travel: it is the address this workspace
    reaches them at, and somebody may well use different ones in two of them.
    """
    now = timezone.now()
    execute(
        f"UPDATE {B2B_EMPLOYEE_TABLE} SET full_name = %s, email = %s, updated_at = %s "
        f"WHERE id = %s",
        [full_name, email, now, employee_id],
    )
    employee = get_workspace_employee(employee_id)

    account_id = (employee or {}).get("account_id")
    if account_id:
        execute(
            f"UPDATE {B2B_EMPLOYEE_TABLE} SET full_name = %s, updated_at = %s "
            f"WHERE account_id = %s AND id <> %s",
            [full_name, now, account_id, employee_id],
        )
    return employee


def set_own_photo(employee_id: int, path: str | None) -> dict[str, Any] | None:
    """Their face, everywhere they work.

    Written to every membership this account holds, the same way the name is
    (see [set_own_profile]) and for the same reason: a photo is about the
    person, not about one workspace's record of them, and leaving the other
    copies behind is how somebody ends up as a face in one room and initials
    in the next. The account row keeps a copy too, because that is what an
    invite preview and the workspace picker read — neither of which has a
    membership to look at yet.
    """
    now = timezone.now()
    execute(
        f"UPDATE {B2B_EMPLOYEE_TABLE} SET photo = %s, updated_at = %s WHERE id = %s",
        [path, now, employee_id],
    )
    employee = get_workspace_employee(employee_id)

    account_id = (employee or {}).get("account_id")
    if account_id:
        execute(
            f"UPDATE {B2B_EMPLOYEE_TABLE} SET photo = %s, updated_at = %s "
            "WHERE account_id = %s AND id <> %s",
            [path, now, account_id, employee_id],
        )
        from apps.b2b.workspace.accounts import B2B_ACCOUNT_TABLE

        execute(
            f"UPDATE {B2B_ACCOUNT_TABLE} SET photo = %s, updated_at = %s WHERE id = %s",
            [path, now, account_id],
        )
    return employee


def set_employee_username(employee_id: int, username: str | None) -> dict[str, Any] | None:
    """Claim a handle, or give one up.

    Returns None when somebody in the same workspace already has it. The
    unique index is what decides — checking first and then writing would let
    two people who pick "@aziz" in the same second both pass the check.
    """
    from django.db import IntegrityError

    try:
        execute(
            f"UPDATE {B2B_EMPLOYEE_TABLE} SET username = %s, updated_at = %s WHERE id = %s",
            [username or None, timezone.now(), employee_id],
        )
    except IntegrityError:
        return None
    return get_workspace_employee(employee_id)


def sync_username_across_memberships(account_id: int, username: str | None) -> None:
    """Copy an account's handle down to every roster row it owns.

    The handle lives on the account (see the note in `create_b2b_tables.py`),
    but each `b2b_employee` row keeps its own copy so roster listing and the
    member search do not need a join per query — the same reason the name and
    photo are copied down. This keeps those copies honest after a rename.
    """
    execute(
        f"UPDATE {B2B_EMPLOYEE_TABLE} SET username = %s, updated_at = %s "
        f"WHERE account_id = %s",
        [username or None, timezone.now(), account_id],
    )


def username_taken(company_id: int, username: str, *, exclude_employee_id: int) -> bool:
    """Whether this handle is already somebody else's, in this workspace.

    Read before the write purely so the answer can be a sentence rather than a
    database error. [set_employee_username] is still the authority.
    """
    row = fetch_one(
        f"SELECT 1 AS taken FROM {B2B_EMPLOYEE_TABLE} "
        f"WHERE company_id = %s AND LOWER(username) = LOWER(%s) AND id <> %s",
        [company_id, username, exclude_employee_id],
    )
    return bool(row)
