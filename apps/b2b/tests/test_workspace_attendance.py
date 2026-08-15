"""The daily roll call.

Attendance is a record about people, so the failures here are not cosmetic:
counting someone absent who was never marked, or letting an employee mark a
colleague, both produce a number a manager will act on.
"""
from datetime import date, datetime, timezone as dt_timezone
from unittest.mock import patch

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.workspace.authentication import WorkspaceUser
from apps.b2b.workspace.views import (
    WorkspaceAttendanceCheckInView,
    WorkspaceAttendanceMarkView,
    WorkspaceAttendanceView,
)

factory = APIRequestFactory()

EMPLOYEE = WorkspaceUser({
    "id": 7, "company_id": 55, "role": "employee",
    "full_name": "Xodim", "phone": "+998900000001",
})
OWNER = WorkspaceUser({
    "id": 9, "company_id": 55, "role": "owner",
    "full_name": "Rahbar", "phone": "+998900000002",
})

ARRIVED = datetime(2026, 8, 15, 8, 45, tzinfo=dt_timezone.utc)


def entry(employee_id, name, status, **extra):
    row = {
        "employee_id": employee_id,
        "full_name": name,
        "position": "Dasturchi",
        "department_name": "IT",
        "status": status,
        "checked_in_at": None,
        "reason": None,
        "marked_by_id": None,
    }
    row.update(extra)
    return row


ROSTER = [
    entry(7, "Xodim", "present", checked_in_at=ARRIVED),
    entry(8, "Sardor", "absent", reason="Kasal"),
    entry(9, "Rahbar", "late", checked_in_at=ARRIVED),
    entry(10, "Madina", None),  # nobody has accounted for them yet
]


class TestRollCall:
    def _get(self, user=EMPLOYEE, rows=ROSTER, query=""):
        request = factory.get(f"/attendance/{query}")
        force_authenticate(request, user=user)
        with patch("apps.b2b.workspace.views.repo") as repo:
            repo.attendance_for_date.return_value = rows
            return WorkspaceAttendanceView.as_view()(request), repo

    def test_late_and_remote_count_as_present(self):
        # The banner says "N ta ishda". Someone who arrived late is at work.
        response, _ = self._get()
        assert response.data["present"] == 2
        assert response.data["absent"] == 1

    def test_unmarked_is_its_own_state(self):
        # Not counted absent: nobody has said they are missing, only that
        # nobody has looked. Reporting them absent invents a fact.
        response, _ = self._get()
        assert response.data["unmarked"] == 1

    def test_the_counts_add_up_to_the_roster(self):
        response, _ = self._get()
        total = (
            response.data["present"]
            + response.data["absent"]
            + response.data["unmarked"]
        )
        assert total == len(ROSTER)

    def test_the_caller_learns_their_own_status(self):
        # Drives whether the app offers a check-in button.
        response, _ = self._get(user=EMPLOYEE)
        assert response.data["my_status"] == "present"

    def test_someone_not_on_the_roster_has_no_status(self):
        response, _ = self._get(rows=[entry(8, "Sardor", "absent")])
        assert response.data["my_status"] is None

    def test_an_employee_may_read_the_roll_call(self):
        # It is on the chat home screen; the point is knowing who is around.
        response, _ = self._get(user=EMPLOYEE)
        assert response.status_code == 200

    def test_a_date_can_be_asked_for(self):
        _, repo = self._get(query="?date=2026-08-14")
        assert repo.attendance_for_date.call_args.args[1] == date(2026, 8, 14)

    def test_a_nonsense_date_falls_back_to_today_rather_than_500ing(self):
        _, repo = self._get(query="?date=not-a-date")
        assert isinstance(repo.attendance_for_date.call_args.args[1], date)


class TestCheckIn:
    def _post(self, existing=None):
        request = factory.post("/attendance/check-in/", {}, format="json")
        force_authenticate(request, user=EMPLOYEE)
        with patch("apps.b2b.workspace.views.repo") as repo:
            repo.attendance_row.return_value = existing
            repo.attendance_for_date.return_value = ROSTER
            response = WorkspaceAttendanceCheckInView.as_view()(request)
            return response, repo

    def test_checking_in_records_the_caller_as_present(self):
        response, repo = self._post()
        assert response.status_code == 200
        kwargs = repo.upsert_attendance.call_args.kwargs
        assert kwargs["employee_id"] == 7
        assert kwargs["status"] == "present"
        # Not recorded as marked by anyone: they did it themselves.
        assert kwargs["marked_by_id"] is None

    def test_checking_in_twice_keeps_the_first_arrival_time(self):
        # Otherwise tapping again at 17:00 rewrites this morning's arrival.
        _, repo = self._post(existing={"checked_in_at": ARRIVED})
        assert repo.upsert_attendance.call_args.kwargs["checked_in_at"] == ARRIVED

    def test_the_first_check_in_stamps_a_time(self):
        _, repo = self._post(existing=None)
        assert repo.upsert_attendance.call_args.kwargs["checked_in_at"] is not None


class TestMarking:
    def _post(self, user, body, in_company=True, existing=None):
        request = factory.post("/attendance/8/", body, format="json")
        force_authenticate(request, user=user)
        with patch("apps.b2b.workspace.views.repo") as repo:
            repo.employee_ids_in_company.return_value = {8} if in_company else set()
            repo.attendance_row.return_value = existing
            repo.attendance_for_date.return_value = ROSTER
            response = WorkspaceAttendanceMarkView.as_view()(request, employee_id=8)
            return response, repo

    def test_an_employee_cannot_mark_a_colleague(self):
        # The whole point of the capability: attendance is a record other
        # people are judged on.
        response, repo = self._post(EMPLOYEE, {"status": "absent"})
        assert response.status_code == 403
        repo.upsert_attendance.assert_not_called()

    def test_a_manager_can(self):
        response, repo = self._post(OWNER, {"status": "absent", "reason": "Kasal"})
        assert response.status_code == 200
        kwargs = repo.upsert_attendance.call_args.kwargs
        assert kwargs["status"] == "absent"
        assert kwargs["reason"] == "Kasal"
        # Recorded, so "who said I was absent" has an answer.
        assert kwargs["marked_by_id"] == 9

    def test_marking_someone_absent_clears_any_arrival_time(self):
        # A row corrected from present to absent must not keep a time that says
        # they arrived.
        _, repo = self._post(OWNER, {"status": "absent"}, existing={"checked_in_at": ARRIVED})
        assert repo.upsert_attendance.call_args.kwargs["checked_in_at"] is None

    def test_marking_present_does_not_overwrite_their_own_check_in(self):
        _, repo = self._post(OWNER, {"status": "present"}, existing={"checked_in_at": ARRIVED})
        assert repo.upsert_attendance.call_args.kwargs["checked_in_at"] == ARRIVED

    def test_another_company_s_employee_is_a_404(self):
        response, repo = self._post(OWNER, {"status": "absent"}, in_company=False)
        assert response.status_code == 404
        repo.upsert_attendance.assert_not_called()

    def test_an_unknown_status_is_refused(self):
        response, repo = self._post(OWNER, {"status": "on_the_moon"})
        assert response.status_code == 400
        repo.upsert_attendance.assert_not_called()

    def test_a_blank_reason_is_stored_as_nothing(self):
        # "" and null mean the same thing and should not both end up in the
        # column, or a filter on `reason IS NULL` misses half of them.
        _, repo = self._post(OWNER, {"status": "absent", "reason": "   "})
        assert repo.upsert_attendance.call_args.kwargs["reason"] is None
