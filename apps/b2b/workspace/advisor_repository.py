"""Raw-SQL reads for Weel AI as the owner's business advisor.

What `advisor.py` hands the model when it asks: the funnel as it stands,
who is in today, which tasks are late, what sold — and the advisor's own
notes, the one table here it writes. Everything else is a read over
tables other modules own, scoped by `company_id` like all of
`repository.py`.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from django.utils import timezone

from apps.b2b.models import LeadStage
from apps.b2b.raw.tables import (
    B2B_AI_ADVISOR_NOTE_TABLE,
    B2B_ATTENDANCE_TABLE,
    B2B_DEPARTMENT_TABLE,
    B2B_EMPLOYEE_TABLE,
    B2B_TASK_ASSIGNEE_TABLE,
    B2B_TASK_TABLE,
    B2B_WORKSPACE_LEAD_TABLE,
)
from apps.shared.raw.db import execute, fetch_all, fetch_one

B2B_PRODUCT_TABLE = "b2b_product"
B2B_STOCK_MOVEMENT_TABLE = "b2b_stock_movement"

OPEN_STAGES = [s for s in LeadStage.ORDER if s not in LeadStage.CLOSED]


def _num(value: Any) -> float | int:
    if isinstance(value, Decimal):
        return float(value)
    return value or 0


# ─── The advisor's notes ─────────────────────────────────────────────────────

def list_notes(company_id: int, *, limit: int = 40) -> list[dict[str, Any]]:
    """Newest last, so the model reads them in the order they were made."""
    rows = fetch_all(
        f"""
        SELECT n.id, n.text, n.created_at, e.full_name AS author
        FROM {B2B_AI_ADVISOR_NOTE_TABLE} n
        LEFT JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = n.employee_id
        WHERE n.company_id = %s
        ORDER BY n.created_at DESC, n.id DESC
        LIMIT %s
        """,
        [company_id, limit],
    )
    return list(reversed(rows))


def add_note(company_id: int, employee_id: int | None, text: str) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        INSERT INTO {B2B_AI_ADVISOR_NOTE_TABLE} (company_id, employee_id, text)
        VALUES (%s, %s, %s)
        RETURNING id, text, created_at
        """,
        [company_id, employee_id, text],
    )


def delete_note(note_id: int, company_id: int) -> bool:
    return bool(execute(
        f"DELETE FROM {B2B_AI_ADVISOR_NOTE_TABLE} WHERE id = %s AND company_id = %s",
        [note_id, company_id],
    ))


# ─── Today ───────────────────────────────────────────────────────────────────

def attendance_on(company_id: int, day: date) -> list[dict[str, Any]]:
    """Every active employee with what was marked for them on `day` —
    ``status`` is null for those nobody marked."""
    return fetch_all(
        f"""
        SELECT e.id AS employee_id, e.full_name, e.position, d.name AS department,
               a.status, a.checked_in_at, a.reason
        FROM {B2B_EMPLOYEE_TABLE} e
        LEFT JOIN {B2B_DEPARTMENT_TABLE} d ON d.id = e.department_id
        LEFT JOIN {B2B_ATTENDANCE_TABLE} a
               ON a.employee_id = e.id AND a.work_date = %s
        WHERE e.company_id = %s AND e.is_active = TRUE
          AND COALESCE(e.is_hidden, FALSE) = FALSE
        ORDER BY d.name NULLS LAST, e.full_name
        """,
        [day, company_id],
    )


# ─── Tasks ───────────────────────────────────────────────────────────────────

def overdue_tasks(company_id: int, *, limit: int = 30) -> list[dict[str, Any]]:
    """Open tasks past their deadline, latest-overdue first, with the
    names of whoever they sit on."""
    return fetch_all(
        f"""
        SELECT t.id, t.title, t.status, t.priority, t.project, t.due_date,
               COALESCE(STRING_AGG(e.full_name, ', ' ORDER BY e.full_name), '') AS assignees
        FROM {B2B_TASK_TABLE} t
        LEFT JOIN {B2B_TASK_ASSIGNEE_TABLE} ta ON ta.task_id = t.id
        LEFT JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = ta.employee_id
        WHERE t.company_id = %s AND t.deleted_at IS NULL AND t.status <> 'done'
          AND t.due_date IS NOT NULL AND t.due_date < NOW()
        GROUP BY t.id
        ORDER BY t.due_date ASC
        LIMIT %s
        """,
        [company_id, limit],
    )


def task_counts(company_id: int) -> dict[str, int]:
    row = fetch_one(
        f"""
        SELECT COUNT(*) FILTER (WHERE status <> 'done')                          AS open,
               COUNT(*) FILTER (WHERE status <> 'done' AND due_date < NOW())      AS overdue,
               COUNT(*) FILTER (WHERE status <> 'done'
                                 AND due_date::date = CURRENT_DATE)               AS due_today,
               COUNT(*) FILTER (WHERE status = 'done'
                                 AND completed_at >= NOW() - INTERVAL '7 days')   AS done_last_7_days
        FROM {B2B_TASK_TABLE}
        WHERE company_id = %s AND deleted_at IS NULL
        """,
        [company_id],
    ) or {}
    return {key: int(value or 0) for key, value in row.items()}


# ─── The funnel ──────────────────────────────────────────────────────────────

def funnel(company_id: int, *, days: int = 30, top: int = 15) -> dict[str, Any]:
    """Open deals by stage, the biggest of them, and what closed lately."""
    since = timezone.now() - timedelta(days=days)
    stages = fetch_all(
        f"""
        SELECT stage, COUNT(*) AS count, COALESCE(SUM(amount), 0) AS amount,
               COUNT(*) FILTER (WHERE claimed_by_id IS NULL) AS unclaimed,
               COUNT(*) FILTER (WHERE due_date IS NOT NULL AND due_date < NOW()) AS overdue
        FROM {B2B_WORKSPACE_LEAD_TABLE}
        WHERE company_id = %s AND deleted_at IS NULL AND kind = 'lead'
          AND stage = __ANY_MARKER__(%s)
        GROUP BY stage
        """,
        [company_id, OPEN_STAGES],
    )
    by_stage = {row["stage"]: row for row in stages}
    closed = fetch_one(
        f"""
        SELECT COUNT(*) FILTER (WHERE stage = %s)                       AS won,
               COALESCE(SUM(amount) FILTER (WHERE stage = %s), 0)       AS won_amount,
               COUNT(*) FILTER (WHERE stage = %s)                       AS lost,
               COALESCE(SUM(amount) FILTER (WHERE stage = %s), 0)       AS lost_amount,
               COUNT(*) FILTER (WHERE created_at >= %s)                 AS created
        FROM {B2B_WORKSPACE_LEAD_TABLE}
        WHERE company_id = %s AND deleted_at IS NULL AND kind = 'lead'
          AND (completed_at >= %s OR created_at >= %s)
        """,
        [LeadStage.WON, LeadStage.WON, LeadStage.LOST, LeadStage.LOST, since,
         company_id, since, since],
    ) or {}
    lost_reasons = fetch_all(
        f"""
        SELECT COALESCE(lost_reason, 'unknown') AS reason, COUNT(*) AS count
        FROM {B2B_WORKSPACE_LEAD_TABLE}
        WHERE company_id = %s AND deleted_at IS NULL AND kind = 'lead'
          AND stage = %s AND completed_at >= %s
        GROUP BY 1 ORDER BY 2 DESC
        """,
        [company_id, LeadStage.LOST, since],
    )
    biggest = fetch_all(
        f"""
        SELECT l.id, l.company_name, l.contact_full_name, l.product_name, l.stage,
               l.amount, l.source, l.due_date, l.created_at, l.updated_at,
               e.full_name AS owner
        FROM {B2B_WORKSPACE_LEAD_TABLE} l
        LEFT JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = l.claimed_by_id
        WHERE l.company_id = %s AND l.deleted_at IS NULL AND l.kind = 'lead'
          AND l.stage = __ANY_MARKER__(%s)
        ORDER BY l.amount DESC, l.updated_at ASC
        LIMIT %s
        """,
        [company_id, OPEN_STAGES, top],
    )
    return {
        "days": days,
        "open_by_stage": [
            {
                "stage": stage,
                "count": int((by_stage.get(stage) or {}).get("count") or 0),
                "amount": _num((by_stage.get(stage) or {}).get("amount")),
                "unclaimed": int((by_stage.get(stage) or {}).get("unclaimed") or 0),
                "overdue": int((by_stage.get(stage) or {}).get("overdue") or 0),
            }
            for stage in OPEN_STAGES
        ],
        "closed_recently": {
            "won": int(closed.get("won") or 0),
            "won_amount": _num(closed.get("won_amount")),
            "lost": int(closed.get("lost") or 0),
            "lost_amount": _num(closed.get("lost_amount")),
            "created": int(closed.get("created") or 0),
            "lost_reasons": [{"reason": r["reason"], "count": int(r["count"])} for r in lost_reasons],
        },
        "biggest_open": [
            {
                "id": row["id"],
                "customer": row.get("company_name") or row.get("contact_full_name"),
                "product": row.get("product_name"),
                "stage": row.get("stage"),
                "amount": _num(row.get("amount")),
                "source": row.get("source"),
                "owner": row.get("owner"),
                "due_date": row.get("due_date"),
                "days_open": (timezone.now() - row["created_at"]).days if row.get("created_at") else None,
                "days_since_touch": (timezone.now() - row["updated_at"]).days if row.get("updated_at") else None,
            }
            for row in biggest
        ],
    }


# ─── What sold ───────────────────────────────────────────────────────────────

def top_products(company_id: int, *, days: int = 30, limit: int = 15) -> list[dict[str, Any]]:
    """Best sellers over the window by revenue, with the margin each made."""
    since = timezone.now() - timedelta(days=days)
    rows = fetch_all(
        f"""
        SELECT p.id, p.name, p.sku,
               SUM(m.quantity)                              AS sold_qty,
               SUM(m.quantity * m.unit_cost)                AS revenue,
               SUM(m.quantity * (m.unit_cost - m.cost_price)) AS profit
        FROM {B2B_STOCK_MOVEMENT_TABLE} m
        JOIN {B2B_PRODUCT_TABLE} p ON p.id = m.product_id
        WHERE m.company_id = %s AND m.kind = 'sale' AND m.created_at >= %s
        GROUP BY p.id
        ORDER BY revenue DESC
        LIMIT %s
        """,
        [company_id, since, limit],
    )
    return [
        {
            "name": row["name"], "sku": row.get("sku"),
            "sold_qty": _num(row.get("sold_qty")),
            "revenue": _num(row.get("revenue")),
            "profit": _num(row.get("profit")),
        }
        for row in rows
    ]


def low_stock(company_id: int, *, limit: int = 20) -> list[dict[str, Any]]:
    """Products at or under their minimum, emptiest first."""
    rows = fetch_all(
        f"""
        SELECT p.name, p.sku, p.min_stock, p.sale_price,
               COALESCE(SUM(s.quantity), 0) AS quantity
        FROM {B2B_PRODUCT_TABLE} p
        LEFT JOIN b2b_stock s ON s.product_id = p.id
        WHERE p.company_id = %s AND p.is_active = TRUE AND p.kind = 'product'
        GROUP BY p.id
        HAVING p.min_stock > 0 AND COALESCE(SUM(s.quantity), 0) <= p.min_stock
        ORDER BY (COALESCE(SUM(s.quantity), 0) / NULLIF(p.min_stock, 0)) ASC, p.name
        LIMIT %s
        """,
        [company_id, limit],
    )
    return [
        {
            "name": row["name"], "sku": row.get("sku"),
            "quantity": _num(row.get("quantity")), "min_stock": _num(row.get("min_stock")),
        }
        for row in rows
    ]
