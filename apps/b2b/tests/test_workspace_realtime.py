"""Presence, read receipts and the live bus.

Three things that are easy to get subtly wrong and hard to notice in a running
app: a green dot that never goes out, a double tick that claims somebody has
read a message they have not, and a write that quietly stops announcing itself.
"""
from unittest.mock import patch

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.workspace import presence
from apps.b2b.workspace.authentication import WorkspaceUser
from apps.b2b.workspace.views import (
    WorkspaceAPIView,
    WorkspaceMessageView,
    WorkspacePresenceView,
    _everyone_read_at,
)

factory = APIRequestFactory()

ALIYA = WorkspaceUser({
    "id": 7,
    "company_id": 55,
    "role": "employee",
    "full_name": "Aliya",
    "phone": "+998900000001",
})

THREAD = {"id": 3, "company_id": 55, "participant_ids": [8]}


class TestPresence:
    """The cache-backed roster of who is holding a socket open."""

    @pytest.fixture(autouse=True)
    def _clear(self):
        from django.core.cache import cache

        cache.clear()
        yield
        cache.clear()

    def test_first_connection_is_the_one_that_announces(self):
        assert presence.mark_online(7) is True

    def test_a_second_device_does_not_announce_an_arrival_twice(self):
        presence.mark_online(7)
        assert presence.mark_online(7) is False

    def test_closing_one_of_two_sockets_leaves_the_person_online(self):
        presence.mark_online(7)
        presence.mark_online(7)

        assert presence.mark_offline(7) is False
        assert presence.online_ids([7]) == {7}

    def test_closing_the_last_socket_puts_them_offline(self):
        presence.mark_online(7)
        assert presence.mark_offline(7) is True
        assert presence.online_ids([7]) == set()

    def test_a_disconnect_with_no_connection_is_not_an_error(self):
        # A worker restart can lose the counter while the socket's own close
        # still runs. Nothing to announce, and nothing to crash over.
        assert presence.mark_offline(7) is True

    def test_last_seen_survives_going_offline(self):
        presence.mark_online(7)
        presence.mark_offline(7)
        assert 7 in presence.last_seen([7])

    def test_only_the_asked_for_employees_come_back(self):
        presence.mark_online(7)
        presence.mark_online(9)
        assert presence.online_ids([9]) == {9}


class TestPresenceEndpoint:
    def test_reports_the_roster_that_is_online(self):
        request = factory.get("/api/b2b/workspace/presence/")
        force_authenticate(request, user=ALIYA)

        with patch(
            "apps.b2b.workspace.repository.company_employee_ids", return_value=[7, 8]
        ), patch(
            "apps.b2b.workspace.presence.online_ids", return_value={8}
        ), patch(
            "apps.b2b.workspace.presence.last_seen", return_value={8: "2026-01-01T00:00:00+00:00"}
        ):
            response = WorkspacePresenceView.as_view()(request)

        assert response.status_code == 200
        assert response.data["online"] == [8]
        assert response.data["last_seen"] == {"8": "2026-01-01T00:00:00+00:00"}


class TestEveryoneReadAt:
    """What turns one tick into two."""

    def test_null_while_somebody_has_never_opened_the_room(self):
        # Two other members, one of whom has never read it. A double tick here
        # would be saying something untrue.
        assert _everyone_read_at({8: "2026-01-01T10:00:00+00:00"}, [8, 9]) is None

    def test_the_earliest_of_everyone_else_is_the_answer(self):
        read_at = _everyone_read_at(
            {8: "2026-01-01T12:00:00+00:00", 9: "2026-01-01T10:00:00+00:00"}, [8, 9]
        )
        assert read_at == "2026-01-01T10:00:00+00:00"

    def test_a_room_with_nobody_else_in_it_reads_as_unread(self):
        assert _everyone_read_at({}, []) is None


class TestMessagesCarryReadState:
    def test_history_says_when_the_other_side_last_read(self):
        request = factory.get("/api/b2b/workspace/chats/3/messages/")
        force_authenticate(request, user=ALIYA)

        with patch(
            "apps.b2b.workspace.repository.get_thread_for_member", return_value=THREAD
        ), patch(
            "apps.b2b.workspace.repository.list_messages", return_value=[]
        ), patch(
            "apps.b2b.workspace.repository.messages_by_ids", return_value={}
        ), patch(
            "apps.b2b.workspace.repository.attachments_for_messages", return_value={}
        ), patch(
            "apps.b2b.workspace.repository.mark_thread_read", return_value="2026-01-01T09:00:00+00:00"
        ), patch(
            "apps.b2b.workspace.repository.reactions_for_messages", return_value={}
        ), patch(
            "apps.b2b.workspace.repository.list_pinned_messages", return_value=[]
        ), patch(
            "apps.b2b.workspace.repository.thread_read_state",
            return_value={8: "2026-01-01T10:00:00+00:00"},
        ), patch(
            "apps.b2b.workspace.realtime.publish_thread"
        ) as publish:
            response = WorkspaceMessageView.as_view()(request, thread_id=3)

        assert response.status_code == 200
        assert response.data["members_read_at"] == {"8": "2026-01-01T10:00:00+00:00"}
        assert response.data["read_at"] == "2026-01-01T10:00:00+00:00"
        # Opening the room is what tells the sender their message was seen.
        assert publish.called


class TestLiveSections:
    """Which URL belongs to which part of the workspace."""

    @pytest.mark.parametrize(
        "url_name,expected",
        [
            ("ws-tasks", "task"),
            ("ws-task-status", "task"),
            ("ws-subtask-toggle", "task"),
            ("ws-events", "calendar"),
            ("ws-event-detail", "calendar"),
            ("ws-leads", "lead"),
            ("ws-lead-stage", "lead"),
            ("ws-attendance-check-in", "attendance"),
            ("ws-join-request-decide", "join_request"),
            ("ws-requests", "request"),
            ("ws-employee-access", "access"),
            ("ws-access-roles", "access"),
            # Chat publishes precise events of its own; a vague second ping on
            # top of them would have every open thread refetching what it had
            # just been handed.
            ("ws-chats", None),
            ("ws-chat-messages", None),
            ("ws-me", None),
        ],
    )
    def test_section_of(self, url_name, expected):
        view = WorkspaceAPIView()
        view.request = type("R", (), {"resolver_match": type("M", (), {"url_name": url_name})})()
        assert view._live_section() == expected
