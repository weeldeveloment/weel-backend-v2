"""Raw-SQL data access for the B2B mobile workspace.

Follows the same conventions as ``apps/b2b/repository.py``: plain dicts in and
out, every query scoped by ``company_id``, no ORM.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Iterable, Sequence

from django.utils import timezone

from shared.raw.db import execute, fetch_all, fetch_one

from apps.b2b.models import (
    EmployeeRole,
    LeadActivityKind,
    LeadLostReason,
    LeadSource,
    LeadStage,
    LeadStatus,
)
from apps.b2b.raw.tables import (
    B2B_CALENDAR_EVENT_TABLE,
    B2B_CALENDAR_PARTICIPANT_TABLE,
    B2B_CHAT_MEMBER_TABLE,
    B2B_CHAT_MESSAGE_TABLE,
    B2B_CHAT_THREAD_TABLE,
    B2B_COMPANY_TABLE,
    B2B_DEPARTMENT_TABLE,
    B2B_ATTENDANCE_TABLE,
    B2B_ATTENDANCE_LOCATION_TABLE,
    B2B_SUPPORT_MESSAGE_TABLE,
    B2B_EMPLOYEE_OF_MONTH_TABLE,
    B2B_EMPLOYEE_TABLE,
    B2B_TASK_ASSIGNEE_TABLE,
    B2B_TASK_COMMENT_TABLE,
    B2B_TASK_SUBTASK_TABLE,
    B2B_TASK_TABLE,
    B2B_USER_TABLE,
    B2B_WORKSPACE_FILE_TABLE,
    B2B_WORKSPACE_LEAD_TABLE,
    B2B_WORKSPACE_CUSTOMER_TABLE,
    B2B_WORKSPACE_LEAD_ACTIVITY_TABLE,
    B2B_WORKSPACE_LEAD_ITEM_TABLE,
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
    suffix = _phone_suffix(phone)
    if not suffix:
        return None
    return fetch_one(
        f"""
        SELECT * FROM {B2B_EMPLOYEE_TABLE}
        WHERE is_active = TRUE
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
        WHERE e.company_id = %s AND e.is_active = TRUE
    """
    params: list[Any] = [company_id]
    if search:
        sql += " AND (e.full_name ILIKE %s OR e.position ILIKE %s)"
        needle = f"%{search}%"
        params += [needle, needle]
    sql += " ORDER BY e.full_name ASC"
    return fetch_all(sql, params)


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

TASK_STATUSES = ("todo", "in_progress", "review", "done")
TASK_PRIORITIES = ("low", "medium", "high", "urgent")


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
    sql = f"SELECT t.* FROM {B2B_TASK_TABLE} t WHERE t.company_id = %s"
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


def get_task(task_id: int, company_id: int) -> dict[str, Any] | None:
    task = fetch_one(
        f"SELECT * FROM {B2B_TASK_TABLE} WHERE id = %s AND company_id = %s",
        [task_id, company_id],
    )
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
    return get_task(task["id"], company_id)


def update_task(task_id: int, company_id: int, **fields: Any) -> dict[str, Any] | None:
    if fields:
        sets = ", ".join(f"{key} = %s" for key in fields)
        params = list(fields.values()) + [timezone.now(), task_id, company_id]
        fetch_one(
            f"UPDATE {B2B_TASK_TABLE} SET {sets}, updated_at = %s "
            f"WHERE id = %s AND company_id = %s RETURNING *",
            params,
        )
    return get_task(task_id, company_id)


def delete_task(task_id: int, company_id: int) -> bool:
    return execute(
        f"DELETE FROM {B2B_TASK_TABLE} WHERE id = %s AND company_id = %s",
        [task_id, company_id],
    ) > 0


def set_task_assignees(task_id: int, employee_ids: Sequence[int]) -> None:
    execute(f"DELETE FROM {B2B_TASK_ASSIGNEE_TABLE} WHERE task_id = %s", [task_id])
    for employee_id in dict.fromkeys(employee_ids):  # de-duplicate, keep order
        execute(
            f"INSERT INTO {B2B_TASK_ASSIGNEE_TABLE} (task_id, employee_id, created_at) "
            f"VALUES (%s, %s, %s) ON CONFLICT (task_id, employee_id) DO NOTHING",
            [task_id, employee_id, timezone.now()],
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


def task_counters(company_id: int, visible_to: int | None = None) -> dict[str, int]:
    """Counts for the stat tiles, computed in the database rather than by
    pulling every task into the app just to count them."""
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
            COUNT(*) FILTER (WHERE status = 'done')                        AS done_count,
            COUNT(*) FILTER (WHERE status <> 'done' AND due_date IS NOT NULL
                             AND due_date::date < CURRENT_DATE)            AS overdue_count,
            COUNT(*) FILTER (WHERE status <> 'done' AND due_date IS NOT NULL
                             AND due_date::date = CURRENT_DATE)            AS due_today_count
        FROM {B2B_TASK_TABLE}
        WHERE {where}
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


# ─── Chat ─────────────────────────────────────────────────────────────────────

def list_threads(company_id: int, employee_id: int) -> list[dict[str, Any]]:
    """Every thread the employee belongs to, with their per-member flags, the
    unread count and the last message — one query, so the list screen does not
    fan out per row."""
    threads = fetch_all(
        f"""
        SELECT
            t.id,
            t.group_name,
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
        ORDER BY m.is_pinned DESC, COALESCE(t.last_message_at, t.created_at) DESC
        """,
        [employee_id, employee_id, company_id],
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
        WHERE t.company_id = %s AND t.group_name IS NULL
        GROUP BY t.id
        HAVING COUNT(*) = 2
           AND BOOL_OR(m.employee_id = %s)
           AND BOOL_OR(m.employee_id = %s)
        LIMIT 1
        """,
        [company_id, a, b],
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
        f"(company_id, group_name, created_by, created_at, updated_at) "
        f"VALUES (%s, %s, %s, %s, %s) RETURNING *",
        [company_id, group_name, created_by, now, now],
    )
    if not thread:
        return None
    for employee_id in dict.fromkeys([created_by, *member_ids]):
        execute(
            f"INSERT INTO {B2B_CHAT_MEMBER_TABLE} (thread_id, employee_id, created_at, updated_at) "
            f"VALUES (%s, %s, %s, %s) ON CONFLICT (thread_id, employee_id) DO NOTHING",
            [thread["id"], employee_id, now, now],
        )
    return get_thread_for_member(thread["id"], company_id, created_by)


def list_messages(
    thread_id: int,
    *,
    before_id: int | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """A page of history, oldest-first for rendering but paged from the newest
    end so opening a room does not read years of backlog."""
    sql = f"SELECT * FROM {B2B_CHAT_MESSAGE_TABLE} WHERE thread_id = %s"
    params: list[Any] = [thread_id]
    if before_id:
        sql += " AND id < %s"
        params.append(before_id)
    sql += " ORDER BY id DESC LIMIT %s"
    params.append(limit)

    return list(reversed(fetch_all(sql, params)))


def send_message(
    thread_id: int,
    sender_id: int,
    text: str,
    reply_to_id: int | None = None,
) -> dict[str, Any] | None:
    now = timezone.now()
    message = fetch_one(
        f"INSERT INTO {B2B_CHAT_MESSAGE_TABLE} "
        "(thread_id, sender_id, text, reply_to_id, created_at) "
        f"VALUES (%s, %s, %s, %s, %s) RETURNING *",
        [thread_id, sender_id, text, reply_to_id, now],
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


def mark_thread_read(thread_id: int, employee_id: int) -> None:
    execute(
        f"UPDATE {B2B_CHAT_MEMBER_TABLE} SET last_read_at = %s, updated_at = %s "
        f"WHERE thread_id = %s AND employee_id = %s",
        [timezone.now(), timezone.now(), thread_id, employee_id],
    )


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
                 WHERE l.customer_id = c.id) AS deal_count
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
        f"SELECT COUNT(*) AS total FROM {B2B_WORKSPACE_LEAD_TABLE} WHERE customer_id = %s",
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
        SELECT id, amount, stage, status, created_at, completed_at
        FROM {B2B_WORKSPACE_LEAD_TABLE}
        WHERE customer_id = %s AND company_id = %s
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
        WHERE customer_id = %s AND company_id = %s
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
LEAD_LOST_REASONS = tuple(LeadLostReason.CHOICES)


def list_leads(
    company_id: int,
    *,
    status: str | None = None,
    stage: str | None = None,
) -> list[dict[str, Any]]:
    sql = f"SELECT * FROM {B2B_WORKSPACE_LEAD_TABLE} WHERE company_id = %s"
    params: list[Any] = [company_id]
    if status:
        sql += " AND status = %s"
        params.append(status)
    if stage:
        sql += " AND stage = %s"
        params.append(stage)
    sql += " ORDER BY created_at DESC, id DESC"
    return fetch_all(sql, params)


def get_lead(lead_id: int, company_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT * FROM {B2B_WORKSPACE_LEAD_TABLE} WHERE id = %s AND company_id = %s",
        [lead_id, company_id],
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
    note: str | None = None,
    claim_for_author: bool = False,
) -> dict[str, Any] | None:
    """Raises a lead, and files its customer in the directory on the way past.

    The contact fields are the lead's own copy rather than a join through
    ``customer_id``: a card can be corrected months later, and a deal should
    keep the name and number it was actually worked against.

    ``amount`` is only honoured when there are no items — with items the total
    is theirs, and ``replace_lead_items`` mirrors it onto the row. A deal priced
    as one round number and a deal broken into lines are both real, and this is
    what lets the sheet accept either without the two disagreeing.
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

    claimed_by = author_id if claim_for_author else None
    lead = fetch_one(
        f"""
        INSERT INTO {B2B_WORKSPACE_LEAD_TABLE}
            (company_id, author_id, customer_id, company_name, contact_full_name,
             contact_phone, contact_position, contact_email, contact_address,
             product_name, quantity, amount, status, stage, source,
             claimed_by_id, claimed_at, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        [
            company_id, author_id, (customer or {}).get("id"), company_name,
            contact_full_name, contact_phone, contact_position, contact_email,
            contact_address, product_name, quantity, amount or 0,
            LeadStatus.IN_PROGRESS if claim_for_author else LeadStatus.NEW,
            LeadStage.NEW, source,
            claimed_by, now if claim_for_author else None, now, now,
        ],
    )
    if not lead:
        return None

    if items:
        replace_lead_items(lead["id"], items)
    add_lead_activity(lead["id"], kind=LeadActivityKind.CREATED, author_id=author_id)
    if claim_for_author:
        add_lead_activity(lead["id"], kind=LeadActivityKind.CLAIMED, author_id=author_id)
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
) -> dict[str, Any] | None:
    """Moves a lead along the funnel, and closes it if the stage closes it.

    The status follows the stage here and nowhere else, so "which stages mean
    done" is a single rule rather than one the callers each re-derive.

    ``lost_reason`` is stored only on the move to ``lost`` and cleared on any
    other move: a lead re-opened out of ``lost`` and closed again for a
    different reason must not keep the old one. ``note`` is the salesperson's
    own words and goes to the history as a comment, where the rest of what was
    said about this deal already lives.
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
        add_lead_activity(
            lead_id,
            kind=LeadActivityKind.COMPLETED if closing else LeadActivityKind.STAGE,
            author_id=employee_id,
            # The two stage names, so the feed can read "Yangi → Taklif
            # yuborildi" without having to guess what it moved from.
            text=f"{current.get('stage') or LeadStage.NEW}>{stage}",
        )
        if note and note.strip():
            add_lead_activity(
                lead_id, kind=LeadActivityKind.COMMENT, author_id=employee_id,
                text=note.strip(),
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
                (lead_id, name, unit, amount, position, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            [
                lead_id, name, (item.get("unit") or "").strip(),
                item.get("amount") or 0, position, timezone.now(),
            ],
        )
    recalc_lead_amount(lead_id)


def add_lead_item(
    lead_id: int, *, name: str, unit: str = "", amount=0
) -> dict[str, Any] | None:
    row = fetch_one(
        f"SELECT COALESCE(MAX(position), -1) + 1 AS next FROM "
        f"{B2B_WORKSPACE_LEAD_ITEM_TABLE} WHERE lead_id = %s",
        [lead_id],
    )
    item = fetch_one(
        f"""
        INSERT INTO {B2B_WORKSPACE_LEAD_ITEM_TABLE}
            (lead_id, name, unit, amount, position, created_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        [lead_id, name, unit, amount, int((row or {}).get("next") or 0), timezone.now()],
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
    the roster to render."""
    return fetch_all(
        f"""
        SELECT a.*,
               e.full_name AS author_name,
               e.photo AS author_photo
        FROM {B2B_WORKSPACE_LEAD_ACTIVITY_TABLE} a
        LEFT JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = a.author_id
        WHERE a.lead_id = %s
        ORDER BY a.created_at DESC, a.id DESC
        LIMIT %s
        """,
        [lead_id, limit],
    )


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
        "ORDER BY created_at DESC, id DESC",
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
        "WHERE company_id = %s AND lead_id = __ANY_MARKER__(%s) GROUP BY lead_id",
        [company_id, list(lead_ids)],
    )
    return {int(row["lead_id"]): int(row["total"]) for row in rows}


# ─── Files ────────────────────────────────────────────────────────────────────

def list_files(company_id: int, kind: str | None = "file") -> list[dict[str, Any]]:
    """The shared drive.

    Defaults to ``kind='file'``: chat attachments and vouchers live in the same
    table because that is what makes the quota one SUM, but they are not drive
    documents and listing them here would fill the Fayllar tab with every photo
    anyone ever sent. Pass ``kind=None`` to get everything.
    """
    if kind is None:
        return fetch_all(
            f"SELECT * FROM {B2B_WORKSPACE_FILE_TABLE} WHERE company_id = %s "
            "ORDER BY created_at DESC, id DESC",
            [company_id],
        )
    return fetch_all(
        f"SELECT * FROM {B2B_WORKSPACE_FILE_TABLE} WHERE company_id = %s AND kind = %s "
        "ORDER BY created_at DESC, id DESC",
        [company_id, kind],
    )


def get_file(file_id: int, company_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT * FROM {B2B_WORKSPACE_FILE_TABLE} WHERE id = %s AND company_id = %s",
        [file_id, company_id],
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
             message_id, trip_id, duration_ms, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        [
            company_id, author_id, name, path, size, kind, content_type,
            message_id, trip_id, duration_ms, timezone.now(),
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
            COALESCE(deals.deals_count, 0)                        AS deals_count
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
              AND claimed_by_id IS NOT NULL
              AND completed_at >= %s AND completed_at < %s
            GROUP BY claimed_by_id
        ) deals ON deals.employee_id = e.id
        WHERE e.company_id = %s AND e.is_active = TRUE
        GROUP BY e.id, e.full_name, e.photo, e.position, d.name, deals.deals_count
        ORDER BY completed_count DESC, deals_count DESC, e.full_name ASC
        """,
        [start, end, company_id, start, end, company_id],
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


def get_employee_of_month(company_id: int, year: int, month: int) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        SELECT eom.year, eom.month, eom.selected_at, e.id AS employee_id, e.full_name, e.photo
        FROM {B2B_EMPLOYEE_OF_MONTH_TABLE} eom
        JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = eom.employee_id
        WHERE eom.company_id = %s AND eom.year = %s AND eom.month = %s
        """,
        [company_id, year, month],
    )


def set_employee_of_month(
    *, company_id: int, year: int, month: int, employee_id: int, selected_by_id: int
) -> dict[str, Any] | None:
    fetch_one(
        f"""
        INSERT INTO {B2B_EMPLOYEE_OF_MONTH_TABLE}
            (company_id, year, month, employee_id, selected_by_id, selected_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (company_id, year, month) DO UPDATE
            SET employee_id = EXCLUDED.employee_id,
                selected_by_id = EXCLUDED.selected_by_id,
                selected_at = EXCLUDED.selected_at
        RETURNING *
        """,
        [company_id, year, month, employee_id, selected_by_id, timezone.now()],
    )
    return get_employee_of_month(company_id, year, month)


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
            d.name          AS department_name,
            a.status,
            a.checked_in_at,
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
    reason: str | None = None,
    marked_by_id: int | None = None,
    check_in_latitude: float | None = None,
    check_in_longitude: float | None = None,
) -> dict[str, Any] | None:
    """Records one employee's day.

    ON CONFLICT rather than a read-then-write: two managers marking the same
    person at once, or an employee double-tapping check-in, would otherwise
    race into two rows the UNIQUE key then rejects outright.

    `checked_in_at` is only overwritten when a new one is given — correcting a
    status from a manager's screen must not erase the time the employee
    actually arrived. The check-in coordinates follow the same rule: a
    manager's own mark carries none, and must not blank out where the
    employee's own check-in happened.
    """
    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO {B2B_ATTENDANCE_TABLE}
            (company_id, employee_id, work_date, status, checked_in_at,
             reason, marked_by_id, check_in_latitude, check_in_longitude,
             created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (employee_id, work_date) DO UPDATE SET
            status             = EXCLUDED.status,
            checked_in_at      = COALESCE(EXCLUDED.checked_in_at, {B2B_ATTENDANCE_TABLE}.checked_in_at),
            reason             = EXCLUDED.reason,
            marked_by_id       = EXCLUDED.marked_by_id,
            check_in_latitude  = COALESCE(EXCLUDED.check_in_latitude, {B2B_ATTENDANCE_TABLE}.check_in_latitude),
            check_in_longitude = COALESCE(EXCLUDED.check_in_longitude, {B2B_ATTENDANCE_TABLE}.check_in_longitude),
            updated_at         = EXCLUDED.updated_at
        RETURNING *
        """,
        [
            company_id, employee_id, work_date, status, checked_in_at,
            reason, marked_by_id, check_in_latitude, check_in_longitude, now, now,
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
