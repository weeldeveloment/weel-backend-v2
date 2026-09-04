"""Weel AI — the analyst built into every workspace.

The button at the top right of the chat list. Where the assistant in
`assistant.py` answers what one employee asks, this one reads the whole
company — tasks, the sales funnel, the calendar, attendance, per person
and per department — and writes a report about it: daily, weekly, monthly
and yearly, on a schedule (`analyst_tasks.py`) and on demand
(`analyst_views.py`). The report names which departments and which people
are doing well and which are falling behind, and what to do about it.

**Whose key.** The deployment's own, from `B2B_ANALYST_API_KEY`, so every
workspace gets reports without pasting anything. A deployment without one
falls back to whichever vendor the workspace connected itself — the same
key the assistant runs on — so the feature still works, just on their bill.

**Two languages at once.** A report is written when nobody is reading it,
so there is no request to take a language from; and the app's users are
split between Uzbek and Russian inside one company. The model is asked for
both in one answer, as JSON, and the app picks the one its reader set.
One call rather than two: the numbers are the expensive part of the
prompt, and they are the same in both.

**No vendor knowledge.** The prompt is plain text and the answer is JSON;
`integrations.ai.complete` does the talking for either vendor.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from apps.b2b.integrations import ai, crypto
from apps.b2b.integrations import repository as int_repo
from apps.b2b.models import IntegrationProvider
from apps.b2b.workspace import analyst_repository as repo

logger = logging.getLogger(__name__)

PERIODS = repo.PERIODS

#: How the model is told to name each window, in each language, so a
#: headline says "kunlik" and not "1-kunlik".
PERIOD_LABELS = {
    "day": {"uz": "kunlik", "ru": "дневной", "en": "daily"},
    "week": {"uz": "haftalik", "ru": "недельный", "en": "weekly"},
    "month": {"uz": "oylik", "ru": "месячный", "en": "monthly"},
    "year": {"uz": "yillik", "ru": "годовой", "en": "yearly"},
}

#: Which model the deployment key runs on when `B2B_ANALYST_MODEL` is unset.
DEFAULT_MODELS = {
    IntegrationProvider.CLAUDE: "claude-opus-5",
    IntegrationProvider.CHATGPT: "gpt-5",
}

#: How long "Hozir tahlil qil" refuses to rerun the same period. A report is
#: a vendor call over the whole company, and the numbers do not change by
#: the minute.
RERUN_COOLDOWN_MINUTES = 30

SYSTEM_PROMPT = """You are Weel AI, the business analyst built into the Weel workspace app. You are given one company's numbers over a window — tasks, sales, attendance, per employee and per department — and you write the report its owner reads at the start of the day.

Write like a sharp, fair chief of staff: direct, concrete, no filler. Name departments and people. Say who did well and who is falling behind, with the numbers that show it, and say what to do about it this week. Be honest but not cruel — a person with no tasks assigned is not lazy, they are unused, and the report should say which. Prefer three sharp findings to ten vague ones. Do not invent anything the data does not contain; if a section has no data, say so in one line and move on.

You must answer with a single JSON object and nothing else — no prose before or after, no code fences. The object has exactly these keys:

{
  "score": <integer 0-100, the company's overall health over this window>,
  "uz": {"headline": <one sentence, Uzbek in Latin script>, "text": <the report, Uzbek in Latin script>},
  "ru": {"headline": <one sentence, Russian>, "text": <the report, Russian>}
}

Both "text" values are the same report, written natively in each language — not a translation of one into the other, and never mixing the two. Plain text with short paragraphs; you may use "•" for bullet lines. No markdown headings, no bold markers, no tables. Structure each report as: an opening verdict (2-3 sentences); what went well; what is falling behind (departments, then people); what to do next (numbered, at most five); and a closing line about the score. Aim for 250-450 words per language for a day or a week, up to 700 for a month or a year."""


@dataclass
class Window:
    period: str
    start: datetime
    end: datetime

    @property
    def start_date(self) -> date:
        return self.start.date()

    @property
    def end_date(self) -> date:
        return self.end.date()


def window_for(period: str, *, now: datetime | None = None) -> Window:
    """The window a period stands for, ending at the start of today.

    The reports run in the morning about what has already happened, so a
    "day" is yesterday — midnight to midnight, local time — a "week" the
    seven days ending last night, a "month" the thirty days before that,
    and a "year" the last 365. Calendar months would leave the 1st with a
    report about one day; rolling windows read the same on every date.
    """
    if period not in PERIODS:
        raise ValueError(f"Unknown period {period!r}")
    now = now or timezone.now()
    local = timezone.localtime(now)
    today = local.replace(hour=0, minute=0, second=0, microsecond=0)
    days = {"day": 1, "week": 7, "month": 30, "year": 365}[period]
    return Window(period=period, start=today - timedelta(days=days), end=today)


def periods_due(today: date) -> list[str]:
    """Which reports the nightly pass writes on a date. Daily every day, the
    weekly on Monday about the week that ended, the monthly on the 1st, the
    yearly on the 1st of January."""
    due = ["day"]
    if today.weekday() == 0:
        due.append("week")
    if today.day == 1:
        due.append("month")
        if today.month == 1:
            due.append("year")
    return due


# ─── Gathering ───────────────────────────────────────────────────────────────

def _num(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return value


def _json_default(value: Any) -> Any:
    return float(value) if isinstance(value, Decimal) else str(value)


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 2) if whole else None


def gather(company_id: int, window: Window) -> dict[str, Any]:
    """Everything the prompt is written from, as one JSON-able dict.

    Per-employee rows straight from the query; per-department totals summed
    here — the model is much better at "the sales department closed nothing
    this week" when the department line is already on the page than when it
    has to add up six people first.
    """
    company = repo.company_row(company_id) or {}
    rows = repo.employee_window_stats(company_id, window.start, window.end)
    totals = repo.company_totals(company_id, window.start, window.end)

    employees = []
    departments: dict[str, dict[str, Any]] = {}
    for row in rows:
        dept = row.get("department_name") or "—"
        person = {
            "id": row["employee_id"],
            "name": row["full_name"],
            "position": row.get("position") or "",
            "role": row.get("role") or "employee",
            "department": dept,
            "tasks_completed": int(row["completed_count"] or 0),
            "tasks_on_time_rate": _rate(int(row["on_time_count"] or 0), int(row["due_count"] or 0)),
            "tasks_open": int(row["open_count"] or 0),
            "tasks_overdue": int(row["overdue_count"] or 0),
            "deals_won": int(row["won_count"] or 0),
            "deals_won_amount": _num(row["won_amount"]) or 0,
            "deals_lost": int(row["lost_count"] or 0),
            "days_present": int(row["present_days"] or 0),
            "days_late": int(row["late_days"] or 0),
            "days_absent": int(row["absent_days"] or 0),
            "days_absent_unexcused": int(row["unexcused_days"] or 0),
        }
        employees.append(person)
        bucket = departments.setdefault(dept, {
            "name": dept, "headcount": 0, "tasks_completed": 0, "tasks_open": 0,
            "tasks_overdue": 0, "deals_won": 0, "deals_won_amount": 0,
            "deals_lost": 0, "days_absent": 0, "days_absent_unexcused": 0,
        })
        bucket["headcount"] += 1
        for key in ("tasks_completed", "tasks_open", "tasks_overdue", "deals_won",
                    "deals_won_amount", "deals_lost", "days_absent",
                    "days_absent_unexcused"):
            bucket[key] += person[key]

    company_line = {
        "tasks_completed": sum(p["tasks_completed"] for p in employees),
        "tasks_open": sum(p["tasks_open"] for p in employees),
        "tasks_overdue": sum(p["tasks_overdue"] for p in employees),
        "deals_won": sum(p["deals_won"] for p in employees),
        "deals_won_amount": sum(p["deals_won_amount"] for p in employees),
        "deals_lost": sum(p["deals_lost"] for p in employees),
        "days_absent_unexcused": sum(p["days_absent_unexcused"] for p in employees),
        **totals,
    }

    return {
        "company": {
            "name": company.get("name") or "",
            "industry": company.get("industry") or "",
            "city": company.get("city") or "",
            "headcount": len(employees),
        },
        "window": {
            "period": window.period,
            "start": window.start_date.isoformat(),
            "end": (window.end_date - timedelta(days=1)).isoformat(),
            "days": (window.end_date - window.start_date).days,
        },
        "company_totals": company_line,
        "departments": sorted(departments.values(), key=lambda d: d["name"]),
        "employees": employees,
    }


# ─── The prompt and its answer ───────────────────────────────────────────────

def build_prompt(data: dict[str, Any]) -> str:
    period = data["window"]["period"]
    labels = PERIOD_LABELS.get(period, PERIOD_LABELS["day"])
    return (
        f"Write the {labels['en']} report ({labels['uz']} / {labels['ru']}) for the "
        f"company below. Amounts are in Uzbek so'm. The window is "
        f"{data['window']['start']} to {data['window']['end']} inclusive "
        f"({data['window']['days']} days). Attendance days count only days that "
        f"were marked; a person with zero everywhere may simply not use the app yet.\n\n"
        f"DATA:\n{json.dumps(data, ensure_ascii=False, default=_json_default)}"
    )


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


@dataclass
class Verdict:
    score: int | None
    headline_uz: str
    headline_ru: str
    text_uz: str
    text_ru: str


def parse_answer(raw: str) -> Verdict:
    """The model's JSON, read leniently.

    The prompt forbids fences and prose, but a vendor that adds them anyway
    should not lose the report: the outermost braces are what is parsed. An
    answer that is not JSON at all is kept as the text of both languages —
    an untranslated report beats an error row.
    """
    text = _FENCE.sub("", (raw or "").strip())
    start, end = text.find("{"), text.rfind("}")
    body: dict[str, Any] | None = None
    if start != -1 and end > start:
        try:
            candidate = json.loads(text[start:end + 1])
            if isinstance(candidate, dict):
                body = candidate
        except ValueError:
            body = None
    if body is None:
        return Verdict(None, "", "", text, text)

    def section(lang: str) -> tuple[str, str]:
        block = body.get(lang)
        if isinstance(block, dict):
            return (str(block.get("headline") or "").strip(),
                    str(block.get("text") or "").strip())
        if isinstance(block, str):
            return "", block.strip()
        return "", ""

    h_uz, t_uz = section("uz")
    h_ru, t_ru = section("ru")
    # One language missing: show the other rather than a blank page.
    t_uz, t_ru = t_uz or t_ru, t_ru or t_uz
    h_uz, h_ru = h_uz or h_ru, h_ru or h_uz
    score = body.get("score")
    try:
        score_value: int | None = max(0, min(100, int(round(float(score)))))
    except (TypeError, ValueError):
        score_value = None
    return Verdict(score_value, h_uz, h_ru, t_uz, t_ru)


# ─── Whose key ───────────────────────────────────────────────────────────────

@dataclass
class Vendor:
    provider: str
    key: str
    model: str
    #: Whether this is the deployment's own key rather than the workspace's.
    builtin: bool


def vendor_for(company_id: int) -> Vendor | None:
    provider = getattr(settings, "B2B_ANALYST_PROVIDER", "claude")
    key = getattr(settings, "B2B_ANALYST_API_KEY", "")
    if key and provider in IntegrationProvider.AI:
        model = getattr(settings, "B2B_ANALYST_MODEL", "") or DEFAULT_MODELS[provider]
        return Vendor(provider=provider, key=key, model=model, builtin=True)

    # No key of our own: the workspace's, if it connected one.
    for candidate in IntegrationProvider.AI:
        integration = int_repo.get_integration(company_id, candidate)
        if not integration or not integration.get("access_token_enc"):
            continue
        model = integration.get("ai_model")
        if not model:
            continue
        try:
            workspace_key = crypto.decrypt(integration["access_token_enc"])
        except (ValueError, ImproperlyConfigured):
            continue
        return Vendor(provider=candidate, key=workspace_key, model=model, builtin=False)
    return None


def is_available(company_id: int) -> bool:
    return bool(getattr(settings, "B2B_ANALYST_ENABLED", True)) and vendor_for(company_id) is not None


# ─── Writing a report ────────────────────────────────────────────────────────

class AnalystUnavailable(Exception):
    """No key to run on — neither the deployment's nor the workspace's."""


def generate(
    company_id: int,
    period: str,
    *,
    requested_by_id: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Reads the company, asks the model, stores the report. Returns the row.

    A vendor failure is stored too, as a ``failed`` row with the error on
    it — so the owner who pressed the button sees why, and the nightly pass
    leaves a trace instead of silently skipping a company.
    """
    vendor = vendor_for(company_id)
    if vendor is None:
        raise AnalystUnavailable()

    window = window_for(period, now=now)
    data = gather(company_id, window)
    prompt = build_prompt(data)
    max_tokens = int(getattr(settings, "B2B_ANALYST_MAX_OUTPUT_TOKENS", 12000))

    try:
        raw = ai.complete(
            vendor.provider, vendor.key, vendor.model,
            [ai.Turn(role="user", text=prompt)],
            system=SYSTEM_PROMPT,
            max_tokens=max_tokens,
        )
    except ai.AiError as exc:
        logger.warning("Weel AI could not write the %s report for company %s: %s",
                       period, company_id, exc)
        return repo.upsert_report(
            company_id=company_id, period=period,
            period_start=window.start_date, period_end=window.end_date,
            status=repo.STATUS_FAILED, provider=vendor.provider, model=vendor.model,
            score=None, headline_uz="", headline_ru="", text_uz="", text_ru="",
            data=data, error=str(exc), requested_by_id=requested_by_id,
        )

    verdict = parse_answer(raw)
    return repo.upsert_report(
        company_id=company_id, period=period,
        period_start=window.start_date, period_end=window.end_date,
        status=repo.STATUS_READY, provider=vendor.provider, model=vendor.model,
        score=verdict.score,
        headline_uz=verdict.headline_uz, headline_ru=verdict.headline_ru,
        text_uz=verdict.text_uz, text_ru=verdict.text_ru,
        data=data, error=None, requested_by_id=requested_by_id,
    )
