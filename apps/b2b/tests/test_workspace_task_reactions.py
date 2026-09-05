"""Reactions on a task — the pills under a card, the way the chat has them.

Two rules matter: a reaction is a toggle (the same emoji twice takes it back),
and the payload groups the raw rows per emoji with the reader's own marked, so
neither client has to count.
"""
from unittest.mock import patch

from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.workspace.authentication import WorkspaceUser
from apps.b2b.workspace.views import WorkspaceTaskReactionView, _task_payload, _task_reactions
from apps.b2b.workspace import inventory_repository

factory = APIRequestFactory()

ASSIGNEE = WorkspaceUser({
    "id": 8, "company_id": 55, "role": "employee", "full_name": "Mas'ul", "phone": "+998900000002",
})
STRANGER = WorkspaceUser({
    "id": 12, "company_id": 55, "role": "employee", "full_name": "Begona", "phone": "+998900000009",
})


def _task(**overrides):
    row = {
        "id": 3, "company_id": 55, "author_id": 7, "title": "Vazifa", "status": "todo",
        "assignee_ids": [8], "subtasks": [], "comments": [], "voice": None, "files": [],
        "reactions": [
            {"task_id": 3, "employee_id": 7, "emoji": "👍"},
            {"task_id": 3, "employee_id": 8, "emoji": "👍"},
            {"task_id": 3, "employee_id": 8, "emoji": "❤️"},
        ],
    }
    row.update(overrides)
    return row


def test_rows_are_grouped_per_emoji_with_the_reader_marked():
    grouped = _task_reactions(_task()["reactions"], viewer_id=8)
    assert grouped == [
        {"emoji": "👍", "count": 2, "employee_ids": [7, 8], "mine": True},
        {"emoji": "❤️", "count": 1, "employee_ids": [8], "mine": True},
    ]
    assert _task_reactions(_task()["reactions"], viewer_id=7)[1]["mine"] is False
    assert _task_reactions(None, viewer_id=7) == []


def test_payload_carries_the_grouped_reactions():
    with patch("apps.b2b.workspace.views._is_completed_task", return_value=False):
        payload = _task_payload(_task(), ASSIGNEE)
    assert payload["reactions"][0] == {"emoji": "👍", "count": 2, "employee_ids": [7, 8], "mine": True}


def _react(user, emoji="👍", task=None):
    request = factory.post("/tasks/3/reactions/", {"emoji": emoji}, format="json")
    force_authenticate(request, user=user)
    with patch("apps.b2b.workspace.views.repo") as repo:
        repo.get_task.return_value = task if task is not None else _task()
        repo.toggle_task_reaction.return_value = True
        response = WorkspaceTaskReactionView.as_view()(request, task_id=3)
    return response, repo


def test_an_assignee_toggles_and_gets_the_task_back():
    response, repo = _react(ASSIGNEE)
    assert response.status_code == 200
    repo.toggle_task_reaction.assert_called_once_with(3, 8, "👍")
    assert response.data["reactions"][0]["emoji"] == "👍"


def test_any_colleague_may_react_the_board_is_the_companys():
    # The board is company-wide (`task_scope` is None for everyone), so a
    # reaction is not gated on being the author or an assignee.
    response, repo = _react(STRANGER)
    assert response.status_code == 200
    repo.toggle_task_reaction.assert_called_once_with(3, 12, "👍")


def test_a_task_that_is_not_there_is_404():
    request = factory.post("/tasks/3/reactions/", {"emoji": "👍"}, format="json")
    force_authenticate(request, user=ASSIGNEE)
    with patch("apps.b2b.workspace.views.repo") as repo:
        repo.get_task.return_value = None
        response = WorkspaceTaskReactionView.as_view()(request, task_id=3)
    assert response.status_code == 404
    repo.toggle_task_reaction.assert_not_called()


def test_a_blank_emoji_is_refused():
    response, repo = _react(ASSIGNEE, emoji="  ")
    assert response.status_code == 400
    repo.toggle_task_reaction.assert_not_called()


def test_reserved_stock_ignores_leads_that_were_deleted():
    # A deleted lead is off the board but its lines are still rows; they must
    # not hold stock for ever.
    assert "l.deleted_at IS NULL" in inventory_repository._OPEN_LEAD_LINES
