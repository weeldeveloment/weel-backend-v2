from __future__ import annotations

import json
import logging
from datetime import date, datetime
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
