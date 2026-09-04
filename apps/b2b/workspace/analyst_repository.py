"""Raw-SQL data access for Weel AI, the built-in analyst.

Two halves. The first reads the company for `analyst.gather` — every active
employee's window in one query, the way `monthly_employee_stats` does, plus
the roster's departments. The second owns the report table and the "seen"
stamp behind the dot on the Weel AI button.

Same conventions as `repository.py`: dicts in and out, every query scoped
by `company_id`.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from django.utils import timezone

from apps.b2b.models import LeadStage
from apps.b2b.raw.tables import (
    B2B_AI_REPORT_SEEN_TABLE,
    B2B_AI_REPORT_TABLE,
    B2B_ATTENDANCE_TABLE,
    B2B_COMPANY_TABLE,
    B2B_DEPARTMENT_TABLE,
    B2B_EMPLOYEE_TABLE,
    B2B_TASK_ASSIGNEE_TABLE,
    B2B_TASK_TABLE,
    B2B_WORKSPACE_LEAD_TABLE,
)
from apps.shared.raw.db import execute, fetch_all, fetch_one

PERIODS = ("day", "week", "month", "year")

STATUS_READY = "ready"
STATUS_FAILED = "failed"


# ─── Reading the company ─────────────────────────────────────────────────────

def employee_window_stats(
    company_id: int, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """Every active employee over one window: tasks, deals, attendance.

    The same shape as `monthly_employee_stats`, over any window rather than
    a calendar month, and with the two as-of-now counts a report about
    "who is falling behind" cannot do without — what is still open on each
    person, and how much of it is late.
    """
    return fetch_all(
        f"""
        SELECT
            e.id                                                  AS employee_id,
            e.full_name,
            e.position,
            e.role,
            d.id                                                  AS department_id,
            d.name                                                AS department_name,
            COALESCE(done.completed_count, 0)                     AS completed_count,
            COALESCE(done.due_count, 0)                           AS due_count,
            COALESCE(done.on_time_count, 0)                       AS on_time_count,
            COALESCE(open_t.open_count, 0)                        AS open_count,
            COALESCE(open_t.overdue_count, 0)                     AS overdue_count,
            COALESCE(deals.won_count, 0)                          AS won_count,
            COALESCE(deals.won_amount, 0)                         AS won_amount,
            COALESCE(deals.lost_count, 0)                         AS lost_count,
            COALESCE(att.present_days, 0)                         AS present_days,
            COALESCE(att.late_days, 0)                            AS late_days,
            COALESCE(att.absent_days, 0)                          AS absent_days,
            COALESCE(att.unexcused_days, 0)                       AS unexcused_days
        FROM {B2B_EMPLOYEE_TABLE} e
        LEFT JOIN {B2B_DEPARTMENT_TABLE} d ON d.id = e.department_id
        LEFT JOIN (
            SELECT ta.employee_id,
                   COUNT(t.id)                                        AS completed_count,
                   COUNT(t.id) FILTER (WHERE t.due_date IS NOT NULL)  AS due_count,
                   COUNT(t.id) FILTER (WHERE t.due_date IS NOT NULL
                                        AND t.completed_at <= t.due_date) AS on_time_count
            FROM {B2B_TASK_ASSIGNEE_TABLE} ta
            JOIN {B2B_TASK_TABLE} t ON t.id = ta.task_id
            WHERE t.company_id = %s AND t.status = 'done'
              AND t.completed_at >= %s AND t.completed_at < %s
            GROUP BY ta.employee_id
        ) done ON done.employee_id = e.id
        LEFT JOIN (
            SELECT ta.employee_id,
                   COUNT(t.id)                                        AS open_count,
                   COUNT(t.id) FILTER (WHERE t.due_date IS NOT NULL
                                        AND t.due_date::date < CURRENT_DATE) AS overdue_count
            FROM {B2B_TASK_ASSIGNEE_TABLE} ta
            JOIN {B2B_TASK_TABLE} t ON t.id = ta.task_id
            WHERE t.company_id = %s AND t.status <> 'done'
            GROUP BY ta.employee_id
        ) open_t ON open_t.employee_id = e.id
        LEFT JOIN (
            SELECT claimed_by_id AS employee_id,
                   COUNT(*) FILTER (WHERE stage = %s)                 AS won_count,
                   COALESCE(SUM(amount) FILTER (WHERE stage = %s), 0) AS won_amount,
                   COUNT(*) FILTER (WHERE stage = %s)                 AS lost_count
            FROM {B2B_WORKSPACE_LEAD_TABLE}
            WHERE company_id = %s AND deleted_at IS NULL AND claimed_by_id IS NOT NULL
              AND completed_at >= %s AND completed_at < %s
            GROUP BY claimed_by_id
        ) deals ON deals.employee_id = e.id
        LEFT JOIN (
            SELECT employee_id,
                   COUNT(*) FILTER (WHERE status IN ('present', 'late', 'remote')) AS present_days,
                   COUNT(*) FILTER (WHERE status = 'late')                        AS late_days,
                   COUNT(*) FILTER (WHERE status = 'absent')                      AS absent_days,
                   COUNT(*) FILTER (WHERE status = 'absent'
                                     AND COALESCE(BTRIM(reason), '') = '')       AS unexcused_days
            FROM {B2B_ATTENDANCE_TABLE}
            WHERE company_id = %s AND work_date >= %s AND work_date < %s
            GROUP BY employee_id
        ) att ON att.employee_id = e.id
        WHERE e.company_id = %s AND e.is_active = TRUE
          AND COALESCE(e.is_hidden, FALSE) = FALSE
        ORDER BY d.name NULLS LAST, e.full_name
        """,
        [
            company_id, start, end,
            company_id,
            LeadStage.WON, LeadStage.WON, LeadStage.LOST, company_id, start, end,
            company_id, start.date(), end.date(),
            company_id,
        ],
    )


def company_totals(company_id: int, start: datetime, end: datetime) -> dict[str, Any]:
    """The company-wide numbers that do not belong to anybody: leads that
    came in, leads nobody has picked up, tasks created."""
    row = fetch_one(
        f"""
        SELECT
            (SELECT COUNT(*) FROM {B2B_WORKSPACE_LEAD_TABLE} l
              WHERE l.company_id = %s AND l.deleted_at IS NULL
                AND l.created_at >= %s AND l.created_at < %s)      AS leads_created,
            (SELECT COUNT(*) FROM {B2B_WORKSPACE_LEAD_TABLE} l
              WHERE l.company_id = %s AND l.deleted_at IS NULL
                AND l.claimed_by_id IS NULL
                AND l.stage NOT IN ({", ".join(["%s"] * len(LeadStage.CLOSED))})) AS leads_unclaimed,
            (SELECT COUNT(*) FROM {B2B_TASK_TABLE} t
              WHERE t.company_id = %s
                AND t.created_at >= %s AND t.created_at < %s)      AS tasks_created,
            (SELECT COUNT(*) FROM {B2B_TASK_TABLE} t
              WHERE t.company_id = %s AND t.status <> 'done'
                AND NOT EXISTS (SELECT 1 FROM {B2B_TASK_ASSIGNEE_TABLE} a
                                 WHERE a.task_id = t.id))          AS tasks_unassigned
        """,
        [
            company_id, start, end,
            company_id, *LeadStage.CLOSED,
            company_id, start, end,
            company_id,
        ],
    ) or {}
    return {key: int(value or 0) for key, value in row.items()}


def company_row(company_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT id, name, industry, city FROM {B2B_COMPANY_TABLE} WHERE id = %s",
        [company_id],
    )


def companies_with_staff() -> list[int]:
    """Every live workspace with at least one active employee — the ones the
    nightly pass writes a report for. A workspace nobody is in has nothing
    to analyse, and a report about it would only ever say so."""
    return [
        row["id"]
        for row in fetch_all(
            f"""
            SELECT c.id FROM {B2B_COMPANY_TABLE} c
            WHERE c.is_active = TRUE
              AND EXISTS (SELECT 1 FROM {B2B_EMPLOYEE_TABLE} e
                           WHERE e.company_id = c.id AND e.is_active = TRUE)
            ORDER BY c.id
            """
        )
    ]


def manager_recipients(company_id: int) -> list[dict[str, Any]]:
    """Who a report is announced to: the people who run the company. An
    employee is not sent the company's verdict on their colleagues."""
    return fetch_all(
        f"""
        SELECT id AS employee_id, company_id, fcm_token
        FROM {B2B_EMPLOYEE_TABLE}
        WHERE company_id = %s AND is_active = TRUE
          AND role IN ('owner', 'admin', 'manager')
        """,
        [company_id],
    )


# ─── Reports ─────────────────────────────────────────────────────────────────

def upsert_report(
    *,
    company_id: int,
    period: str,
    period_start: date,
    period_end: date,
    status: str,
    provider: str | None,
    model: str | None,
    score: int | None,
    headline_uz: str,
    headline_ru: str,
    text_uz: str,
    text_ru: str,
    data: dict[str, Any] | None,
    error: str | None,
    requested_by_id: int | None,
) -> dict[str, Any] | None:
    """One report per (company, period, window). Rerunning the same window —
    an owner pressing "Hozir tahlil qil" after the nightly pass — rewrites
    the text in place rather than filing a second copy."""
    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO {B2B_AI_REPORT_TABLE}
            (company_id, period, period_start, period_end, status, provider, model,
             score, headline_uz, headline_ru, text_uz, text_ru, data, error,
             requested_by_id, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
        ON CONFLICT (company_id, period, period_start) DO UPDATE SET
            period_end = EXCLUDED.period_end,
            status = EXCLUDED.status,
            provider = EXCLUDED.provider,
            model = EXCLUDED.model,
            score = EXCLUDED.score,
            headline_uz = EXCLUDED.headline_uz,
            headline_ru = EXCLUDED.headline_ru,
            text_uz = EXCLUDED.text_uz,
            text_ru = EXCLUDED.text_ru,
            data = EXCLUDED.data,
            error = EXCLUDED.error,
            requested_by_id = EXCLUDED.requested_by_id,
            -- A rerun is a new report to the reader: it moves to the top and
            -- puts the dot back on the button.
            created_at = EXCLUDED.created_at,
            updated_at = EXCLUDED.updated_at
        RETURNING *
        """,
        [
            company_id, period, period_start, period_end, status, provider, model,
            score, headline_uz[:300], headline_ru[:300], text_uz, text_ru,
            json.dumps(data) if data is not None else None, error,
            requested_by_id, now, now,
        ],
    )


def list_reports(
    company_id: int, *, period: str | None = None, limit: int = 30
) -> list[dict[str, Any]]:
    """Newest first, without the bodies — the list screen shows a headline
    and a score per row, and the text is fetched when a row is opened."""
    sql = f"""
        SELECT id, company_id, period, period_start, period_end, status, provider,
               model, score, headline_uz, headline_ru, error, requested_by_id,
               created_at
        FROM {B2B_AI_REPORT_TABLE}
        WHERE company_id = %s
    """
    params: list[Any] = [company_id]
    if period in PERIODS:
        sql += " AND period = %s"
        params.append(period)
    sql += " ORDER BY created_at DESC, id DESC LIMIT %s"
    params.append(max(1, min(int(limit), 200)))
    return fetch_all(sql, params)


def get_report(report_id: int, company_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"SELECT * FROM {B2B_AI_REPORT_TABLE} WHERE id = %s AND company_id = %s",
        [report_id, company_id],
    )


def latest_report(company_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        SELECT id, period, period_start, period_end, status, score,
               headline_uz, headline_ru, created_at
        FROM {B2B_AI_REPORT_TABLE}
        WHERE company_id = %s AND status = %s
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        [company_id, STATUS_READY],
    )


def report_made_recently(company_id: int, period: str, *, within_minutes: int) -> bool:
    """Whether a report for this period was written in the last little
    while — the throttle on "Hozir tahlil qil", which spends the key's money
    every time it is pressed."""
    row = fetch_one(
        f"""
        SELECT 1 FROM {B2B_AI_REPORT_TABLE}
        WHERE company_id = %s AND period = %s AND status = %s
          AND created_at > NOW() - (%s * INTERVAL '1 minute')
        LIMIT 1
        """,
        [company_id, period, STATUS_READY, within_minutes],
    )
    return row is not None


# ─── The dot ─────────────────────────────────────────────────────────────────

def unseen_count(company_id: int, employee_id: int) -> int:
    row = fetch_one(
        f"""
        SELECT COUNT(*) AS n
        FROM {B2B_AI_REPORT_TABLE} r
        LEFT JOIN {B2B_AI_REPORT_SEEN_TABLE} s ON s.employee_id = %s
        WHERE r.company_id = %s AND r.status = %s
          AND (s.seen_at IS NULL OR r.created_at > s.seen_at)
        """,
        [employee_id, company_id, STATUS_READY],
    )
    return int((row or {}).get("n") or 0)


def mark_seen(employee_id: int) -> None:
    now = timezone.now()
    execute(
        f"""
        INSERT INTO {B2B_AI_REPORT_SEEN_TABLE} (employee_id, seen_at)
        VALUES (%s, %s)
        ON CONFLICT (employee_id) DO UPDATE SET seen_at = EXCLUDED.seen_at
        """,
        [employee_id, now],
    )
