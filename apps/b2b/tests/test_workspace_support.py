"""Yordam markazi: the employee's own thread with WEEL support.

Runs against a mocked repository — the view has no capability gate and no
SQL worth exercising here, so what is worth pinning is the three things it
does decide: whose thread it reads, that reading marks support's replies seen,
and that an employee cannot write on somebody else's behalf.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from django.conf import settings
from django.utils import timezone

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.workspace.authentication import WorkspaceUser
from apps.b2b.workspace.views import WorkspaceSupportView

COMPANY_ID = 77
EMPLOYEE_ID = 9

factory = APIRequestFactory()


def _user(role: str = "employee", employee_id: int = EMPLOYEE_ID) -> WorkspaceUser:
    return WorkspaceUser({
        "id": employee_id,
        "company_id": COMPANY_ID,
        "role": role,
        "full_name": "Support Tester",
        "phone": "+998900000000",
    })


def _call(request, user):
    force_authenticate(request, user=user)
    return WorkspaceSupportView.as_view()(request)


def _message(message_id: int, text: str, is_staff: bool) -> dict:
    return {
        "id": message_id,
        "text": text,
        "is_staff": is_staff,
        "created_at": timezone.now(),
    }


class TestReading:
    def test_reads_only_the_callers_own_thread(self):
        request = factory.get("/support/")
        with (
            patch(
                "apps.b2b.workspace.views.repo.list_support_messages",
                return_value=[_message(1, "Salom", False)],
            ) as listed,
            patch("apps.b2b.workspace.views.repo.mark_support_read"),
        ):
            response = _call(request, _user())

        assert response.status_code == 200
        assert response.data[0]["text"] == "Salom"
        # The employee id comes from the session, never from the request.
        listed.assert_called_once_with(EMPLOYEE_ID)

    def test_opening_the_screen_marks_support_replies_seen(self):
        request = factory.get("/support/")
        with (
            patch(
                "apps.b2b.workspace.views.repo.list_support_messages",
                return_value=[_message(2, "Yordam beramiz", True)],
            ),
            patch("apps.b2b.workspace.views.repo.mark_support_read") as marked,
        ):
            _call(request, _user())

        marked.assert_called_once_with(EMPLOYEE_ID)

    def test_a_plain_employee_may_read_it(self):
        """No capability gates this — every employee has a help desk."""
        request = factory.get("/support/")
        with (
            patch("apps.b2b.workspace.views.repo.list_support_messages", return_value=[]),
            patch("apps.b2b.workspace.views.repo.mark_support_read"),
        ):
            response = _call(request, _user("employee"))

        assert response.status_code == 200


class TestWriting:
    def test_a_message_is_stored_against_the_caller(self):
        request = factory.post("/support/", {"text": "  Hisobot ochilmayapti  "}, format="json")
        with patch(
            "apps.b2b.workspace.views.repo.create_support_message",
            return_value=_message(3, "Hisobot ochilmayapti", False),
        ) as created:
            response = _call(request, _user())

        assert response.status_code == 201
        assert response.data["is_staff"] is False
        created.assert_called_once_with(
            company_id=COMPANY_ID,
            employee_id=EMPLOYEE_ID,
            # Trimmed by the serializer, so a message that is only whitespace
            # cannot reach the inbox looking like a real one.
            text="Hisobot ochilmayapti",
        )

    @pytest.mark.parametrize("text", ["", "   ", "\n\t"])
    def test_an_empty_message_is_refused(self, text):
        request = factory.post("/support/", {"text": text}, format="json")
        with patch("apps.b2b.workspace.views.repo.create_support_message") as created:
            response = _call(request, _user())

        assert response.status_code == 400
        created.assert_not_called()

    def test_the_employee_cannot_write_as_support(self):
        """`is_staff` is not part of the request body — a line posted from the
        app is always the employee's own, whatever it claims."""
        request = factory.post(
            "/support/", {"text": "Salom", "is_staff": True}, format="json",
        )
        with patch(
            "apps.b2b.workspace.views.repo.create_support_message",
            return_value=_message(4, "Salom", False),
        ) as created:
            response = _call(request, _user())

        assert response.status_code == 201
        assert "is_staff" not in created.call_args.kwargs
        assert response.data["is_staff"] is False
