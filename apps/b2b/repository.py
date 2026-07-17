from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from django.utils import timezone

from shared.raw.db import execute, fetch_all, fetch_one
from apps.b2b.models import BudgetRequestStatus, HotelBookingRequestStatus
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
    B2B_HOTEL_BOOKING_REQUEST_TABLE,
    B2B_HOTEL_BOOKING_ROOM_TABLE,
    B2B_HOTEL_BOOKING_ROOM_EMPLOYEE_TABLE,
    B2B_LEAD_REQUEST_TABLE,
)

logger = logging.getLogger(__name__)


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


def list_departments_with_budget(company_id: int, *, search: str | None = None) -> list[dict[str, Any]]:
    """Return every department of *company_id* together with its owner-set
    budget limit and how much of it has been used.

    - ``budget_limit`` – taken from the department's ``b2b_travel_policy_rule``
      row (``applies_to='department'``), or ``None`` if the owner hasn't set
      one for this department.
    - ``used_amount``  – sum of approved budget-request amounts for the
      department's employees (scalar subquery, so it isn't inflated by
      unrelated joins).
    """
    if search:
        return fetch_all(
            f"""
            SELECT
                d.id AS department_id,
                d.company_id AS company_id,
                d.name AS department_name,
                d.created_at AS created_at,
                (
                    SELECT r.budget_limit
                    FROM {B2B_TRAVEL_POLICY_RULE_TABLE} r
                    WHERE r.target_id = d.id AND r.applies_to = 'department'
                    ORDER BY r.id DESC
                    LIMIT 1
                ) AS budget_limit,
                (
                    SELECT COALESCE(SUM(br.amount), 0)
                    FROM {B2B_BUDGET_REQUEST_TABLE} br
                    JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = br.employee_id
                    WHERE e.department_id = d.id AND br.status = 'approved'
                ) AS used_amount
            FROM {B2B_DEPARTMENT_TABLE} d
            WHERE d.company_id = %s AND d.name ILIKE %s
            ORDER BY d.name ASC
            """,
            [company_id, f"%{search}%"],
        )
    return fetch_all(
        f"""
        SELECT
            d.id AS department_id,
            d.company_id AS company_id,
            d.name AS department_name,
            d.created_at AS created_at,
            (
                SELECT r.budget_limit
                FROM {B2B_TRAVEL_POLICY_RULE_TABLE} r
                WHERE r.target_id = d.id AND r.applies_to = 'department'
                ORDER BY r.id DESC
                LIMIT 1
            ) AS budget_limit,
            (
                SELECT COALESCE(SUM(br.amount), 0)
                FROM {B2B_BUDGET_REQUEST_TABLE} br
                JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = br.employee_id
                WHERE e.department_id = d.id AND br.status = 'approved'
            ) AS used_amount
        FROM {B2B_DEPARTMENT_TABLE} d
        WHERE d.company_id = %s
        ORDER BY d.name ASC
        """,
        [company_id],
    )


# ─── Employees ────────────────────────────────────────────────────────────────

def create_employee(*, company_id: int, full_name: str, **kwargs: Any) -> dict[str, Any] | None:
    now = timezone.now()
    cols = ["company_id", "full_name", "created_at", "updated_at"]
    vals = [company_id, full_name, now, now]
    field_map = {
        "department_id": int, "position": str, "email": str, "phone": str,
        "date_of_birth": lambda v: v, "passport_series": str, "passport_pinfl": str,
        "passport_upload_front": str, "passport_upload_back": str,
        "photo": str, "individual_limit": lambda v: v, "status": str, "role": str,
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

def delete_employee(employee_id: int, company_id: int | None = None) -> dict[str, Any] | None: # noqa
    if not company_id:
        return Response("Comany Not Found", status=404)

    if not employee_id:
        return Response("Employee Not Found", status=404)

    return fetch_one(
        f"DELETE FROM {B2b_EMPLOYEE_TABLE} WHERE id = %s company_id = %s RETURNING *", # noqa
        [employee_id, company_id],
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
    empty = _to_pg_json([])
    return fetch_one(
        f"INSERT INTO {B2B_TRAVEL_POLICY_TABLE} (company_id, allowed_star_ratings, allowed_weel_classifications, blacklisted_properties, preferred_properties, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *",
        [company_id, empty, empty, empty, empty, now, now],
    ) or {}


def update_travel_policy(company_id: int, **kwargs: Any) -> dict[str, Any] | None:
    pg_json_fields = {"allowed_star_ratings", "allowed_weel_classifications", "blacklisted_properties", "preferred_properties"}
    sanitized = {}
    for k, v in kwargs.items():
        if k in pg_json_fields and isinstance(v, list):
            sanitized[k] = _to_pg_json(v)
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


# ─── Lead Requests ────────────────────────────────────────────────────────────

def create_lead_request(
    *,
    full_name: str,
    company_name: str,
    email: str,
    phone_number: str,
) -> dict[str, Any] | None:
    """A prospective business owner's public 'become a partner' application."""
    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO {B2B_LEAD_REQUEST_TABLE}
            (full_name, company_name, email, phone_number, created_at)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING *
        """,
        [full_name, company_name, email, phone_number, now],
    )


# ─── Budget Requests ──────────────────────────────────────────────────────────

def create_budget_request(
    *,
    requested_by: int,
    amount: Decimal,
    trip_id: int | None = None,
    employee_id: int | None = None,
    department_id: int | None = None,
    description: str | None = None,
) -> dict[str, Any] | None:
    """Create a budget request, targeting either an employee or a department.

    Always stored ``pending`` — the owner reviews and approves/rejects every
    request via ``POST /budget-requests/<id>/review/``.
    """
    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO {B2B_BUDGET_REQUEST_TABLE}
            (trip_id, employee_id, department_id, requested_by, amount, description, status, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        [trip_id, employee_id, department_id, requested_by, amount, description, BudgetRequestStatus.PENDING, now, now],
    )


def list_budget_requests(company_id: int, *, status: str | None = None) -> list[dict[str, Any]]:
    conditions = ["COALESCE(t.company_id, e.company_id, d.company_id) = %s"]
    params: list[Any] = [company_id]
    if status:
        conditions.append("br.status = %s")
        params.append(status)
    where = " AND ".join(conditions)
    return fetch_all(
        f"""
        SELECT br.*, t.name as trip_name, e.full_name as employee_name, d.name as department_name
        FROM {B2B_BUDGET_REQUEST_TABLE} br
        LEFT JOIN {B2B_BUSINESS_TRIP_TABLE} t ON t.id = br.trip_id
        LEFT JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = br.employee_id
        LEFT JOIN {B2B_DEPARTMENT_TABLE} d ON d.id = br.department_id
        WHERE {where}
        ORDER BY br.created_at DESC
        """,
        params,
    )


def review_budget_request(
    request_id: int, status: str, reviewed_by: int, review_description: str | None = None
) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        UPDATE {B2B_BUDGET_REQUEST_TABLE}
        SET status = %s, reviewed_by = %s, reviewed_at = %s, review_description = %s, updated_at = %s
        WHERE id = %s
        RETURNING *
        """,
        [status, reviewed_by, timezone.now(), review_description, timezone.now(), request_id],
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


# ─── Hotel Booking Requests (2-step: hotel+dates -> rooms+employees) ──────────
#
# A booking request is the "group" that makes a multi-room, multi-employee
# hotel booking show up as ONE entry in the executer's booking history. Each
# room in it maps to a real ``pms_booking`` row created inside that hotel's
# own tenant schema (see apps/hotels/repository.py + apps/property/hotel_repository.py
# for the schema-switching machinery) — there is no cross-schema FK, just a
# plain ``pms_booking_id`` reference resolved via ``tenant_schema``.

def create_hotel_booking_request(
    *,
    company_id: int,
    trip_id: int | None,
    tenant_schema: str,
    hotel_property_id: int,
    hotel_name: str | None,
    check_in: date,
    check_out: date,
    requested_by: int | None,
) -> dict[str, Any] | None:
    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO {B2B_HOTEL_BOOKING_REQUEST_TABLE}
            (company_id, trip_id, tenant_schema, hotel_property_id, hotel_name,
             check_in, check_out, status, requested_by, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        [
            company_id, trip_id, tenant_schema, hotel_property_id, hotel_name,
            check_in, check_out, HotelBookingRequestStatus.PENDING, requested_by, now, now,
        ],
    )


def add_hotel_booking_room(
    *,
    booking_request_id: int,
    room_id: int,
    room_name: str | None,
    pms_booking_id: int | None,
    price_per_night: Decimal | None,
    total_price: Decimal | None,
) -> dict[str, Any] | None:
    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO {B2B_HOTEL_BOOKING_ROOM_TABLE}
            (booking_request_id, room_id, room_name, pms_booking_id, price_per_night, total_price, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        [booking_request_id, room_id, room_name, pms_booking_id, price_per_night, total_price, now, now],
    )


def add_hotel_booking_room_employee(*, booking_room_id: int, employee_id: int) -> dict[str, Any] | None:
    now = timezone.now()
    return fetch_one(
        f"""
        INSERT INTO {B2B_HOTEL_BOOKING_ROOM_EMPLOYEE_TABLE} (booking_room_id, employee_id, created_at, updated_at)
        VALUES (%s, %s, %s, %s)
        RETURNING *
        """,
        [booking_room_id, employee_id, now, now],
    )


def get_hotel_booking_request(booking_request_id: int, company_id: int | None = None) -> dict[str, Any] | None:
    if company_id is not None:
        return fetch_one(
            f"SELECT * FROM {B2B_HOTEL_BOOKING_REQUEST_TABLE} WHERE id = %s AND company_id = %s",
            [booking_request_id, company_id],
        )
    return fetch_one(f"SELECT * FROM {B2B_HOTEL_BOOKING_REQUEST_TABLE} WHERE id = %s", [booking_request_id])


def list_hotel_booking_requests(
    company_id: int,
    *,
    trip_id: int | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    conditions = ["br.company_id = %s"]
    params: list[Any] = [company_id]
    if trip_id is not None:
        conditions.append("br.trip_id = %s")
        params.append(trip_id)
    if status:
        conditions.append("br.status = %s")
        params.append(status)
    where = " AND ".join(conditions)
    return fetch_all(
        f"""
        SELECT
            br.*,
            (SELECT COUNT(*) FROM {B2B_HOTEL_BOOKING_ROOM_TABLE} r WHERE r.booking_request_id = br.id) AS room_count,
            (
                SELECT COUNT(*) FROM {B2B_HOTEL_BOOKING_ROOM_EMPLOYEE_TABLE} re
                JOIN {B2B_HOTEL_BOOKING_ROOM_TABLE} r ON r.id = re.booking_room_id
                WHERE r.booking_request_id = br.id
            ) AS employee_count
        FROM {B2B_HOTEL_BOOKING_REQUEST_TABLE} br
        WHERE {where}
        ORDER BY br.created_at DESC
        """,
        params,
    )


def list_hotel_booking_rooms(booking_request_id: int) -> list[dict[str, Any]]:
    rooms = fetch_all(
        f"SELECT * FROM {B2B_HOTEL_BOOKING_ROOM_TABLE} WHERE booking_request_id = %s ORDER BY id ASC",
        [booking_request_id],
    )
    if not rooms:
        return rooms
    room_ids = [r["id"] for r in rooms]
    employees = fetch_all(
        f"""
        SELECT re.booking_room_id, e.id AS employee_id, e.full_name, e.position
        FROM {B2B_HOTEL_BOOKING_ROOM_EMPLOYEE_TABLE} re
        JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = re.employee_id
        WHERE re.booking_room_id = ANY(%s)
        ORDER BY re.id ASC
        """,
        [room_ids],
    )
    by_room: dict[int, list[dict[str, Any]]] = {}
    for emp in employees:
        by_room.setdefault(emp["booking_room_id"], []).append(emp)
    for room in rooms:
        room["employees"] = by_room.get(room["id"], [])
    return rooms


def update_hotel_booking_request_status(
    booking_request_id: int,
    status: str,
    *,
    reviewed_at: datetime | None = None,
) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        UPDATE {B2B_HOTEL_BOOKING_REQUEST_TABLE}
        SET status = %s, reviewed_at = %s, updated_at = %s
        WHERE id = %s
        RETURNING *
        """,
        [status, reviewed_at, timezone.now(), booking_request_id],
    )


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


# ─── Dashboard summary ───────────────────────────────────────────────────────

def get_dashboard_summary(company_id: int) -> dict[str, Any]:
    """Return the 4 top-line dashboard numbers for a company:

    - ``monthly_limit``          – owner-set overall monthly budget (b2b_travel_policy.monthly_budget)
    - ``spent_this_month``       – sum of approved budget-request amounts reviewed/created this calendar month
    - ``active_employees``       – distinct employees currently on a trip or with an upcoming one
    - ``pending_limit_requests`` – count of budget-requests awaiting review
    """
    now = timezone.now()
    year, month = now.year, now.month

    policy = get_or_create_travel_policy(company_id)
    monthly_limit = policy.get("monthly_budget") or Decimal("0")

    spent_row = fetch_one(
        f"""
        SELECT COALESCE(SUM(br.amount), 0) AS spent
        FROM {B2B_BUDGET_REQUEST_TABLE} br
        JOIN {B2B_BUSINESS_TRIP_TABLE} t ON t.id = br.trip_id
        WHERE t.company_id = %s AND br.status = 'approved'
          AND EXTRACT(YEAR FROM COALESCE(br.reviewed_at, br.created_at)) = %s
          AND EXTRACT(MONTH FROM COALESCE(br.reviewed_at, br.created_at)) = %s
        """,
        [company_id, year, month],
    ) or {}

    active_employees_row = fetch_one(
        f"""
        SELECT COUNT(DISTINCT te.employee_id) AS cnt
        FROM {B2B_TRIP_EMPLOYEE_TABLE} te
        JOIN {B2B_BUSINESS_TRIP_TABLE} t ON t.id = te.trip_id
        WHERE t.company_id = %s
          AND t.status IN ('active', 'pending')
          AND te.status NOT IN ('cancelled', 'checked_out')
          AND t.end_date >= CURRENT_DATE
        """,
        [company_id],
    ) or {}

    pending_requests_row = fetch_one(
        f"""
        SELECT COUNT(*) AS cnt
        FROM {B2B_BUDGET_REQUEST_TABLE} br
        JOIN {B2B_BUSINESS_TRIP_TABLE} t ON t.id = br.trip_id
        WHERE t.company_id = %s AND br.status = 'pending'
        """,
        [company_id],
    ) or {}

    return {
        "monthly_limit": monthly_limit,
        "spent_this_month": spent_row.get("spent") or Decimal("0"),
        "active_employees": active_employees_row.get("cnt") or 0,
        "pending_limit_requests": pending_requests_row.get("cnt") or 0,
    }


# ─── Top employees by trip count ────────────────────────────────────────────

def get_top_employees_by_trip_count(company_id: int, limit: int = 5) -> list[dict[str, Any]]:
    """Return the employees with the most business-trip assignments for a
    company, ordered by ``trip_count`` DESC (distinct trips per employee)."""
    return fetch_all(
        f"""
        SELECT
            e.id              AS employee_id,
            e.full_name       AS full_name,
            e.position        AS position,
            e.email           AS email,
            e.phone           AS phone,
            d.id              AS department_id,
            d.name            AS department_name,
            COUNT(DISTINCT te.trip_id) AS trip_count
        FROM {B2B_EMPLOYEE_TABLE} e
        JOIN {B2B_TRIP_EMPLOYEE_TABLE} te ON te.employee_id = e.id
        JOIN {B2B_BUSINESS_TRIP_TABLE} t ON t.id = te.trip_id AND t.company_id = %s
        LEFT JOIN {B2B_DEPARTMENT_TABLE} d ON d.id = e.department_id
        WHERE e.company_id = %s AND e.is_active = TRUE
        GROUP BY e.id, e.full_name, e.position, e.email, e.phone, d.id, d.name
        ORDER BY trip_count DESC, e.full_name ASC
        LIMIT %s
        """,
        [company_id, company_id, limit],
    )


# ─── Top hotels by booking count ────────────────────────────────────────────

def get_top_hotels_by_booking_count(company_id: int, limit: int = 3) -> list[dict[str, Any]]:
    """Return the hotels this company has booked the most, ordered by
    ``booking_count`` DESC. Grouped by ``(tenant_schema, hotel_property_id)``
    (a hotel can live in any tenant schema, so the plain id alone isn't
    a stable key).
    """
    return fetch_all(
        f"""
        SELECT
            br.tenant_schema,
            br.hotel_property_id,
            MAX(br.hotel_name) AS hotel_name,
            COUNT(DISTINCT br.id) AS booking_count,
            COALESCE(SUM(room.total_price), 0) AS total_spend
        FROM {B2B_HOTEL_BOOKING_REQUEST_TABLE} br
        LEFT JOIN {B2B_HOTEL_BOOKING_ROOM_TABLE} room ON room.booking_request_id = br.id
        WHERE br.company_id = %s
        GROUP BY br.tenant_schema, br.hotel_property_id
        ORDER BY booking_count DESC, MAX(br.hotel_name) ASC
        LIMIT %s
        """,
        [company_id, limit],
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
