"""Weel AI, the built-in analyst, where it does not need a database.

What is worth pinning is the shape of the thing rather than the vendor's
prose: which window a period means and which reports a date is due, how
the model's answer is read — JSON as asked, JSON in a fence anyway, and no
JSON at all — how the per-person rows fold into departments, and whose key
the report runs on.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="Asia/Tashkent", REST_FRAMEWORK={})

from apps.b2b.workspace import analyst
from apps.b2b.workspace import analyst_repository as repo


# ─── Windows ─────────────────────────────────────────────────────────────────

def test_a_day_is_yesterday_in_local_time():
    # 03:00 UTC on the 5th is 08:00 in Tashkent, so "today" starts on the 5th.
    now = datetime(2026, 9, 5, 3, 0, tzinfo=dt_timezone.utc)
    window = analyst.window_for("day", now=now)
    assert window.start_date == date(2026, 9, 4)
    assert window.end_date == date(2026, 9, 5)


def test_the_longer_windows_end_at_the_same_midnight():
    now = datetime(2026, 9, 5, 3, 0, tzinfo=dt_timezone.utc)
    assert analyst.window_for("week", now=now).start_date == date(2026, 8, 29)
    assert analyst.window_for("month", now=now).start_date == date(2026, 8, 6)
    assert analyst.window_for("year", now=now).start_date == date(2025, 9, 5)


def test_an_unknown_period_is_refused():
    with pytest.raises(ValueError):
        analyst.window_for("fortnight")


def test_which_reports_a_date_is_due():
    assert analyst.periods_due(date(2026, 9, 3)) == ["day"]           # a Thursday
    assert analyst.periods_due(date(2026, 9, 7)) == ["day", "week"]   # a Monday
    assert analyst.periods_due(date(2026, 10, 1)) == ["day", "month"]
    assert analyst.periods_due(date(2027, 1, 1)) == ["day", "month", "year"]
    # 1 February 2027 is a Monday: everything but the year.
    assert analyst.periods_due(date(2027, 2, 1)) == ["day", "week", "month"]


# ─── Reading the answer ──────────────────────────────────────────────────────

ANSWER = {
    "score": 71.6,
    "uz": {"headline": "Savdo yaxshi, vazifalar kechikmoqda", "text": "Kunlik hisobot…"},
    "ru": {"headline": "Продажи хорошо, задачи отстают", "text": "Дневной отчёт…"},
}


def test_json_answer_is_read_in_both_languages():
    verdict = analyst.parse_answer(json.dumps(ANSWER, ensure_ascii=False))
    assert verdict.score == 72
    assert verdict.headline_uz.startswith("Savdo")
    assert verdict.headline_ru.startswith("Продажи")
    assert verdict.text_uz == "Kunlik hisobot…"
    assert verdict.text_ru == "Дневной отчёт…"


def test_a_fenced_answer_still_reads():
    raw = "```json\n" + json.dumps(ANSWER, ensure_ascii=False) + "\n```"
    assert analyst.parse_answer(raw).score == 72


def test_prose_around_the_json_is_ignored():
    raw = "Here is the report:\n" + json.dumps(ANSWER) + "\nHope this helps."
    assert analyst.parse_answer(raw).text_ru == "Дневной отчёт…"


def test_an_answer_with_no_json_is_kept_as_the_text_of_both():
    verdict = analyst.parse_answer("Kompaniya bu hafta yaxshi ishladi.")
    assert verdict.score is None
    assert verdict.text_uz == verdict.text_ru == "Kompaniya bu hafta yaxshi ishladi."


def test_one_missing_language_falls_back_to_the_other():
    verdict = analyst.parse_answer(json.dumps({"score": 40, "uz": {"headline": "H", "text": "T"}}))
    assert verdict.text_ru == "T"
    assert verdict.headline_ru == "H"


def test_score_is_clamped_and_bad_scores_are_dropped():
    assert analyst.parse_answer(json.dumps({"score": 140, "uz": {"text": "x"}})).score == 100
    assert analyst.parse_answer(json.dumps({"score": "n/a", "uz": {"text": "x"}})).score is None


# ─── Gathering ───────────────────────────────────────────────────────────────

ROWS = [
    {
        "employee_id": 1, "full_name": "Aziz", "position": "Sotuvchi", "role": "employee",
        "department_id": 10, "department_name": "Savdo",
        "completed_count": 4, "due_count": 2, "on_time_count": 1,
        "open_count": 3, "overdue_count": 1,
        "won_count": 2, "won_amount": Decimal("1500000.00"), "lost_count": 1,
        "present_days": 5, "late_days": 1, "absent_days": 0, "unexcused_days": 0,
    },
    {
        "employee_id": 2, "full_name": "Madina", "position": "Sotuvchi", "role": "employee",
        "department_id": 10, "department_name": "Savdo",
        "completed_count": 0, "due_count": 0, "on_time_count": 0,
        "open_count": 0, "overdue_count": 0,
        "won_count": 0, "won_amount": None, "lost_count": 0,
        "present_days": 2, "late_days": 0, "absent_days": 3, "unexcused_days": 2,
    },
    {
        "employee_id": 3, "full_name": "Sardor", "position": "Dizayner", "role": "manager",
        "department_id": None, "department_name": None,
        "completed_count": 7, "due_count": 7, "on_time_count": 7,
        "open_count": 2, "overdue_count": 0,
        "won_count": 0, "won_amount": None, "lost_count": 0,
        "present_days": 5, "late_days": 0, "absent_days": 0, "unexcused_days": 0,
    },
]

TOTALS = {"leads_created": 9, "leads_unclaimed": 2, "tasks_created": 11, "tasks_unassigned": 1}


@pytest.fixture
def company_rows():
    with patch.object(repo, "company_row", return_value={"name": "Weel", "industry": "IT", "city": "Toshkent"}), \
         patch.object(repo, "employee_window_stats", return_value=ROWS), \
         patch.object(repo, "company_totals", return_value=TOTALS):
        yield


def test_people_fold_into_departments(company_rows):
    window = analyst.window_for("week", now=datetime(2026, 9, 5, 3, 0, tzinfo=dt_timezone.utc))
    data = analyst.gather(55, window)

    assert data["company"] == {"name": "Weel", "industry": "IT", "city": "Toshkent", "headcount": 3}
    assert data["window"] == {"period": "week", "start": "2026-08-29", "end": "2026-09-04", "days": 7}

    by_name = {d["name"]: d for d in data["departments"]}
    assert set(by_name) == {"Savdo", "—"}
    savdo = by_name["Savdo"]
    assert savdo["headcount"] == 2
    assert savdo["tasks_completed"] == 4
    assert savdo["deals_won"] == 2
    assert savdo["deals_won_amount"] == 1500000.0
    assert savdo["days_absent_unexcused"] == 2

    aziz = data["employees"][0]
    assert aziz["tasks_on_time_rate"] == 0.5
    # No deadlines at all: not 0%, which would read as "always late".
    assert data["employees"][1]["tasks_on_time_rate"] is None
    assert data["company_totals"]["tasks_completed"] == 11
    assert data["company_totals"]["leads_unclaimed"] == 2


def test_the_prompt_carries_the_data_as_json(company_rows):
    window = analyst.window_for("day", now=datetime(2026, 9, 5, 3, 0, tzinfo=dt_timezone.utc))
    prompt = analyst.build_prompt(analyst.gather(55, window))
    assert "daily report (kunlik / дневной)" in prompt
    payload = json.loads(prompt.split("DATA:\n", 1)[1])
    assert payload["employees"][0]["name"] == "Aziz"


# ─── Whose key ───────────────────────────────────────────────────────────────

def test_the_deployment_key_wins_when_set(settings):
    settings.B2B_ANALYST_PROVIDER = "claude"
    settings.B2B_ANALYST_API_KEY = "sk-ant-deployment"
    settings.B2B_ANALYST_MODEL = ""
    vendor = analyst.vendor_for(55)
    assert vendor.builtin is True
    assert vendor.provider == "claude"
    assert vendor.model == "claude-opus-5"
    assert vendor.key == "sk-ant-deployment"


def test_without_a_deployment_key_the_workspace_key_is_used(settings):
    settings.B2B_ANALYST_API_KEY = ""

    def integration(company_id, provider):
        if provider == "chatgpt":
            return {"id": 1, "access_token_enc": "enc", "ai_model": "gpt-5"}
        return None

    with patch("apps.b2b.workspace.analyst.int_repo.get_integration", side_effect=integration), \
         patch("apps.b2b.workspace.analyst.crypto.decrypt", return_value="sk-workspace"):
        vendor = analyst.vendor_for(55)
    assert vendor.builtin is False
    assert (vendor.provider, vendor.model, vendor.key) == ("chatgpt", "gpt-5", "sk-workspace")


def test_no_key_anywhere_means_unavailable(settings):
    settings.B2B_ANALYST_API_KEY = ""
    with patch("apps.b2b.workspace.analyst.int_repo.get_integration", return_value=None):
        assert analyst.vendor_for(55) is None
        assert analyst.is_available(55) is False


# ─── Writing a report ────────────────────────────────────────────────────────

def test_generate_stores_the_verdict(settings, company_rows):
    settings.B2B_ANALYST_PROVIDER = "claude"
    settings.B2B_ANALYST_API_KEY = "sk-ant-deployment"
    stored = {}

    def upsert(**fields):
        stored.update(fields)
        return {"id": 9, **fields}

    with patch("apps.b2b.workspace.analyst.ai.complete",
               return_value=json.dumps(ANSWER, ensure_ascii=False)) as complete, \
         patch.object(repo, "upsert_report", side_effect=upsert):
        report = analyst.generate(
            55, "day", requested_by_id=7,
            now=datetime(2026, 9, 5, 3, 0, tzinfo=dt_timezone.utc),
        )

    assert report["id"] == 9
    assert stored["status"] == "ready"
    assert stored["score"] == 72
    assert stored["headline_uz"].startswith("Savdo")
    assert stored["period_start"] == date(2026, 9, 4)
    assert stored["requested_by_id"] == 7
    # The whole company went to the model, with the system prompt, and with
    # room for two languages' worth of report.
    _, kwargs = complete.call_args
    assert kwargs["system"] == analyst.SYSTEM_PROMPT
    assert kwargs["max_tokens"] == settings.B2B_ANALYST_MAX_OUTPUT_TOKENS


def test_a_vendor_failure_is_stored_as_a_failed_row(settings, company_rows):
    from apps.b2b.integrations.ai import AiError

    settings.B2B_ANALYST_API_KEY = "sk-ant-deployment"
    stored = {}

    def upsert(**fields):
        stored.update(fields)
        return {"id": 10, **fields}

    with patch("apps.b2b.workspace.analyst.ai.complete", side_effect=AiError("Rate limited", 429)), \
         patch.object(repo, "upsert_report", side_effect=upsert):
        analyst.generate(55, "week")
    assert stored["status"] == "failed"
    assert stored["error"] == "Rate limited"
    assert stored["text_uz"] == ""


def test_generate_without_a_key_raises(settings):
    settings.B2B_ANALYST_API_KEY = ""
    with patch("apps.b2b.workspace.analyst.int_repo.get_integration", return_value=None):
        with pytest.raises(analyst.AnalystUnavailable):
            analyst.generate(55, "day")


# ─── The advisor's extras ────────────────────────────────────────────────────

def test_extras_fold_the_funnel_shelf_and_notes_into_the_report():
    from apps.b2b.workspace import advisor_repository as arepo
    from apps.b2b.workspace import inventory_repository as inv_repo

    window = analyst.window_for("week", now=datetime(2026, 9, 5, 3, 0, tzinfo=dt_timezone.utc))
    funnel = {"open_by_stage": [{"stage": "new", "count": 2}], "closed_recently": {"created": 2}}
    with patch.object(arepo, "funnel", return_value=funnel), \
         patch.object(inv_repo, "summary", return_value={"product_count": 3, "daily": [1, 2]}), \
         patch.object(arepo, "low_stock", return_value=[{"name": "Qog'oz"}]), \
         patch.object(arepo, "top_products", return_value=[]), \
         patch.object(arepo, "list_notes", return_value=[{"id": 1, "text": "No wholesale."}]):
        out = analyst.extras(55, window)
    assert out["funnel"] is funnel
    assert out["inventory"]["product_count"] == 3
    assert "daily" not in out["inventory"]
    assert out["inventory"]["low_stock"] == [{"name": "Qog'oz"}]
    assert out["owner_notes"] == ["No wholesale."]


def test_extras_leave_out_what_the_company_does_not_use():
    from apps.b2b.workspace import advisor_repository as arepo
    from apps.b2b.workspace import inventory_repository as inv_repo

    window = analyst.window_for("day")
    empty_funnel = {"open_by_stage": [{"stage": "new", "count": 0}], "closed_recently": {"created": 0}}
    with patch.object(arepo, "funnel", return_value=empty_funnel), \
         patch.object(inv_repo, "summary", side_effect=RuntimeError("no module")), \
         patch.object(arepo, "list_notes", return_value=[]):
        assert analyst.extras(55, window) == {}
