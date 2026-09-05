"""The AI assistant's row on the chat list, and the saved room above it.

Pinned here: that the assistant refuses politely when no vendor is
connected, that a Weel AI report dropped into the chat reaches the vendor
as the person's words, and that the saved room is what the thread list
puts first.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.workspace import assistant
from apps.b2b.workspace.assistant import AssistantMessagesView, AssistantView
from apps.b2b.workspace.authentication import WorkspaceUser
from apps.b2b.workspace.views import _thread_payload

factory = APIRequestFactory()


class _User(WorkspaceUser):
    def __init__(self, employee: dict, caps: dict | None = None):
        super().__init__(employee)
        self._caps = caps or {}

    @property
    def capabilities(self) -> dict:
        return self._caps


EMPLOYEE = _User({
    "id": 8, "company_id": 55, "role": "employee", "full_name": "Madina",
    "position": "Sotuvchi", "phone": "+998900000008",
})


def _connected(company_id):
    return "claude", {"id": 1, "ai_model": "claude-opus-5", "status": "connected"}, "sk-ant-x"


def _nobody(company_id):
    return None, None, None


@pytest.fixture(autouse=True)
def _no_own_key():
    """These tests are about the workspace key. Nobody here pasted one of
    their own, and there is no database to ask."""
    with patch.object(assistant.assistant_keys, "get", return_value=None):
        yield


# ─── The thread list ─────────────────────────────────────────────────────────

def test_a_thread_payload_names_its_kind():
    saved = {"id": 3, "kind": "saved", "participant_ids": [], "unread": 0}
    assert _thread_payload(saved)["kind"] == "saved"
    # A row read by a query that did not select the column is an ordinary
    # chat, which is what every row was before the column existed.
    assert _thread_payload({"id": 4})["kind"] == "chat"


# ─── The assistant ───────────────────────────────────────────────────────────

def test_status_says_when_nobody_has_connected_a_vendor():
    from django.utils import translation

    with translation.override("uz"), \
         patch.object(assistant, "connected_vendor", _nobody), \
         patch.object(assistant, "conversation_for", return_value=None):
        request = factory.get("/assistant/")
        force_authenticate(request, user=EMPLOYEE)
        response = AssistantView.as_view()(request)
    assert response.status_code == 200
    assert response.data["connected"] is False
    # Anybody may connect their own key now, whatever their role.
    assert response.data["can_connect"] is True
    assert response.data["own"] is False
    assert response.data["connection"] is None
    assert response.data["message_count"] == 0
    assert response.data["name"] == "AI yordamchi"


def test_sending_without_a_vendor_is_refused_without_storing_anything():
    with patch.object(assistant, "connected_vendor", _nobody), \
         patch.object(assistant.ai_repo, "append_message") as append:
        request = factory.post("/assistant/messages/", {"text": "Salom"}, format="json")
        force_authenticate(request, user=EMPLOYEE)
        response = AssistantMessagesView.as_view()(request)
    assert response.status_code == 409
    append.assert_not_called()


def test_a_message_is_stored_before_the_vendor_answers_and_the_answer_after():
    stored: list[tuple[str, str]] = []
    conversation = {"id": 42, "message_count": 0}

    def append(conversation_id, role, text):
        stored.append((role, text))
        return {"id": len(stored), "role": role, "text": text}

    with patch.object(assistant, "connected_vendor", _connected), \
         patch.object(assistant, "conversation_for", return_value=conversation), \
         patch.object(assistant.ai_repo, "append_message", side_effect=append), \
         patch.object(assistant.ai_repo, "recent_messages",
                      return_value=[{"role": "user", "text": "Salom"}]), \
         patch.object(assistant.ai_repo, "list_messages", return_value=[]), \
         patch.object(assistant.b2b_repo, "get_company", return_value={"name": "Weel"}), \
         patch.object(assistant.ai, "complete", return_value="Salom, Madina!") as complete:
        request = factory.post("/assistant/messages/", {"text": "Salom"}, format="json")
        force_authenticate(request, user=EMPLOYEE)
        response = AssistantMessagesView.as_view()(request)

    assert response.status_code == 200
    assert stored == [("user", "Salom"), ("assistant", "Salom, Madina!")]
    # The person, their job and their company are in the briefing, and so
    # is the analyst the assistant is told it works with.
    _, kwargs = complete.call_args
    assert "Madina, Sotuvchi, employee at Weel" in kwargs["system"]
    assert "Weel AI" in kwargs["system"]


def test_a_report_turn_reaches_the_vendor_as_the_persons_words():
    history = [
        {"role": "user", "text": "Salom"},
        {"role": "assistant", "text": "Salom!"},
        {"role": assistant.ROLE_REPORT, "text": "Savdo bo'limi 3 ta bitim yutqazdi."},
        {"role": "user", "text": "Qanday tuzatish mumkin?"},
    ]
    with patch.object(assistant.ai_repo, "recent_messages", return_value=history):
        turns = assistant._turns(42)
    assert [t.role for t in turns] == ["user", "assistant", "user", "user"]
    assert turns[2].text.startswith("Weel AI report:")
    assert "3 ta bitim" in turns[2].text


def test_a_vendor_error_marks_the_key_and_says_why():
    from apps.b2b.integrations.ai import AiError

    conversation = {"id": 42, "message_count": 1}
    with patch.object(assistant, "connected_vendor", _connected), \
         patch.object(assistant.ai_repo, "recent_messages",
                      return_value=[{"role": "user", "text": "Salom"}]), \
         patch.object(assistant.b2b_repo, "get_company", return_value={"name": "Weel"}), \
         patch.object(assistant.ai, "complete", side_effect=AiError("Bad key", 401)), \
         patch.object(assistant.int_repo, "set_integration_status") as mark:
        text, refusal = assistant.answer(EMPLOYEE, conversation)
    assert text is None
    assert refusal.status_code == 400
    assert refusal.data["detail"] == "Bad key"
    mark.assert_called_once()


@pytest.mark.parametrize("language,expected", [("uz", "AI yordamchi"), ("ru", "AI-помощник")])
def test_the_row_is_named_in_the_readers_language(language, expected):
    from django.utils import translation

    with translation.override(language), \
         patch.object(assistant, "connected_vendor", _nobody), \
         patch.object(assistant, "conversation_for", return_value=None):
        assert assistant.status_payload(EMPLOYEE)["name"] == expected
