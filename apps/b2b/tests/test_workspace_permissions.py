"""Role rules for the B2B mobile workspace.

The mobile app is used by a company's own staff, so the interesting cases are
all "what may this role *not* do": an employee must not be able to create or
edit tasks, invite colleagues to events, open group chats, or touch work that
was never given to them. These run against mocked repository calls — the rules
live in the views, not in SQL, so no database is needed.
"""
from unittest.mock import patch

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.workspace.authentication import WorkspaceUser
from apps.b2b.workspace.roles import capabilities_for
from apps.b2b.workspace.views import (
    WorkspaceEventListCreateView,
    WorkspaceTaskDetailView,
    WorkspaceTaskListCreateView,
    WorkspaceTaskStatusView,
    WorkspaceThreadListCreateView,
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


def _task(**overrides):
    task = {
        "id": 10,
        "company_id": COMPANY_ID,
        "title": "Hisobot",
        "description": "",
        "status": "todo",
        "priority": "medium",
        "project": None,
        "due_date": None,
        "author_id": OWNER_ID,
        "created_at": None,
        "assignee_ids": [],
        "subtasks": [],
        "comments": [],
    }
    task.update(overrides)
    return task


# ─── Capability map ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("role, can_create", [("owner", True), ("performer", True), ("employee", True), ("guest", False)])
def test_anybody_with_the_task_list_may_create_a_task(role, can_create):
    """TZ v2 §6: creating a record is open to whoever can open the module.
    The default guest cannot open it, so the flag stays off for them."""
    assert capabilities_for(role)["can_create_task"] is can_create



def test_an_employee_still_owns_their_own_work():
    caps = capabilities_for("employee")
    assert caps["can_update_task_status"] is True
    assert caps["can_comment_task"] is True
    assert caps["can_create_personal_event"] is True
    # ...but sees only their slice of the company.
    assert caps["sees_all_company_data"] is False


def test_management_manages_the_roster():
    """TZ v2 §11: the manager too, for the employees and guests of their own
    workspace; a plain employee not at all."""
    assert capabilities_for("owner")["can_manage_team"] is True
    assert capabilities_for("performer")["can_manage_team"] is True
    assert capabilities_for("employee")["can_manage_team"] is False



# ─── Tasks ────────────────────────────────────────────────────────────────────

def test_a_guest_without_the_task_list_cannot_create_a_task():
    """The create endpoint stays closed to whoever cannot open the module —
    which is what the default guest is (TZ v2 §6, read together with §12)."""
    request = factory.post("/tasks/", {"title": "Yangi vazifa"}, format="json")
    response = _call(WorkspaceTaskListCreateView, request, _user("guest", 77))
    assert response.status_code == 403


@patch("apps.b2b.workspace.views.repo.employee_ids_in_company", return_value={EMPLOYEE_ID})
@patch("apps.b2b.workspace.views.repo.create_task")
def test_an_employee_creates_a_task(create_task, _ids):
    """TZ v2 §6 / §11 "Создавать записи: Сотрудник — Да"."""
    create_task.return_value = _task(author_id=EMPLOYEE_ID)
    request = factory.post("/tasks/", {"title": "Yangi vazifa"}, format="json")
    response = _call(WorkspaceTaskListCreateView, request, EMPLOYEE)
    assert response.status_code == 201, response.data



@patch("apps.b2b.workspace.views.repo.employee_ids_in_company", return_value={EMPLOYEE_ID})
@patch("apps.b2b.workspace.views.repo.create_task")
def test_manager_creates_a_task(create_task, _ids):
    create_task.return_value = _task(assignee_ids=[EMPLOYEE_ID])
    request = factory.post(
        "/tasks/", {"title": "Hisobot", "assignee_ids": [EMPLOYEE_ID]}, format="json"
    )
    response = _call(WorkspaceTaskListCreateView, request, OWNER)
    assert response.status_code == 201
    assert response.data["assignee_ids"] == [EMPLOYEE_ID]


@patch("apps.b2b.workspace.views.repo.employee_ids_in_company", return_value=set())
def test_assignee_from_another_company_is_rejected(_ids):
    request = factory.post("/tasks/", {"title": "X", "assignee_ids": [999]}, format="json")
    response = _call(WorkspaceTaskListCreateView, request, OWNER)
    assert response.status_code == 400
    assert "assignee_ids" in response.data


@patch("apps.b2b.workspace.views.repo.task_counters", return_value={})
@patch("apps.b2b.workspace.views.repo.list_tasks", return_value=[])
def test_employee_list_covers_the_whole_company_too(list_tasks, _counters):
    # The board is the company's work: an employee reads all of it and narrows
    # to their own with the app's "Menikilar" toggle. Writing stays gated by
    # role — see the edit/create tests above.
    _call(WorkspaceTaskListCreateView, factory.get("/tasks/"), EMPLOYEE)
    assert list_tasks.call_args.kwargs["visible_to"] is None


@patch("apps.b2b.workspace.views.repo.task_counters", return_value={})
@patch("apps.b2b.workspace.views.repo.list_tasks", return_value=[])
def test_manager_list_covers_the_whole_company(list_tasks, _counters):
    _call(WorkspaceTaskListCreateView, factory.get("/tasks/"), OWNER)
    assert list_tasks.call_args.kwargs["visible_to"] is None


@patch("apps.b2b.workspace.views.repo.get_task")
def test_employee_cannot_edit_a_task(get_task):
    get_task.return_value = _task(assignee_ids=[EMPLOYEE_ID])
    request = factory.patch("/tasks/10/", {"title": "Boshqa nom"}, format="json")
    response = _call(WorkspaceTaskDetailView, request, EMPLOYEE, task_id=10)
    assert response.status_code == 403


@patch("apps.b2b.workspace.views.repo.list_task_activity", return_value=[])
@patch("apps.b2b.workspace.views.repo.get_task")
def test_an_employee_may_open_a_task_that_is_not_theirs(get_task, _activity):
    # It is on the board they can now list, so opening the card must work —
    # read-only: `can_edit`/`can_delete` in the payload stay False for them.
    get_task.return_value = _task(assignee_ids=[], author_id=OWNER_ID)
    response = _call(WorkspaceTaskDetailView, factory.get("/tasks/10/"), EMPLOYEE, task_id=10)
    assert response.status_code == 200
    assert response.data["can_edit"] is False


@patch("apps.b2b.workspace.views.repo.update_task")
@patch("apps.b2b.workspace.views.repo.get_task")
def test_employee_advances_a_task_assigned_to_them(get_task, update_task):
    get_task.return_value = _task(assignee_ids=[EMPLOYEE_ID])
    update_task.return_value = _task(assignee_ids=[EMPLOYEE_ID], status="in_progress")
    request = factory.post("/tasks/10/status/", {"status": "in_progress"}, format="json")
    response = _call(WorkspaceTaskStatusView, request, EMPLOYEE, task_id=10)
    assert response.status_code == 200
    assert response.data["status"] == "in_progress"


@patch("apps.b2b.workspace.views.repo.get_task")
def test_employee_cannot_advance_someone_elses_task(get_task):
    get_task.return_value = _task(assignee_ids=[999])
    request = factory.post("/tasks/10/status/", {"status": "done"}, format="json")
    response = _call(WorkspaceTaskStatusView, request, EMPLOYEE, task_id=10)
    assert response.status_code == 403


@patch("apps.b2b.workspace.views.repo.list_task_activity", return_value=[])
@patch("apps.b2b.workspace.views.repo.get_task")
def test_task_payload_tells_the_app_which_buttons_to_show(get_task, _activity):
    get_task.return_value = _task(assignee_ids=[EMPLOYEE_ID])
    response = _call(WorkspaceTaskDetailView, factory.get("/tasks/10/"), EMPLOYEE, task_id=10)
    assert response.data["can_edit"] is False
    assert response.data["can_delete"] is False
    assert response.data["can_change_status"] is True


@patch("apps.b2b.workspace.tasks.notify_task_assigned.delay")
@patch("apps.b2b.workspace.views.repo.update_task")
@patch("apps.b2b.workspace.views.repo.set_task_assignees")
@patch(
    "apps.b2b.workspace.views.repo.employee_ids_in_company",
    return_value={EMPLOYEE_ID},
)
@patch("apps.b2b.workspace.views.repo.get_task")
def test_the_author_may_reassign_a_task_without_edit_rights(
    get_task, _ids, set_assignees, update_task, _queued
):
    """An employee raised the task and gave it to a colleague who is now out
    sick. They have no ``can_edit_task``, but handing it to somebody else is
    the one write they keep."""
    get_task.return_value = _task(author_id=EMPLOYEE_ID, assignee_ids=[OWNER_ID])
    update_task.return_value = _task(author_id=EMPLOYEE_ID, assignee_ids=[EMPLOYEE_ID])

    request = factory.patch("/tasks/10/", {"assignee_ids": [EMPLOYEE_ID]}, format="json")
    response = _call(WorkspaceTaskDetailView, request, EMPLOYEE, task_id=10)

    assert response.status_code == 200
    set_assignees.assert_called_once()
    assert set_assignees.call_args.args[1] == [EMPLOYEE_ID]


@patch("apps.b2b.workspace.views.repo.get_task")
def test_the_author_still_cannot_edit_anything_but_the_assignees(get_task):
    get_task.return_value = _task(author_id=EMPLOYEE_ID, assignee_ids=[OWNER_ID])
    request = factory.patch(
        "/tasks/10/",
        {"assignee_ids": [EMPLOYEE_ID], "title": "Boshqa nom"},
        format="json",
    )
    response = _call(WorkspaceTaskDetailView, request, EMPLOYEE, task_id=10)
    assert response.status_code == 403


@patch("apps.b2b.workspace.views.repo.list_task_activity", return_value=[])
@patch("apps.b2b.workspace.views.repo.get_task")
def test_can_reassign_is_the_author_or_a_manager_only(get_task, _activity):
    get_task.return_value = _task(author_id=EMPLOYEE_ID, assignee_ids=[])
    mine = _call(WorkspaceTaskDetailView, factory.get("/tasks/10/"), EMPLOYEE, task_id=10)
    assert mine.data["can_edit"] is False
    assert mine.data["can_reassign"] is True

    get_task.return_value = _task(author_id=OWNER_ID, assignee_ids=[])
    not_mine = _call(
        WorkspaceTaskDetailView, factory.get("/tasks/10/"), EMPLOYEE, task_id=10
    )
    assert not_mine.data["can_reassign"] is False


# ─── Calendar ─────────────────────────────────────────────────────────────────

def test_employee_cannot_invite_colleagues_to_an_event():
    request = factory.post("/events/", {
        "title": "Yig'ilish",
        "starts_at": "2026-08-10T09:00:00Z",
        "ends_at": "2026-08-10T10:00:00Z",
        "participant_ids": [OWNER_ID],
    }, format="json")
    response = _call(WorkspaceEventListCreateView, request, EMPLOYEE)
    assert response.status_code == 403


@patch("apps.b2b.workspace.views.repo.employee_ids_in_company", return_value={EMPLOYEE_ID})
@patch("apps.b2b.workspace.views.repo.create_event")
def test_employee_may_keep_a_personal_calendar(create_event, _ids):
    create_event.return_value = {
        "id": 3, "author_id": EMPLOYEE_ID, "participant_ids": [EMPLOYEE_ID],
    }
    request = factory.post("/events/", {
        "title": "Shaxsiy",
        "event_type": "personal",
        "starts_at": "2026-08-10T09:00:00Z",
        "ends_at": "2026-08-10T10:00:00Z",
    }, format="json")
    response = _call(WorkspaceEventListCreateView, request, EMPLOYEE)
    assert response.status_code == 201
    # The event is filed against them, whatever they sent.
    assert create_event.call_args.kwargs["participant_ids"] == [EMPLOYEE_ID]


def test_an_event_cannot_end_before_it_starts():
    request = factory.post("/events/", {
        "title": "Teskari",
        "starts_at": "2026-08-10T10:00:00Z",
        "ends_at": "2026-08-10T09:00:00Z",
    }, format="json")
    response = _call(WorkspaceEventListCreateView, request, OWNER)
    assert response.status_code == 400


# ─── Chat ─────────────────────────────────────────────────────────────────────

def test_employee_cannot_open_a_group_chat():
    request = factory.post(
        "/chats/", {"member_ids": [OWNER_ID], "group_name": "Jamoa"}, format="json"
    )
    response = _call(WorkspaceThreadListCreateView, request, EMPLOYEE)
    assert response.status_code == 403


@patch("apps.b2b.workspace.views.repo.get_thread_for_member")
@patch("apps.b2b.workspace.views.repo.find_direct_thread")
@patch("apps.b2b.workspace.views.repo.employee_ids_in_company", return_value={OWNER_ID})
@patch("apps.b2b.workspace.views.repo.create_thread")
def test_opening_a_direct_chat_twice_reuses_the_room(
    create_thread, _ids, find_direct, get_thread
):
    find_direct.return_value = {"id": 7}
    get_thread.return_value = {"id": 7, "group_name": None, "participant_ids": [OWNER_ID],
                               "unread": 0, "is_pinned": False, "is_muted": False}
    request = factory.post("/chats/", {"member_ids": [OWNER_ID]}, format="json")
    response = _call(WorkspaceThreadListCreateView, request, EMPLOYEE)

    assert response.status_code == 200
    assert response.data["id"] == 7
    create_thread.assert_not_called()


def test_a_chat_with_only_yourself_is_rejected():
    request = factory.post("/chats/", {"member_ids": [EMPLOYEE_ID]}, format="json")
    response = _call(WorkspaceThreadListCreateView, request, EMPLOYEE)
    assert response.status_code == 400


# ─── TZ v2 §8: a finished task is the owner's or the administrator's ─────────

@patch("apps.b2b.workspace.views.repo.update_task")
@patch("apps.b2b.workspace.views.repo.get_task")
def test_the_assignee_cannot_reopen_a_finished_task(get_task, update_task):
    get_task.return_value = _task(assignee_ids=[EMPLOYEE_ID], status="done")
    request = factory.post("/tasks/10/status/", {"status": "in_progress"}, format="json")
    response = _call(WorkspaceTaskStatusView, request, EMPLOYEE, task_id=10)
    assert response.status_code == 403
    update_task.assert_not_called()

    # Nor a manager who could have moved it a minute before it closed.
    response = _call(WorkspaceTaskStatusView, request, _user("performer", 88), task_id=10)
    assert response.status_code == 403
    update_task.assert_not_called()

    # The owner may.
    update_task.return_value = _task(assignee_ids=[EMPLOYEE_ID], status="in_progress")
    response = _call(WorkspaceTaskStatusView, request, OWNER, task_id=10)
    assert response.status_code == 200
    update_task.assert_called_once()


@patch("apps.b2b.workspace.views.repo.list_task_activity", return_value=[])
@patch("apps.b2b.workspace.views.repo.get_task")
def test_a_finished_task_draws_no_buttons_below_the_administrator(get_task, _activity):
    get_task.return_value = _task(assignee_ids=[EMPLOYEE_ID], status="done")
    response = _call(WorkspaceTaskDetailView, factory.get("/tasks/10/"), EMPLOYEE, task_id=10)
    assert response.status_code == 200
    assert response.data["can_change_status"] is False
    assert response.data["can_edit"] is False

    response = _call(WorkspaceTaskDetailView, factory.get("/tasks/10/"), OWNER, task_id=10)
    assert response.data["can_change_status"] is True
    assert response.data["can_edit"] is True
