from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from django.utils import timezone

from shared.raw.db import execute, fetch_all, fetch_one
from apps.b2b.raw.tables import (
    B2B_COMPANY_TABLE,
    B2B_USER_TABLE,
    B2B_USER_SESSION_TABLE,
    B2B_DEPARTMENT_TABLE,
    B2B_EMPLOYEE_TABLE,
    B2B_BUSINESS_TRIP_TABLE,
    B2B_TRIP_EMPLOYEE_TABLE,
    B2B_TRAVEL_POLICY_TABLE,
    B2B_TRAVEL_POLICY_RULE_TABLE,
    B2B_BUDGET_REQUEST_TABLE,
    B2B_TRAVEL_VOUCHER_TABLE,
)

logger = logging.getLogger(__name__)


def _to_pg_array(v: Any) -> str:
    if not isinstance(v, list):
        return v
    escaped = [f'"{str(x).replace(chr(92), chr(92)*2).replace(chr(34), chr(92)+chr(34))}"' for x in v]
    return "{" + ",".join(escaped) + "}"


def _to_pg_json(v: Any) -> Any:
    if isinstance(v, (list, dict)):
        return json.dumps(v)
    return v


# ─── Company ──────────────────────────────────────────────────────────────────

def create_company(*, name: str, **kwargs: Any) -> dict[str, Any] | None:
    now = timezone.now()
    cols = ["name", "created_at", "updated_at"]
    vals = [name, now, now]
    field_map = {
        "legal_name": str, "inn": str, "city": str, "district": str,
        "legal_address": str, "industry": str, "employee_count": int,
    }
    for key, caster in field_map.items():
        if key in kwargs and kwargs[key] is not None:
            cols.append(key)
            vals.append(caster(kwargs[key]))

    placeholders = ", ".join(["%s"] * len(cols))
    return fetch_one(
        f"INSERT INTO {B2B_COMPANY_TABLE} ({', '.join(cols)}) VALUES ({placeholders}) RETURNING *",
        vals,
    )


def get_company(company_id: int) -> dict[str, Any] | None:
    return fetch_one(f"SELECT * FROM {B2B_COMPANY_TABLE} WHERE id = %s AND is_active = TRUE", [company_id])


def update_company(company_id: int, **kwargs: Any) -> dict[str, Any] | None:
    if not kwargs:
        return get_company(company_id)
    sets = ", ".join(f"{k} = %s" for k in kwargs)
    values = list(kwargs.values()) + [timezone.now(), company_id]
    return fetch_one(
        f"UPDATE {B2B_COMPANY_TABLE} SET {sets}, updated_at = %s WHERE id = %s RETURNING *",
        values,
    )


# ─── B2B Users ────────────────────────────────────────────────────────────────

def create_b2b_user(*, company_id: int, phone: str, **kwargs: Any) -> dict[str, Any] | None:
    now = timezone.now()
    cols = ["company_id", "phone", "created_at", "updated_at"]
    vals = [company_id, phone, now, now]
    field_map = {"email": str, "first_name": str, "last_name": str, "role": str}
    for key, caster in field_map.items():
        if key in kwargs and kwargs[key] is not None:
            cols.append(key)
            vals.append(caster(kwargs[key]))

    placeholders = ", ".join(["%s"] * len(cols))
    return fetch_one(
        f"INSERT INTO {B2B_USER_TABLE} ({', '.join(cols)}) VALUES ({placeholders}) RETURNING *",
        vals,
    )


def get_b2b_user_by_phone(phone: str) -> dict[str, Any] | None:
    return fetch_one(f"SELECT * FROM {B2B_USER_TABLE} WHERE phone = %s AND is_active = TRUE", [phone])


def get_b2b_user(user_id: int) -> dict[str, Any] | None:
    return fetch_one(f"SELECT * FROM {B2B_USER_TABLE} WHERE id = %s AND is_active = TRUE", [user_id])


def list_b2b_users(company_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        f"SELECT * FROM {B2B_USER_TABLE} WHERE company_id = %s AND is_active = TRUE ORDER BY first_name ASC",
        [company_id],
    )


# ─── Departments ──────────────────────────────────────────────────────────────

def create_department(*, company_id: int, name: str) -> dict[str, Any] | None:
    now = timezone.now()
    return fetch_one(
        f"INSERT INTO {B2B_DEPARTMENT_TABLE} (company_id, name, created_at, updated_at) VALUES (%s, %s, %s, %s) RETURNING *",
        [company_id, name, now, now],
    )


def list_departments(company_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        f"SELECT * FROM {B2B_DEPARTMENT_TABLE} WHERE company_id = %s ORDER BY name ASC",
        [company_id],
    )


# ─── Employees ────────────────────────────────────────────────────────────────

def create_employee(*, company_id: int, full_name: str, **kwargs: Any) -> dict[str, Any] | None:
    now = timezone.now()
    cols = ["company_id", "full_name", "created_at", "updated_at"]
    vals = [company_id, full_name, now, now]
    field_map = {
        "department_id": int, "position": str, "email": str, "phone": str,
        "date_of_birth": lambda v: v, "passport_series": str, "passport_number": str,
        "pinfl": str, "individual_limit": lambda v: v, "status": str,
    }
    for key, caster in field_map.items():
        if key in kwargs and kwargs[key] is not None:
            cols.append(key)
            vals.append(caster(kwargs[key]))

    placeholders = ", ".join(["%s"] * len(cols))
    return fetch_one(
        f"INSERT INTO {B2B_EMPLOYEE_TABLE} ({', '.join(cols)}) VALUES ({placeholders}) RETURNING *",
        vals,
    )


def list_employees(company_id: int, *, search: str | None = None) -> list[dict[str, Any]]:
    if search:
        return fetch_all(
            f"""
            SELECT e.*, d.name as department_name
            FROM {B2B_EMPLOYEE_TABLE} e
            LEFT JOIN {B2B_DEPARTMENT_TABLE} d ON d.id = e.department_id
            WHERE e.company_id = %s AND e.is_active = TRUE
              AND (e.full_name ILIKE %s OR e.phone ILIKE %s OR e.position ILIKE %s)
            ORDER BY e.full_name ASC
            """,
            [company_id, f"%{search}%", f"%{search}%", f"%{search}%"],
        )
    return fetch_all(
        f"""
        SELECT e.*, d.name as department_name
        FROM {B2B_EMPLOYEE_TABLE} e
        LEFT JOIN {B2B_DEPARTMENT_TABLE} d ON d.id = e.department_id
        WHERE e.company_id = %s AND e.is_active = TRUE
        ORDER BY e.full_name ASC
        """,
        [company_id],
    )


def get_employee(employee_id: int, company_id: int | None = None) -> dict[str, Any] | None:
    if company_id:
        return fetch_one(
            f"SELECT * FROM {B2B_EMPLOYEE_TABLE} WHERE id = %s AND company_id = %s",
            [employee_id, company_id],
        )
    return fetch_one(f"SELECT * FROM {B2B_EMPLOYEE_TABLE} WHERE id = %s", [employee_id])


def update_employee(employee_id: int, **kwargs: Any) -> dict[str, Any] | None:
    if not kwargs:
        return None
    sets = ", ".join(f"{k} = %s" for k in kwargs)
    values = list(kwargs.values()) + [timezone.now(), employee_id]
    return fetch_one(
        f"UPDATE {B2B_EMPLOYEE_TABLE} SET {sets}, updated_at = %s WHERE id = %s RETURNING *",
        values,
    )


# ─── Business Trips ───────────────────────────────────────────────────────────

def create_trip(*, company_id: int, name: str, **kwargs: Any) -> dict[str, Any] | None:
    now = timezone.now()
    cols = ["company_id", "name", "created_at", "updated_at"]
    vals = [company_id, name, now, now]
    field_map = {
        "destination_city": str, "start_date": lambda v: v, "end_date": lambda v: v,
        "budget": lambda v: v, "status": str, "created_by": int, "notes": str,
    }
    for key, caster in field_map.items():
        if key in kwargs and kwargs[key] is not None:
            cols.append(key)
            vals.append(caster(kwargs[key]))

    placeholders = ", ".join(["%s"] * len(cols))
    return fetch_one(
        f"INSERT INTO {B2B_BUSINESS_TRIP_TABLE} ({', '.join(cols)}) VALUES ({placeholders}) RETURNING *",
        vals,
    )


def list_trips(company_id: int, *, status: str | None = None) -> list[dict[str, Any]]:
    conditions = ["company_id = %s"]
    params: list[Any] = [company_id]
    if status:
        conditions.append("status = %s")
        params.append(status)
    where = " AND ".join(conditions)
    return fetch_all(
        f"SELECT * FROM {B2B_BUSINESS_TRIP_TABLE} WHERE {where} ORDER BY created_at DESC",
        params,
    )


def get_trip(trip_id: int, company_id: int | None = None) -> dict[str, Any] | None:
    if company_id:
        return fetch_one(
            f"SELECT * FROM {B2B_BUSINESS_TRIP_TABLE} WHERE id = %s AND company_id = %s",
            [trip_id, company_id],
        )
    return fetch_one(f"SELECT * FROM {B2B_BUSINESS_TRIP_TABLE} WHERE id = %s", [trip_id])


def update_trip(trip_id: int, **kwargs: Any) -> dict[str, Any] | None:
    if not kwargs:
        return None
    sets = ", ".join(f"{k} = %s" for k in kwargs)
    values = list(kwargs.values()) + [timezone.now(), trip_id]
    return fetch_one(
        f"UPDATE {B2B_BUSINESS_TRIP_TABLE} SET {sets}, updated_at = %s WHERE id = %s RETURNING *",
        values,
    )


def add_trip_employee(*, trip_id: int, employee_id: int, **kwargs: Any) -> dict[str, Any] | None:
    now = timezone.now()
    cols = ["trip_id", "employee_id", "created_at", "updated_at"]
    vals = [trip_id, employee_id, now, now]
    field_map = {
        "property_id": int, "room_id": int, "check_in": lambda v: v,
        "check_out": lambda v: v, "pms_booking_id": int, "status": str,
    }
    for key, caster in field_map.items():
        if key in kwargs and kwargs[key] is not None:
            cols.append(key)
            vals.append(caster(kwargs[key]))

    placeholders = ", ".join(["%s"] * len(cols))
    return fetch_one(
        f"INSERT INTO {B2B_TRIP_EMPLOYEE_TABLE} ({', '.join(cols)}) VALUES ({placeholders}) RETURNING *",
        vals,
    )


def list_trip_employees(trip_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT te.*, e.full_name, e.position, e.phone, e.email
        FROM {B2B_TRIP_EMPLOYEE_TABLE} te
        LEFT JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = te.employee_id
        WHERE te.trip_id = %s
        ORDER BY e.full_name ASC
        """,
        [trip_id],
    )


# ─── Travel Policy ────────────────────────────────────────────────────────────

def get_or_create_travel_policy(company_id: int) -> dict[str, Any]:
    existing = fetch_one(
        f"SELECT * FROM {B2B_TRAVEL_POLICY_TABLE} WHERE company_id = %s", [company_id]
    )
    if existing:
        return existing
    now = timezone.now()
    return fetch_one(
        f"INSERT INTO {B2B_TRAVEL_POLICY_TABLE} (company_id, allowed_star_ratings, allowed_weel_classifications, blacklisted_properties, preferred_properties, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *",
        [company_id, "{}", "{}", "{}", "{}", now, now],
    ) or {}


def update_travel_policy(company_id: int, **kwargs: Any) -> dict[str, Any] | None:
    pg_array_fields = {"allowed_star_ratings", "allowed_weel_classifications", "blacklisted_properties", "preferred_properties"}
    sanitized = {}
    for k, v in kwargs.items():
        if k in pg_array_fields and isinstance(v, list):
            sanitized[k] = _to_pg_array(v)
        else:
            sanitized[k] = v

    if not sanitized:
        return get_or_create_travel_policy(company_id)

    sets = ", ".join(f"{k} = %s" for k in sanitized)
    values = list(sanitized.values()) + [timezone.now(), company_id]
    return fetch_one(
        f"UPDATE {B2B_TRAVEL_POLICY_TABLE} SET {sets}, updated_at = %s WHERE company_id = %s RETURNING *",
        values,
    )


def list_policy_rules(company_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT r.* FROM {B2B_TRAVEL_POLICY_RULE_TABLE} r
        JOIN {B2B_TRAVEL_POLICY_TABLE} p ON p.id = r.policy_id
        WHERE p.company_id = %s
        """,
        [company_id],
    )


def list_policy_rules_by_type(company_id: int, applies_to: str) -> list[dict[str, Any]]:
    return fetch_all(
        f"""
        SELECT r.* FROM {B2B_TRAVEL_POLICY_RULE_TABLE} r
        JOIN {B2B_TRAVEL_POLICY_TABLE} p ON p.id = r.policy_id
        WHERE p.company_id = %s AND r.applies_to = %s
        ORDER BY r.id ASC
        """,
        [company_id, applies_to],
    )


def get_policy_rule(rule_id: int, company_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        SELECT r.* FROM {B2B_TRAVEL_POLICY_RULE_TABLE} r
        JOIN {B2B_TRAVEL_POLICY_TABLE} p ON p.id = r.policy_id
        WHERE r.id = %s AND p.company_id = %s
        """,
        [rule_id, company_id],
    )


def create_policy_rule(
    *,
    company_id: int,
    applies_to: str,
    target_id: int | None,
    budget_limit: Decimal | None,
) -> dict[str, Any] | None:
    policy = get_or_create_travel_policy(company_id)
    if not policy:
        return None
    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO {B2B_TRAVEL_POLICY_RULE_TABLE}
            (policy_id, applies_to, target_id, budget_limit, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        [policy["id"], applies_to, target_id, budget_limit, now, now],
    )


def update_policy_rule(rule_id: int, **kwargs: Any) -> dict[str, Any] | None:
    if not kwargs:
        return fetch_one(
            f"SELECT * FROM {B2B_TRAVEL_POLICY_RULE_TABLE} WHERE id = %s",
            [rule_id],
        )
    sets = ", ".join(f"{k} = %s" for k in kwargs)
    values = list(kwargs.values()) + [timezone.now(), rule_id]
    return fetch_one(
        f"UPDATE {B2B_TRAVEL_POLICY_RULE_TABLE} SET {sets}, updated_at = %s WHERE id = %s RETURNING *",
        values,
    )


def delete_policy_rule(rule_id: int) -> int:
    return execute(
        f"DELETE FROM {B2B_TRAVEL_POLICY_RULE_TABLE} WHERE id = %s",
        [rule_id],
    )


# ─── Budget Requests ──────────────────────────────────────────────────────────

def create_budget_request(*, trip_id: int, employee_id: int, requested_by: int, amount: Decimal, reason: str) -> dict[str, Any] | None:
    now = timezone.now()
    return fetch_one(
        f"INSERT INTO {B2B_BUDGET_REQUEST_TABLE} (trip_id, employee_id, requested_by, amount, reason, status, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, 'pending', %s, %s) RETURNING *",
        [trip_id, employee_id, requested_by, amount, reason, now, now],
    )


def list_budget_requests(company_id: int, *, status: str | None = None) -> list[dict[str, Any]]:
    conditions = ["t.company_id = %s"]
    params: list[Any] = [company_id]
    if status:
        conditions.append("br.status = %s")
        params.append(status)
    where = " AND ".join(conditions)
    return fetch_all(
        f"""
        SELECT br.*, t.name as trip_name, e.full_name as employee_name
        FROM {B2B_BUDGET_REQUEST_TABLE} br
        JOIN {B2B_BUSINESS_TRIP_TABLE} t ON t.id = br.trip_id
        JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = br.employee_id
        WHERE {where}
        ORDER BY br.created_at DESC
        """,
        params,
    )


def review_budget_request(request_id: int, status: str, reviewed_by: int) -> dict[str, Any] | None:
    return fetch_one(
        f"UPDATE {B2B_BUDGET_REQUEST_TABLE} SET status = %s, reviewed_by = %s, reviewed_at = %s, updated_at = %s WHERE id = %s RETURNING *",
        [status, reviewed_by, timezone.now(), timezone.now(), request_id],
    )


# ─── Vouchers ─────────────────────────────────────────────────────────────────

def create_voucher(*, trip_id: int, voucher_number: str, pdf_url: str | None = None) -> dict[str, Any] | None:
    now = timezone.now()
    return fetch_one(
        f"INSERT INTO {B2B_TRAVEL_VOUCHER_TABLE} (trip_id, voucher_number, pdf_url, generated_at, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s) RETURNING *",
        [trip_id, voucher_number, pdf_url, now, now, now],
    )


def get_voucher(trip_id: int) -> dict[str, Any] | None:
    return fetch_one(f"SELECT * FROM {B2B_TRAVEL_VOUCHER_TABLE} WHERE trip_id = %s", [trip_id])


# ─── Statistics ───────────────────────────────────────────────────────────────

_PERIOD_DELTAS: dict[str, timedelta] = {
    "1h": timedelta(hours=1),
    "1d": timedelta(days=1),
    "14d": timedelta(days=14),
    "1m": timedelta(days=30),
    "3m": timedelta(days=90),
    "1y": timedelta(days=365),
}


def _spending_for_period(company_id: int, since: datetime | None) -> dict[str, Any]:
    if since:
        row = fetch_one(
            f"""
            SELECT
                COALESCE(SUM(t.budget), 0) AS total_budget,
                COUNT(t.id) AS total_trips,
                COALESCE((
                    SELECT SUM(br.amount)
                    FROM {B2B_BUDGET_REQUEST_TABLE} br
                    JOIN {B2B_BUSINESS_TRIP_TABLE} bt ON bt.id = br.trip_id
                    WHERE bt.company_id = %s AND br.status = 'approved' AND br.created_at >= %s
                ), 0) AS approved_spend
            FROM {B2B_BUSINESS_TRIP_TABLE} t
            WHERE t.company_id = %s AND t.created_at >= %s
            """,
            [company_id, since, company_id, since],
        ) or {}
    else:
        row = fetch_one(
            f"""
            SELECT
                COALESCE(SUM(t.budget), 0) AS total_budget,
                COUNT(t.id) AS total_trips,
                COALESCE((
                    SELECT SUM(br.amount)
                    FROM {B2B_BUDGET_REQUEST_TABLE} br
                    JOIN {B2B_BUSINESS_TRIP_TABLE} bt ON bt.id = br.trip_id
                    WHERE bt.company_id = %s AND br.status = 'approved'
                ), 0) AS approved_spend
            FROM {B2B_BUSINESS_TRIP_TABLE} t
            WHERE t.company_id = %s
            """,
            [company_id, company_id],
        ) or {}
    return {
        "total_budget": str(row.get("total_budget") or "0"),
        "total_trips": row.get("total_trips") or 0,
        "approved_spend": str(row.get("approved_spend") or "0"),
    }


def get_spending_overview(company_id: int) -> dict[str, Any]:
    now = timezone.now()
    result: dict[str, Any] = {}
    for key, delta in _PERIOD_DELTAS.items():
        result[key] = _spending_for_period(company_id, now - delta)
    result["all"] = _spending_for_period(company_id, None)
    return result


def get_department_spending(company_id: int, since: datetime | None = None) -> list[dict[str, Any]]:
    if since:
        return fetch_all(
            f"""
            SELECT
                d.id AS department_id,
                d.name AS department_name,
                COUNT(DISTINCT te.trip_id) AS total_trips,
                COUNT(DISTINCT te.employee_id) AS total_employees,
                COALESCE(SUM(br.amount), 0) AS approved_spend
            FROM {B2B_DEPARTMENT_TABLE} d
            LEFT JOIN {B2B_EMPLOYEE_TABLE} e ON e.department_id = d.id
            LEFT JOIN {B2B_TRIP_EMPLOYEE_TABLE} te ON te.employee_id = e.id
                AND te.created_at >= %s
            LEFT JOIN {B2B_BUDGET_REQUEST_TABLE} br ON br.employee_id = e.id
                AND br.status = 'approved' AND br.created_at >= %s
            WHERE d.company_id = %s
            GROUP BY d.id, d.name
            ORDER BY approved_spend DESC
            """,
            [since, since, company_id],
        )
    return fetch_all(
        f"""
        SELECT
            d.id AS department_id,
            d.name AS department_name,
            COUNT(DISTINCT te.trip_id) AS total_trips,
            COUNT(DISTINCT te.employee_id) AS total_employees,
            COALESCE(SUM(br.amount), 0) AS approved_spend
        FROM {B2B_DEPARTMENT_TABLE} d
        LEFT JOIN {B2B_EMPLOYEE_TABLE} e ON e.department_id = d.id
        LEFT JOIN {B2B_TRIP_EMPLOYEE_TABLE} te ON te.employee_id = e.id
        LEFT JOIN {B2B_BUDGET_REQUEST_TABLE} br ON br.employee_id = e.id
            AND br.status = 'approved'
        WHERE d.company_id = %s
        GROUP BY d.id, d.name
        ORDER BY approved_spend DESC
        """,
        [company_id],
    )


# ─── Recent trip employees ──────────────────────────────────────────────────

def list_recent_trip_employees(company_id: int, limit: int = 5) -> list[dict[str, Any]]:
    """Return the most recent trip-employee assignments across the company.

    Ordered by ``te.created_at`` DESC so the caller gets the latest ones first.
    Joined with the trip table to expose trip name/dates/destination and with
    the employee table to expose employee info.
    """
    return fetch_all(
        f"""
        SELECT
            te.id              AS trip_employee_id,
            te.trip_id         AS trip_id,
            te.employee_id     AS employee_id,
            te.check_in        AS check_in,
            te.check_out       AS check_out,
            te.status          AS trip_employee_status,
            te.created_at      AS assigned_at,
            t.name             AS trip_name,
            t.destination_city AS destination_city,
            t.start_date       AS trip_start_date,
            t.end_date         AS trip_end_date,
            t.status           AS trip_status,
            e.full_name        AS full_name,
            e.position         AS position,
            e.email            AS email,
            e.phone            AS phone,
            d.name             AS department_name,
            d.id               AS department_id
        FROM {B2B_TRIP_EMPLOYEE_TABLE} te
        JOIN {B2B_BUSINESS_TRIP_TABLE} t ON t.id = te.trip_id
        JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = te.employee_id
        LEFT JOIN {B2B_DEPARTMENT_TABLE} d ON d.id = e.department_id
        WHERE t.company_id = %s
        ORDER BY te.created_at DESC
        LIMIT %s
        """,
        [company_id, limit],
    )


# ─── Active trip employees (yolda / borgan) ─────────────────────────────────

_VALID_ACTIVE_TRIP_TYPES = {"yolda", "borgan", "all"}


def list_active_trip_employees(company_id: int, type_: str = "all") -> list[dict[str, Any]]:
    """Return trip-employee rows for trips that are currently in progress or
    scheduled to start in the future.

    Args:
        company_id: scope to a single company.
        type_: ``"yolda"``  – trip is active and today falls between
                              ``start_date`` and ``end_date``.
               ``"borgan"`` – trip hasn't started yet (``start_date`` is in
                              the future).
               ``"all"``    – union of both.
    """
    if type_ not in _VALID_ACTIVE_TRIP_TYPES:
        type_ = "all"

    if type_ == "yolda":
        date_filter = "AND CURRENT_DATE BETWEEN t.start_date AND t.end_date"
    elif type_ == "borgan":
        date_filter = "AND t.start_date > CURRENT_DATE"
    else:
        date_filter = "AND t.end_date >= CURRENT_DATE"

    return fetch_all(
        f"""
        SELECT
            te.id              AS trip_employee_id,
            te.trip_id         AS trip_id,
            te.employee_id     AS employee_id,
            te.check_in        AS check_in,
            te.check_out       AS check_out,
            te.status          AS trip_employee_status,
            te.created_at      AS assigned_at,
            t.name             AS trip_name,
            t.destination_city AS destination_city,
            t.start_date       AS trip_start_date,
            t.end_date         AS trip_end_date,
            t.status           AS trip_status,
            e.full_name        AS full_name,
            e.position         AS position,
            e.email            AS email,
            e.phone            AS phone,
            d.name             AS department_name,
            d.id               AS department_id
        FROM {B2B_TRIP_EMPLOYEE_TABLE} te
        JOIN {B2B_BUSINESS_TRIP_TABLE} t ON t.id = te.trip_id
        JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = te.employee_id
        LEFT JOIN {B2B_DEPARTMENT_TABLE} d ON d.id = e.department_id
        WHERE t.company_id = %s
          AND t.status IN ('active', 'pending')
          AND te.status NOT IN ('cancelled', 'checked_out')
          {date_filter}
        ORDER BY t.start_date ASC, e.full_name ASC
        """,
        [company_id],
    )


# ─── Department monthly spending ────────────────────────────────────────────

def get_department_monthly_spending(
    company_id: int, year: int, month: int
) -> list[dict[str, Any]]:
    """For every department of *company_id* return the totals for the given
    calendar month.

    ``month_spend`` = sum of approved budget-request amounts whose
    ``reviewed_at`` (or, when NULL, ``created_at``) falls inside the month.

    ``month_trips`` = count of distinct trips whose ``start_date`` falls inside
    the month for any employee of that department.
    """
    return fetch_all(
        f"""
        SELECT
            d.id   AS department_id,
            d.name AS department_name,
            COUNT(DISTINCT te.trip_id) FILTER (
                WHERE EXTRACT(YEAR FROM t.start_date) = %s
                  AND EXTRACT(MONTH FROM t.start_date) = %s
            ) AS month_trips,
            COUNT(DISTINCT te.employee_id) AS total_employees,
            COALESCE(SUM(br.amount) FILTER (
                WHERE br.status = 'approved'
                  AND EXTRACT(YEAR FROM COALESCE(br.reviewed_at, br.created_at)) = %s
                  AND EXTRACT(MONTH FROM COALESCE(br.reviewed_at, br.created_at)) = %s
            ), 0) AS month_spend
        FROM {B2B_DEPARTMENT_TABLE} d
        LEFT JOIN {B2B_EMPLOYEE_TABLE} e ON e.department_id = d.id
        LEFT JOIN {B2B_TRIP_EMPLOYEE_TABLE} te ON te.employee_id = e.id
        LEFT JOIN {B2B_BUSINESS_TRIP_TABLE} t ON t.id = te.trip_id
        LEFT JOIN {B2B_BUDGET_REQUEST_TABLE} br ON br.employee_id = e.id
        WHERE d.company_id = %s
        GROUP BY d.id, d.name
        ORDER BY d.name ASC
        """,
        [year, month, year, month, company_id],
    )
