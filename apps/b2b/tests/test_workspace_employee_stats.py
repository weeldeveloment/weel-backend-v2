"""The card the chat opens when you tap a colleague's name.

It draws two numbers — what they have finished and what they are working on —
and until this endpoint existed it drew both from whatever the roster row
happened to carry, which was nothing: every colleague read as 0 done and 0 in
progress on every phone.

Two things are worth pinning and neither is visible from the SQL: the counts
are over the tasks *assigned* to somebody rather than the ones they wrote, and
an id from another workspace is a 404 rather than a person with no work on.
"""
from unittest.mock import patch

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.workspace.authentication import WorkspaceUser
from apps.b2b.workspace.views import WorkspaceEmployeeStatsView

factory = APIRequestFactory()

COMPANY_ID = 55
VIEWER = WorkspaceUser({
    "id": 7,
    "company_id": COMPANY_ID,
    "role": "employee",
    "full_name": "Xodim",
    "phone": "+998900000001",
})


def _call(view_class, request, user, **kwargs):
    force_authenticate(request, user=user)
    return view_class.as_view()(request, **kwargs)


def _employee(company_id: int = COMPANY_ID):
    return {"id": 9, "company_id": company_id, "full_name": "Nilufar Karimova"}


def test_the_card_reads_the_counts_off_the_tasks_assigned_to_them():
    counts = {
        "done_count": 14,
        "in_progress_count": 3,
        "todo_count": 2,
        "overdue_count": 1,
    }
    with (
        patch(
            "apps.b2b.workspace.views.repo.get_workspace_employee",
            return_value=_employee(),
        ),
        patch(
            "apps.b2b.workspace.views.repo.employee_task_counters",
            return_value=counts,
        ) as counted,
    ):
        response = _call(
            WorkspaceEmployeeStatsView,
            factory.get("/employees/9/stats/"),
            VIEWER,
            employee_id=9,
        )

    assert response.status_code == 200
    assert response.data["tasks_done"] == 14
    assert response.data["tasks_in_progress"] == 3
    # Counted for the person whose card is open, inside the caller's company.
    assert counted.call_args.args == (COMPANY_ID, 9)


def test_somebody_from_another_workspace_is_not_found():
    """Not "a colleague with nothing on": an id that belongs to a different
    company must not confirm that the person exists at all."""
    with (
        patch(
            "apps.b2b.workspace.views.repo.get_workspace_employee",
            return_value=_employee(company_id=999),
        ),
        patch("apps.b2b.workspace.views.repo.employee_task_counters") as counted,
    ):
        response = _call(
            WorkspaceEmployeeStatsView,
            factory.get("/employees/9/stats/"),
            VIEWER,
            employee_id=9,
        )

    assert response.status_code == 404
    counted.assert_not_called()


def test_a_missing_employee_is_a_404_and_not_a_row_of_zeroes():
    with (
        patch(
            "apps.b2b.workspace.views.repo.get_workspace_employee",
            return_value=None,
        ),
        patch("apps.b2b.workspace.views.repo.employee_task_counters") as counted,
    ):
        response = _call(
            WorkspaceEmployeeStatsView,
            factory.get("/employees/404/stats/"),
            VIEWER,
            employee_id=404,
        )

    assert response.status_code == 404
    counted.assert_not_called()


def test_counts_missing_from_the_row_read_as_zero():
    """The repository answers with one aggregate row; an empty table gives it
    nothing to unpack, and the card must still draw."""
    with (
        patch(
            "apps.b2b.workspace.views.repo.get_workspace_employee",
            return_value=_employee(),
        ),
        patch(
            "apps.b2b.workspace.views.repo.employee_task_counters",
            return_value={},
        ),
    ):
        response = _call(
            WorkspaceEmployeeStatsView,
            factory.get("/employees/9/stats/"),
            VIEWER,
            employee_id=9,
        )

    assert response.status_code == 200
    assert response.data["tasks_done"] == 0
    assert response.data["tasks_in_progress"] == 0
