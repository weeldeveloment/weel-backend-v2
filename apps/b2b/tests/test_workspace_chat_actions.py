"""Replying to a message, and deleting one.

Both are new doors onto the same rows, so both are places where a wrong check
leaks or destroys something. The two that matter: a reply must not be able to
quote a message from a room the caller cannot read, and an employee must not be
able to delete somebody else's message.
"""
from unittest.mock import patch

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.workspace.authentication import WorkspaceUser
from apps.b2b.workspace.views import WorkspaceMessageDetailView, WorkspaceMessageView

factory = APIRequestFactory()

EMPLOYEE = WorkspaceUser({
    "id": 7,
    "company_id": 55,
    "role": "employee",
    "full_name": "Xodim",
    "phone": "+998900000001",
})

OWNER = WorkspaceUser({
    "id": 9,
    "company_id": 55,
    "role": "owner",
    "full_name": "Rahbar",
    "phone": "+998900000002",
})

THREAD = {"id": 3, "company_id": 55}


def _message(**overrides):
    row = {
        "id": 100,
        "thread_id": 3,
        "sender_id": 7,
        "text": "salom",
        "reply_to_id": None,
        "created_at": "2026-08-15T10:00:00Z",
    }
    row.update(overrides)
    return row


class TestReply:
    def test_a_reply_records_the_message_it_answers(self):
        request = factory.post(
            "/chats/3/messages/", {"text": "javob", "reply_to_id": 100}, format="json"
        )
        force_authenticate(request, user=EMPLOYEE)

        with patch("apps.b2b.workspace.views.repo") as repo, patch(
            "apps.b2b.workspace.views.broadcast_message"
        ):
            repo.get_thread_for_member.return_value = THREAD
            repo.get_message.return_value = _message()
            repo.send_message.return_value = _message(id=101, reply_to_id=100)
            response = WorkspaceMessageView.as_view()(request, thread_id=3)

        assert response.status_code == 201
        assert repo.send_message.call_args.kwargs["reply_to_id"] == 100
        assert response.data["reply_to"]["id"] == 100

    def test_a_reply_to_a_message_in_another_room_is_refused(self):
        # The lookup is scoped to the thread, so an id from a room this caller
        # cannot read comes back empty — quoting it would put its text on their
        # screen.
        request = factory.post(
            "/chats/3/messages/", {"text": "javob", "reply_to_id": 999}, format="json"
        )
        force_authenticate(request, user=EMPLOYEE)

        with patch("apps.b2b.workspace.views.repo") as repo:
            repo.get_thread_for_member.return_value = THREAD
            repo.get_message.return_value = None
            response = WorkspaceMessageView.as_view()(request, thread_id=3)

        assert response.status_code == 400
        repo.send_message.assert_not_called()

    def test_the_quote_is_trimmed(self):
        long_text = "x" * 500
        request = factory.post(
            "/chats/3/messages/", {"text": "javob", "reply_to_id": 100}, format="json"
        )
        force_authenticate(request, user=EMPLOYEE)

        with patch("apps.b2b.workspace.views.repo") as repo, patch(
            "apps.b2b.workspace.views.broadcast_message"
        ):
            repo.get_thread_for_member.return_value = THREAD
            repo.get_message.return_value = _message(text=long_text)
            repo.send_message.return_value = _message(id=101, reply_to_id=100)
            response = WorkspaceMessageView.as_view()(request, thread_id=3)

        quote = response.data["reply_to"]
        assert len(quote["text"]) == 120
        assert quote["is_truncated"] is True

    def test_an_ordinary_message_quotes_nothing(self):
        request = factory.post("/chats/3/messages/", {"text": "salom"}, format="json")
        force_authenticate(request, user=EMPLOYEE)

        with patch("apps.b2b.workspace.views.repo") as repo, patch(
            "apps.b2b.workspace.views.broadcast_message"
        ):
            repo.get_thread_for_member.return_value = THREAD
            repo.send_message.return_value = _message(id=101)
            response = WorkspaceMessageView.as_view()(request, thread_id=3)

        assert response.data["reply_to"] is None
        repo.get_message.assert_not_called()


class TestDelete:
    def _delete(self, user, message):
        request = factory.delete("/chats/3/messages/100/")
        force_authenticate(request, user=user)
        with patch("apps.b2b.workspace.views.repo") as repo, patch(
            "apps.b2b.workspace.views.broadcast_deletion"
        ) as broadcast:
            repo.get_thread_for_member.return_value = THREAD
            repo.get_message.return_value = message
            repo.attachments_for_messages.return_value = {}
            response = WorkspaceMessageDetailView.as_view()(
                request, thread_id=3, message_id=100
            )
            return response, repo, broadcast

    def test_you_can_delete_your_own(self):
        response, repo, broadcast = self._delete(EMPLOYEE, _message(sender_id=7))
        assert response.status_code == 204
        repo.delete_message.assert_called_once_with(100, 3)
        broadcast.assert_called_once_with(3, 100)

    def test_an_employee_cannot_delete_someone_elses(self):
        response, repo, _ = self._delete(EMPLOYEE, _message(sender_id=8))
        assert response.status_code == 403
        repo.delete_message.assert_not_called()

    def test_an_owner_can_delete_anyone_s(self):
        # A manager has to be able to take down what was posted in a shared
        # room; an employee must not be able to edit the record.
        response, repo, _ = self._delete(OWNER, _message(sender_id=7))
        assert response.status_code == 204
        repo.delete_message.assert_called_once()

    def test_a_missing_message_is_a_404_not_a_crash(self):
        response, repo, _ = self._delete(EMPLOYEE, None)
        assert response.status_code == 404
        repo.delete_message.assert_not_called()

    def test_deleting_outside_your_thread_is_a_404(self):
        request = factory.delete("/chats/3/messages/100/")
        force_authenticate(request, user=EMPLOYEE)
        with patch("apps.b2b.workspace.views.repo") as repo:
            repo.get_thread_for_member.return_value = None
            response = WorkspaceMessageDetailView.as_view()(
                request, thread_id=3, message_id=100
            )
        assert response.status_code == 404
        repo.get_message.assert_not_called()
