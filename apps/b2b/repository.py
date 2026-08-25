from __future__ import annotations

import calendar
import json
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from django.utils import timezone

from shared.raw.db import execute, fetch_all, fetch_one
from apps.property.hotel_repository import _safe_schema_name
from apps.b2b.models import B2BUserRole, BudgetRequestStatus, HotelBookingRequestStatus
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


def get_org(org_id: int | None) -> dict[str, Any] | None:
    """The company (product sense) a workspace belongs to — see the note in
    `create_b2b_tables.py` on the `b2b_org` / `b2b_company` naming split."""
    if org_id is None:
        return None
    return fetch_one("SELECT * FROM b2b_org WHERE id = %s", [org_id])


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


def _get_b2b_user_by_phone_any(phone: str) -> dict[str, Any] | None:
    """Like ``get_b2b_user_by_phone`` but also matches inactive rows, so a
    previously-revoked login can be found and reactivated instead of hitting
    the ``phone`` unique constraint on re-insert."""
    return fetch_one(f"SELECT * FROM {B2B_USER_TABLE} WHERE phone = %s", [phone])


def _split_full_name(full_name: str | None) -> tuple[str, str | None]:
    parts = (full_name or "").strip().split(None, 1)
    if not parts:
        return "", None
    if len(parts) == 1:
        return parts[0], None
    return parts[0], parts[1]


def _normalize_phone(phone: str) -> str:
    """Matches the normalization ``B2BLoginSendOTPSerializer``/
    ``B2BLoginVerifySerializer`` apply to the phone a user types in at
    login — without this, a performer's phone stored with spaces (or
    missing the leading ``+``) would never match at login time."""
    value = phone.replace(" ", "").strip()
    if not value.startswith("+"):
        value = "+" + value
    return value


def sync_performer_b2b_login(company_id: int, employee: dict[str, Any]) -> dict[str, Any] | None:
    """Grant (or refresh) B2B login access for an employee who was just made
    the company's ``performer`` — they log in with their phone number the
    same way the owner does, via ``B2BLoginSendOTPView``/``B2BLoginVerifyView``.
    """
    if not employee.get("phone"):
        return None
    phone = _normalize_phone(employee["phone"])

    first_name, last_name = _split_full_name(employee.get("full_name"))
    existing = _get_b2b_user_by_phone_any(phone)
    if existing:
        if existing["company_id"] != company_id:
            logger.warning(
                "Cannot grant B2B login for phone %s: already tied to another company (id=%s).",
                phone, existing["company_id"],
            )
            return None
        return update_b2b_user(
            existing["id"], company_id,
            role=B2BUserRole.PERFORMER, is_active=True,
            email=employee.get("email"), first_name=first_name, last_name=last_name,
        )

    return create_b2b_user(
        company_id=company_id, phone=phone, email=employee.get("email"),
        first_name=first_name, last_name=last_name, role=B2BUserRole.PERFORMER,
    )


def revoke_b2b_login_by_phone(company_id: int, phone: str | None) -> None:
    """Revoke B2B login access previously granted via
    ``sync_performer_b2b_login`` — used when an employee is demoted from
    performer or removed."""
    if not phone:
        return
    existing = get_b2b_user_by_phone(_normalize_phone(phone))
    if existing and existing["company_id"] == company_id and existing["role"] == B2BUserRole.PERFORMER:
        deactivate_b2b_user(existing["id"], company_id)


def get_b2b_user(user_id: int) -> dict[str, Any] | None:
    return fetch_one(f"SELECT * FROM {B2B_USER_TABLE} WHERE id = %s AND is_active = TRUE", [user_id])


def list_b2b_users(company_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        f"SELECT * FROM {B2B_USER_TABLE} WHERE company_id = %s AND is_active = TRUE ORDER BY first_name ASC",
        [company_id],
    )


def update_b2b_user(user_id: int, company_id: int, **kwargs: Any) -> dict[str, Any] | None:
    if not kwargs:
        return get_b2b_user(user_id)
    sets = ", ".join(f"{k} = %s" for k in kwargs)
    values = list(kwargs.values()) + [timezone.now(), user_id, company_id]
    return fetch_one(
        f"""
        UPDATE {B2B_USER_TABLE} SET {sets}, updated_at = %s
        WHERE id = %s AND company_id = %s
        RETURNING *
        """,
        values,
    )


def deactivate_b2b_user(user_id: int, company_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        UPDATE {B2B_USER_TABLE}
        SET is_active = FALSE, updated_at = %s
        WHERE id = %s AND company_id = %s AND is_active = TRUE
        RETURNING *
        """,
        [timezone.now(), user_id, company_id],
    )


# ─── Departments ──────────────────────────────────────────────────────────────

def _month_range(month: str) -> tuple[datetime, datetime]:
    """Parse a ``YYYY-MM`` string into a ``[start, end)`` datetime range
    covering that calendar month, in the current timezone. A malformed
    value (bad separator, non-numeric part, month outside 1-12) falls
    back to the current month instead of raising — same forgiving
    behavior as the view-layer ``_parse_year_month`` for this same
    ``month`` query param elsewhere in the app."""
    now = timezone.now()
    try:
        year_str, mon_str = month.split("-")
        year, mon = int(year_str), int(mon_str)
        if mon < 1 or mon > 12:
            raise ValueError
    except (ValueError, AttributeError):
        year, mon = now.year, now.month
    tz = timezone.get_current_timezone()
    start = datetime(year, mon, 1, tzinfo=tz)
    end_year, end_month = _shift_months(year, mon, 1)
    end = datetime(end_year, end_month, 1, tzinfo=tz)
    return start, end


def create_department(*, company_id: int, name: str, color: str | None = None) -> dict[str, Any] | None:
    now = timezone.now()
    if color:
        return fetch_one(
            f"INSERT INTO {B2B_DEPARTMENT_TABLE} (company_id, name, color, created_at, updated_at) VALUES (%s, %s, %s, %s, %s) RETURNING *",
            [company_id, name, color, now, now],
        )
    return fetch_one(
        f"INSERT INTO {B2B_DEPARTMENT_TABLE} (company_id, name, created_at, updated_at) VALUES (%s, %s, %s, %s) RETURNING *",
        [company_id, name, now, now],
    )


def list_departments(company_id: int) -> list[dict[str, Any]]:
    return fetch_all(
        f"SELECT * FROM {B2B_DEPARTMENT_TABLE} WHERE company_id = %s ORDER BY name ASC",
        [company_id],
    )


def update_department(department_id: int, company_id: int, **kwargs: Any) -> dict[str, Any] | None:
    if not kwargs:
        return fetch_one(
            f"SELECT * FROM {B2B_DEPARTMENT_TABLE} WHERE id = %s AND company_id = %s",
            [department_id, company_id],
        )
    sets = ", ".join(f"{k} = %s" for k in kwargs)
    values = list(kwargs.values()) + [timezone.now(), department_id, company_id]
    return fetch_one(
        f"UPDATE {B2B_DEPARTMENT_TABLE} SET {sets}, updated_at = %s WHERE id = %s AND company_id = %s RETURNING *",
        values,
    )


def count_department_employees(department_id: int) -> int:
    row = fetch_one(
        f"SELECT COUNT(*) AS cnt FROM {B2B_EMPLOYEE_TABLE} WHERE department_id = %s AND is_active = TRUE",
        [department_id],
    )
    return int((row or {}).get("cnt") or 0)


def deactivate_department_employees(department_id: int, company_id: int) -> int:
    """Soft-delete every active employee in a department in one go — the
    same ``is_active = FALSE`` deactivation ``delete_employee`` does for one
    employee at a time, applied to the whole department at once. Used when
    the owner chooses to delete a department along with its people instead
    of moving them elsewhere first."""
    return execute(
        f"""
        UPDATE {B2B_EMPLOYEE_TABLE}
        SET is_active = FALSE, updated_at = %s
        WHERE department_id = %s AND company_id = %s AND is_active = TRUE
        """,
        [timezone.now(), department_id, company_id],
    )


def delete_department(department_id: int, company_id: int) -> bool:
    """Hard-delete an empty department. Callers must confirm it has no
    active employees first (see ``count_department_employees``) — deleting
    one that still does would only null out ``employee.department_id``
    (the FK is ``ON DELETE SET NULL``, not cascade), silently orphaning
    them, so the API blocks it earlier instead."""
    rowcount = execute(
        f"DELETE FROM {B2B_DEPARTMENT_TABLE} WHERE id = %s AND company_id = %s",
        [department_id, company_id],
    )
    return rowcount > 0


def move_department_employees(*, from_department_id: int, to_department_id: int, company_id: int) -> int:
    """Reassign every employee of one department to another, both within
    *company_id*. Returns how many rows moved."""
    return execute(
        f"""
        UPDATE {B2B_EMPLOYEE_TABLE} e
        SET department_id = %s, updated_at = %s
        FROM {B2B_DEPARTMENT_TABLE} d_to
        WHERE e.department_id = %s
          AND d_to.id = %s AND d_to.company_id = %s
          AND e.company_id = %s
        """,
        [to_department_id, timezone.now(), from_department_id, to_department_id, company_id, company_id],
    )


def list_departments_with_budget(
    company_id: int, *, search: str | None = None, month: str | None = None,
) -> list[dict[str, Any]]:
    """Return every department of *company_id* together with the budget it
    can draw on and how much of that has actually been spent.

    - ``budget_limit`` – the department's own ``b2b_travel_policy_rule`` row
      (``applies_to='department'``) plus the company-wide "Ко всем" rule
      (``applies_to='all'``): everyone shares that pool on top of whatever
      the department has of its own. ``None`` only when neither is set.
    - ``used_amount``  – real money spent: the sum of confirmed hotel-booking
      room totals for the department's employees, both past stays and
      upcoming ones (booking requests are approved/paid at booking time, not
      at check-in), scoped to *month* (``YYYY-MM``) by when the booking was
      confirmed when given, otherwise all-time. Budget-request amounts are
      NOT spend — approving one raises the shared limit instead (see
      ``review_budget_request``), so counting it here would double-dip.
    - ``on_trip_amount`` – sum paid for the department's employees' hotel
      bookings that are confirmed but whose check-in date hasn't happened
      yet (money already committed to an upcoming trip, a subset of
      ``used_amount``). Always "as of now", not scoped to *month*.
    """
    used_filter = f"req.status = '{HotelBookingRequestStatus.CONFIRMED}'"
    used_params: list[Any] = []
    if month:
        start, end = _month_range(month)
        used_filter += " AND COALESCE(req.reviewed_at, req.created_at) >= %s AND COALESCE(req.reviewed_at, req.created_at) < %s"
        used_params = [start, end]

    where = ["d.company_id = %s"]
    where_params: list[Any] = [company_id]
    if search:
        where.append("d.name ILIKE %s")
        where_params.append(f"%{search}%")

    return fetch_all(
        f"""
        SELECT
            d.id AS department_id,
            d.company_id AS company_id,
            d.name AS department_name,
            d.color AS color,
            d.created_at AS created_at,
            CASE
                WHEN dr.budget_limit IS NULL AND gr.budget_limit IS NULL THEN NULL
                ELSE COALESCE(dr.budget_limit, 0) + COALESCE(gr.budget_limit, 0)
            END AS budget_limit,
            (
                SELECT COALESCE(SUM(room.total_price), 0)
                FROM {B2B_HOTEL_BOOKING_ROOM_TABLE} room
                JOIN {B2B_HOTEL_BOOKING_ROOM_EMPLOYEE_TABLE} room_emp ON room_emp.booking_room_id = room.id
                JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = room_emp.employee_id
                JOIN {B2B_HOTEL_BOOKING_REQUEST_TABLE} req ON req.id = room.booking_request_id
                WHERE e.department_id = d.id AND {used_filter}
            ) AS used_amount,
            (
                SELECT COALESCE(SUM(room.total_price), 0)
                FROM {B2B_HOTEL_BOOKING_ROOM_TABLE} room
                JOIN {B2B_HOTEL_BOOKING_ROOM_EMPLOYEE_TABLE} room_emp ON room_emp.booking_room_id = room.id
                JOIN {B2B_EMPLOYEE_TABLE} e2 ON e2.id = room_emp.employee_id
                JOIN {B2B_HOTEL_BOOKING_REQUEST_TABLE} req ON req.id = room.booking_request_id
                WHERE e2.department_id = d.id
                  AND req.status = '{HotelBookingRequestStatus.CONFIRMED}'
                  AND req.check_in >= CURRENT_DATE
            ) AS on_trip_amount
        FROM {B2B_DEPARTMENT_TABLE} d
        LEFT JOIN LATERAL (
            SELECT r.budget_limit
            FROM {B2B_TRAVEL_POLICY_RULE_TABLE} r
            WHERE r.target_id = d.id AND r.applies_to = 'department'
            ORDER BY r.id DESC
            LIMIT 1
        ) dr ON TRUE
        LEFT JOIN LATERAL (
            SELECT r.budget_limit
            FROM {B2B_TRAVEL_POLICY_RULE_TABLE} r
            JOIN {B2B_TRAVEL_POLICY_TABLE} p ON p.id = r.policy_id
            WHERE p.company_id = d.company_id AND r.applies_to = 'all'
            ORDER BY r.id DESC
            LIMIT 1
        ) gr ON TRUE
        WHERE {' AND '.join(where)}
        ORDER BY d.name ASC
        """,
        used_params + where_params,
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


def list_employees_with_individual_limit(
    company_id: int, *, search: str | None = None, month: str | None = None,
) -> list[dict[str, Any]]:
    """Return active employees who have a personal (``individual_limit``)
    budget set, together with how much of it has been used.

    - ``used_amount`` – always 0; kept in the payload for API compatibility.
      Budget-request approvals no longer touch ``individual_limit`` (see
      ``review_budget_request`` — they top up the company-wide policy limit
      instead), so there's nothing to net out here.
    - ``on_trip_amount`` – sum paid for the employee's hotel bookings that
      are confirmed but whose check-in date hasn't happened yet. Always
      "as of now", not scoped to *month*.
    """
    used_params: list[Any] = []

    where = [
        "e.company_id = %s",
        "e.is_active = TRUE",
        "e.individual_limit IS NOT NULL",
    ]
    where_params: list[Any] = [company_id]
    if search:
        where.append("(e.full_name ILIKE %s OR e.position ILIKE %s)")
        where_params.extend([f"%{search}%", f"%{search}%"])

    return fetch_all(
        f"""
        SELECT
            e.id AS id,
            e.full_name AS full_name,
            e.position AS position,
            e.photo AS photo,
            e.department_id AS department_id,
            d.name AS department_name,
            e.individual_limit AS individual_limit,
            0::numeric AS used_amount,
            (
                SELECT COALESCE(SUM(room.total_price), 0)
                FROM {B2B_HOTEL_BOOKING_ROOM_TABLE} room
                JOIN {B2B_HOTEL_BOOKING_ROOM_EMPLOYEE_TABLE} room_emp ON room_emp.booking_room_id = room.id
                JOIN {B2B_HOTEL_BOOKING_REQUEST_TABLE} req ON req.id = room.booking_request_id
                WHERE room_emp.employee_id = e.id
                  AND req.status = '{HotelBookingRequestStatus.CONFIRMED}'
                  AND req.check_in >= CURRENT_DATE
            ) AS on_trip_amount
        FROM {B2B_EMPLOYEE_TABLE} e
        LEFT JOIN {B2B_DEPARTMENT_TABLE} d ON d.id = e.department_id
        WHERE {' AND '.join(where)}
        ORDER BY e.full_name ASC
        """,
        used_params + where_params,
    )


def get_employee(employee_id: int, company_id: int | None = None) -> dict[str, Any] | None:
    if company_id:
        return fetch_one(
            f"SELECT * FROM {B2B_EMPLOYEE_TABLE} WHERE id = %s AND company_id = %s",
            [employee_id, company_id],
        )
    return fetch_one(f"SELECT * FROM {B2B_EMPLOYEE_TABLE} WHERE id = %s", [employee_id])


def get_active_employee(employee_id: int, company_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        SELECT * FROM {B2B_EMPLOYEE_TABLE}
        WHERE id = %s AND company_id = %s AND is_active = TRUE
        """,
        [employee_id, company_id],
    )


def lock_employees_for_booking(employee_ids: list[int]) -> None:
    """Take a transaction-scoped advisory lock per employee before checking
    for overlapping hotel bookings, so two concurrent booking requests can't
    both read "no overlap" for the same employee and double-book them. There
    is no row on the employee to `SELECT ... FOR UPDATE`, so this uses
    Postgres advisory locks instead; they release automatically at
    COMMIT/ROLLBACK, no matching unlock call needed. Callers must sort/dedupe
    ids the same way to avoid deadlocking against each other."""
    # Namespaced with an arbitrary fixed first key so this lock space can't
    # collide with any other advisory lock keyed by a bare integer id.
    lock_namespace = 911001
    for employee_id in sorted(set(employee_ids)):
        execute("SELECT pg_advisory_xact_lock(%s, %s)", [lock_namespace, employee_id])


def employee_has_overlapping_hotel_booking(
    employee_id: int,
    *,
    check_in: date,
    check_out: date,
) -> bool:
    row = fetch_one(
        f"""
        SELECT EXISTS (
            SELECT 1
            FROM {B2B_HOTEL_BOOKING_ROOM_EMPLOYEE_TABLE} bre
            JOIN {B2B_HOTEL_BOOKING_ROOM_TABLE} brm ON brm.id = bre.booking_room_id
            JOIN {B2B_HOTEL_BOOKING_REQUEST_TABLE} br ON br.id = brm.booking_request_id
            WHERE bre.employee_id = %s
              AND br.status IN (%s, %s)
              AND br.check_in < %s
              AND br.check_out > %s
        ) AS has_overlap
        """,
        [
            employee_id,
            HotelBookingRequestStatus.PENDING,
            HotelBookingRequestStatus.CONFIRMED,
            check_out,
            check_in,
        ],
    )
    return bool(row and row["has_overlap"])


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
    if not company_id or not employee_id:
        return None
    return fetch_one(
        f"""
        UPDATE {B2B_EMPLOYEE_TABLE}
        SET is_active = FALSE, updated_at = %s
        WHERE id = %s AND company_id = %s AND is_active = TRUE
        RETURNING *
        """,
        [timezone.now(), employee_id, company_id],
    )


def delete_trip(trip_id: int, company_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        DELETE FROM {B2B_BUSINESS_TRIP_TABLE}
        WHERE id = %s AND company_id = %s AND status IN ('draft', 'cancelled')
        RETURNING *
        """,
        [trip_id, company_id],
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


def sync_trip_statuses_for_date(today: date) -> int:
    """Transition trips whose dates now put them in a different phase:
    `pending`/`draft` → `active` once `start_date` is reached, and
    `active` → `completed` once `end_date` has passed. Returns the number
    of rows updated. Meant to be run once a day (see b2b.tasks)."""
    activated = execute(
        f"""
        UPDATE {B2B_BUSINESS_TRIP_TABLE}
        SET status = 'active', updated_at = %s
        WHERE status IN ('draft', 'pending')
          AND start_date IS NOT NULL AND start_date <= %s
          AND (end_date IS NULL OR end_date >= %s)
        """,
        [timezone.now(), today, today],
    )
    completed = execute(
        f"""
        UPDATE {B2B_BUSINESS_TRIP_TABLE}
        SET status = 'completed', updated_at = %s
        WHERE status IN ('draft', 'pending', 'active')
          AND end_date IS NOT NULL AND end_date < %s
        """,
        [timezone.now(), today],
    )
    return (activated or 0) + (completed or 0)


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


def upsert_trip_employee(*, trip_id: int, employee_id: int, **kwargs: Any) -> dict[str, Any] | None:
    existing = fetch_one(
        f"""
        SELECT id FROM {B2B_TRIP_EMPLOYEE_TABLE}
        WHERE trip_id = %s AND employee_id = %s
        ORDER BY id DESC LIMIT 1
        """,
        [trip_id, employee_id],
    )
    if not existing:
        return add_trip_employee(trip_id=trip_id, employee_id=employee_id, **kwargs)

    updates = {key: value for key, value in kwargs.items() if value is not None}
    if not updates:
        return fetch_one(
            f"SELECT * FROM {B2B_TRIP_EMPLOYEE_TABLE} WHERE id = %s",
            [existing["id"]],
        )
    sets = ", ".join(f"{key} = %s" for key in updates)
    return fetch_one(
        f"""
        UPDATE {B2B_TRIP_EMPLOYEE_TABLE}
        SET {sets}, updated_at = %s
        WHERE id = %s
        RETURNING *
        """,
        [*updates.values(), timezone.now(), existing["id"]],
    )


def update_trip_employee_status_by_pms_booking(pms_booking_id: int, status: str) -> None:
    execute(
        f"""
        UPDATE {B2B_TRIP_EMPLOYEE_TABLE}
        SET status = %s, updated_at = %s
        WHERE pms_booking_id = %s
        """,
        [status, timezone.now(), pms_booking_id],
    )


def update_booking_request_employee_statuses(booking_request_id: int, status: str) -> None:
    execute(
        f"""
        UPDATE {B2B_TRIP_EMPLOYEE_TABLE} te
        SET status = %s, updated_at = %s
        WHERE te.pms_booking_id IN (
            SELECT room.pms_booking_id
            FROM {B2B_HOTEL_BOOKING_ROOM_TABLE} room
            WHERE room.booking_request_id = %s
              AND room.pms_booking_id IS NOT NULL
        )
        """,
        [status, timezone.now(), booking_request_id],
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


def get_rule_used_amount(company_id: int, applies_to: str, target_id: int | None) -> Decimal:
    """Real money spent against a policy rule's scope: the sum of confirmed
    hotel-booking room totals, all-time (past stays and upcoming ones alike).

    - ``all``        – every confirmed booking in the company.
    - ``department`` – confirmed bookings for that department's employees.
    - ``employee``    – confirmed bookings for that one employee.
    """
    if applies_to == "department" and target_id:
        row = fetch_one(
            f"""
            SELECT COALESCE(SUM(room.total_price), 0) AS used
            FROM {B2B_HOTEL_BOOKING_ROOM_TABLE} room
            JOIN {B2B_HOTEL_BOOKING_ROOM_EMPLOYEE_TABLE} room_emp ON room_emp.booking_room_id = room.id
            JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = room_emp.employee_id
            JOIN {B2B_HOTEL_BOOKING_REQUEST_TABLE} req ON req.id = room.booking_request_id
            WHERE e.department_id = %s AND req.status = '{HotelBookingRequestStatus.CONFIRMED}'
            """,
            [target_id],
        )
    elif applies_to == "employee" and target_id:
        row = fetch_one(
            f"""
            SELECT COALESCE(SUM(room.total_price), 0) AS used
            FROM {B2B_HOTEL_BOOKING_ROOM_TABLE} room
            JOIN {B2B_HOTEL_BOOKING_ROOM_EMPLOYEE_TABLE} room_emp ON room_emp.booking_room_id = room.id
            JOIN {B2B_HOTEL_BOOKING_REQUEST_TABLE} req ON req.id = room.booking_request_id
            WHERE room_emp.employee_id = %s AND req.status = '{HotelBookingRequestStatus.CONFIRMED}'
            """,
            [target_id],
        )
    else:
        row = fetch_one(
            f"""
            SELECT COALESCE(SUM(room.total_price), 0) AS used
            FROM {B2B_HOTEL_BOOKING_ROOM_TABLE} room
            JOIN {B2B_HOTEL_BOOKING_REQUEST_TABLE} req ON req.id = room.booking_request_id
            WHERE req.company_id = %s AND req.status = '{HotelBookingRequestStatus.CONFIRMED}'
            """,
            [company_id],
        )
    return (row or {}).get("used") or Decimal("0")


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


def list_budget_requests(
    company_id: int, *, status: str | None = None, requested_by: int | None = None,
) -> list[dict[str, Any]]:
    """List budget requests with the financial context the owner's review
    modal needs: for a department request, that department's Travel Policy
    limit and how much of it employees have used this month; for a personal
    request, the employee's own spend this month plus the remaining pool of
    their department (an over-limit personal trip draws from the shared
    department budget, not a separate personal limit).
    """
    conditions = ["COALESCE(t.company_id, e.company_id, d.company_id) = %s"]
    params: list[Any] = [company_id]
    if status:
        conditions.append("br.status = %s")
        params.append(status)
    if requested_by is not None:
        # Performers only ever see the requests they raised themselves.
        conditions.append("br.requested_by = %s")
        params.append(requested_by)
    where = " AND ".join(conditions)
    start, end = _month_range(timezone.now().strftime("%Y-%m"))
    return fetch_all(
        f"""
        SELECT
            br.*,
            t.name as trip_name,
            t.destination_city as trip_destination,
            e.full_name as employee_name,
            e.position as employee_position,
            COALESCE(d.name, ed.name) as department_name,
            requester.first_name as requester_first_name,
            requester.last_name as requester_last_name,
            requester.role as requester_role,
            (
                SELECT r.budget_limit
                FROM {B2B_TRAVEL_POLICY_RULE_TABLE} r
                WHERE r.target_id = COALESCE(br.department_id, e.department_id) AND r.applies_to = 'department'
                ORDER BY r.id DESC
                LIMIT 1
            ) AS department_budget_limit,
            (
                SELECT COALESCE(SUM(br2.amount), 0)
                FROM {B2B_BUDGET_REQUEST_TABLE} br2
                JOIN {B2B_EMPLOYEE_TABLE} e2 ON e2.id = br2.employee_id
                WHERE e2.department_id = COALESCE(br.department_id, e.department_id)
                  AND br2.status = 'approved'
                  AND br2.created_at >= %s AND br2.created_at < %s
            ) AS department_used_amount,
            (
                SELECT COALESCE(SUM(br3.amount), 0)
                FROM {B2B_BUDGET_REQUEST_TABLE} br3
                WHERE br3.employee_id = br.employee_id
                  AND br3.status = 'approved'
                  AND br3.created_at >= %s AND br3.created_at < %s
            ) AS employee_used_amount
        FROM {B2B_BUDGET_REQUEST_TABLE} br
        LEFT JOIN {B2B_BUSINESS_TRIP_TABLE} t ON t.id = br.trip_id
        LEFT JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = br.employee_id
        LEFT JOIN {B2B_DEPARTMENT_TABLE} d ON d.id = br.department_id
        LEFT JOIN {B2B_DEPARTMENT_TABLE} ed ON ed.id = e.department_id
        LEFT JOIN {B2B_USER_TABLE} requester ON requester.id = br.requested_by
        WHERE {where}
        ORDER BY br.created_at DESC
        """,
        [start, end, start, end, *params],
    )


def list_transactions(
    company_id: int, *, search: str | None = None, page: int = 1, page_size: int = 10
) -> dict[str, Any]:
    """Paginated transaction history for the analytics page. Combines two
    money-movement sources: budget requests (`b2b_budget_request`) and hotel
    bookings (`b2b_hotel_booking_request` + room totals) — a trip's actual
    spend can arrive through either flow, and only showing budget requests
    left the table empty for companies that book hotels directly.
    `category` is `hotel` for booking rows / trips with a hotel booking,
    `trip` otherwise. `status` for hotel rows is mapped to the same
    approved/pending/rejected vocabulary as budget requests."""
    conditions = ["combined.company_id = %s"]
    params: list[Any] = [company_id]
    if search:
        conditions.append("(combined.employee_name ILIKE %s OR combined.department_name ILIKE %s)")
        params.extend([f"%{search}%", f"%{search}%"])
    where = " AND ".join(conditions)

    combined_cte = f"""
        WITH combined AS (
            SELECT
                br.id, br.created_at, br.amount, br.status::text AS status,
                e.full_name AS employee_name,
                d.name AS department_name,
                t.destination_city AS direction,
                EXISTS (
                    SELECT 1 FROM {B2B_HOTEL_BOOKING_REQUEST_TABLE} hbr WHERE hbr.trip_id = br.trip_id
                ) AS has_hotel,
                COALESCE(t.company_id, e.company_id, d.company_id) AS company_id
            FROM {B2B_BUDGET_REQUEST_TABLE} br
            LEFT JOIN {B2B_BUSINESS_TRIP_TABLE} t ON t.id = br.trip_id
            LEFT JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = br.employee_id
            LEFT JOIN {B2B_DEPARTMENT_TABLE} d ON d.id = br.department_id

            UNION ALL

            SELECT
                (1000000 + hbr.id) AS id, hbr.created_at,
                COALESCE(room_totals.total_price, 0) AS amount,
                CASE hbr.status
                    WHEN 'confirmed' THEN 'approved'
                    WHEN 'pending' THEN 'pending'
                    ELSE 'rejected'
                END AS status,
                emp.full_name AS employee_name,
                dept.name AS department_name,
                t.destination_city AS direction,
                TRUE AS has_hotel,
                hbr.company_id AS company_id
            FROM {B2B_HOTEL_BOOKING_REQUEST_TABLE} hbr
            LEFT JOIN {B2B_BUSINESS_TRIP_TABLE} t ON t.id = hbr.trip_id
            LEFT JOIN LATERAL (
                SELECT COALESCE(SUM(room.total_price), 0) AS total_price
                FROM {B2B_HOTEL_BOOKING_ROOM_TABLE} room
                WHERE room.booking_request_id = hbr.id
            ) room_totals ON TRUE
            LEFT JOIN LATERAL (
                SELECT te.employee_id
                FROM {B2B_TRIP_EMPLOYEE_TABLE} te
                WHERE te.trip_id = hbr.trip_id
                ORDER BY te.created_at ASC
                LIMIT 1
            ) first_te ON TRUE
            LEFT JOIN {B2B_EMPLOYEE_TABLE} emp ON emp.id = first_te.employee_id
            LEFT JOIN {B2B_DEPARTMENT_TABLE} dept ON dept.id = emp.department_id
        )
    """

    count_row = fetch_one(
        f"""
        {combined_cte}
        SELECT COUNT(*) AS cnt FROM combined WHERE {where}
        """,
        params,
    ) or {}

    offset = max(page - 1, 0) * page_size
    rows = fetch_all(
        f"""
        {combined_cte}
        SELECT combined.id, combined.created_at, combined.amount, combined.status,
               combined.employee_name, combined.department_name, combined.direction,
               combined.has_hotel
        FROM combined
        WHERE {where}
        ORDER BY combined.created_at DESC
        LIMIT %s OFFSET %s
        """,
        [*params, page_size, offset],
    )

    return {
        "count": count_row.get("cnt") or 0,
        "results": rows,
    }


def review_budget_request(
    request_id: int, status: str, reviewed_by: int, review_description: str | None = None
) -> dict[str, Any] | None:
    """Approve or reject a budget request.

    Approving actually grants the money: the requested amount is added to the
    company's shared travel-policy limit (the "Ко всем" rule), so everyone —
    not just the requester — can immediately book what was short. Rejecting
    only records the decision.

    Re-reviewing an already-approved request does not top the limit up twice.
    """
    previous = fetch_one(
        f"SELECT status, employee_id, department_id, amount FROM {B2B_BUDGET_REQUEST_TABLE} WHERE id = %s",
        [request_id],
    )
    if not previous:
        return None

    now = timezone.now()
    updated = fetch_one(
        f"""
        UPDATE {B2B_BUDGET_REQUEST_TABLE}
        SET status = %s, reviewed_by = %s, reviewed_at = %s, review_description = %s, updated_at = %s
        WHERE id = %s
        RETURNING *
        """,
        [status, reviewed_by, now, review_description, now, request_id],
    )
    if not updated:
        return None

    if status == "approved" and previous.get("status") != "approved":
        _grant_budget_request(previous)

    return updated


def _grant_budget_request(request_row: dict[str, Any]) -> None:
    """Raise the company-wide ("Ко всем") travel-policy limit by the approved
    amount. There's no per-employee or per-department top-up: every approval
    raises the one shared limit everyone books against."""
    amount = request_row.get("amount")
    if amount is None:
        return

    employee_id = request_row.get("employee_id")
    department_id = request_row.get("department_id")
    company_id = None
    if employee_id:
        employee = get_employee(employee_id)
        company_id = employee.get("company_id") if employee else None
    elif department_id:
        department = fetch_one(
            f"SELECT company_id FROM {B2B_DEPARTMENT_TABLE} WHERE id = %s", [department_id]
        )
        company_id = department.get("company_id") if department else None
    if not company_id:
        return

    policy = get_or_create_travel_policy(company_id)
    global_rule = fetch_one(
        f"""
        SELECT id FROM {B2B_TRAVEL_POLICY_RULE_TABLE}
        WHERE policy_id = %s AND applies_to = 'all'
        ORDER BY id DESC LIMIT 1
        """,
        [policy.get("id")],
    )
    if global_rule:
        execute(
            f"""
            UPDATE {B2B_TRAVEL_POLICY_RULE_TABLE}
            SET budget_limit = COALESCE(budget_limit, 0) + %s, updated_at = %s
            WHERE id = %s
            """,
            [amount, timezone.now(), global_rule["id"]],
        )
    else:
        create_policy_rule(
            company_id=company_id, applies_to="all", target_id=None, budget_limit=amount,
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
    hotel_guid: str,
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
            (company_id, trip_id, hotel_guid, tenant_schema, hotel_property_id,
             hotel_name, check_in, check_out, status, requested_by, created_at,
             updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        [
            company_id, trip_id, hotel_guid, tenant_schema, hotel_property_id, hotel_name,
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


def get_b2b_booking_request_by_pms_booking_id(pms_booking_id: int) -> dict[str, Any] | None:
    return fetch_one(
        f"""
        SELECT br.*
        FROM {B2B_HOTEL_BOOKING_REQUEST_TABLE} br
        JOIN {B2B_HOTEL_BOOKING_ROOM_TABLE} brm ON brm.booking_request_id = br.id
        WHERE brm.pms_booking_id = %s
        LIMIT 1
        """,
        [pms_booking_id],
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


def _company_budget_limit(company_id: int) -> Decimal:
    """The company-wide limit set in the travel policy (the ``all`` rule in
    b2b_travel_policy_rule; falls back to the legacy
    b2b_travel_policy.monthly_budget column). Same source as the dashboard's
    ``monthly_limit`` — see ``get_dashboard_summary``."""
    policy = get_or_create_travel_policy(company_id)
    global_rule = fetch_one(
        f"""
        SELECT r.budget_limit
        FROM {B2B_TRAVEL_POLICY_RULE_TABLE} r
        WHERE r.policy_id = %s AND r.applies_to = 'all'
        ORDER BY r.id DESC
        LIMIT 1
        """,
        [policy.get("id")],
    ) or {}
    return global_rule.get("budget_limit") or policy.get("monthly_budget") or Decimal("0")


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
        booking_row = fetch_one(
            f"""
            SELECT COALESCE(SUM(room.total_price), 0) AS booking_spend
            FROM {B2B_HOTEL_BOOKING_ROOM_TABLE} room
            JOIN {B2B_HOTEL_BOOKING_REQUEST_TABLE} req ON req.id = room.booking_request_id
            WHERE req.company_id = %s AND req.status = '{HotelBookingRequestStatus.CONFIRMED}'
              AND COALESCE(req.reviewed_at, req.created_at) >= %s
            """,
            [company_id, since],
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
        booking_row = fetch_one(
            f"""
            SELECT COALESCE(SUM(room.total_price), 0) AS booking_spend
            FROM {B2B_HOTEL_BOOKING_ROOM_TABLE} room
            JOIN {B2B_HOTEL_BOOKING_REQUEST_TABLE} req ON req.id = room.booking_request_id
            WHERE req.company_id = %s AND req.status = '{HotelBookingRequestStatus.CONFIRMED}'
            """,
            [company_id],
        ) or {}

    approved_spend = (row.get("approved_spend") or Decimal("0")) + (
        booking_row.get("booking_spend") or Decimal("0")
    )
    total_budget = row.get("total_budget") or Decimal("0")
    company_limit = _company_budget_limit(company_id)
    return {
        "total_budget": str(total_budget),
        "total_trips": row.get("total_trips") or 0,
        "approved_spend": str(approved_spend),
        "remaining_limit": str(max(company_limit - approved_spend, Decimal("0"))),
        "requested_extra_limit": str(max(approved_spend - company_limit, Decimal("0"))),
    }


def get_spending_overview(company_id: int) -> dict[str, Any]:
    now = timezone.now()
    result: dict[str, Any] = {}
    for key, delta in _PERIOD_DELTAS.items():
        result[key] = _spending_for_period(company_id, now - delta)
    result["all"] = _spending_for_period(company_id, None)
    return result


# ─── Statistics chart (date-bucketed spending series) ─────────────────────────

_CHART_BUCKET_INTERVAL: dict[str, str] = {
    "1h": "5 minutes",
    "1d": "1 hour",
    "14d": "1 day",
    "1m": "1 day",
    "3m": "3 days",
    "1y": "1 month",
    "all": "1 month",
}


def _approved_spend_for_range(company_id: int, start: datetime, end: datetime) -> Decimal:
    """Actual company spend in ``[start, end)``: approved ad-hoc budget
    requests PLUS confirmed hotel bookings (the real money spent on
    trips), matching the calculation used by ``get_dashboard_summary``."""
    row = fetch_one(
        f"""
        SELECT COALESCE(SUM(br.amount), 0) AS approved_spend
        FROM {B2B_BUDGET_REQUEST_TABLE} br
        JOIN {B2B_BUSINESS_TRIP_TABLE} bt ON bt.id = br.trip_id
        WHERE bt.company_id = %s AND br.status = 'approved'
          AND br.created_at >= %s AND br.created_at < %s
        """,
        [company_id, start, end],
    ) or {}
    approved_spend = row.get("approved_spend") or Decimal("0")

    booking_row = fetch_one(
        f"""
        SELECT COALESCE(SUM(room.total_price), 0) AS booking_spend
        FROM {B2B_HOTEL_BOOKING_ROOM_TABLE} room
        JOIN {B2B_HOTEL_BOOKING_REQUEST_TABLE} req ON req.id = room.booking_request_id
        WHERE req.company_id = %s AND req.status = '{HotelBookingRequestStatus.CONFIRMED}'
          AND COALESCE(req.reviewed_at, req.created_at) >= %s
          AND COALESCE(req.reviewed_at, req.created_at) < %s
        """,
        [company_id, start, end],
    ) or {}
    booking_spend = booking_row.get("booking_spend") or Decimal("0")

    return approved_spend + booking_spend


def _earliest_approved_spend_at(company_id: int) -> datetime | None:
    row = fetch_one(
        f"""
        SELECT MIN(br.created_at) AS earliest
        FROM {B2B_BUDGET_REQUEST_TABLE} br
        JOIN {B2B_BUSINESS_TRIP_TABLE} bt ON bt.id = br.trip_id
        WHERE bt.company_id = %s AND br.status = 'approved'
        """,
        [company_id],
    ) or {}
    earliest = row.get("earliest")

    booking_row = fetch_one(
        f"""
        SELECT MIN(COALESCE(req.reviewed_at, req.created_at)) AS earliest
        FROM {B2B_HOTEL_BOOKING_REQUEST_TABLE} req
        WHERE req.company_id = %s AND req.status = '{HotelBookingRequestStatus.CONFIRMED}'
        """,
        [company_id],
    ) or {}
    booking_earliest = booking_row.get("earliest")

    candidates = [d for d in (earliest, booking_earliest) if d is not None]
    return min(candidates) if candidates else None


def get_spending_chart(company_id: int, period: str) -> dict[str, Any]:
    now = timezone.now()
    interval = _CHART_BUCKET_INTERVAL[period]

    if period == "all":
        earliest = _earliest_approved_spend_at(company_id)
        start = earliest or (now - _PERIOD_DELTAS["1y"])
    else:
        start = now - _PERIOD_DELTAS[period]

    span = now - start
    prev_start = start - span
    prev_end = start

    rows = fetch_all(
        f"""
        SELECT
            d.bucket AS bucket,
            COALESCE((
                SELECT SUM(br.amount)
                FROM {B2B_BUDGET_REQUEST_TABLE} br
                JOIN {B2B_BUSINESS_TRIP_TABLE} bt ON bt.id = br.trip_id
                WHERE bt.company_id = %s AND br.status = 'approved'
                  AND br.created_at >= d.bucket AND br.created_at < d.bucket + %s::interval
            ), 0)
            +
            COALESCE((
                SELECT SUM(room.total_price)
                FROM {B2B_HOTEL_BOOKING_ROOM_TABLE} room
                JOIN {B2B_HOTEL_BOOKING_REQUEST_TABLE} req ON req.id = room.booking_request_id
                WHERE req.company_id = %s AND req.status = '{HotelBookingRequestStatus.CONFIRMED}'
                  AND COALESCE(req.reviewed_at, req.created_at) >= d.bucket
                  AND COALESCE(req.reviewed_at, req.created_at) < d.bucket + %s::interval
            ), 0) AS value
        FROM generate_series(%s::timestamptz, %s::timestamptz, %s::interval) AS d(bucket)
        ORDER BY d.bucket
        """,
        [company_id, interval, company_id, interval, start, now, interval],
    )

    points = [
        {"date": row["bucket"].isoformat(), "value": str(row["value"] or "0")}
        for row in rows
    ]

    current_total = _approved_spend_for_range(company_id, start, now)
    previous_total = _approved_spend_for_range(company_id, prev_start, prev_end)

    if previous_total > 0:
        change_percent = float((current_total - previous_total) / previous_total * 100)
    else:
        change_percent = 100.0 if current_total > 0 else 0.0

    return {
        "period": period,
        "total": str(current_total),
        "change_percent": round(change_percent, 1),
        "points": points,
    }


def _shift_months(year: int, month: int, delta: int) -> tuple[int, int]:
    """Shift (year, month) by *delta* calendar months (can be negative)."""
    zero_based = (year * 12 + (month - 1)) + delta
    return zero_based // 12, zero_based % 12 + 1


def get_monthly_spending_chart(company_id: int, months: int = 12) -> dict[str, Any]:
    """Month-by-month approved-spend series for the last *months* calendar
    months (including the current one), each point carrying its own
    month-over-month ``change_percent`` versus the preceding month.
    """
    now = timezone.now()
    year, month = now.year, now.month

    # Oldest bucket first: months-1 .. 0 back from the current month, plus
    # one extra bucket before the window so the first point has a baseline
    # to compute change_percent against.
    bucket_months = [_shift_months(year, month, -offset) for offset in range(months, -1, -1)]

    bucket_totals: list[Decimal] = []
    for by, bm in bucket_months:
        start = datetime(by, bm, 1, tzinfo=now.tzinfo)
        end_y, end_m = _shift_months(by, bm, 1)
        end = datetime(end_y, end_m, 1, tzinfo=now.tzinfo)
        bucket_totals.append(_approved_spend_for_range(company_id, start, end))

    points = []
    for i in range(1, len(bucket_months)):
        by, bm = bucket_months[i]
        value = bucket_totals[i]
        previous = bucket_totals[i - 1]
        if previous > 0:
            change_percent = float((value - previous) / previous * 100)
        else:
            change_percent = 100.0 if value > 0 else 0.0
        points.append({
            "year": by,
            "month": bm,
            "value": str(value),
            "change_percent": round(change_percent, 1),
        })

    total = sum(bucket_totals[1:], Decimal("0"))
    return {"months": months, "total": str(total), "points": points}


def get_weekly_spending_chart(company_id: int, year: int, month: int) -> dict[str, Any]:
    """Week-by-week approved-spend series for a single calendar month
    (weeks 1-7, 8-14, 15-21, 22-28, 29-end), each with its own
    week-over-week ``change_percent``.
    """
    tzinfo = timezone.now().tzinfo
    days_in_month = calendar.monthrange(year, month)[1]

    # Chunk boundaries: day-of-month starts at 1, 8, 15, 22, 29, ...
    week_starts = list(range(1, days_in_month + 1, 7))

    ranges: list[tuple[datetime, datetime]] = []
    # Baseline bucket: the 7 days immediately preceding day 1 of the month.
    month_start = datetime(year, month, 1, tzinfo=tzinfo)
    ranges.append((month_start - timedelta(days=7), month_start))
    for day in week_starts:
        start = datetime(year, month, day, tzinfo=tzinfo)
        end_day = min(day + 7, days_in_month + 1)
        if end_day > days_in_month:
            end_y, end_m = _shift_months(year, month, 1)
            end = datetime(end_y, end_m, 1, tzinfo=tzinfo)
        else:
            end = datetime(year, month, end_day, tzinfo=tzinfo)
        ranges.append((start, end))

    totals = [_approved_spend_for_range(company_id, s, e) for s, e in ranges]

    weeks = []
    for i in range(1, len(ranges)):
        value = totals[i]
        previous = totals[i - 1]
        if previous > 0:
            change_percent = float((value - previous) / previous * 100)
        else:
            change_percent = 100.0 if value > 0 else 0.0
        start, end = ranges[i]
        weeks.append({
            "week": i,
            "start": start.date().isoformat(),
            "end": (end - timedelta(days=1)).date().isoformat(),
            "value": str(value),
            "change_percent": round(change_percent, 1),
        })

    total = sum(totals[1:], Decimal("0"))
    return {"year": year, "month": month, "total": str(total), "weeks": weeks}


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

_VALID_ACTIVE_TRIP_TYPES = {"yolda", "borgan", "all", "tugagan"}


def _active_trip_employees_where(
    company_id: int,
    type_: str,
    search: str | None,
    department_id: int | None,
    status: str | None,
    date_from: str | None,
    date_to: str | None,
) -> tuple[str, list[Any]]:
    """Shared WHERE-clause builder for list/count of active trip employees."""
    if type_ not in _VALID_ACTIVE_TRIP_TYPES:
        type_ = "all"

    if type_ == "yolda":
        date_filter = "AND CURRENT_DATE BETWEEN t.start_date AND t.end_date"
    elif type_ == "borgan":
        date_filter = "AND t.start_date > CURRENT_DATE"
    elif type_ == "tugagan":
        # Archive: trips that have already ended.
        date_filter = "AND t.end_date < CURRENT_DATE"
    else:
        date_filter = "AND t.end_date >= CURRENT_DATE"

    clauses = [
        "t.company_id = %s",
        "t.status IN ('active', 'pending', 'completed')"
        if type_ == "tugagan"
        else "t.status IN ('active', 'pending')",
    ]
    params: list[Any] = [company_id]

    if status:
        clauses.append("te.status = %s")
        params.append(status)
    elif type_ == "tugagan":
        # Archive: a finished trip's assignments are expected to be
        # checked_out — only drop cancelled ones.
        clauses.append("te.status != 'cancelled'")
    else:
        clauses.append("te.status NOT IN ('cancelled', 'checked_out')")

    if search:
        clauses.append("e.full_name ILIKE %s")
        params.append(f"%{search}%")

    if department_id:
        clauses.append("d.id = %s")
        params.append(department_id)

    if date_from:
        clauses.append("t.end_date >= %s")
        params.append(date_from)

    if date_to:
        clauses.append("t.start_date <= %s")
        params.append(date_to)

    where_sql = " AND ".join(clauses) + f" {date_filter}"
    return where_sql, params


def list_active_trip_employees(
    company_id: int,
    type_: str = "all",
    limit: int | None = None,
    offset: int | None = None,
    search: str | None = None,
    department_id: int | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    """Return trip-employee rows for trips that are currently in progress or
    scheduled to start in the future.

    Args:
        company_id: scope to a single company.
        type_: ``"yolda"``   – trip is active and today falls between
                               ``start_date`` and ``end_date``.
               ``"borgan"``  – trip hasn't started yet (``start_date`` is in
                               the future).
               ``"all"``     – union of both (excludes finished trips).
               ``"tugagan"`` – archive: trips that have already ended
                               (``end_date`` in the past).
        limit: cap the number of rows returned (soonest ``start_date`` first).
               ``None`` returns every matching row.
        offset: number of rows to skip (used together with ``limit`` for
               pagination). Ignored when ``limit`` is ``None``.
        search: filter by employee full name (case-insensitive, partial match).
        department_id: filter to a single department.
        status: filter to a single ``b2b_trip_employee.status`` value. When
               omitted, ``cancelled`` and ``checked_out`` assignments are
               excluded (previous default behaviour).
        date_from: only include trips ending on/after this date (``YYYY-MM-DD``).
        date_to: only include trips starting on/before this date (``YYYY-MM-DD``).
    """
    where_sql, params = _active_trip_employees_where(
        company_id, type_, search, department_id, status, date_from, date_to
    )

    if limit:
        params.append(limit)
    if limit and offset:
        params.append(offset)

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
            e.role             AS role,
            e.email            AS email,
            e.phone            AS phone,
            e.photo            AS photo,
            d.name             AS department_name,
            d.id               AS department_id,
            br.hotel_name      AS hotel_name,
            br.tenant_schema   AS tenant_schema,
            br.hotel_property_id AS hotel_property_id,
            te.pms_booking_id  AS pms_booking_id,
            hbr.room_name      AS room_name,
            hbr.price_per_night AS price_per_night,
            hbr.total_price    AS total_price
        FROM {B2B_TRIP_EMPLOYEE_TABLE} te
        JOIN {B2B_BUSINESS_TRIP_TABLE} t ON t.id = te.trip_id
        JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = te.employee_id
        LEFT JOIN {B2B_DEPARTMENT_TABLE} d ON d.id = e.department_id
        LEFT JOIN {B2B_HOTEL_BOOKING_REQUEST_TABLE} br ON br.trip_id = t.id
        LEFT JOIN {B2B_HOTEL_BOOKING_ROOM_TABLE} hbr ON hbr.pms_booking_id = te.pms_booking_id
        WHERE {where_sql}
        ORDER BY t.start_date ASC, e.full_name ASC
        {"LIMIT %s" if limit else ""}
        {"OFFSET %s" if (limit and offset) else ""}
        """,
        params,
    )


def count_active_trip_employees(
    company_id: int,
    type_: str = "all",
    search: str | None = None,
    department_id: int | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> int:
    """Total row count for the same filters as :func:`list_active_trip_employees`."""
    where_sql, params = _active_trip_employees_where(
        company_id, type_, search, department_id, status, date_from, date_to
    )
    row = fetch_one(
        f"""
        SELECT COUNT(*) AS cnt
        FROM {B2B_TRIP_EMPLOYEE_TABLE} te
        JOIN {B2B_BUSINESS_TRIP_TABLE} t ON t.id = te.trip_id
        JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = te.employee_id
        LEFT JOIN {B2B_DEPARTMENT_TABLE} d ON d.id = e.department_id
        WHERE {where_sql}
        """,
        params,
    )
    return (row or {}).get("cnt") or 0


# ─── Dashboard summary ───────────────────────────────────────────────────────

def get_dashboard_summary(company_id: int) -> dict[str, Any]:
    """Return the 4 top-line dashboard numbers for a company:

    - ``monthly_limit``          – owner-set company-wide budget limit
      (the ``all`` rule in b2b_travel_policy_rule; falls back to the
      legacy b2b_travel_policy.monthly_budget column if no rule exists)
    - ``spent_this_month``       – sum of confirmed hotel-booking room
      totals (the actual money spent) reviewed this calendar month
    - ``active_employees``       – distinct employees currently on a trip or with an upcoming one
    - ``pending_limit_requests`` – count of budget-requests awaiting review
    """
    now = timezone.now()
    year, month = now.year, now.month

    policy = get_or_create_travel_policy(company_id)
    global_rule = fetch_one(
        f"""
        SELECT r.budget_limit
        FROM {B2B_TRAVEL_POLICY_RULE_TABLE} r
        WHERE r.policy_id = %s AND r.applies_to = 'all'
        ORDER BY r.id DESC
        LIMIT 1
        """,
        [policy.get("id")],
    ) or {}
    monthly_limit = global_rule.get("budget_limit") or policy.get("monthly_budget") or Decimal("0")

    spent_row = fetch_one(
        f"""
        SELECT COALESCE(SUM(room.total_price), 0) AS spent
        FROM {B2B_HOTEL_BOOKING_ROOM_TABLE} room
        JOIN {B2B_HOTEL_BOOKING_REQUEST_TABLE} req ON req.id = room.booking_request_id
        WHERE req.company_id = %s AND req.status = '{HotelBookingRequestStatus.CONFIRMED}'
          AND EXTRACT(YEAR FROM COALESCE(req.reviewed_at, req.created_at)) = %s
          AND EXTRACT(MONTH FROM COALESCE(req.reviewed_at, req.created_at)) = %s
        """,
        [company_id, year, month],
    ) or {}

    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
    prev_spent_row = fetch_one(
        f"""
        SELECT COALESCE(SUM(room.total_price), 0) AS spent
        FROM {B2B_HOTEL_BOOKING_ROOM_TABLE} room
        JOIN {B2B_HOTEL_BOOKING_REQUEST_TABLE} req ON req.id = room.booking_request_id
        WHERE req.company_id = %s AND req.status = '{HotelBookingRequestStatus.CONFIRMED}'
          AND EXTRACT(YEAR FROM COALESCE(req.reviewed_at, req.created_at)) = %s
          AND EXTRACT(MONTH FROM COALESCE(req.reviewed_at, req.created_at)) = %s
        """,
        [company_id, prev_year, prev_month],
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

    # `trip_id` is optional on a budget request — most are raised for an
    # employee or a department with no trip attached. Inner-joining the trip
    # dropped exactly those, so the card read 0 while the review queue was
    # full. Scope the company the same way `list_budget_requests` does.
    pending_requests_row = fetch_one(
        f"""
        SELECT COUNT(*) AS cnt
        FROM {B2B_BUDGET_REQUEST_TABLE} br
        LEFT JOIN {B2B_BUSINESS_TRIP_TABLE} t ON t.id = br.trip_id
        LEFT JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = br.employee_id
        LEFT JOIN {B2B_DEPARTMENT_TABLE} d ON d.id = br.department_id
        WHERE COALESCE(t.company_id, e.company_id, d.company_id) = %s
          AND br.status = 'pending'
        """,
        [company_id],
    ) or {}

    spent_this_month = spent_row.get("spent") or Decimal("0")
    prev_spent = prev_spent_row.get("spent") or Decimal("0")
    if prev_spent > 0:
        change_percent = float((spent_this_month - prev_spent) / prev_spent * 100)
    else:
        change_percent = 100.0 if spent_this_month > 0 else 0.0

    return {
        "monthly_limit": monthly_limit,
        "spent_this_month": spent_this_month,
        "active_employees": active_employees_row.get("cnt") or 0,
        "pending_limit_requests": pending_requests_row.get("cnt") or 0,
        "change_percent": round(change_percent, 1),
    }


def get_remaining_monthly_budget(company_id: int) -> Decimal:
    """Company-wide budget left for the current calendar month.

    Mirrors ``get_dashboard_summary``'s monthly_limit/spent_this_month
    calculation so booking creation can enforce, server-side, the same
    number the dashboard and the booking UI already show the user — the
    UI's own check is client-side only and can be bypassed by calling the
    API directly.
    """
    now = timezone.now()
    year, month = now.year, now.month

    policy = get_or_create_travel_policy(company_id)
    global_rule = fetch_one(
        f"""
        SELECT r.budget_limit
        FROM {B2B_TRAVEL_POLICY_RULE_TABLE} r
        WHERE r.policy_id = %s AND r.applies_to = 'all'
        ORDER BY r.id DESC
        LIMIT 1
        """,
        [policy.get("id")],
    ) or {}
    monthly_limit = global_rule.get("budget_limit") or policy.get("monthly_budget") or Decimal("0")

    spent_row = fetch_one(
        f"""
        SELECT COALESCE(SUM(room.total_price), 0) AS spent
        FROM {B2B_HOTEL_BOOKING_ROOM_TABLE} room
        JOIN {B2B_HOTEL_BOOKING_REQUEST_TABLE} req ON req.id = room.booking_request_id
        WHERE req.company_id = %s AND req.status = '{HotelBookingRequestStatus.CONFIRMED}'
          AND EXTRACT(YEAR FROM COALESCE(req.reviewed_at, req.created_at)) = %s
          AND EXTRACT(MONTH FROM COALESCE(req.reviewed_at, req.created_at)) = %s
        """,
        [company_id, year, month],
    ) or {}
    spent_this_month = spent_row.get("spent") or Decimal("0")

    if monthly_limit <= 0:
        # No limit configured for this company — unlimited, nothing to enforce.
        return Decimal("Infinity")
    return monthly_limit - spent_this_month


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
            e.photo           AS photo,
            d.id              AS department_id,
            d.name            AS department_name,
            COUNT(DISTINCT te.trip_id) AS trip_count
        FROM {B2B_EMPLOYEE_TABLE} e
        JOIN {B2B_TRIP_EMPLOYEE_TABLE} te ON te.employee_id = e.id
        JOIN {B2B_BUSINESS_TRIP_TABLE} t ON t.id = te.trip_id AND t.company_id = %s
        LEFT JOIN {B2B_DEPARTMENT_TABLE} d ON d.id = e.department_id
        WHERE e.company_id = %s AND e.is_active = TRUE
        GROUP BY e.id, e.full_name, e.position, e.email, e.phone, e.photo, d.id, d.name
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
            MAX(br.hotel_guid::text) AS hotel_guid,
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


def _filter_verified_hotels(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop hotels that haven't passed verification. Each hotel's
    ``pms_property`` row lives in its own tenant schema, so this is a
    cross-schema ``UNION ALL`` lookup (same pattern as
    ``_enrich_with_pms_vouchers`` in views.py), keyed by
    ``(tenant_schema, hotel_property_id)``. A hotel missing from
    ``pms_property`` (deleted/unknown) is treated as unverified."""
    by_schema: dict[str, set[int]] = {}
    for row in rows:
        schema = _safe_schema_name(row.get("tenant_schema"))
        property_id = row.get("hotel_property_id")
        if schema and property_id:
            by_schema.setdefault(schema, set()).add(property_id)

    if not by_schema:
        return []

    parts: list[str] = []
    params: list[Any] = []
    for schema, property_ids in by_schema.items():
        placeholders = ", ".join(["%s"] * len(property_ids))
        parts.append(
            f"SELECT %s AS _schema, id, is_verified "
            f"FROM {schema}.pms_property "
            f"WHERE id IN ({placeholders})"
        )
        params.append(schema)
        params.extend(property_ids)

    try:
        verified_map = {
            (r["_schema"], r["id"]): bool(r["is_verified"])
            for r in fetch_all(" UNION ALL ".join(parts), params)
        }
    except Exception:
        verified_map = {}

    return [
        row
        for row in rows
        if verified_map.get((_safe_schema_name(row.get("tenant_schema")), row.get("hotel_property_id")))
    ]


def get_hotel_monthly_summary(company_id: int, year: int, month: int, limit: int = 5) -> dict[str, Any]:
    """Money spent on hotels this calendar month, plus the top *limit*
    verified hotels booked this month ordered by ``booking_count`` DESC.
    Only ``confirmed`` bookings count as actual spend."""
    spend_row = fetch_one(
        f"""
        SELECT COALESCE(SUM(room.total_price), 0) AS spend
        FROM {B2B_HOTEL_BOOKING_REQUEST_TABLE} br
        LEFT JOIN {B2B_HOTEL_BOOKING_ROOM_TABLE} room ON room.booking_request_id = br.id
        WHERE br.company_id = %s AND br.status = 'confirmed'
          AND EXTRACT(YEAR FROM br.created_at) = %s AND EXTRACT(MONTH FROM br.created_at) = %s
        """,
        [company_id, year, month],
    ) or {}

    # Unfiltered candidates, ranked - verification is applied afterwards, so
    # we can't cap this query at `limit` without risking fewer than `limit`
    # verified hotels surviving the filter.
    candidates = fetch_all(
        f"""
        SELECT
            br.tenant_schema,
            br.hotel_property_id,
            MAX(br.hotel_name) AS hotel_name,
            MAX(br.hotel_guid::text) AS hotel_guid,
            COUNT(DISTINCT br.id) AS booking_count,
            COALESCE(SUM(room.total_price), 0) AS total_spend
        FROM {B2B_HOTEL_BOOKING_REQUEST_TABLE} br
        LEFT JOIN {B2B_HOTEL_BOOKING_ROOM_TABLE} room ON room.booking_request_id = br.id
        WHERE br.company_id = %s AND br.status = 'confirmed'
          AND EXTRACT(YEAR FROM br.created_at) = %s AND EXTRACT(MONTH FROM br.created_at) = %s
        GROUP BY br.tenant_schema, br.hotel_property_id
        ORDER BY booking_count DESC, MAX(br.hotel_name) ASC
        """,
        [company_id, year, month],
    )

    top_hotels = _filter_verified_hotels(candidates)[:limit]

    return {
        "month_spend": str(spend_row.get("spend") or "0"),
        "top_hotels": top_hotels,
    }


# ─── Trip status summary (this month) ──────────────────────────────────────

def get_trip_status_summary(company_id: int, year: int, month: int) -> dict[str, int]:
    """For trips whose ``start_date`` falls in the given calendar month,
    count the distinct employees assigned per trip status:

    - ``active``    – employees currently away on a trip (komandirovkaga ketgan)
    - ``pending``   – employees with a trip date set, awaiting departure
    - ``completed`` – employees who went and came back
    - ``cancelled`` – employees whose trip/booking was cancelled
    """
    rows = fetch_all(
        f"""
        SELECT t.status AS status, COUNT(DISTINCT te.employee_id) AS cnt
        FROM {B2B_BUSINESS_TRIP_TABLE} t
        JOIN {B2B_TRIP_EMPLOYEE_TABLE} te ON te.trip_id = t.id
        WHERE t.company_id = %s
          AND EXTRACT(YEAR FROM t.start_date) = %s AND EXTRACT(MONTH FROM t.start_date) = %s
          AND t.status IN ('active', 'pending', 'completed', 'cancelled')
        GROUP BY t.status
        """,
        [company_id, year, month],
    )
    counts = {row["status"]: row["cnt"] for row in rows}
    return {
        "active": counts.get("active", 0),
        "pending": counts.get("pending", 0),
        "completed": counts.get("completed", 0),
        "cancelled": counts.get("cancelled", 0),
    }


# ─── Department monthly spending ────────────────────────────────────────────

def get_department_monthly_spending(
    company_id: int, year: int, month: int
) -> list[dict[str, Any]]:
    """For every department of *company_id* return the totals for the given
    calendar month.

    ``budget_limit`` = the department's own assigned limit (the
    ``department`` rule in b2b_travel_policy_rule for that department).

    ``month_spend`` = sum of confirmed hotel-booking room totals (the
    actual money spent) for the department's employees, whose
    ``reviewed_at`` (or, when NULL, ``created_at``) falls inside the month.

    ``month_trips`` = count of distinct trips whose ``start_date`` falls inside
    the month for any employee of that department.

    ``prev_month_spend`` = same as ``month_spend`` but for the preceding
    calendar month, used to derive the month-over-month ``change_percent``.
    """
    prev_year, prev_month = (year - 1, 12) if month == 1 else (year, month - 1)
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
            (
                SELECT r.budget_limit
                FROM {B2B_TRAVEL_POLICY_RULE_TABLE} r
                WHERE r.target_id = d.id AND r.applies_to = 'department'
                ORDER BY r.id DESC
                LIMIT 1
            ) AS budget_limit,
            (
                SELECT COALESCE(SUM(room.total_price), 0)
                FROM {B2B_HOTEL_BOOKING_ROOM_TABLE} room
                JOIN {B2B_HOTEL_BOOKING_ROOM_EMPLOYEE_TABLE} room_emp ON room_emp.booking_room_id = room.id
                JOIN {B2B_EMPLOYEE_TABLE} re ON re.id = room_emp.employee_id
                JOIN {B2B_HOTEL_BOOKING_REQUEST_TABLE} req ON req.id = room.booking_request_id
                WHERE re.department_id = d.id
                  AND req.status = '{HotelBookingRequestStatus.CONFIRMED}'
                  AND EXTRACT(YEAR FROM COALESCE(req.reviewed_at, req.created_at)) = %s
                  AND EXTRACT(MONTH FROM COALESCE(req.reviewed_at, req.created_at)) = %s
            ) AS month_spend,
            (
                SELECT COALESCE(SUM(room.total_price), 0)
                FROM {B2B_HOTEL_BOOKING_ROOM_TABLE} room
                JOIN {B2B_HOTEL_BOOKING_ROOM_EMPLOYEE_TABLE} room_emp ON room_emp.booking_room_id = room.id
                JOIN {B2B_EMPLOYEE_TABLE} re ON re.id = room_emp.employee_id
                JOIN {B2B_HOTEL_BOOKING_REQUEST_TABLE} req ON req.id = room.booking_request_id
                WHERE re.department_id = d.id
                  AND req.status = '{HotelBookingRequestStatus.CONFIRMED}'
                  AND EXTRACT(YEAR FROM COALESCE(req.reviewed_at, req.created_at)) = %s
                  AND EXTRACT(MONTH FROM COALESCE(req.reviewed_at, req.created_at)) = %s
            ) AS prev_month_spend
        FROM {B2B_DEPARTMENT_TABLE} d
        LEFT JOIN {B2B_EMPLOYEE_TABLE} e ON e.department_id = d.id
        LEFT JOIN {B2B_TRIP_EMPLOYEE_TABLE} te ON te.employee_id = e.id
        LEFT JOIN {B2B_BUSINESS_TRIP_TABLE} t ON t.id = te.trip_id
        WHERE d.company_id = %s
        GROUP BY d.id, d.name
        ORDER BY d.name ASC
        """,
        [year, month, year, month, prev_year, prev_month, company_id],
    )


# ─── Dashboard notifications ────────────────────────────────────────────────

def list_dashboard_notifications(company_id: int, limit: int = 8) -> list[dict[str, Any]]:
    """Build the dashboard notification feed from existing tables (no
    dedicated notification model exists yet). Four event types, matching the
    dashboard's notification card:

    - ``limit_exceeded``     – an employee's approved spend this month exceeds
                                their ``individual_limit``.
    - ``budget_threshold``   – a department has used >= 90% of its budget
                                limit (``b2b_travel_policy_rule``).
    - ``trip_approved``      – a budget request tied to a trip was approved.
    - ``documents_uploaded`` – an employee has a passport scan on file.
    - ``trip_started``       – an employee was assigned to a business trip.
    - ``trip_completed``     – a business trip's status became ``completed``.
    """
    now = timezone.now()
    year, month = now.year, now.month

    limit_exceeded_rows = fetch_all(
        f"""
        SELECT
            e.full_name AS employee_name,
            SUM(br.amount) AS spent,
            e.individual_limit AS limit_amount,
            MAX(COALESCE(br.reviewed_at, br.created_at)) AS occurred_at
        FROM {B2B_EMPLOYEE_TABLE} e
        JOIN {B2B_BUDGET_REQUEST_TABLE} br ON br.employee_id = e.id
        WHERE e.company_id = %s
          AND e.individual_limit IS NOT NULL AND e.individual_limit > 0
          AND br.status = 'approved'
          AND EXTRACT(YEAR FROM COALESCE(br.reviewed_at, br.created_at)) = %s
          AND EXTRACT(MONTH FROM COALESCE(br.reviewed_at, br.created_at)) = %s
        GROUP BY e.id, e.full_name, e.individual_limit
        HAVING SUM(br.amount) > e.individual_limit
        ORDER BY occurred_at DESC
        LIMIT %s
        """,
        [company_id, year, month, limit],
    )

    budget_threshold_rows = fetch_all(
        f"""
        SELECT department_name, budget_limit, used_amount, occurred_at FROM (
            SELECT
                d.name AS department_name,
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
                ) AS used_amount,
                (
                    SELECT MAX(COALESCE(br.reviewed_at, br.created_at))
                    FROM {B2B_BUDGET_REQUEST_TABLE} br
                    JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = br.employee_id
                    WHERE e.department_id = d.id AND br.status = 'approved'
                ) AS occurred_at
            FROM {B2B_DEPARTMENT_TABLE} d
            WHERE d.company_id = %s
        ) sub
        WHERE budget_limit IS NOT NULL AND budget_limit > 0
          AND used_amount >= budget_limit * 0.9
        ORDER BY occurred_at DESC NULLS LAST
        LIMIT %s
        """,
        [company_id, limit],
    )

    trip_approved_rows = fetch_all(
        f"""
        SELECT
            e.full_name AS employee_name,
            t.destination_city AS destination_city,
            t.name AS trip_name,
            br.reviewed_at AS occurred_at
        FROM {B2B_BUDGET_REQUEST_TABLE} br
        JOIN {B2B_BUSINESS_TRIP_TABLE} t ON t.id = br.trip_id
        LEFT JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = br.employee_id
        WHERE t.company_id = %s AND br.status = 'approved' AND br.reviewed_at IS NOT NULL
        ORDER BY br.reviewed_at DESC
        LIMIT %s
        """,
        [company_id, limit],
    )

    documents_uploaded_rows = fetch_all(
        f"""
        SELECT full_name AS employee_name, created_at AS occurred_at
        FROM {B2B_EMPLOYEE_TABLE}
        WHERE company_id = %s AND is_active = TRUE
          AND (passport_upload_front IS NOT NULL OR passport_upload_back IS NOT NULL)
        ORDER BY created_at DESC
        LIMIT %s
        """,
        [company_id, limit],
    )

    trip_started_rows = fetch_all(
        f"""
        SELECT
            e.full_name AS employee_name,
            t.destination_city AS destination_city,
            t.name AS trip_name,
            te.created_at AS occurred_at
        FROM {B2B_TRIP_EMPLOYEE_TABLE} te
        JOIN {B2B_BUSINESS_TRIP_TABLE} t ON t.id = te.trip_id
        LEFT JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = te.employee_id
        WHERE t.company_id = %s AND te.status != 'cancelled'
        ORDER BY te.created_at DESC
        LIMIT %s
        """,
        [company_id, limit],
    )

    trip_completed_rows = fetch_all(
        f"""
        SELECT
            e.full_name AS employee_name,
            t.destination_city AS destination_city,
            t.name AS trip_name,
            t.updated_at AS occurred_at
        FROM {B2B_BUSINESS_TRIP_TABLE} t
        JOIN {B2B_TRIP_EMPLOYEE_TABLE} te ON te.trip_id = t.id
        LEFT JOIN {B2B_EMPLOYEE_TABLE} e ON e.id = te.employee_id
        WHERE t.company_id = %s AND t.status = 'completed'
        ORDER BY t.updated_at DESC
        LIMIT %s
        """,
        [company_id, limit],
    )

    notifications: list[dict[str, Any]] = []

    for row in limit_exceeded_rows:
        excess = (row["spent"] or Decimal("0")) - (row["limit_amount"] or Decimal("0"))
        amount_text = f"{excess:,.0f}".replace(",", " ")
        notifications.append({
            "type": "limit_exceeded",
            "message": f"{row['employee_name']} превысил лимит на {amount_text} UZS",
            "occurred_at": row["occurred_at"],
        })

    for row in budget_threshold_rows:
        pct = int((row["used_amount"] / row["budget_limit"]) * 100)
        notifications.append({
            "type": "budget_threshold",
            "message": f"Бюджет «{row['department_name']}» - {pct}% месячного лимита",
            "occurred_at": row["occurred_at"] or now,
        })

    for row in trip_approved_rows:
        destination = row["destination_city"] or row["trip_name"] or "поездка"
        employee_name = row["employee_name"] or "Сотрудник"
        notifications.append({
            "type": "trip_approved",
            "message": f"{employee_name} поездка в {destination} одобрена",
            "occurred_at": row["occurred_at"],
        })

    for row in documents_uploaded_rows:
        notifications.append({
            "type": "documents_uploaded",
            "message": f"{row['employee_name']} документы загружены",
            "occurred_at": row["occurred_at"],
        })

    for row in trip_started_rows:
        destination = row["destination_city"] or row["trip_name"] or "поездка"
        employee_name = row["employee_name"] or "Сотрудник"
        notifications.append({
            "type": "trip_started",
            "message": f"{employee_name} отправлен(а) в командировку в {destination}",
            "occurred_at": row["occurred_at"],
        })

    for row in trip_completed_rows:
        destination = row["destination_city"] or row["trip_name"] or "поездка"
        employee_name = row["employee_name"] or "Сотрудник"
        notifications.append({
            "type": "trip_completed",
            "message": f"{employee_name} вернулся(лась) из командировки в {destination}",
            "occurred_at": row["occurred_at"],
        })

    notifications.sort(key=lambda n: n["occurred_at"], reverse=True)
    return notifications[:limit]
