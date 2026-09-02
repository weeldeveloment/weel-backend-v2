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
    WorkspaceAttendanceAbsenceView,
    WorkspaceAttendanceCheckInView,
    WorkspaceAttendanceCheckOutView,
    WorkspaceAttendanceLocationView,
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

    def test_the_caller_learns_back_the_reason_they_filed(self):
        # The app shows "Kelmadim: Kasal" rather than only knowing that the
        # day is closed.
        response, _ = self._get(
            rows=[entry(7, "Xodim", "absent", reason="Kasal")],
        )
        assert response.data["my_reason"] == "Kasal"

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
    def _post(self, existing=None, location=None, body=None):
        request = factory.post("/attendance/check-in/", body or {}, format="json")
        force_authenticate(request, user=EMPLOYEE)
        with patch("apps.b2b.workspace.views.repo") as repo:
            repo.attendance_row.return_value = existing
            repo.attendance_for_date.return_value = ROSTER
            # No geofence configured unless a test says otherwise — the
            # default company has never turned this on.
            repo.get_attendance_location.return_value = location
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

    def test_no_geofence_configured_needs_no_coordinates(self):
        response, repo = self._post(location=None)
        assert response.status_code == 200
        repo.upsert_attendance.assert_called_once()

    def test_a_disabled_geofence_needs_no_coordinates(self):
        response, repo = self._post(
            location={"is_enabled": False, "latitude": 41.3, "longitude": 69.2, "radius_meters": 200}
        )
        assert response.status_code == 200

    def test_an_enabled_geofence_refuses_a_check_in_with_no_location(self):
        response, repo = self._post(
            location={"is_enabled": True, "latitude": 41.3, "longitude": 69.2, "radius_meters": 200},
        )
        assert response.status_code == 400
        repo.upsert_attendance.assert_not_called()

    def test_inside_the_radius_checks_in(self):
        # ~11m north of the office point — well inside a 200m radius.
        response, repo = self._post(
            location={"is_enabled": True, "latitude": 41.3000, "longitude": 69.2000, "radius_meters": 200},
            body={"latitude": 41.3001, "longitude": 69.2000},
        )
        assert response.status_code == 200
        kwargs = repo.upsert_attendance.call_args.kwargs
        assert kwargs["check_in_latitude"] == 41.3001
        assert kwargs["check_in_longitude"] == 69.2000

    def test_outside_the_radius_is_refused(self):
        # ~1.1km away — outside a 200m radius.
        response, repo = self._post(
            location={"is_enabled": True, "latitude": 41.3000, "longitude": 69.2000, "radius_meters": 200},
            body={"latitude": 41.3100, "longitude": 69.2000},
        )
        assert response.status_code == 400
        assert response.data["code"] == "too_far_from_workplace"
        repo.upsert_attendance.assert_not_called()


class TestSelfAbsence:
    """The other half of the check-in button.

    Somebody the geofence turned away has no way to mark themselves present,
    so this is the only thing they can do with their day besides leave it
    unmarked — which reads as nobody having looked at them.
    """

    def _post(self, body=None, user=EMPLOYEE):
        request = factory.post("/attendance/absence/", body or {}, format="json")
        force_authenticate(request, user=user)
        with patch("apps.b2b.workspace.views.repo") as repo:
            repo.attendance_for_date.return_value = ROSTER
            response = WorkspaceAttendanceAbsenceView.as_view()(request)
            return response, repo

    def test_reporting_yourself_absent_records_the_reason(self):
        response, repo = self._post({"reason": "Kasal bo'lib qoldim"})
        assert response.status_code == 200
        kwargs = repo.upsert_attendance.call_args.kwargs
        assert kwargs["employee_id"] == 7
        assert kwargs["status"] == "absent"
        assert kwargs["reason"] == "Kasal bo'lib qoldim"

    def test_it_is_not_recorded_as_marked_by_a_manager(self):
        # `marked_by_id` is what answers "who said I was absent". Filling it in
        # with the caller's own id would make their own word look like a
        # manager's ruling.
        _, repo = self._post({"reason": "Yo'lda"})
        assert repo.upsert_attendance.call_args.kwargs["marked_by_id"] is None

    def test_an_absence_carries_no_arrival_time(self):
        _, repo = self._post({"reason": "Ta'tilda"})
        assert repo.upsert_attendance.call_args.kwargs["checked_in_at"] is None

    def test_a_reason_is_required(self):
        # Without one this is indistinguishable from never opening the app.
        response, repo = self._post({})
        assert response.status_code == 400
        repo.upsert_attendance.assert_not_called()

    def test_a_blank_reason_is_refused_rather_than_stored_empty(self):
        response, repo = self._post({"reason": "   "})
        assert response.status_code == 400
        repo.upsert_attendance.assert_not_called()

    def test_it_needs_no_capability(self):
        # An employee filing their own absence is the entire point; requiring
        # `can_manage_attendance` would leave them with no way to answer at all.
        response, _ = self._post({"reason": "Kasal"}, user=EMPLOYEE)
        assert response.status_code == 200

    def test_a_future_day_is_refused(self):
        response, repo = self._post(
            {"reason": "Kasal", "date": "2099-01-01"},
        )
        assert response.status_code == 400
        repo.upsert_attendance.assert_not_called()

    def test_the_day_comes_back_with_the_reason_on_it(self):
        response, _ = self._post({"reason": "Kasal"})
        assert response.status_code == 200
        assert "my_reason" in response.data


LEFT = datetime(2026, 8, 15, 18, 3, tzinfo=dt_timezone.utc)


class TestCheckOut:
    """"Ketdim" — the other end of the day.

    Unlike check-in it carries no geofence: leaving the area is the point of
    it. But it can only close a day that was opened — a departure with no
    matching arrival is not a state the roll call has.
    """

    def _post(self, existing={"status": "present", "checked_in_at": ARRIVED}, body=None):
        request = factory.post("/attendance/check-out/", body or {}, format="json")
        force_authenticate(request, user=EMPLOYEE)
        with patch("apps.b2b.workspace.views.repo") as repo:
            repo.attendance_row.return_value = existing
            repo.attendance_for_date.return_value = ROSTER
            response = WorkspaceAttendanceCheckOutView.as_view()(request)
            return response, repo

    def test_checking_out_stamps_a_departure_time(self):
        response, repo = self._post()
        assert response.status_code == 200
        assert repo.upsert_attendance.call_args.kwargs["checked_out_at"] is not None

    def test_it_keeps_the_arrival_time(self):
        # A check-out must not read as a fresh check-in.
        _, repo = self._post()
        assert repo.upsert_attendance.call_args.kwargs["checked_in_at"] == ARRIVED

    def test_checking_out_twice_keeps_the_first_departure_time(self):
        # Otherwise tapping again on the way past the door pushes "left at" later
        # than when they actually went.
        _, repo = self._post(
            existing={"status": "present", "checked_in_at": ARRIVED, "checked_out_at": LEFT},
        )
        assert repo.upsert_attendance.call_args.kwargs["checked_out_at"] == LEFT

    def test_you_cannot_check_out_of_a_day_you_never_checked_into(self):
        response, repo = self._post(existing=None)
        assert response.status_code == 400
        assert response.data["code"] == "not_checked_in"
        repo.upsert_attendance.assert_not_called()

    def test_an_absent_day_cannot_be_checked_out_of(self):
        response, repo = self._post(existing={"status": "absent", "reason": "Kasal"})
        assert response.status_code == 400
        repo.upsert_attendance.assert_not_called()

    def test_a_late_arrival_can_still_check_out(self):
        response, repo = self._post(
            existing={"status": "late", "checked_in_at": ARRIVED},
        )
        assert response.status_code == 200
        # The status it arrived with is kept, not flattened to "present".
        assert repo.upsert_attendance.call_args.kwargs["status"] == "late"

    def test_it_needs_no_capability(self):
        response, _ = self._post()
        assert response.status_code == 200

    def test_coordinates_are_stored_when_the_phone_sends_them(self):
        _, repo = self._post(body={"latitude": 41.31, "longitude": 69.24})
        kwargs = repo.upsert_attendance.call_args.kwargs
        assert kwargs["check_out_latitude"] == 41.31
        assert kwargs["check_out_longitude"] == 69.24

    def test_being_far_from_the_office_does_not_refuse_a_check_out(self):
        # No geofence is consulted at all — the repo is never asked for one.
        response, repo = self._post(body={"latitude": 42.0, "longitude": 70.0})
        assert response.status_code == 200
        repo.get_attendance_location.assert_not_called()

    def test_the_day_comes_back_with_the_departure_on_it(self):
        response, _ = self._post()
        assert response.status_code == 200
        assert "my_checked_out_at" in response.data


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


PERFORMER = WorkspaceUser({
    "id": 11, "company_id": 55, "role": "performer",
    "full_name": "Menejer", "phone": "+998900000003",
})


class TestLocation:
    def _get(self, user=EMPLOYEE, location=None):
        request = factory.get("/attendance/location/")
        force_authenticate(request, user=user)
        with patch("apps.b2b.workspace.views.repo") as repo:
            repo.get_attendance_location.return_value = location
            return WorkspaceAttendanceLocationView.as_view()(request), repo

    def _put(self, user=OWNER, body=None, existing=None):
        request = factory.put("/attendance/location/", body or {}, format="json")
        force_authenticate(request, user=user)
        with patch("apps.b2b.workspace.views.repo") as repo:
            repo.get_attendance_location.return_value = existing
            repo.upsert_attendance_location.side_effect = (
                lambda **kw: {**kw, "updated_at": None}
            )
            response = WorkspaceAttendanceLocationView.as_view()(request)
            return response, repo

    def test_anyone_may_read_whether_it_is_on(self):
        # The app needs this before it knows whether check-in has to carry
        # coordinates — an employee, not just the owner, has to read it.
        response, _ = self._get(user=EMPLOYEE, location={
            "is_enabled": True, "latitude": 41.3, "longitude": 69.2,
            "radius_meters": 200, "updated_at": None,
        })
        assert response.status_code == 200
        assert response.data["is_enabled"] is True

    def test_nothing_configured_yet_reads_as_disabled(self):
        response, _ = self._get(location=None)
        assert response.status_code == 200
        assert response.data["is_enabled"] is False

    def test_an_employee_cannot_change_it(self):
        response, repo = self._put(user=EMPLOYEE, body={"is_enabled": True, "latitude": 41.3, "longitude": 69.2})
        assert response.status_code == 403
        repo.upsert_attendance_location.assert_not_called()

    def test_a_performer_manager_cannot_change_it_either(self):
        # Company policy, not a manager's day-to-day call — only the owner.
        response, repo = self._put(user=PERFORMER, body={"is_enabled": True, "latitude": 41.3, "longitude": 69.2})
        assert response.status_code == 403
        repo.upsert_attendance_location.assert_not_called()

    def test_the_owner_can_set_the_point_and_turn_it_on(self):
        response, repo = self._put(
            user=OWNER, body={"is_enabled": True, "latitude": 41.3, "longitude": 69.2, "radius_meters": 150}
        )
        assert response.status_code == 200
        kwargs = repo.upsert_attendance_location.call_args.kwargs
        assert kwargs["is_enabled"] is True
        assert kwargs["latitude"] == 41.3
        assert kwargs["longitude"] == 69.2
        assert kwargs["radius_meters"] == 150
        assert kwargs["updated_by_id"] == 9

    def test_turning_it_on_with_no_point_ever_set_is_refused(self):
        response, repo = self._put(user=OWNER, body={"is_enabled": True}, existing=None)
        assert response.status_code == 400
        repo.upsert_attendance_location.assert_not_called()

    def test_turning_it_back_on_reuses_the_point_already_on_file(self):
        response, repo = self._put(
            user=OWNER,
            body={"is_enabled": True},
            existing={"is_enabled": False, "latitude": 41.3, "longitude": 69.2, "radius_meters": 200},
        )
        assert response.status_code == 200
        kwargs = repo.upsert_attendance_location.call_args.kwargs
        assert kwargs["latitude"] == 41.3
        assert kwargs["longitude"] == 69.2

    def test_turning_it_off_needs_no_point(self):
        response, repo = self._put(user=OWNER, body={"is_enabled": False}, existing=None)
        assert response.status_code == 200
        repo.upsert_attendance_location.assert_called_once()
