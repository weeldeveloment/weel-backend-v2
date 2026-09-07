"""Three task statuses: new, in progress, done.

There used to be a fourth. "review" was a place work could land — the checklist
put it there by itself when the last step was ticked — that no screen offered
as a destination and the app had no tab for, so a task could disappear into a
status nobody could see or move it out of. These pin the set down and the two
behaviours that used to depend on it.
"""
from unittest.mock import patch

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.workspace import repository as repo
from apps.b2b.workspace.authentication import WorkspaceUser
from apps.b2b.workspace.serializers import TaskStatusSerializer
from apps.b2b.workspace.views import WorkspaceSubtaskToggleView

factory = APIRequestFactory()

EMPLOYEE = WorkspaceUser({
    "id": 7,
    "company_id": 55,
    "role": "employee",
    "full_name": "Xodim",
    "phone": "+998900000001",
})


def _task(**overrides):
    row = {
        "id": 3,
        "company_id": 55,
        "author_id": 7,
        "status": "in_progress",
        "assignee_ids": [7],
        "subtasks": [{"id": 1, "is_done": True}, {"id": 2, "is_done": True}],
        "comments": [],
        "voice": None,
    }
    row.update(overrides)
    return row


class TestTheStatusSet:
    def test_there_are_exactly_three(self):
        assert repo.TASK_STATUSES == ("todo", "in_progress", "done")

    def test_review_is_no_longer_accepted(self):
        # The write endpoints validate against the same tuple, so a client
        # still sending the old status is told rather than silently storing a
        # value nothing can render.
        serializer = TaskStatusSerializer(data={"status": "review"})
        assert not serializer.is_valid()

    @pytest.mark.parametrize("status", ["todo", "in_progress", "done"])
    def test_each_of_the_three_is_accepted(self, status):
        assert TaskStatusSerializer(data={"status": status}).is_valid()


class TestTickingTheLastStep:
    def test_leaves_the_task_where_it_is(self):
        # Finishing the checklist is what the person does; saying the task is
        # done is a separate decision they make afterwards. This used to move
        # the task to "review" on their behalf.
        request = factory.post("/tasks/3/subtasks/2/toggle/")
        force_authenticate(request, user=EMPLOYEE)

        with patch("apps.b2b.workspace.views.repo") as mocked:
            mocked.get_task.return_value = _task()
            mocked.toggle_subtask.return_value = True
            response = WorkspaceSubtaskToggleView.as_view()(
                request, task_id=3, subtask_id=2
            )

        assert response.status_code == 200
        assert response.data["status"] == "in_progress"
        mocked.update_task.assert_not_called()


class TestCounters:
    def test_one_count_per_status_plus_the_derived_buckets(self):
        # The tabs count whole companies, not the page of tasks that came
        # back — a company with more tasks than fit in one page would
        # otherwise be under-reported by every tile on the screen.
        with patch("apps.b2b.workspace.repository.fetch_one") as fetch_one:
            fetch_one.return_value = {
                "open_count": 5,
                "todo_count": 2,
                "in_progress_count": 3,
                "done_count": 16,
                "overdue_count": 4,
                "due_today_count": 1,
            }
            counters = repo.task_counters(company_id=55)

        sql = fetch_one.call_args.args[0]
        assert "AS todo_count" in sql
        assert "AS in_progress_count" in sql
        assert counters["todo_count"] == 2
        assert counters["in_progress_count"] == 3


# ─── A task raised off a deal ─────────────────────────────────────────────────

def test_a_task_raised_off_a_lead_lands_on_the_lead_s_history():
    """The lead card's "next step" became a real task, so the deal has to
    hear about it — both when it is agreed and when it is done."""
    with (
        patch("apps.b2b.workspace.repository.fetch_one", return_value={"id": 91}),
        patch("apps.b2b.workspace.repository.set_task_assignees"),
        patch("apps.b2b.workspace.repository.replace_subtasks"),
        patch("apps.b2b.workspace.repository.add_task_activity"),
        patch("apps.b2b.workspace.repository.get_task", return_value={"id": 91}),
        patch("apps.b2b.workspace.repository.add_lead_activity") as logged,
    ):
        repo.create_task(
            company_id=55, author_id=7, title="Shartnomani yuborish", lead_id=12,
        )

    logged.assert_called_once()
    assert logged.call_args.args[0] == 12
    assert logged.call_args.kwargs["kind"] == "task_created"
    assert logged.call_args.kwargs["target_id"] == 91
    assert logged.call_args.kwargs["text"] == "Shartnomani yuborish"


def test_an_ordinary_task_leaves_every_lead_alone():
    with (
        patch("apps.b2b.workspace.repository.fetch_one", return_value={"id": 91}),
        patch("apps.b2b.workspace.repository.set_task_assignees"),
        patch("apps.b2b.workspace.repository.replace_subtasks"),
        patch("apps.b2b.workspace.repository.add_task_activity"),
        patch("apps.b2b.workspace.repository.get_task", return_value={"id": 91}),
        patch("apps.b2b.workspace.repository.add_lead_activity") as logged,
    ):
        repo.create_task(company_id=55, author_id=7, title="Ofisga borish")

    logged.assert_not_called()


@pytest.mark.parametrize(
    "moved_to,expected",
    [("done", 1), ("in_progress", 0)],
)
def test_only_finishing_a_lead_s_task_reaches_the_deal(moved_to, expected):
    """Moving it along the board is the assignee's own business; finishing
    it is the deal's."""
    current = {"title": "Shartnomani yuborish", "status": "todo", "lead_id": 12}
    with (
        patch("apps.b2b.workspace.repository.fetch_one", return_value=current),
        patch("apps.b2b.workspace.repository.add_task_activity"),
        patch("apps.b2b.workspace.repository.get_task", return_value={"id": 91}),
        patch("apps.b2b.workspace.repository.add_lead_activity") as logged,
    ):
        repo.update_task(91, 55, actor_id=7, status=moved_to)

    assert logged.call_count == expected
    if expected:
        assert logged.call_args.kwargs["kind"] == "task_done"
        assert logged.call_args.kwargs["target_id"] == 91
