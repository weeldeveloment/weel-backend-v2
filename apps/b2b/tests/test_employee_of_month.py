"""Employee of the month: owner-only picking, and the stats it's picked from.

Two layers, like test_workspace_permissions.py and test_hotel_booking.py:
permission gating runs against a mocked repository (fast, no database); the
stats math and the upsert run against a live PostgreSQL database, because
they depend on SQL this project has no ORM model for.

    WEEL_INTEGRATION_DB=1 \\
    DJANGO_SETTINGS_MODULE=core.settings \\
    DB_NAME=weel_test DB_HOST=127.0.0.1 \\
    pytest apps/b2b/tests/test_employee_of_month.py

Point DB_NAME at a throwaway database — never at the one serving traffic.
"""
from __future__ import annotations

import os
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.conf import settings
from django.utils import timezone

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.workspace.authentication import WorkspaceUser
from apps.b2b.workspace.views import (
    WorkspaceEmployeeMonthlyStatsView,
    WorkspaceEmployeeOfMonthView,
)

COMPANY_ID = 55
OWNER_ID = 1
EMPLOYEE_ID = 2

factory = APIRequestFactory()


def _user(role: str, employee_id: int) -> WorkspaceUser:
    return WorkspaceUser({
        "id": employee_id,
        "company_id": COMPANY_ID,
        "role": role,
        "full_name": "Test Person",
        "phone": "+998900000000",
    })


OWNER = _user("owner", OWNER_ID)
EMPLOYEE = _user("employee", EMPLOYEE_ID)


def _call(view_class, request, user, **kwargs):
    force_authenticate(request, user=user)
    return view_class.as_view()(request, **kwargs)


# ─── Permission gating (mocked repository, no database) ────────────────────

class TestStatsPermission:
    def test_employee_cannot_see_monthly_stats(self):
        request = factory.get("/employee-of-month/stats/")
        response = _call(WorkspaceEmployeeMonthlyStatsView, request, EMPLOYEE)
        assert response.status_code == 403

    def test_owner_can_see_monthly_stats(self):
        request = factory.get("/employee-of-month/stats/")
        with patch("apps.b2b.workspace.views.repo.monthly_employee_stats", return_value=[]):
            response = _call(WorkspaceEmployeeMonthlyStatsView, request, OWNER)
        assert response.status_code == 200


class TestSelectionPermission:
    def test_employee_cannot_pick_the_winner(self):
        request = factory.post("/employee-of-month/", {"employee_id": OWNER_ID}, format="json")
        response = _call(WorkspaceEmployeeOfMonthView, request, EMPLOYEE)
        assert response.status_code == 403

    def test_owner_can_pick_the_winner(self):
        request = factory.post("/employee-of-month/", {"employee_id": EMPLOYEE_ID}, format="json")
        winner = {
            "employee_id": EMPLOYEE_ID, "full_name": "Test Person", "photo": None,
            "year": 2026, "month": 1, "selected_at": timezone.now(),
        }
        with (
            patch(
                "apps.b2b.workspace.views.repo.employee_ids_in_company",
                return_value={EMPLOYEE_ID},
            ),
            patch("apps.b2b.workspace.views.repo.set_employee_of_month", return_value=winner),
        ):
            response = _call(WorkspaceEmployeeOfMonthView, request, OWNER)
        assert response.status_code == 200
        assert response.data["employee_id"] == EMPLOYEE_ID

    def test_owner_cannot_pick_someone_outside_the_company(self):
        request = factory.post("/employee-of-month/", {"employee_id": 999}, format="json")
        with patch("apps.b2b.workspace.views.repo.employee_ids_in_company", return_value=set()):
            response = _call(WorkspaceEmployeeOfMonthView, request, OWNER)
        assert response.status_code == 400

    def test_anyone_can_read_the_current_pick(self):
        request = factory.get("/employee-of-month/")
        with patch("apps.b2b.workspace.views.repo.get_employee_of_month", return_value=None):
            response = _call(WorkspaceEmployeeOfMonthView, request, EMPLOYEE)
        assert response.status_code == 204


# ─── DB access markers for the classes below only ───────────────────────────
#
# A module-level `pytestmark` would also skip TestStatsPermission and
# TestSelectionPermission above, which are meant to run in the default sqlite
# suite same as test_workspace_permissions.py. Applied per-class instead.

_needs_db = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("WEEL_INTEGRATION_DB") != "1",
        reason=(
            "Needs a live PostgreSQL database with the raw-SQL schema. "
            "Set WEEL_INTEGRATION_DB=1 and point DB_NAME at a throwaway database."
        ),
    ),
    pytest.mark.django_db(transaction=True),
]


@pytest.fixture(scope="module")
def company(django_db_setup, django_db_blocker):
    from shared.raw.db import execute, fetch_one

    with django_db_blocker.unblock():
        execute(
            """
            INSERT INTO b2b_company (name, is_active, created_at, updated_at)
            VALUES ('Employee of the Month Test Co', TRUE, NOW(), NOW())
            """
        )
        return fetch_one(
            "SELECT id FROM b2b_company WHERE name = 'Employee of the Month Test Co' "
            "ORDER BY id DESC LIMIT 1"
        )


@pytest.fixture(scope="module")
def employees(company, django_db_blocker):
    from shared.raw.db import execute, fetch_all

    with django_db_blocker.unblock():
        for name in ("Fast Fadli", "Slow Salim", "Idle Iroda"):
            execute(
                """
                INSERT INTO b2b_employee (company_id, full_name, role, is_active, created_at, updated_at)
                VALUES (%s, %s, 'employee', TRUE, NOW(), NOW())
                """,
                [company["id"], name],
            )
        return fetch_all(
            "SELECT id, full_name FROM b2b_employee WHERE company_id = %s ORDER BY id ASC",
            [company["id"]],
        )


def _create_task(company_id: int, assignee_id: int, *, due_in_days: int | None, completed_offset_days: int | None):
    """A done or open task assigned to one employee.

    ``completed_offset_days`` is measured from the due date (or from now, if
    there is none) — negative finishes early, positive finishes late.
    """
    from shared.raw.db import execute, fetch_one

    now = timezone.now()
    due_date = now + timedelta(days=due_in_days) if due_in_days is not None else None
    completed_at = None
    status = "todo"
    if completed_offset_days is not None:
        status = "done"
        anchor = due_date or now
        completed_at = anchor + timedelta(days=completed_offset_days)

    task = fetch_one(
        """
        INSERT INTO b2b_task (company_id, title, status, priority, due_date,
            completed_at, author_id, created_at, updated_at)
        VALUES (%s, 'Test task', %s, 'medium', %s, %s, %s, NOW(), NOW())
        RETURNING id
        """,
        [company_id, status, due_date, completed_at, assignee_id],
    )
    execute(
        "INSERT INTO b2b_task_assignee (task_id, employee_id, created_at) VALUES (%s, %s, NOW())",
        [task["id"], assignee_id],
    )
    return task["id"]


class TestMonthlyStats:
    pytestmark = _needs_db

    def test_completed_count_and_on_time_rate(self, company, employees, django_db_blocker):
        from apps.b2b.workspace.repository import monthly_employee_stats

        fast, slow, idle = employees[0], employees[1], employees[2]

        with django_db_blocker.unblock():
            # Fast: two tasks finished on time.
            _create_task(company["id"], fast["id"], due_in_days=1, completed_offset_days=-1)
            _create_task(company["id"], fast["id"], due_in_days=2, completed_offset_days=0)
            # Slow: one on time, one late.
            _create_task(company["id"], slow["id"], due_in_days=1, completed_offset_days=-1)
            _create_task(company["id"], slow["id"], due_in_days=1, completed_offset_days=2)
            # A task with no due date at all — must not count toward due/on-time.
            _create_task(company["id"], slow["id"], due_in_days=None, completed_offset_days=0)
            # An open task must not be counted as completed.
            _create_task(company["id"], idle["id"], due_in_days=1, completed_offset_days=None)

            now = timezone.now()
            stats = monthly_employee_stats(company["id"], now.year, now.month)

        by_id = {row["employee_id"]: row for row in stats}

        assert by_id[fast["id"]]["completed_count"] == 2
        assert by_id[fast["id"]]["due_count"] == 2
        assert by_id[fast["id"]]["on_time_count"] == 2

        assert by_id[slow["id"]]["completed_count"] == 3
        assert by_id[slow["id"]]["due_count"] == 2
        assert by_id[slow["id"]]["on_time_count"] == 1

        assert by_id[idle["id"]]["completed_count"] == 0

        # Sorted by completed_count desc: the query ranks by volume, not by
        # on-time rate — the owner reads both numbers and judges quality
        # themselves, rather than the query encoding a "best" that would be
        # undefined for anyone with zero due-date tasks.
        assert stats[0]["employee_id"] == slow["id"]


class TestSelection:
    pytestmark = _needs_db

    def test_set_then_get_round_trip(self, company, employees, django_db_blocker):
        from apps.b2b.workspace.repository import get_employee_of_month, set_employee_of_month

        winner = employees[0]
        with django_db_blocker.unblock():
            now = timezone.now()
            set_employee_of_month(
                company_id=company["id"], year=now.year, month=now.month,
                employee_id=winner["id"], selected_by_id=winner["id"],
            )
            fetched = get_employee_of_month(company["id"], now.year, now.month)

        assert fetched is not None
        assert fetched["employee_id"] == winner["id"]

    def test_picking_again_replaces_the_winner(self, company, employees, django_db_blocker):
        from apps.b2b.workspace.repository import get_employee_of_month, set_employee_of_month

        first, second = employees[0], employees[1]
        with django_db_blocker.unblock():
            now = timezone.now()
            set_employee_of_month(
                company_id=company["id"], year=now.year, month=now.month,
                employee_id=first["id"], selected_by_id=first["id"],
            )
            set_employee_of_month(
                company_id=company["id"], year=now.year, month=now.month,
                employee_id=second["id"], selected_by_id=second["id"],
            )
            fetched = get_employee_of_month(company["id"], now.year, now.month)

        # One row per company per month — the second pick overwrites, not adds.
        assert fetched["employee_id"] == second["id"]

    def test_no_pick_yet_returns_none(self, company, django_db_blocker):
        from apps.b2b.workspace.repository import get_employee_of_month

        with django_db_blocker.unblock():
            fetched = get_employee_of_month(company["id"], 1999, 1)

        assert fetched is None
