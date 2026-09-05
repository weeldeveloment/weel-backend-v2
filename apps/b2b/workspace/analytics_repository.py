"""Where a report subscription lives — «Hisobotga obuna bo'lish».

One row per person per section: whether it is on, how often, who receives
it and by which channel. The export sheet on the phone reads and writes it;
the beat pass in ``analytics_tasks`` reads what is due each morning.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any

from django.utils import timezone

from apps.b2b.raw.tables import B2B_EMPLOYEE_TABLE
from apps.shared.raw.db import execute, fetch_all, fetch_one

B2B_REPORT_SUBSCRIPTION_TABLE = "b2b_report_subscription"

FREQUENCY_DAILY = "daily"
FREQUENCY_WEEKLY = "weekly"
FREQUENCY_MONTHLY = "monthly"
FREQUENCIES = (FREQUENCY_DAILY, FREQUENCY_WEEKLY, FREQUENCY_MONTHLY)

CHANNEL_CHAT = "chat"
CHANNEL_EMAIL = "email"
CHANNELS = (CHANNEL_CHAT, CHANNEL_EMAIL)

#: How many addresses one subscription may fan out to. A report is for the
#: people who run the company, not a mailing list.
MAX_RECIPIENTS = 10


def _shape(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out = dict(row)
    for key in ("recipients", "channels"):
        value = out.get(key)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                value = []
        out[key] = list(value or [])
    return out


def get_subscription(company_id: int, employee_id: int, section: str) -> dict[str, Any] | None:
    return _shape(fetch_one(
        f"SELECT * FROM {B2B_REPORT_SUBSCRIPTION_TABLE} "
        "WHERE company_id = %s AND employee_id = %s AND section = %s",
        [company_id, employee_id, section],
    ))


def upsert_subscription(
    company_id: int,
    employee_id: int,
    section: str,
    *,
    is_enabled: bool,
    frequency: str,
    recipients: list[str],
    channels: list[str],
) -> dict[str, Any]:
    now = timezone.now()
    row = fetch_one(
        f"""
        INSERT INTO {B2B_REPORT_SUBSCRIPTION_TABLE}
            (company_id, employee_id, section, is_enabled, frequency, recipients, channels,
             created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)
        ON CONFLICT (employee_id, section) DO UPDATE SET
            is_enabled = EXCLUDED.is_enabled,
            frequency  = EXCLUDED.frequency,
            recipients = EXCLUDED.recipients,
            channels   = EXCLUDED.channels,
            -- Switching a report back on clears the last failure: it is a new
            -- attempt, and the old reason would only confuse.
            last_error = CASE WHEN EXCLUDED.is_enabled THEN NULL
                              ELSE {B2B_REPORT_SUBSCRIPTION_TABLE}.last_error END,
            updated_at = EXCLUDED.updated_at
        RETURNING *
        """,
        [
            company_id, employee_id, section, bool(is_enabled), frequency,
            json.dumps(list(recipients)), json.dumps(list(channels)), now, now,
        ],
    )
    return _shape(row) or {}


def get_subscription_by_id(subscription_id: int) -> dict[str, Any] | None:
    return _shape(fetch_one(
        f"""
        SELECT s.*, e.full_name, e.role, e.email AS employee_email, e.fcm_token, e.is_active
        FROM {B2B_REPORT_SUBSCRIPTION_TABLE} s
        JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = s.employee_id
        WHERE s.id = %s
        """,
        [subscription_id],
    ))


def frequencies_due(today: date) -> list[str]:
    """Which cadences fire on a date: daily every day, the weekly on Monday
    about the week that ended, the monthly on the 1st about last month."""
    due = [FREQUENCY_DAILY]
    if today.weekday() == 0:
        due.append(FREQUENCY_WEEKLY)
    if today.day == 1:
        due.append(FREQUENCY_MONTHLY)
    return due


def due_subscriptions(today: date) -> list[dict[str, Any]]:
    """Every switched-on subscription whose cadence fires today, for a live
    employee, that has not already gone out today."""
    due = frequencies_due(today)
    marks = ", ".join(["%s"] * len(due))
    return [
        _shape(row) or {}
        for row in fetch_all(
            f"""
            SELECT s.*
            FROM {B2B_REPORT_SUBSCRIPTION_TABLE} s
            JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = s.employee_id
            WHERE s.is_enabled = TRUE AND e.is_active = TRUE
              AND s.frequency IN ({marks})
              AND (s.last_sent_at IS NULL OR s.last_sent_at::date < %s)
            ORDER BY s.id
            """,
            [*due, today],
        )
    ]


def mark_delivery(subscription_id: int, *, error: str | None, delivered: bool) -> None:
    """Stamp the attempt.

    ``delivered`` is whether *any* channel got the report out; that is what
    stops the next morning's pass from posting the same week into the chat
    again because the mailbox was down. The error, if there was one, is kept
    beside it for the sheet to show. Nothing delivered: ``last_sent_at``
    stays as it was and tomorrow tries again.
    """
    now = timezone.now()
    if delivered:
        execute(
            f"UPDATE {B2B_REPORT_SUBSCRIPTION_TABLE} "
            "SET last_sent_at = %s, last_error = %s, updated_at = %s WHERE id = %s",
            [now, (error or None) and error[:500], now, subscription_id],
        )
    else:
        execute(
            f"UPDATE {B2B_REPORT_SUBSCRIPTION_TABLE} SET last_error = %s, updated_at = %s WHERE id = %s",
            [(error or "delivery_failed")[:500], now, subscription_id],
        )
