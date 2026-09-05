"""Weel AI as the owner's business advisor, where it needs no database.

Pinned: the toolbox dispatches by name and refuses what it does not have;
the saved notes reach the prompt with their ids; and the chat view sends
whoever runs the company to the advisor and everybody else to the plain
own-work answer.
"""
from __future__ import annotations

from datetime import datetime, timezone as dt_timezone
from unittest.mock import patch

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="Asia/Tashkent", REST_FRAMEWORK={})

from rest_framework.test import APIRequestFactory, force_authenticate

from apps.b2b.integrations import ai
from apps.b2b.workspace import advisor, analyst, analyst_views
from apps.b2b.workspace.analyst_views import WeelAiChatView
from apps.b2b.workspace.authentication import WorkspaceUser

factory = APIRequestFactory()


class _User(WorkspaceUser):
    def __init__(self, employee: dict, caps: dict | None = None):
        super().__init__(employee)
        self._caps = caps or {}

    @property
    def capabilities(self) -> dict:
        return self._caps


OWNER = _User(
    {"id": 1, "company_id": 55, "role": "owner", "full_name": "Bobur", "position": "Direktor"},
    {"sees_all_company_data": True},
)
EMPLOYEE = _User(
    {"id": 8, "company_id": 55, "role": "employee", "full_name": "Madina", "position": "Sotuvchi"},
)

VENDOR = analyst.Vendor(provider="claude", key="sk-ant-deployment", model="claude-opus-5", builtin=True)


# ─── The toolbox ─────────────────────────────────────────────────────────────

def test_every_tool_has_a_handler():
    box = advisor.Toolbox(OWNER)
    for tool in advisor.TOOLS:
        assert callable(getattr(box, f"tool_{tool.name}")), tool.name


def test_an_unknown_tool_is_refused_to_the_model():
    with pytest.raises(ai.ToolError):
        advisor.Toolbox(OWNER)("delete_everything", {})


def test_remember_stores_a_note_for_the_company_and_the_person():
    with patch.object(advisor.arepo, "add_note", return_value={"id": 7}) as add:
        result = advisor.Toolbox(OWNER)("remember", {"text": "Alpha undercuts us on price."})
    add.assert_called_once_with(55, 1, "Alpha undercuts us on price.")
    assert result == {"saved": True, "note_id": 7, "text": "Alpha undercuts us on price."}


def test_remember_refuses_an_empty_note():
    with pytest.raises(ai.ToolError):
        advisor.Toolbox(OWNER)("remember", {"text": " "})


def test_a_bad_period_is_refused_before_any_query():
    with pytest.raises(ai.ToolError):
        advisor.Toolbox(OWNER)("company_overview", {"period": "fortnight"})


def test_tool_results_are_json_clean():
    from decimal import Decimal

    rows = [{"stage": "new", "amount": Decimal("12.50"), "when": datetime(2026, 9, 1, tzinfo=dt_timezone.utc)}]
    with patch.object(advisor.arepo, "funnel", return_value={"biggest_open": rows}):
        out = advisor.Toolbox(OWNER)("sales_funnel", {"days": 999})
    assert out["biggest_open"][0]["amount"] == 12.5
    assert out["biggest_open"][0]["when"].startswith("2026-09-01")


# ─── The prompt ──────────────────────────────────────────────────────────────

def test_notes_reach_the_prompt_with_their_ids():
    notes = [
        {"id": 3, "text": "Madina is on leave until the 20th.", "created_at": datetime(2026, 9, 1, tzinfo=dt_timezone.utc)},
        {"id": 5, "text": "We dropped the wholesale line.", "created_at": datetime(2026, 9, 4, tzinfo=dt_timezone.utc)},
    ]
    with patch.object(advisor.arepo, "list_notes", return_value=notes), \
         patch.object(advisor.b2b_repo, "get_company", return_value={"name": "Weel", "industry": "IT", "city": "Toshkent"}):
        prompt = advisor.system_prompt(OWNER, language="uz")
    assert "[id 3, 2026-09-01] Madina is on leave until the 20th." in prompt
    assert "[id 5, 2026-09-04] We dropped the wholesale line." in prompt
    assert "Weel (IT, Toshkent)" in prompt
    assert "Bobur, Direktor, owner" in prompt
    assert "Uzbek (Latin script)" in prompt


def test_no_notes_says_so():
    with patch.object(advisor.arepo, "list_notes", return_value=[]), \
         patch.object(advisor.b2b_repo, "get_company", return_value={}):
        assert advisor.NO_NOTES in advisor.system_prompt(OWNER, language="ru")


# ─── The view ────────────────────────────────────────────────────────────────

def _post(user, text="Salom"):
    request = factory.post("/analyst/chat/", {"text": text}, format="json")
    force_authenticate(request, user=user)
    return WeelAiChatView.as_view()(request)


@pytest.fixture
def chat_store():
    stored = []
    conversation = {"id": 42, "message_count": 0}

    def append(conversation_id, role, text):
        stored.append((role, text))
        return {"id": len(stored), "role": role, "text": text}

    with patch.object(analyst, "vendor_for", return_value=VENDOR), \
         patch.object(analyst, "is_available", return_value=True), \
         patch.object(analyst_views.ai_repo, "find_owned_conversation", return_value=conversation), \
         patch.object(analyst_views.ai_repo, "append_message", side_effect=append), \
         patch.object(analyst_views.ai_repo, "recent_messages", return_value=[{"role": "user", "text": "Salom"}]), \
         patch.object(analyst_views.ai_repo, "list_messages", return_value=[]), \
         patch.object(analyst_views.repo, "latest_report", return_value=None), \
         patch.object(analyst_views.repo, "unseen_count", return_value=0):
        yield stored


def test_whoever_runs_the_company_gets_the_advisor(chat_store):
    with patch.object(advisor, "answer", return_value="Bu hafta uchta narsa.") as answer, \
         patch.object(ai, "complete") as plain:
        response = _post(OWNER, "Bu hafta nima qilay?")
    assert response.status_code == 200
    assert response.data["advisor"] is True
    answer.assert_called_once()
    plain.assert_not_called()
    assert chat_store == [("user", "Bu hafta nima qilay?"), ("assistant", "Bu hafta uchta narsa.")]


def test_an_employee_gets_the_own_work_answer(chat_store):
    with patch.object(advisor, "answer") as answer, \
         patch.object(ai, "complete", return_value="Bugun avval bitimni yoping.") as plain, \
         patch.object(analyst_views, "_weel_ai_context", return_value={"me": None, "open_tasks": []}), \
         patch.object(analyst_views.b2b_repo, "get_company", return_value={"name": "Weel"}):
        response = _post(EMPLOYEE, "Bugun nima qilay?")
    assert response.status_code == 200
    assert response.data["advisor"] is False
    answer.assert_not_called()
    plain.assert_called_once()
    assert chat_store[-1] == ("assistant", "Bugun avval bitimni yoping.")


def test_the_advisor_runs_on_the_deployment_key_with_the_tools():
    with patch.object(ai, "complete_with_tools", return_value="ok") as run, \
         patch.object(advisor, "system_prompt", return_value="SYSTEM"), \
         patch.object(advisor, "turns_for", return_value=[ai.Turn("user", "hi")]):
        assert advisor.answer(OWNER, {"id": 42}, vendor=VENDOR, language="uz") == "ok"
    args, kwargs = run.call_args
    assert args[:3] == ("claude", "sk-ant-deployment", "claude-opus-5")
    assert kwargs["tools"] is advisor.TOOLS
    assert isinstance(kwargs["call"], advisor.Toolbox)
    assert kwargs["system"] == "SYSTEM"


def test_a_report_is_talked_through_in_the_weel_ai_chat(chat_store):
    from apps.b2b.workspace.analyst_views import AnalystDiscussView

    report = {"id": 5, "status": "ready", "headline_uz": "Savdo tushdi.", "text_uz": "Batafsil.",
              "headline_ru": "Продажи упали.", "text_ru": "Подробно."}
    with patch.object(analyst_views.repo, "get_report", return_value=report), \
         patch.object(advisor, "answer", return_value="Uch qadam.") as answer:
        request = factory.post("/analyst/reports/5/discuss/", {}, format="json")
        force_authenticate(request, user=OWNER)
        response = AnalystDiscussView.as_view()(request, report_id=5)
    assert response.status_code == 200
    answer.assert_called_once()
    assert chat_store[0] == (advisor.ROLE_REPORT, "Savdo tushdi.\n\nBatafsil.")
    assert chat_store[1][0] == "user" and chat_store[1][1].startswith("Qanday tuzatish")
    assert chat_store[2] == ("assistant", "Uch qadam.")


def test_a_report_card_reaches_the_model_as_the_persons_words():
    history = [
        {"role": "user", "text": "Salom"},
        {"role": "assistant", "text": "Salom!"},
        {"role": advisor.ROLE_REPORT, "text": "Savdo bo'limi 3 ta bitim yutqazdi."},
        {"role": "user", "text": "Qanday tuzatish mumkin?"},
    ]
    with patch.object(advisor.ai_repo, "recent_messages", return_value=history):
        turns = advisor.turns_for(42)
    assert [t.role for t in turns] == ["user", "assistant", "user", "user"]
    assert turns[2].text.startswith("Weel AI report:")
