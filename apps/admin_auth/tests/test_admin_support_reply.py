"""Who a staff reply is credited to.

`b2b_support_message.author_user_id` references `b2b_user` — a company's own dashboard
user. The person replying from the admin inbox is a WEEL admin out of `users`, which is a
different table with its own id space, so their id is not a valid value for that column.
Passing it violated the foreign key on every reply (a 500 the moment no `b2b_user` shared
the id) and silently credited the message to an unrelated person whenever one did.

`is_staff` is what marks a line as support's, and it is set here rather than taken from
the body. This pins the column back to null so a later edit cannot reintroduce the crash.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from rest_framework.test import APIRequestFactory, force_authenticate

from apps.admin_auth.b2b_admin_views import AdminB2BSupportThreadView

factory = APIRequestFactory()

# What the repository hands back after an insert — the response serializer needs all of it.
STORED = {
    "id": 99,
    "text": "Parolni tikladik",
    "is_staff": True,
    "created_at": datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc),
}
# A WEEL admin whose id deliberately collides with a plausible b2b_user id.
ADMIN = SimpleNamespace(id=1, role="admin", is_active=True, is_authenticated=True)

REPO = "apps.admin_auth.b2b_admin_views.workspace_repo"


def _reply(text="Parolni tikladik"):
    request = factory.post("/", {"text": text}, format="json")
    force_authenticate(request, user=ADMIN)
    return AdminB2BSupportThreadView.as_view()(request, employee_id=26)


def test_a_staff_reply_is_not_credited_to_a_b2b_user():
    with patch(REPO) as repo:
        repo.support_employee.return_value = {"id": 26, "company_id": 7}
        repo.create_support_message.return_value = STORED
        response = _reply()

    assert response.status_code == 201
    kwargs = repo.create_support_message.call_args.kwargs
    assert kwargs["author_user_id"] is None, "an admin id is not a b2b_user id"
    assert kwargs["is_staff"] is True, "is_staff is what marks the line as support's"


def test_the_reply_lands_in_the_thread_the_question_came_from():
    """The company is taken off the employee, never off the request body."""
    with patch(REPO) as repo:
        repo.support_employee.return_value = {"id": 26, "company_id": 7}
        repo.create_support_message.return_value = STORED
        _reply()

    kwargs = repo.create_support_message.call_args.kwargs
    assert kwargs["company_id"] == 7 and kwargs["employee_id"] == 26


def test_replying_to_an_unknown_employee_is_a_404():
    with patch(REPO) as repo:
        repo.support_employee.return_value = None
        response = _reply()
    assert response.status_code == 404
    repo.create_support_message.assert_not_called()


def test_an_empty_reply_is_refused():
    with patch(REPO) as repo:
        repo.support_employee.return_value = {"id": 26, "company_id": 7}
        response = _reply(text="")
    assert response.status_code == 400
    repo.create_support_message.assert_not_called()
