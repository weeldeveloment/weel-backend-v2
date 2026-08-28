"""Editing, forwarding, pinning and reacting.

Four new doors onto the same rows. The two that matter most are the ones where
a wrong check is not a broken screen but a forgery: nobody but the author may
rewrite a message, and a forward must not be able to copy text out of a room
the sender was never in.
"""
from unittest.mock import patch

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.workspace.authentication import WorkspaceUser
from apps.b2b.workspace.views import (
    WorkspaceMessageDetailView,
    WorkspaceMessagePinView,
    WorkspaceMessageReactionView,
    WorkspaceMessageView,
    _reaction_payload,
)

factory = APIRequestFactory()

AUTHOR = WorkspaceUser({
    "id": 7,
    "company_id": 55,
    "role": "employee",
    "full_name": "Muallif",
    "phone": "+998900000001",
})

OWNER = WorkspaceUser({
    "id": 9,
    "company_id": 55,
    "role": "owner",
    "full_name": "Rahbar",
    "phone": "+998900000002",
})

THREAD = {"id": 3, "company_id": 55, "participant_ids": [8]}
MESSAGE = {
    "id": 100,
    "thread_id": 3,
    "sender_id": 7,
    "text": "salom",
    "created_at": "2026-01-01T10:00:00+00:00",
}


def _in_thread(**extra):
    """Patches the two lookups every one of these views starts with."""
    return patch.multiple(
        "apps.b2b.workspace.repository",
        get_thread_for_member=lambda *a, **k: THREAD,
        get_message=lambda *a, **k: MESSAGE,
        attachments_for_messages=lambda *a, **k: {},
        **extra,
    )


class TestEditing:
    def test_the_author_can_rewrite_their_own(self):
        request = factory.patch(
            "/api/b2b/workspace/chats/3/messages/100/", {"text": "salom, yangi"}
        )
        force_authenticate(request, user=AUTHOR)

        edited = {**MESSAGE, "text": "salom, yangi", "edited_at": "2026-01-01T11:00:00+00:00"}
        with _in_thread(edit_message=lambda *a, **k: edited), patch(
            "apps.b2b.workspace.realtime.broadcast_edit"
        ) as broadcast:
            response = WorkspaceMessageDetailView.as_view()(
                request, thread_id=3, message_id=100
            )

        assert response.status_code == 200
        assert response.data["text"] == "salom, yangi"
        # The bubble has to be able to say it was changed.
        assert response.data["edited_at"] is not None
        assert broadcast.called

    def test_a_manager_may_not_rewrite_somebody_else(self):
        # Deleting somebody's message is a visible act a manager needs.
        # Rewriting it is a forgery, and no role gets to do it.
        request = factory.patch(
            "/api/b2b/workspace/chats/3/messages/100/", {"text": "men aytmadim"}
        )
        force_authenticate(request, user=OWNER)

        with _in_thread():
            response = WorkspaceMessageDetailView.as_view()(
                request, thread_id=3, message_id=100
            )

        assert response.status_code == 403

    def test_an_edit_cannot_blank_a_message(self):
        # Emptying it is a delete wearing an edit's clothes, and delete has its
        # own rules about who may do it.
        request = factory.patch("/api/b2b/workspace/chats/3/messages/100/", {"text": "   "})
        force_authenticate(request, user=AUTHOR)

        with _in_thread():
            response = WorkspaceMessageDetailView.as_view()(
                request, thread_id=3, message_id=100
            )

        assert response.status_code == 400


class TestForwarding:
    def test_the_server_copies_the_original_text(self):
        # Never the client's. Otherwise anyone could put words in a
        # colleague's mouth and have the bubble attribute them.
        request = factory.post(
            "/api/b2b/workspace/chats/3/messages/",
            {"forward_message_id": 100, "text": "men buni yozmadim"},
        )
        force_authenticate(request, user=AUTHOR)

        sent = {}

        def _send(thread_id, sender_id, text, **kwargs):
            sent.update(text=text, forwarded_from_id=kwargs.get("forwarded_from_id"))
            return {**MESSAGE, "id": 101, "text": text}

        with patch.multiple(
            "apps.b2b.workspace.repository",
            get_thread_for_member=lambda *a, **k: THREAD,
            message_visible_to=lambda *a, **k: {**MESSAGE, "sender_id": 8, "text": "asl matn"},
            send_message=_send,
            attachments_for_messages=lambda *a, **k: {},
        ), patch("apps.b2b.workspace.realtime.broadcast_message"):
            response = WorkspaceMessageView.as_view()(request, thread_id=3)

        assert response.status_code == 201
        assert sent["text"] == "asl matn"
        # The label points at the person, so it survives the original room.
        assert sent["forwarded_from_id"] == 8

    def test_a_message_from_a_room_the_sender_is_not_in_is_refused(self):
        request = factory.post(
            "/api/b2b/workspace/chats/3/messages/", {"forward_message_id": 100}
        )
        force_authenticate(request, user=AUTHOR)

        with patch.multiple(
            "apps.b2b.workspace.repository",
            get_thread_for_member=lambda *a, **k: THREAD,
            message_visible_to=lambda *a, **k: None,
        ):
            response = WorkspaceMessageView.as_view()(request, thread_id=3)

        assert response.status_code == 400


class TestPinning:
    def test_anybody_in_the_room_can_pin(self):
        # The pin is about the room, not about who wrote the message — its
        # whole purpose is putting somebody *else's* address at the top.
        request = factory.post("/api/b2b/workspace/chats/3/messages/100/pin/")
        force_authenticate(request, user=AUTHOR)

        pinned = {}

        def _pin(message_id, thread_id, *, pinned_by):
            pinned["by"] = pinned_by
            return {**MESSAGE, "pinned_at": "2026-01-01T12:00:00+00:00"}

        with _in_thread(set_message_pinned=_pin), patch(
            "apps.b2b.workspace.realtime.publish_thread"
        ):
            response = WorkspaceMessagePinView.as_view()(
                request, thread_id=3, message_id=100
            )

        assert response.status_code == 200
        assert pinned["by"] == 7

    def test_unpinning_clears_who_pinned_it(self):
        request = factory.delete("/api/b2b/workspace/chats/3/messages/100/pin/")
        force_authenticate(request, user=AUTHOR)

        pinned = {}

        def _pin(message_id, thread_id, *, pinned_by):
            pinned["by"] = pinned_by
            return {**MESSAGE, "pinned_at": None}

        with _in_thread(set_message_pinned=_pin), patch(
            "apps.b2b.workspace.realtime.publish_thread"
        ):
            response = WorkspaceMessagePinView.as_view()(
                request, thread_id=3, message_id=100
            )

        assert response.status_code == 200
        assert pinned["by"] is None


class TestReactions:
    def test_a_reaction_comes_back_on_the_message(self):
        request = factory.post(
            "/api/b2b/workspace/chats/3/messages/100/reactions/", {"emoji": "👍"}
        )
        force_authenticate(request, user=AUTHOR)

        with _in_thread(
            toggle_reaction=lambda *a, **k: True,
            reactions_for_messages=lambda ids: {
                100: [
                    {"message_id": 100, "employee_id": 7, "emoji": "👍"},
                    {"message_id": 100, "employee_id": 8, "emoji": "👍"},
                ]
            },
        ), patch("apps.b2b.workspace.realtime.publish_thread"):
            response = WorkspaceMessageReactionView.as_view()(
                request, thread_id=3, message_id=100
            )

        assert response.status_code == 200
        assert response.data["reactions"] == [{"emoji": "👍", "count": 2, "mine": True}]

    def test_an_empty_reaction_is_refused(self):
        request = factory.post(
            "/api/b2b/workspace/chats/3/messages/100/reactions/", {"emoji": "  "}
        )
        force_authenticate(request, user=AUTHOR)

        with _in_thread():
            response = WorkspaceMessageReactionView.as_view()(
                request, thread_id=3, message_id=100
            )

        assert response.status_code == 400


class TestReactionPayload:
    def test_folds_one_entry_per_emoji(self):
        folded = _reaction_payload(
            [
                {"employee_id": 7, "emoji": "👍"},
                {"employee_id": 8, "emoji": "👍"},
                {"employee_id": 8, "emoji": "🔥"},
            ],
            viewer_id=8,
        )
        assert folded == [
            {"emoji": "👍", "count": 2, "mine": True},
            {"emoji": "🔥", "count": 1, "mine": True},
        ]

    def test_mine_is_about_the_reader_and_nobody_else(self):
        folded = _reaction_payload([{"employee_id": 8, "emoji": "👍"}], viewer_id=7)
        assert folded == [{"emoji": "👍", "count": 1, "mine": False}]

    def test_the_order_does_not_move_under_a_tap(self):
        # Most-reacted first, then by emoji. A list that reorders itself every
        # time somebody taps is one nobody can aim at.
        folded = _reaction_payload(
            [
                {"employee_id": 1, "emoji": "🔥"},
                {"employee_id": 2, "emoji": "👍"},
                {"employee_id": 3, "emoji": "👍"},
            ],
            viewer_id=99,
        )
        assert [r["emoji"] for r in folded] == ["👍", "🔥"]

    def test_no_reactions_is_an_empty_list(self):
        assert _reaction_payload(None, viewer_id=7) == []
