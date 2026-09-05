"""«Hisobotlar» — the KPI screen, one section at a time.

The old ``/reports/`` answered three fixed sections over a rolling window in
one response and still does, for the web dashboard. This module is the
redesigned phone screen: five tabs (sales, tasks, stock, trips, attendance),
each a grid of eight figures compared with the *previous calendar period*,
a sparkline per figure, a per-employee table, and a drill-down list behind
every figure.

Everything is counted in the database and shaped here. Two rules run through
all of it:

* **Calendar windows, not rolling ones.** "Oy" is the month the anchor date
  falls in, cut off at *now* when that month is still running; the comparison
  is the same stretch of the previous period, so the 5th of September is read
  against the 1st–5th of August and not against the whole of August.
* **One clock.** The window is resolved once per request and handed down, so
  the eight figures, the table and the drill-down cannot disagree about where
  the period ends.

Money leaves as strings for the same reason it does in ``repository`` — an
eleven-figure so'm total does not survive a JSON float.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Callable

from django.utils import timezone

from apps.b2b.models import LeadKind, LeadStage
from apps.b2b.raw.tables import (
    B2B_ATTENDANCE_TABLE,
    B2B_BUSINESS_TRIP_TABLE,
    B2B_EMPLOYEE_TABLE,
    B2B_PRODUCT_TABLE,
    B2B_STOCK_MOVEMENT_TABLE,
    B2B_STOCK_TABLE,
    B2B_TASK_ASSIGNEE_TABLE,
    B2B_TASK_TABLE,
    B2B_TRIP_EMPLOYEE_TABLE,
    B2B_WAREHOUSE_TABLE,
    B2B_WORKSPACE_LEAD_ACTIVITY_TABLE,
    B2B_WORKSPACE_LEAD_ITEM_TABLE,
    B2B_WORKSPACE_LEAD_TABLE,
)
from apps.b2b.workspace.storage import photo_url
from apps.shared.raw.db import fetch_all, fetch_one

# ─── Vocabulary ──────────────────────────────────────────────────────────────

SECTION_SALES = "sales"
SECTION_TASKS = "tasks"
SECTION_STOCK = "stock"
SECTION_TRIPS = "trips"
SECTION_ATTENDANCE = "attendance"
SECTIONS = (SECTION_SALES, SECTION_TASKS, SECTION_STOCK, SECTION_TRIPS, SECTION_ATTENDANCE)

PERIODS = ("day", "week", "month", "year")
DEFAULT_PERIOD = "month"

#: How a trend line is bucketed for each period. A year in days would be 365
#: points on a card two fingers wide; a day in months would be one.
_BUCKETS = {"day": "hour", "week": "day", "month": "day", "year": "month"}

PRESENT_STATUSES = ("present", "late", "remote")


class AnalyticsError(ValueError):
    """A parameter the screen sent that names nothing — an unknown section or
    metric. A 400 rather than a fallback: unlike the period, which is a
    preference, the section is the question itself."""


# ─── Windows ─────────────────────────────────────────────────────────────────

def _add_months(day: date, months: int) -> date:
    month0 = day.month - 1 + months
    year = day.year + month0 // 12
    month = month0 % 12 + 1
    return date(year, month, 1)


def _bounds(period: str, anchor: date) -> tuple[date, date]:
    """The calendar period [start, end) the anchor date falls in."""
    if period == "day":
        return anchor, anchor + timedelta(days=1)
    if period == "week":
        start = anchor - timedelta(days=anchor.weekday())
        return start, start + timedelta(days=7)
    if period == "month":
        start = anchor.replace(day=1)
        return start, _add_months(start, 1)
    start = date(anchor.year, 1, 1)
    return start, date(anchor.year + 1, 1, 1)


def _previous_anchor(period: str, start: date) -> date:
    if period == "day":
        return start - timedelta(days=1)
    if period == "week":
        return start - timedelta(days=7)
    if period == "month":
        return _add_months(start, -1)
    return date(start.year - 1, 1, 1)


def _aware(day: date, tz) -> datetime:
    return timezone.make_aware(datetime.combine(day, time.min), tz)


@dataclass(frozen=True)
class Window:
    period: str
    #: Where the period starts, and where it is cut off — *now* when the
    #: period is still running, its own end otherwise.
    start: datetime
    end: datetime
    full_end: datetime
    #: The same stretch of the previous period — see the module note.
    compare_start: datetime
    compare_end: datetime
    #: DATE columns (a trip's start, an attendance day) are compared against
    #: local dates, exclusive on the right.
    start_date: date
    end_date: date
    compare_start_date: date
    compare_end_date: date
    bucket: str
    #: Local, naive bucket starts covering [start, end). What a sparkline is
    #: indexed by.
    buckets: tuple[datetime, ...]
    tz_name: str

    @property
    def complete(self) -> bool:
        return self.end >= self.full_end

    def as_json(self) -> dict[str, Any]:
        # Every stamp in the reader's own zone: the screen prints "1–5 sentyabr"
        # straight off these and a UTC midnight would read as the day before.
        local = timezone.localtime
        return {
            "period": self.period,
            "start": local(self.start).isoformat(),
            "end": local(self.end).isoformat(),
            "full_end": local(self.full_end).isoformat(),
            "compare_start": local(self.compare_start).isoformat(),
            "compare_end": local(self.compare_end).isoformat(),
            "bucket": self.bucket,
            "complete": self.complete,
        }


def _bucket_starts(start: datetime, end: datetime, bucket: str, tz) -> tuple[datetime, ...]:
    """Naive local bucket starts from [start] up to (not including) [end]."""
    local = timezone.localtime(start, tz).replace(tzinfo=None)
    stop = timezone.localtime(end, tz).replace(tzinfo=None)
    out: list[datetime] = []
    cursor = local
    guard = 0
    while cursor < stop and guard < 800:
        out.append(cursor)
        if bucket == "hour":
            cursor = cursor + timedelta(hours=1)
        elif bucket == "day":
            cursor = cursor + timedelta(days=1)
        else:
            cursor = datetime.combine(_add_months(cursor.date(), 1), time.min)
        guard += 1
    return tuple(out) if out else (local,)


def resolve_window(period: str, anchor: date | None = None, *, now: datetime | None = None) -> Window:
    """The window a period name and an anchor date stand for, on one clock.

    An unknown period is the default rather than an error, as on the old
    endpoint: it comes off a query string and a report is a page to read. An
    anchor in the future is today — there is nothing there to count.
    """
    period = period if period in PERIODS else DEFAULT_PERIOD
    now = now or timezone.now()
    tz = timezone.get_current_timezone()
    today = timezone.localtime(now, tz).date()
    anchor = min(anchor or today, today)

    start_d, full_end_d = _bounds(period, anchor)
    start = _aware(start_d, tz)
    full_end = _aware(full_end_d, tz)
    end = max(start, min(full_end, now))

    prev_start_d, prev_full_end_d = _bounds(period, _previous_anchor(period, start_d))
    compare_start = _aware(prev_start_d, tz)
    prev_full_end = _aware(prev_full_end_d, tz)
    if end >= full_end:
        compare_end = prev_full_end
    else:
        compare_end = min(compare_start + (end - start), prev_full_end)

    clipped = end < full_end
    end_date = timezone.localtime(end, tz).date() + timedelta(days=1) if clipped else full_end_d
    compare_clipped = compare_end < prev_full_end
    compare_end_date = (
        timezone.localtime(compare_end, tz).date() + timedelta(days=1)
        if compare_clipped
        else prev_full_end_d
    )

    bucket = _BUCKETS[period]
    return Window(
        period=period,
        start=start,
        end=end,
        full_end=full_end,
        compare_start=compare_start,
        compare_end=compare_end,
        start_date=start_d,
        end_date=end_date,
        compare_start_date=prev_start_d,
        compare_end_date=compare_end_date,
        bucket=bucket,
        buckets=_bucket_starts(start, end, bucket, tz),
        tz_name=timezone.get_current_timezone_name(),
    )


# ─── Shaping ─────────────────────────────────────────────────────────────────

def _money(value: Any) -> str:
    return str(Decimal(str(value or 0)).quantize(Decimal("0.01")))


def _num(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def _int(value: Any) -> int:
    return int(value or 0)


def _ratio(numerator: Any, denominator: Any) -> float | None:
    d = _num(denominator)
    if d == 0:
        return None
    return _num(numerator) / d


def _change(current: float | None, previous: float | None) -> float | None:
    """Relative movement, or None when there is nothing to move from."""
    if current is None or previous is None or previous == 0:
        return None
    return round((current - previous) / abs(previous), 4)


def _sum_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total: dict[str, Any] = {}
    for row in rows:
        for key, value in row.items():
            if key == "bucket":
                continue
            if isinstance(value, (int, float, Decimal)):
                total[key] = (total.get(key) or 0) + value
    return total


def _fill(window: Window, rows: list[dict[str, Any]], date_column: bool = False) -> list[dict[str, Any]]:
    """Rows grouped by bucket, spread over every bucket of the window so a
    quiet day is a zero on the line rather than a gap it is drawn through."""
    index: dict[datetime, int] = {b: i for i, b in enumerate(window.buckets)}
    filled: list[dict[str, Any]] = [{} for _ in window.buckets]
    for row in rows:
        bucket = row.get("bucket")
        if bucket is None:
            continue
        if isinstance(bucket, datetime):
            key = bucket.replace(tzinfo=None) if bucket.tzinfo else bucket
        else:
            key = datetime.combine(bucket, time.min)
        # A DATE column bucketed by the hour is the day it fell on.
        if date_column and window.bucket == "hour":
            key = key.replace(hour=0, minute=0, second=0, microsecond=0)
        i = index.get(key)
        if i is None:
            # A row just outside the bucket grid (a clock skew, a DST edge):
            # counted in the total, dropped from the line.
            continue
        filled[i] = {k: v for k, v in row.items() if k != "bucket"}
    return filled


@dataclass(frozen=True)
class MetricSpec:
    key: str
    fmt: str
    #: Which way is good news — a longer deal cycle is a worse number.
    better: str
    value: Callable[[dict[str, Any]], float | None]
    #: True when the figure is a present-tense fact (open tasks, stock on the
    #: shelf) and so has neither a comparison nor a trend line.
    snapshot: bool = False


def _metric(spec: MetricSpec, current: dict[str, Any], previous: dict[str, Any] | None,
            buckets: list[dict[str, Any]] | None, snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if spec.snapshot:
        value = spec.value(snapshot or {})
        return {
            "key": spec.key,
            "format": spec.fmt,
            "better": spec.better,
            "value": _out(spec.fmt, value),
            "previous": None,
            "change": None,
            "spark": [],
            "snapshot": True,
        }
    value = spec.value(current)
    prev = spec.value(previous or {})
    spark = [spec.value(b) for b in (buckets or [])]
    return {
        "key": spec.key,
        "format": spec.fmt,
        "better": spec.better,
        "value": _out(spec.fmt, value),
        "previous": _out(spec.fmt, prev),
        "change": _change(value, prev),
        "spark": [round(v or 0.0, 4) for v in spark],
        "snapshot": False,
    }


def _out(fmt: str, value: float | None) -> Any:
    """Money stays a string; a missing average is null, not zero — an average
    of nothing is not 0 days."""
    if value is None:
        return None if fmt in ("days", "hours", "clock", "percent") else _out(fmt, 0.0)
    if fmt == "money":
        return _money(round(value, 2))
    if fmt in ("count",):
        return int(round(value))
    return round(value, 4)


# ─── Scopes ──────────────────────────────────────────────────────────────────

def _lead_scope(employee_id: int | None) -> tuple[str, list[Any]]:
    """"Mine" on the funnel: raised by me or claimed by me — the pair the sales
    board and the old report both filter by."""
    if employee_id is None:
        return "", []
    return " AND (l.author_id = %s OR l.claimed_by_id = %s)", [employee_id, employee_id]


def _task_scope(employee_id: int | None) -> tuple[str, list[Any]]:
    if employee_id is None:
        return "", []
    return (
        f" AND (t.author_id = %s OR EXISTS (SELECT 1 FROM {B2B_TASK_ASSIGNEE_TABLE} a "
        f"WHERE a.task_id = t.id AND a.employee_id = %s))",
        [employee_id, employee_id],
    )


def _movement_scope(employee_id: int | None) -> tuple[str, list[Any]]:
    if employee_id is None:
        return "", []
    return " AND m.author_id = %s", [employee_id]


def _trip_scope(employee_id: int | None) -> tuple[str, list[Any]]:
    if employee_id is None:
        return "", []
    return (
        f" AND EXISTS (SELECT 1 FROM {B2B_TRIP_EMPLOYEE_TABLE} te "
        f"WHERE te.trip_id = t.id AND te.employee_id = %s)",
        [employee_id],
    )


def _attendance_scope(employee_id: int | None) -> tuple[str, list[Any]]:
    if employee_id is None:
        return "", []
    return " AND a.employee_id = %s", [employee_id]


def _group(window: Window | None, column: str, *, date_column: bool = False) -> tuple[str, str, list[Any]]:
    """The SELECT head, the GROUP BY tail and their parameters for a query
    bucketed by [column] — or an ungrouped one when [window] is None."""
    if window is None:
        return "", "", []
    bucket = window.bucket
    if date_column:
        bucket = "day" if bucket == "hour" else bucket
        return f"date_trunc(%s, {column}::timestamp) AS bucket, ", " GROUP BY 1", [bucket]
    return (
        f"date_trunc(%s, {column} AT TIME ZONE %s) AS bucket, ",
        " GROUP BY 1",
        [bucket, window.tz_name],
    )


# ─── Sotuv ───────────────────────────────────────────────────────────────────

def _sales_closed(company_id: int, start: datetime, end: datetime, employee_id: int | None,
                  window: Window | None) -> list[dict[str, Any]]:
    """Deals decided in [start, end), by the moment they closed."""
    scope, scope_params = _lead_scope(employee_id)
    head, tail, group_params = _group(window, "l.completed_at")
    return fetch_all(
        f"""
        SELECT {head}
            COUNT(*) FILTER (WHERE l.stage = %s)                            AS won,
            COUNT(*) FILTER (WHERE l.stage = %s)                            AS lost,
            COALESCE(SUM(l.amount) FILTER (WHERE l.stage = %s), 0)          AS revenue,
            COALESCE(SUM(EXTRACT(EPOCH FROM (l.completed_at - l.created_at)) / 86400.0)
                FILTER (WHERE l.stage = %s AND l.kind = %s), 0)             AS cycle_days,
            COUNT(*) FILTER (WHERE l.stage = %s AND l.kind = %s)            AS cycle_n,
            COALESCE(SUM(CASE WHEN g.n > 0 THEN g.qty ELSE l.quantity END)
                FILTER (WHERE l.stage = %s), 0)                             AS goods,
            COALESCE(SUM(g.cogs) FILTER (WHERE l.stage = %s), 0)            AS cogs
        FROM {B2B_WORKSPACE_LEAD_TABLE} l
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS n,
                   COALESCE(SUM(i.qty), 0) AS qty,
                   COALESCE(SUM(i.qty * COALESCE(p.purchase_price, 0)), 0) AS cogs
            FROM {B2B_WORKSPACE_LEAD_ITEM_TABLE} i
            LEFT JOIN {B2B_PRODUCT_TABLE} p ON p.id = i.product_id
            WHERE i.lead_id = l.id
        ) g ON TRUE
        WHERE l.company_id = %s AND l.deleted_at IS NULL
          AND l.stage IN (%s, %s)
          AND l.completed_at >= %s AND l.completed_at < %s{scope}
        {tail}
        """,
        [
            *group_params,
            LeadStage.WON, LeadStage.LOST, LeadStage.WON,
            LeadStage.WON, LeadKind.LEAD, LeadStage.WON, LeadKind.LEAD,
            LeadStage.WON, LeadStage.WON,
            company_id, LeadStage.WON, LeadStage.LOST, start, end, *scope_params,
        ],
    )


def _sales_raised(company_id: int, start: datetime, end: datetime, employee_id: int | None,
                  window: Window | None) -> list[dict[str, Any]]:
    """Leads raised in [start, end), and how long the first answer took —
    the claim, or the first thing anybody did on the card, whichever came
    first. Quick sales are born answered and are left out."""
    scope, scope_params = _lead_scope(employee_id)
    head, tail, group_params = _group(window, "l.created_at")
    return fetch_all(
        f"""
        SELECT {head}
            COUNT(*)                                                        AS raised,
            COALESCE(SUM(EXTRACT(EPOCH FROM (r.first_at - l.created_at)) / 3600.0)
                FILTER (WHERE r.first_at IS NOT NULL), 0)                   AS response_hours,
            COUNT(*) FILTER (WHERE r.first_at IS NOT NULL)                  AS response_n
        FROM {B2B_WORKSPACE_LEAD_TABLE} l
        LEFT JOIN LATERAL (
            SELECT LEAST(
                l.claimed_at,
                (SELECT MIN(a.created_at) FROM {B2B_WORKSPACE_LEAD_ACTIVITY_TABLE} a
                 WHERE a.lead_id = l.id AND a.kind <> 'created'
                   AND a.created_at > l.created_at)
            ) AS first_at
        ) r ON TRUE
        WHERE l.company_id = %s AND l.deleted_at IS NULL AND l.kind = %s
          AND l.created_at >= %s AND l.created_at < %s{scope}
        {tail}
        """,
        [*group_params, company_id, LeadKind.LEAD, start, end, *scope_params],
    )


def _sales_agg(company_id: int, start: datetime, end: datetime, employee_id: int | None,
               window: Window | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    closed = _sales_closed(company_id, start, end, employee_id, window)
    raised = _sales_raised(company_id, start, end, employee_id, window)
    if window is None:
        total = {**(closed[0] if closed else {}), **(raised[0] if raised else {})}
        total.pop("bucket", None)
        return total, []
    buckets = _fill(window, closed)
    for i, row in enumerate(_fill(window, raised)):
        buckets[i] = {**buckets[i], **row}
    return _sum_rows(buckets), buckets


SALES_METRICS = (
    MetricSpec("revenue", "money", "up", lambda a: _num(a.get("revenue"))),
    MetricSpec("avg_check", "money", "up", lambda a: _ratio(a.get("revenue"), a.get("won"))),
    MetricSpec("deals", "count", "up", lambda a: _num(a.get("won"))),
    MetricSpec("conversion", "percent", "up",
               lambda a: _ratio(a.get("won"), _num(a.get("won")) + _num(a.get("lost")))),
    MetricSpec("cycle", "days", "down", lambda a: _ratio(a.get("cycle_days"), a.get("cycle_n"))),
    MetricSpec("response", "hours", "down",
               lambda a: _ratio(a.get("response_hours"), a.get("response_n"))),
    MetricSpec("goods", "qty", "up", lambda a: _num(a.get("goods"))),
    MetricSpec("profit", "money", "up", lambda a: _num(a.get("revenue")) - _num(a.get("cogs"))),
)


def _sales_employees(company_id: int, window: Window, employee_id: int | None) -> dict[str, Any]:
    scope, scope_params = _lead_scope(employee_id)

    def rows(start: datetime, end: datetime) -> list[dict[str, Any]]:
        return fetch_all(
            f"""
            SELECT e.id AS employee_id, e.full_name, e.photo,
                   COUNT(*) FILTER (WHERE l.stage = %s)                    AS won,
                   COUNT(*) FILTER (WHERE l.stage = %s)                    AS lost,
                   COALESCE(SUM(l.amount) FILTER (WHERE l.stage = %s), 0)  AS revenue
            FROM {B2B_WORKSPACE_LEAD_TABLE} l
            JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = COALESCE(l.claimed_by_id, l.author_id)
            WHERE l.company_id = %s AND l.deleted_at IS NULL
              AND l.stage IN (%s, %s)
              AND l.completed_at >= %s AND l.completed_at < %s{scope}
            GROUP BY e.id, e.full_name, e.photo
            ORDER BY revenue DESC, won DESC, e.full_name ASC
            """,
            [LeadStage.WON, LeadStage.LOST, LeadStage.WON, company_id,
             LeadStage.WON, LeadStage.LOST, start, end, *scope_params],
        )

    current = rows(window.start, window.end)
    previous = {r["employee_id"]: _num(r["revenue"]) for r in rows(window.compare_start, window.compare_end)}
    out = []
    total_revenue = 0.0
    total_won = 0
    total_lost = 0
    total_prev = 0.0
    for r in current:
        revenue = _num(r["revenue"])
        won = _int(r["won"])
        lost = _int(r["lost"])
        total_revenue += revenue
        total_won += won
        total_lost += lost
        prev = previous.get(r["employee_id"], 0.0)
        total_prev += prev
        out.append(_employee_row(r, {
            "revenue": _money(revenue),
            "deals": won,
            "avg_check": _money(revenue / won) if won else None,
            "conversion": _ratio(won, won + lost),
        }, _change(revenue, prev)))
    total_prev += sum(v for k, v in previous.items() if k not in {r["employee_id"] for r in current})
    return {
        "columns": [
            _column("revenue", "money"), _column("deals", "count"),
            _column("avg_check", "money"), _column("conversion", "percent"),
        ],
        "rows": out,
        "total": {
            "revenue": _money(total_revenue),
            "deals": total_won,
            "avg_check": _money(total_revenue / total_won) if total_won else None,
            "conversion": _ratio(total_won, total_won + total_lost),
            "change": _change(total_revenue, total_prev),
        },
    }


# ─── Vazifalar ───────────────────────────────────────────────────────────────

def _tasks_done(company_id: int, start: datetime, end: datetime, employee_id: int | None,
                window: Window | None) -> list[dict[str, Any]]:
    scope, scope_params = _task_scope(employee_id)
    head, tail, group_params = _group(window, "t.completed_at")
    return fetch_all(
        f"""
        SELECT {head}
            COUNT(*)                                                          AS completed,
            COUNT(*) FILTER (WHERE t.due_date IS NOT NULL)                    AS due_n,
            COUNT(*) FILTER (WHERE t.due_date IS NOT NULL
                               AND t.completed_at <= t.due_date)              AS on_time,
            COUNT(*) FILTER (WHERE t.due_date IS NOT NULL
                               AND t.completed_at > t.due_date)               AS late,
            COALESCE(SUM(EXTRACT(EPOCH FROM (t.completed_at - t.created_at)) / 86400.0), 0)
                                                                              AS cycle_days
        FROM {B2B_TASK_TABLE} t
        WHERE t.company_id = %s AND t.deleted_at IS NULL AND t.status = 'done'
          AND t.completed_at >= %s AND t.completed_at < %s{scope}
        {tail}
        """,
        [*group_params, company_id, start, end, *scope_params],
    )


def _tasks_created(company_id: int, start: datetime, end: datetime, employee_id: int | None,
                   window: Window | None) -> list[dict[str, Any]]:
    scope, scope_params = _task_scope(employee_id)
    head, tail, group_params = _group(window, "t.created_at")
    return fetch_all(
        f"""
        SELECT {head} COUNT(*) AS created
        FROM {B2B_TASK_TABLE} t
        WHERE t.company_id = %s AND t.deleted_at IS NULL
          AND t.created_at >= %s AND t.created_at < %s{scope}
        {tail}
        """,
        [*group_params, company_id, start, end, *scope_params],
    )


def _tasks_now(company_id: int, employee_id: int | None) -> dict[str, Any]:
    scope, scope_params = _task_scope(employee_id)
    return fetch_one(
        f"""
        SELECT COUNT(*) FILTER (WHERE t.status <> 'done')                    AS open,
               COUNT(*) FILTER (WHERE t.status <> 'done' AND t.due_date IS NOT NULL
                                  AND t.due_date::date < CURRENT_DATE)       AS overdue
        FROM {B2B_TASK_TABLE} t
        WHERE t.company_id = %s AND t.deleted_at IS NULL{scope}
        """,
        [company_id, *scope_params],
    ) or {}


def _tasks_agg(company_id: int, start: datetime, end: datetime, employee_id: int | None,
               window: Window | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    done = _tasks_done(company_id, start, end, employee_id, window)
    created = _tasks_created(company_id, start, end, employee_id, window)
    if window is None:
        total = {**(done[0] if done else {}), **(created[0] if created else {})}
        total.pop("bucket", None)
        return total, []
    buckets = _fill(window, done)
    for i, row in enumerate(_fill(window, created)):
        buckets[i] = {**buckets[i], **row}
    return _sum_rows(buckets), buckets


TASKS_METRICS = (
    MetricSpec("completed", "count", "up", lambda a: _num(a.get("completed"))),
    MetricSpec("created", "count", "up", lambda a: _num(a.get("created"))),
    MetricSpec("on_time", "percent", "up", lambda a: _ratio(a.get("on_time"), a.get("due_n"))),
    MetricSpec("late", "count", "down", lambda a: _num(a.get("late"))),
    MetricSpec("cycle", "days", "down", lambda a: _ratio(a.get("cycle_days"), a.get("completed"))),
    MetricSpec("completion", "percent", "up", lambda a: _ratio(a.get("completed"), a.get("created"))),
    MetricSpec("open", "count", "down", lambda a: _num(a.get("open")), snapshot=True),
    MetricSpec("overdue", "count", "down", lambda a: _num(a.get("overdue")), snapshot=True),
)


def _tasks_employees(company_id: int, window: Window, employee_id: int | None) -> dict[str, Any]:
    scope = " AND a.employee_id = %s" if employee_id is not None else ""
    scope_params = [employee_id] if employee_id is not None else []

    def rows(start: datetime, end: datetime) -> list[dict[str, Any]]:
        return fetch_all(
            f"""
            SELECT e.id AS employee_id, e.full_name, e.photo,
                   COUNT(*)                                                AS completed,
                   COUNT(*) FILTER (WHERE t.due_date IS NOT NULL)          AS due_n,
                   COUNT(*) FILTER (WHERE t.due_date IS NOT NULL
                                      AND t.completed_at <= t.due_date)    AS on_time,
                   COUNT(*) FILTER (WHERE t.due_date IS NOT NULL
                                      AND t.completed_at > t.due_date)     AS late
            FROM {B2B_TASK_TABLE} t
            JOIN {B2B_TASK_ASSIGNEE_TABLE} a ON a.task_id = t.id
            JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = a.employee_id
            WHERE t.company_id = %s AND t.deleted_at IS NULL AND t.status = 'done'
              AND t.completed_at >= %s AND t.completed_at < %s{scope}
            GROUP BY e.id, e.full_name, e.photo
            ORDER BY completed DESC, on_time DESC, e.full_name ASC
            """,
            [company_id, start, end, *scope_params],
        )

    current = rows(window.start, window.end)
    previous = {r["employee_id"]: _num(r["completed"]) for r in rows(window.compare_start, window.compare_end)}
    out = []
    totals = {"completed": 0, "due_n": 0, "on_time": 0, "late": 0}
    for r in current:
        for k in totals:
            totals[k] += _int(r[k])
        out.append(_employee_row(r, {
            "completed": _int(r["completed"]),
            "on_time": _ratio(r["on_time"], r["due_n"]),
            "late": _int(r["late"]),
        }, _change(_num(r["completed"]), previous.get(r["employee_id"], 0.0))))
    return {
        "columns": [_column("completed", "count"), _column("on_time", "percent"), _column("late", "count")],
        "rows": out,
        "total": {
            "completed": totals["completed"],
            "on_time": _ratio(totals["on_time"], totals["due_n"]),
            "late": totals["late"],
            "change": _change(float(totals["completed"]), float(sum(previous.values()))),
        },
    }


# ─── Sklad ───────────────────────────────────────────────────────────────────

def _stock_moves(company_id: int, start: datetime, end: datetime, employee_id: int | None,
                 window: Window | None) -> list[dict[str, Any]]:
    scope, scope_params = _movement_scope(employee_id)
    head, tail, group_params = _group(window, "m.created_at")
    return fetch_all(
        f"""
        SELECT {head}
            COUNT(*)                                                                    AS movements,
            COALESCE(SUM(ABS(m.quantity)) FILTER (WHERE m.kind = 'receipt'), 0)         AS receipt_qty,
            COALESCE(SUM(ABS(m.quantity) * m.unit_cost) FILTER (WHERE m.kind = 'receipt'), 0)
                                                                                        AS receipt_value,
            COALESCE(SUM(ABS(m.quantity)) FILTER (WHERE m.kind = 'sale'), 0)            AS sold_qty,
            COALESCE(SUM(ABS(m.quantity) * m.unit_cost) FILTER (WHERE m.kind = 'sale'), 0)
                                                                                        AS sold_value,
            COALESCE(SUM(ABS(m.quantity)) FILTER (WHERE m.kind = 'write_off'), 0)       AS write_off_qty,
            COALESCE(SUM(ABS(m.quantity) * COALESCE(NULLIF(m.cost_price, 0), m.unit_cost))
                FILTER (WHERE m.kind = 'write_off'), 0)                                 AS write_off_value,
            COALESCE(SUM(ABS(m.quantity)) FILTER (WHERE m.kind = 'return'), 0)          AS return_qty
        FROM {B2B_STOCK_MOVEMENT_TABLE} m
        WHERE m.company_id = %s
          AND m.created_at >= %s AND m.created_at < %s{scope}
        {tail}
        """,
        [*group_params, company_id, start, end, *scope_params],
    )


def _stock_now(company_id: int) -> dict[str, Any]:
    value = fetch_one(
        f"""
        SELECT COALESCE(SUM(s.quantity * p.purchase_price), 0) AS stock_value
        FROM {B2B_STOCK_TABLE} s
        JOIN {B2B_PRODUCT_TABLE} p ON p.id = s.product_id
        WHERE p.company_id = %s AND p.is_active = TRUE
        """,
        [company_id],
    ) or {}
    low = fetch_one(
        f"""
        SELECT COUNT(*) AS low_stock
        FROM (
            SELECT p.id, p.min_stock, COALESCE(SUM(s.quantity), 0) AS on_hand
            FROM {B2B_PRODUCT_TABLE} p
            LEFT JOIN {B2B_STOCK_TABLE} s ON s.product_id = p.id
            WHERE p.company_id = %s AND p.is_active = TRUE AND p.min_stock > 0
            GROUP BY p.id, p.min_stock
        ) x
        WHERE x.on_hand <= x.min_stock
        """,
        [company_id],
    ) or {}
    return {**value, **low}


def _stock_agg(company_id: int, start: datetime, end: datetime, employee_id: int | None,
               window: Window | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    moves = _stock_moves(company_id, start, end, employee_id, window)
    if window is None:
        total = dict(moves[0]) if moves else {}
        total.pop("bucket", None)
        return total, []
    buckets = _fill(window, moves)
    return _sum_rows(buckets), buckets


STOCK_METRICS = (
    MetricSpec("sold_value", "money", "up", lambda a: _num(a.get("sold_value"))),
    MetricSpec("sold_qty", "qty", "up", lambda a: _num(a.get("sold_qty"))),
    MetricSpec("receipt_value", "money", "up", lambda a: _num(a.get("receipt_value"))),
    MetricSpec("write_off_value", "money", "down", lambda a: _num(a.get("write_off_value"))),
    MetricSpec("movements", "count", "up", lambda a: _num(a.get("movements"))),
    MetricSpec("return_qty", "qty", "down", lambda a: _num(a.get("return_qty"))),
    MetricSpec("stock_value", "money", "up", lambda a: _num(a.get("stock_value")), snapshot=True),
    MetricSpec("low_stock", "count", "down", lambda a: _num(a.get("low_stock")), snapshot=True),
)


def _stock_employees(company_id: int, window: Window, employee_id: int | None) -> dict[str, Any]:
    scope, scope_params = _movement_scope(employee_id)

    def rows(start: datetime, end: datetime) -> list[dict[str, Any]]:
        return fetch_all(
            f"""
            SELECT e.id AS employee_id, e.full_name, e.photo,
                   COUNT(*)                                                             AS movements,
                   COALESCE(SUM(ABS(m.quantity) * m.unit_cost) FILTER (WHERE m.kind = 'sale'), 0)
                                                                                        AS sold_value,
                   COALESCE(SUM(ABS(m.quantity) * m.unit_cost) FILTER (WHERE m.kind = 'receipt'), 0)
                                                                                        AS receipt_value
            FROM {B2B_STOCK_MOVEMENT_TABLE} m
            JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = m.author_id
            WHERE m.company_id = %s
              AND m.created_at >= %s AND m.created_at < %s{scope}
            GROUP BY e.id, e.full_name, e.photo
            ORDER BY sold_value DESC, movements DESC, e.full_name ASC
            """,
            [company_id, start, end, *scope_params],
        )

    current = rows(window.start, window.end)
    previous = {r["employee_id"]: _num(r["sold_value"]) for r in rows(window.compare_start, window.compare_end)}
    out = []
    totals = {"movements": 0, "sold_value": 0.0, "receipt_value": 0.0}
    for r in current:
        totals["movements"] += _int(r["movements"])
        totals["sold_value"] += _num(r["sold_value"])
        totals["receipt_value"] += _num(r["receipt_value"])
        out.append(_employee_row(r, {
            "movements": _int(r["movements"]),
            "sold_value": _money(r["sold_value"]),
            "receipt_value": _money(r["receipt_value"]),
        }, _change(_num(r["sold_value"]), previous.get(r["employee_id"], 0.0))))
    return {
        "columns": [_column("movements", "count"), _column("sold_value", "money"), _column("receipt_value", "money")],
        "rows": out,
        "total": {
            "movements": totals["movements"],
            "sold_value": _money(totals["sold_value"]),
            "receipt_value": _money(totals["receipt_value"]),
            "change": _change(totals["sold_value"], float(sum(previous.values()))),
        },
    }


# ─── Komandirovka ────────────────────────────────────────────────────────────

def _trips_started(company_id: int, start_d: date, end_d: date, employee_id: int | None,
                   window: Window | None) -> list[dict[str, Any]]:
    scope, scope_params = _trip_scope(employee_id)
    head, tail, group_params = _group(window, "t.start_date", date_column=True)
    return fetch_all(
        f"""
        SELECT {head}
            COUNT(*)                                                        AS trips,
            COALESCE(SUM(t.budget), 0)                                      AS budget,
            COUNT(*) FILTER (WHERE t.status = 'completed')                  AS completed,
            COUNT(*) FILTER (WHERE t.status = 'cancelled')                  AS cancelled,
            COALESCE(SUM(GREATEST(t.end_date - t.start_date, 0) + 1)
                FILTER (WHERE t.end_date IS NOT NULL), 0)                   AS days,
            COUNT(*) FILTER (WHERE t.end_date IS NOT NULL)                  AS days_n,
            COALESCE(SUM(h.n), 0)                                           AS travellers
        FROM {B2B_BUSINESS_TRIP_TABLE} t
        LEFT JOIN LATERAL (
            SELECT COUNT(*) AS n FROM {B2B_TRIP_EMPLOYEE_TABLE} te
            WHERE te.trip_id = t.id AND te.status <> 'cancelled'
        ) h ON TRUE
        WHERE t.company_id = %s AND t.start_date IS NOT NULL
          AND t.start_date >= %s AND t.start_date < %s{scope}
        {tail}
        """,
        [*group_params, company_id, start_d, end_d, *scope_params],
    )


def _trips_distinct(company_id: int, start_d: date, end_d: date, employee_id: int | None) -> int:
    scope, scope_params = _trip_scope(employee_id)
    row = fetch_one(
        f"""
        SELECT COUNT(DISTINCT te.employee_id) AS n
        FROM {B2B_BUSINESS_TRIP_TABLE} t
        JOIN {B2B_TRIP_EMPLOYEE_TABLE} te ON te.trip_id = t.id AND te.status <> 'cancelled'
        WHERE t.company_id = %s AND t.start_date IS NOT NULL
          AND t.start_date >= %s AND t.start_date < %s{scope}
        """,
        [company_id, start_d, end_d, *scope_params],
    )
    return _int((row or {}).get("n"))


def _trips_now(company_id: int, employee_id: int | None) -> dict[str, Any]:
    scope, scope_params = _trip_scope(employee_id)
    return fetch_one(
        f"""
        SELECT COUNT(*) AS active
        FROM {B2B_BUSINESS_TRIP_TABLE} t
        WHERE t.company_id = %s AND t.status = 'active'{scope}
        """,
        [company_id, *scope_params],
    ) or {}


def _trips_agg(company_id: int, window: Window, employee_id: int | None, *, compare: bool
               ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if compare:
        start_d, end_d = window.compare_start_date, window.compare_end_date
        rows = _trips_started(company_id, start_d, end_d, employee_id, None)
        total = dict(rows[0]) if rows else {}
        total.pop("bucket", None)
        total["distinct_travellers"] = _trips_distinct(company_id, start_d, end_d, employee_id)
        return total, []
    start_d, end_d = window.start_date, window.end_date
    buckets = _fill(window, _trips_started(company_id, start_d, end_d, employee_id, window), date_column=True)
    total = _sum_rows(buckets)
    total["distinct_travellers"] = _trips_distinct(company_id, start_d, end_d, employee_id)
    return total, buckets


TRIPS_METRICS = (
    MetricSpec("trips", "count", "up", lambda a: _num(a.get("trips"))),
    MetricSpec("travellers", "count", "up",
               lambda a: _num(a.get("distinct_travellers")) if "distinct_travellers" in a else _num(a.get("travellers"))),
    MetricSpec("budget", "money", "up", lambda a: _num(a.get("budget"))),
    MetricSpec("avg_budget", "money", "down", lambda a: _ratio(a.get("budget"), a.get("trips"))),
    MetricSpec("avg_days", "days", "up", lambda a: _ratio(a.get("days"), a.get("days_n"))),
    MetricSpec("completed", "count", "up", lambda a: _num(a.get("completed"))),
    MetricSpec("cancelled", "count", "down", lambda a: _num(a.get("cancelled"))),
    MetricSpec("active", "count", "up", lambda a: _num(a.get("active")), snapshot=True),
)


def _trips_employees(company_id: int, window: Window, employee_id: int | None) -> dict[str, Any]:
    scope = " AND te.employee_id = %s" if employee_id is not None else ""
    scope_params = [employee_id] if employee_id is not None else []

    def rows(start_d: date, end_d: date) -> list[dict[str, Any]]:
        return fetch_all(
            f"""
            SELECT e.id AS employee_id, e.full_name, e.photo,
                   COUNT(*)                                                     AS trips,
                   COALESCE(SUM(GREATEST(t.end_date - t.start_date, 0) + 1)
                       FILTER (WHERE t.end_date IS NOT NULL), 0)                AS days,
                   COALESCE(SUM(t.budget), 0)                                   AS budget
            FROM {B2B_BUSINESS_TRIP_TABLE} t
            JOIN {B2B_TRIP_EMPLOYEE_TABLE} te ON te.trip_id = t.id AND te.status <> 'cancelled'
            JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = te.employee_id
            WHERE t.company_id = %s AND t.start_date IS NOT NULL
              AND t.start_date >= %s AND t.start_date < %s{scope}
            GROUP BY e.id, e.full_name, e.photo
            ORDER BY trips DESC, days DESC, e.full_name ASC
            """,
            [company_id, start_d, end_d, *scope_params],
        )

    current = rows(window.start_date, window.end_date)
    previous = {r["employee_id"]: _num(r["trips"]) for r in rows(window.compare_start_date, window.compare_end_date)}
    out = []
    totals = {"trips": 0, "days": 0, "budget": 0.0}
    for r in current:
        totals["trips"] += _int(r["trips"])
        totals["days"] += _int(r["days"])
        totals["budget"] += _num(r["budget"])
        out.append(_employee_row(r, {
            "trips": _int(r["trips"]),
            "days": _int(r["days"]),
            "budget": _money(r["budget"]),
        }, _change(_num(r["trips"]), previous.get(r["employee_id"], 0.0))))
    return {
        "columns": [_column("trips", "count"), _column("days", "count"), _column("budget", "money")],
        "rows": out,
        "total": {
            "trips": totals["trips"],
            "days": totals["days"],
            "budget": _money(totals["budget"]),
            "change": _change(float(totals["trips"]), float(sum(previous.values()))),
        },
    }


# ─── Davomat ─────────────────────────────────────────────────────────────────

def _attendance_days(company_id: int, start_d: date, end_d: date, employee_id: int | None,
                     window: Window | None, tz_name: str) -> list[dict[str, Any]]:
    scope, scope_params = _attendance_scope(employee_id)
    head, tail, group_params = _group(window, "a.work_date", date_column=True)
    present = ", ".join(["%s"] * len(PRESENT_STATUSES))
    return fetch_all(
        f"""
        SELECT {head}
            COUNT(*) FILTER (WHERE a.status IN ({present}))                          AS present,
            COUNT(*) FILTER (WHERE a.status = 'absent')                              AS absent,
            COUNT(*) FILTER (WHERE a.status = 'late')                                AS late,
            COUNT(*) FILTER (WHERE a.status = 'remote')                              AS remote,
            COALESCE(SUM(EXTRACT(EPOCH FROM ((a.checked_in_at AT TIME ZONE %s)::time)) / 60.0)
                FILTER (WHERE a.checked_in_at IS NOT NULL), 0)                       AS checkin_minutes,
            COUNT(*) FILTER (WHERE a.checked_in_at IS NOT NULL)                      AS checkin_n,
            COALESCE(SUM(EXTRACT(EPOCH FROM (a.checked_out_at - a.checked_in_at)) / 3600.0)
                FILTER (WHERE a.checked_in_at IS NOT NULL AND a.checked_out_at > a.checked_in_at), 0)
                                                                                     AS hours,
            COUNT(*) FILTER (WHERE a.checked_in_at IS NOT NULL
                               AND a.checked_out_at > a.checked_in_at)               AS hours_n
        FROM {B2B_ATTENDANCE_TABLE} a
        WHERE a.company_id = %s
          AND a.work_date >= %s AND a.work_date < %s{scope}
        {tail}
        """,
        [*group_params, *PRESENT_STATUSES, tz_name, company_id, start_d, end_d, *scope_params],
    )


def _attendance_distinct(company_id: int, start_d: date, end_d: date, employee_id: int | None) -> int:
    scope, scope_params = _attendance_scope(employee_id)
    row = fetch_one(
        f"""
        SELECT COUNT(DISTINCT a.employee_id) AS n FROM {B2B_ATTENDANCE_TABLE} a
        WHERE a.company_id = %s AND a.work_date >= %s AND a.work_date < %s{scope}
        """,
        [company_id, start_d, end_d, *scope_params],
    )
    return _int((row or {}).get("n"))


def _attendance_agg(company_id: int, window: Window, employee_id: int | None, *, compare: bool
                    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if compare:
        start_d, end_d = window.compare_start_date, window.compare_end_date
        rows = _attendance_days(company_id, start_d, end_d, employee_id, None, window.tz_name)
        total = dict(rows[0]) if rows else {}
        total.pop("bucket", None)
        total["people"] = _attendance_distinct(company_id, start_d, end_d, employee_id)
        return total, []
    start_d, end_d = window.start_date, window.end_date
    buckets = _fill(
        window,
        _attendance_days(company_id, start_d, end_d, employee_id, window, window.tz_name),
        date_column=True,
    )
    total = _sum_rows(buckets)
    total["people"] = _attendance_distinct(company_id, start_d, end_d, employee_id)
    return total, buckets


ATTENDANCE_METRICS = (
    MetricSpec("rate", "percent", "up",
               lambda a: _ratio(a.get("present"), _num(a.get("present")) + _num(a.get("absent")))),
    MetricSpec("present", "count", "up", lambda a: _num(a.get("present"))),
    MetricSpec("absent", "count", "down", lambda a: _num(a.get("absent"))),
    MetricSpec("late", "count", "down", lambda a: _num(a.get("late"))),
    MetricSpec("remote", "count", "up", lambda a: _num(a.get("remote"))),
    MetricSpec("avg_checkin", "clock", "down", lambda a: _ratio(a.get("checkin_minutes"), a.get("checkin_n"))),
    MetricSpec("avg_hours", "hours", "up", lambda a: _ratio(a.get("hours"), a.get("hours_n"))),
    MetricSpec("people", "count", "up",
               lambda a: _num(a.get("people")) if "people" in a else _num(a.get("present"))),
)


def _attendance_employees(company_id: int, window: Window, employee_id: int | None) -> dict[str, Any]:
    scope, scope_params = _attendance_scope(employee_id)
    present = ", ".join(["%s"] * len(PRESENT_STATUSES))

    def rows(start_d: date, end_d: date) -> list[dict[str, Any]]:
        return fetch_all(
            f"""
            SELECT e.id AS employee_id, e.full_name, e.photo,
                   COUNT(*) FILTER (WHERE a.status IN ({present}))   AS present,
                   COUNT(*) FILTER (WHERE a.status = 'late')         AS late,
                   COUNT(*) FILTER (WHERE a.status = 'absent')       AS absent
            FROM {B2B_ATTENDANCE_TABLE} a
            JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = a.employee_id
            WHERE a.company_id = %s
              AND a.work_date >= %s AND a.work_date < %s{scope}
            GROUP BY e.id, e.full_name, e.photo
            ORDER BY present DESC, late ASC, e.full_name ASC
            """,
            [*PRESENT_STATUSES, company_id, start_d, end_d, *scope_params],
        )

    current = rows(window.start_date, window.end_date)
    previous = {r["employee_id"]: _num(r["present"]) for r in rows(window.compare_start_date, window.compare_end_date)}
    out = []
    totals = {"present": 0, "late": 0, "absent": 0}
    for r in current:
        for k in totals:
            totals[k] += _int(r[k])
        out.append(_employee_row(r, {
            "present": _int(r["present"]),
            "late": _int(r["late"]),
            "absent": _int(r["absent"]),
        }, _change(_num(r["present"]), previous.get(r["employee_id"], 0.0))))
    return {
        "columns": [_column("present", "count"), _column("late", "count"), _column("absent", "count")],
        "rows": out,
        "total": {
            **totals,
            "change": _change(float(totals["present"]), float(sum(previous.values()))),
        },
    }


# ─── Assembly ────────────────────────────────────────────────────────────────

def _column(key: str, fmt: str) -> dict[str, str]:
    return {"key": key, "format": fmt}


def _employee_row(row: dict[str, Any], values: dict[str, Any], change: float | None) -> dict[str, Any]:
    return {
        "employee_id": int(row["employee_id"]),
        "full_name": row.get("full_name") or "",
        "photo": photo_url(row.get("photo")),
        "values": values,
        "change": change,
    }


_SPECS: dict[str, tuple[MetricSpec, ...]] = {
    SECTION_SALES: SALES_METRICS,
    SECTION_TASKS: TASKS_METRICS,
    SECTION_STOCK: STOCK_METRICS,
    SECTION_TRIPS: TRIPS_METRICS,
    SECTION_ATTENDANCE: ATTENDANCE_METRICS,
}


def metric_keys(section: str) -> list[str]:
    if section not in _SPECS:
        raise AnalyticsError(f"unknown section {section!r}")
    return [spec.key for spec in _SPECS[section]]


def section_report(company_id: int, section: str, window: Window, *,
                   employee_id: int | None = None) -> dict[str, Any]:
    """One tab of the screen: eight figures and the per-employee table.

    ``employee_id`` narrows everything to one person's work — the reader's
    own when they are not a manager, or the salesperson a manager picked.
    """
    if section not in _SPECS:
        raise AnalyticsError(f"unknown section {section!r}")

    if section == SECTION_SALES:
        current, buckets = _sales_agg(company_id, window.start, window.end, employee_id, window)
        previous, _ = _sales_agg(company_id, window.compare_start, window.compare_end, employee_id, None)
        snapshot: dict[str, Any] = {}
        employees = _sales_employees(company_id, window, employee_id)
    elif section == SECTION_TASKS:
        current, buckets = _tasks_agg(company_id, window.start, window.end, employee_id, window)
        previous, _ = _tasks_agg(company_id, window.compare_start, window.compare_end, employee_id, None)
        snapshot = _tasks_now(company_id, employee_id)
        employees = _tasks_employees(company_id, window, employee_id)
    elif section == SECTION_STOCK:
        current, buckets = _stock_agg(company_id, window.start, window.end, employee_id, window)
        previous, _ = _stock_agg(company_id, window.compare_start, window.compare_end, employee_id, None)
        snapshot = _stock_now(company_id)
        employees = _stock_employees(company_id, window, employee_id)
    elif section == SECTION_TRIPS:
        current, buckets = _trips_agg(company_id, window, employee_id, compare=False)
        previous, _ = _trips_agg(company_id, window, employee_id, compare=True)
        snapshot = _trips_now(company_id, employee_id)
        employees = _trips_employees(company_id, window, employee_id)
    else:
        current, buckets = _attendance_agg(company_id, window, employee_id, compare=False)
        previous, _ = _attendance_agg(company_id, window, employee_id, compare=True)
        snapshot = {}
        employees = _attendance_employees(company_id, window, employee_id)

    return {
        "section": section,
        "metrics": [_metric(spec, current, previous, buckets, snapshot) for spec in _SPECS[section]],
        "employees": employees,
    }


# ─── Detalizatsiya ───────────────────────────────────────────────────────────

_SORTS = ("date", "amount")


def list_items(company_id: int, section: str, metric: str, window: Window, *,
               employee_id: int | None = None, sort: str = "date",
               limit: int = 50, offset: int = 0, cap: int = 200) -> dict[str, Any]:
    """The rows behind one figure — the deals that made the revenue, the
    tasks that were late, the movements that left the shelf.

    Returned with the count and the sum of the whole selection, not just the
    page: the header says "Jami 127 ta yozuv · 48,5 mln" and a page of fifty
    could not know either number.
    """
    if section not in _SPECS:
        raise AnalyticsError(f"unknown section {section!r}")
    if metric not in metric_keys(section):
        raise AnalyticsError(f"unknown metric {metric!r} for {section}")
    sort = sort if sort in _SORTS else "date"
    # Two hundred a page on the phone; the export asks for the lot and says so.
    limit = max(1, min(int(limit or 50), cap))
    offset = max(0, int(offset or 0))

    builder = {
        SECTION_SALES: _sales_items,
        SECTION_TASKS: _tasks_items,
        SECTION_STOCK: _stock_items,
        SECTION_TRIPS: _trips_items,
        SECTION_ATTENDANCE: _attendance_items,
    }[section]
    return builder(company_id, metric, window, employee_id, sort, limit, offset)


def _page(sql_from: str, params: list[Any], *, select: str, amount_expr: str, date_expr: str,
          sort: str, limit: int, offset: int, shape: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
    total = fetch_one(
        f"SELECT COUNT(*) AS n, COALESCE(SUM({amount_expr}), 0) AS amount {sql_from}", params
    ) or {}
    order = f"{amount_expr} DESC NULLS LAST, {date_expr} DESC" if sort == "amount" else f"{date_expr} DESC NULLS LAST"
    rows = fetch_all(
        f"SELECT {select} {sql_from} ORDER BY {order} LIMIT %s OFFSET %s",
        [*params, limit, offset],
    )
    return {
        "count": _int(total.get("n")),
        "amount": _money(total.get("amount")),
        "rows": [shape(r) for r in rows],
        "limit": limit,
        "offset": offset,
    }


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _sales_items(company_id, metric, window: Window, employee_id, sort, limit, offset):
    scope, scope_params = _lead_scope(employee_id)
    if metric == "response":
        where = (
            f"FROM {B2B_WORKSPACE_LEAD_TABLE} l LEFT JOIN {B2B_EMPLOYEE_TABLE} e "
            f"ON e.id = COALESCE(l.claimed_by_id, l.author_id) "
            f"WHERE l.company_id = %s AND l.deleted_at IS NULL AND l.kind = %s "
            f"AND l.created_at >= %s AND l.created_at < %s{scope}"
        )
        params = [company_id, LeadKind.LEAD, window.start, window.end, *scope_params]
        date_expr = "l.created_at"
    else:
        stages = [LeadStage.WON, LeadStage.LOST] if metric == "conversion" else [LeadStage.WON]
        marks = ", ".join(["%s"] * len(stages))
        where = (
            f"FROM {B2B_WORKSPACE_LEAD_TABLE} l LEFT JOIN {B2B_EMPLOYEE_TABLE} e "
            f"ON e.id = COALESCE(l.claimed_by_id, l.author_id) "
            f"WHERE l.company_id = %s AND l.deleted_at IS NULL AND l.stage IN ({marks}) "
            f"AND l.completed_at >= %s AND l.completed_at < %s{scope}"
        )
        params = [company_id, *stages, window.start, window.end, *scope_params]
        date_expr = "l.completed_at"
    return _page(
        where, params,
        select=(
            "l.id, l.company_name, l.contact_full_name, l.product_name, l.stage, l.kind, "
            "l.amount, l.created_at, l.completed_at, e.full_name AS employee_name"
        ),
        amount_expr="l.amount", date_expr=date_expr, sort=sort, limit=limit, offset=offset,
        shape=lambda r: {
            "id": int(r["id"]),
            "kind": "lead",
            "title": r["company_name"] or r["contact_full_name"] or "",
            "subtitle": r.get("product_name") or None,
            "status": r["stage"],
            "amount": _money(r["amount"]),
            "date": _iso(r["completed_at"] if metric != "response" else r["created_at"]),
            "employee": r.get("employee_name"),
        },
    )


def _tasks_items(company_id, metric, window: Window, employee_id, sort, limit, offset):
    scope, scope_params = _task_scope(employee_id)
    base = (
        f"FROM {B2B_TASK_TABLE} t LEFT JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = t.author_id "
        f"WHERE t.company_id = %s AND t.deleted_at IS NULL"
    )
    if metric == "created":
        where = f"{base} AND t.created_at >= %s AND t.created_at < %s{scope}"
        params = [company_id, window.start, window.end, *scope_params]
        date_expr = "t.created_at"
    elif metric in ("open", "overdue"):
        extra = " AND t.due_date IS NOT NULL AND t.due_date::date < CURRENT_DATE" if metric == "overdue" else ""
        where = f"{base} AND t.status <> 'done'{extra}{scope}"
        params = [company_id, *scope_params]
        date_expr = "t.due_date"
    else:
        extra = {
            "on_time": " AND t.due_date IS NOT NULL AND t.completed_at <= t.due_date",
            "late": " AND t.due_date IS NOT NULL AND t.completed_at > t.due_date",
        }.get(metric, "")
        where = f"{base} AND t.status = 'done' AND t.completed_at >= %s AND t.completed_at < %s{extra}{scope}"
        params = [company_id, window.start, window.end, *scope_params]
        date_expr = "t.completed_at"
    return _page(
        where, params,
        select="t.id, t.title, t.status, t.priority, t.due_date, t.created_at, t.completed_at, e.full_name AS employee_name",
        amount_expr="0", date_expr=date_expr, sort="date", limit=limit, offset=offset,
        shape=lambda r: {
            "id": int(r["id"]),
            "kind": "task",
            "title": r["title"] or "",
            "subtitle": r.get("priority"),
            "status": r["status"],
            "amount": None,
            "date": _iso(r["completed_at"] or r["due_date"] or r["created_at"]),
            "employee": r.get("employee_name"),
        },
    )


def _stock_items(company_id, metric, window: Window, employee_id, sort, limit, offset):
    scope, scope_params = _movement_scope(employee_id)
    kind = {
        "sold_value": "sale", "sold_qty": "sale", "receipt_value": "receipt",
        "write_off_value": "write_off", "return_qty": "return",
    }.get(metric)
    if metric in ("stock_value", "low_stock"):
        where = (
            f"FROM (SELECT p.id, p.name, p.sku, p.unit, p.min_stock, p.purchase_price, p.updated_at, "
            f"COALESCE(SUM(s.quantity), 0) AS on_hand FROM {B2B_PRODUCT_TABLE} p "
            f"LEFT JOIN {B2B_STOCK_TABLE} s ON s.product_id = p.id "
            f"WHERE p.company_id = %s AND p.is_active = TRUE GROUP BY p.id) x"
            + (" WHERE x.min_stock > 0 AND x.on_hand <= x.min_stock" if metric == "low_stock" else "")
        )
        return _page(
            where, [company_id],
            select="x.id, x.name, x.sku, x.unit, x.min_stock, x.on_hand, x.purchase_price, x.updated_at",
            amount_expr="x.on_hand * x.purchase_price", date_expr="x.updated_at",
            sort=sort, limit=limit, offset=offset,
            shape=lambda r: {
                "id": int(r["id"]),
                "kind": "product",
                "title": r["name"] or "",
                "subtitle": r.get("sku"),
                "status": "low" if _num(r["min_stock"]) > 0 and _num(r["on_hand"]) <= _num(r["min_stock"]) else "ok",
                "amount": _money(_num(r["on_hand"]) * _num(r["purchase_price"])),
                "qty": _num(r["on_hand"]),
                "unit": r.get("unit"),
                "date": _iso(r["updated_at"]),
                "employee": None,
            },
        )
    kind_clause = " AND m.kind = %s" if kind else ""
    kind_params = [kind] if kind else []
    where = (
        f"FROM {B2B_STOCK_MOVEMENT_TABLE} m JOIN {B2B_PRODUCT_TABLE} p ON p.id = m.product_id "
        f"LEFT JOIN {B2B_WAREHOUSE_TABLE} w ON w.id = m.warehouse_id "
        f"LEFT JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = m.author_id "
        f"WHERE m.company_id = %s AND m.created_at >= %s AND m.created_at < %s{kind_clause}{scope}"
    )
    params = [company_id, window.start, window.end, *kind_params, *scope_params]
    return _page(
        where, params,
        select="m.id, m.kind, m.quantity, m.unit_cost, m.created_at, p.name, p.unit, w.name AS warehouse, e.full_name AS employee_name",
        amount_expr="ABS(m.quantity) * m.unit_cost", date_expr="m.created_at",
        sort=sort, limit=limit, offset=offset,
        shape=lambda r: {
            "id": int(r["id"]),
            "kind": "movement",
            "title": r["name"] or "",
            "subtitle": r.get("warehouse"),
            "status": r["kind"],
            "amount": _money(abs(_num(r["quantity"])) * _num(r["unit_cost"])),
            "qty": abs(_num(r["quantity"])),
            "unit": r.get("unit"),
            "date": _iso(r["created_at"]),
            "employee": r.get("employee_name"),
        },
    )


def _trips_items(company_id, metric, window: Window, employee_id, sort, limit, offset):
    scope, scope_params = _trip_scope(employee_id)
    base = f"FROM {B2B_BUSINESS_TRIP_TABLE} t WHERE t.company_id = %s"
    if metric == "active":
        where = f"{base} AND t.status = 'active'{scope}"
        params = [company_id, *scope_params]
    else:
        extra = {"completed": " AND t.status = 'completed'", "cancelled": " AND t.status = 'cancelled'"}.get(metric, "")
        where = f"{base} AND t.start_date IS NOT NULL AND t.start_date >= %s AND t.start_date < %s{extra}{scope}"
        params = [company_id, window.start_date, window.end_date, *scope_params]
    return _page(
        where, params,
        select=(
            "t.id, t.name, t.destination_city, t.status, t.budget, t.start_date, t.end_date, "
            f"(SELECT COUNT(*) FROM {B2B_TRIP_EMPLOYEE_TABLE} te WHERE te.trip_id = t.id AND te.status <> 'cancelled') AS headcount"
        ),
        amount_expr="t.budget", date_expr="t.start_date", sort=sort, limit=limit, offset=offset,
        shape=lambda r: {
            "id": int(r["id"]),
            "kind": "trip",
            "title": r["name"] or "",
            "subtitle": r.get("destination_city"),
            "status": r["status"],
            "amount": _money(r["budget"]) if r.get("budget") is not None else None,
            "qty": _num(r.get("headcount")),
            "date": _iso(r["start_date"]),
            "date_end": _iso(r["end_date"]),
            "employee": None,
        },
    )


def _attendance_items(company_id, metric, window: Window, employee_id, sort, limit, offset):
    scope, scope_params = _attendance_scope(employee_id)
    status = {
        "present": f" AND a.status IN ({', '.join(['%s'] * len(PRESENT_STATUSES))})",
        "absent": " AND a.status = 'absent'",
        "late": " AND a.status = 'late'",
        "remote": " AND a.status = 'remote'",
        "avg_checkin": " AND a.checked_in_at IS NOT NULL",
        "avg_hours": " AND a.checked_in_at IS NOT NULL AND a.checked_out_at > a.checked_in_at",
    }.get(metric, "")
    status_params = list(PRESENT_STATUSES) if metric == "present" else []
    where = (
        f"FROM {B2B_ATTENDANCE_TABLE} a JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = a.employee_id "
        f"WHERE a.company_id = %s AND a.work_date >= %s AND a.work_date < %s{status}{scope}"
    )
    params = [company_id, window.start_date, window.end_date, *status_params, *scope_params]
    return _page(
        where, params,
        select="a.id, a.work_date, a.status, a.checked_in_at, a.checked_out_at, a.reason, e.full_name",
        amount_expr="0", date_expr="a.work_date", sort="date", limit=limit, offset=offset,
        shape=lambda r: {
            "id": int(r["id"]),
            "kind": "attendance",
            "title": r["full_name"] or "",
            "subtitle": r.get("reason"),
            "status": r["status"],
            "amount": None,
            "date": _iso(r["work_date"]),
            "checked_in_at": _iso(r["checked_in_at"]),
            "checked_out_at": _iso(r["checked_out_at"]),
            "employee": r["full_name"],
        },
    )
