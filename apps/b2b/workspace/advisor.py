"""Weel AI as the owner's business advisor.

The Weel AI chat has two readers. An employee asks about their own work,
and `analyst_views` hands the model their month up front. Whoever runs the
company — owner, administrator, manager, anybody with
``sees_all_company_data`` — gets *this*: the same chat, but the model may
look the company up itself while it answers. It is given tools — the
funnel, the shelf, today's roll call, late tasks, the calendar, the
reports it wrote — and calls whichever the question needs, so "why did
sales drop" reads the funnel and the lost reasons, and "what should I do
this week" reads all of it.

**Memory.** The owner tells it things the numbers do not show — "we lose
deals to Alpha on price", "Madina is on leave until the 20th", "we decided
to drop the wholesale line" — and it keeps them (`remember`) in
`b2b_ai_advisor_note`. Every later conversation starts with those notes,
so the advice stays consistent from one day to the next.

**Whose key.** The deployment's own, the same as the reports
(`analyst.vendor_for`). Runs on either vendor: `ai.complete_with_tools`
speaks both dialects of tool use.
"""
from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any

from django.conf import settings
from django.utils import timezone

from apps.b2b import repository as b2b_repo
from apps.b2b.integrations import ai
from apps.b2b.integrations import ai_repository as ai_repo
from apps.b2b.workspace import advisor_repository as arepo
from apps.b2b.workspace import analyst
from apps.b2b.workspace import analyst_repository as reports_repo
from apps.b2b.workspace import inventory_repository as inv_repo
from apps.b2b.workspace import repository as wrepo

logger = logging.getLogger(__name__)

LANGUAGE_NAMES = {"uz": "Uzbek (Latin script)", "ru": "Russian", "en": "English"}

#: A turn that is neither side of the conversation — a Weel AI report the
#: person asked about from the report page ("Qanday tuzatish mumkin?").
#: Stored under its own role so the app draws it as a card; sent to the
#: model as the person's turn.
ROLE_REPORT = "report"

SYSTEM_PROMPT = """You are Weel AI, the business advisor built into the Weel workspace app, talking to the person who runs this company. You advise on the whole business: sales and the funnel, the team and who is behind, attendance, tasks and deadlines, stock and what sells, cash tied up on the shelf, and what to do about each.

Language: answer in the language the person writes in. The app's languages are Uzbek (Latin script) and Russian; if the message is ambiguous or very short, answer in {language}. Never mix languages in one answer, and never switch to English unless the person writes in English.

How to work:
- You have tools that read the company's live data. Use them before you state any number, name or verdict — never guess or recall figures from earlier in the conversation when a tool can give the current ones. For a broad question ("how are we doing", "what should I do this week") read several: the overview, the funnel, tasks, stock.
- Be concrete. Name the deal, the person, the product, the amount, the deadline. Say what to do, by whom, by when, and what to check next. Prefer three sharp recommendations over ten vague ones.
- Be honest about what the data does not show. If something is missing or zero because nobody uses that part of the app yet, say so, and say what to start recording.
- When the person tells you a fact, a decision or a preference worth keeping — a decision taken, a customer's situation, a person's leave, something you should stop suggesting — store it with `remember`, in one short sentence, and confirm in a few words. Do not store what the tools already know. Use `forget` when they say a note is wrong or no longer true.
- Amounts are in Uzbek so'm. Dates are Asia/Tashkent. Today is {today}.
- Plain paragraphs and simple lists; no markdown headings, no tables. Keep it as short as the question allows.

Company: {company} ({industry}, {city}). You are talking to {name}, {position}, {role}.

{notes}"""

NOTES_HEADER = "What you were told to keep in mind (oldest first; do not restate unless relevant):"
NO_NOTES = "You have no saved notes about this company yet."


# ─── The tools ───────────────────────────────────────────────────────────────

def _schema(**properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "additionalProperties": False}


TOOLS = [
    ai.Tool(
        name="company_overview",
        description=(
            "The whole company over a window: per-department and per-employee tasks "
            "completed / open / overdue, deals won and lost with amounts, attendance "
            "(present / late / absent days), leads created and unclaimed, plus a stock "
            "and sales summary. Start here for any broad question."
        ),
        input_schema=_schema(period={
            "type": "string", "enum": ["day", "week", "month", "year"],
            "description": "Which window. 'day' is yesterday, 'week' the last 7 days, "
                           "'month' the last 30, 'year' the last 365. Default 'month'.",
        }),
    ),
    ai.Tool(
        name="sales_funnel",
        description=(
            "The sales funnel as it stands: open deals by stage with counts, amounts, how "
            "many are unclaimed or past their due date; the biggest open deals with owner, "
            "days open and days since last touch; deals won and lost lately with amounts "
            "and the reasons given for losses."
        ),
        input_schema=_schema(days={
            "type": "integer", "minimum": 1, "maximum": 365,
            "description": "How many days back the won/lost figures cover. Default 30.",
        }),
    ),
    ai.Tool(
        name="inventory_status",
        description=(
            "The warehouse: products, quantity on hand, stock value at purchase and at "
            "retail, how many products are low or out of stock, revenue / cost / profit "
            "and turnover over the window, the products at or under their minimum, and "
            "the best sellers with the margin each made."
        ),
        input_schema=_schema(days={
            "type": "integer", "minimum": 1, "maximum": 365,
            "description": "Window for sales, purchases and best sellers. Default 30.",
        }),
    ),
    ai.Tool(
        name="tasks_status",
        description=(
            "Tasks: how many are open, overdue, due today and done in the last 7 days, "
            "and the list of overdue tasks with title, priority, project, deadline and "
            "who they sit on."
        ),
        input_schema=_schema(),
    ),
    ai.Tool(
        name="attendance",
        description=(
            "The roll call for one day: every active employee with what was marked "
            "(present, late, remote, absent with reason) or nothing at all. Default today."
        ),
        input_schema=_schema(date={
            "type": "string", "description": "ISO date (YYYY-MM-DD). Default today.",
        }),
    ),
    ai.Tool(
        name="upcoming_events",
        description="Calendar events for the company over the next days: title, start, end, participants.",
        input_schema=_schema(days={
            "type": "integer", "minimum": 1, "maximum": 60,
            "description": "How many days ahead. Default 7.",
        }),
    ),
    ai.Tool(
        name="recent_reports",
        description=(
            "The reports Weel AI already wrote for this company (daily, weekly, monthly, "
            "yearly): period, score out of 100, headline, and the full text of the newest."
        ),
        input_schema=_schema(limit={
            "type": "integer", "minimum": 1, "maximum": 20, "description": "Default 5.",
        }),
    ),
    ai.Tool(
        name="remember",
        description=(
            "Keep one short sentence in memory for every future conversation about this "
            "company: a decision, a fact about the business, a customer's situation, a "
            "person's absence, something to stop suggesting. Not for numbers the other "
            "tools already know."
        ),
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string", "minLength": 3, "maxLength": 500}},
            "required": ["text"],
            "additionalProperties": False,
        },
    ),
    ai.Tool(
        name="forget",
        description="Delete one saved note by its id (the ids are listed with the notes).",
        input_schema={
            "type": "object",
            "properties": {"note_id": {"type": "integer"}},
            "required": ["note_id"],
            "additionalProperties": False,
        },
    ),
]


def _dump(value: Any) -> Any:
    """JSON-able copy: Decimals to floats, dates to ISO strings."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=analyst._json_default))


def _int(args: dict[str, Any], key: str, default: int, *, lo: int, hi: int) -> int:
    try:
        value = int(args.get(key, default) or default)
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


class Toolbox:
    """The tools bound to one company and the person asking — what
    `ai.complete_with_tools` calls back into."""

    def __init__(self, user) -> None:
        self.user = user
        self.company_id = user.company_id

    def __call__(self, name: str, args: dict[str, Any]) -> Any:
        handler = getattr(self, f"tool_{name}", None)
        if handler is None:
            raise ai.ToolError(f"There is no tool called {name}.")
        return _dump(handler(args))

    def tool_company_overview(self, args: dict[str, Any]) -> Any:
        period = args.get("period") or "month"
        if period not in analyst.PERIODS:
            raise ai.ToolError("period must be one of day, week, month, year.")
        window = analyst.window_for(period)
        data = analyst.gather(self.company_id, window)
        try:
            summary = inv_repo.summary(self.company_id, date_from=window.start, date_to=window.end)
            summary.pop("daily", None)
            data["inventory"] = summary
        except Exception:  # noqa: BLE001 - a company without the module still gets the rest
            logger.debug("Weel AI advisor: no inventory summary for company %s", self.company_id, exc_info=True)
        return data

    def tool_sales_funnel(self, args: dict[str, Any]) -> Any:
        return arepo.funnel(self.company_id, days=_int(args, "days", 30, lo=1, hi=365))

    def tool_inventory_status(self, args: dict[str, Any]) -> Any:
        days = _int(args, "days", 30, lo=1, hi=365)
        now = timezone.now()
        summary = inv_repo.summary(self.company_id, date_from=now - timedelta(days=days), date_to=now)
        summary.pop("daily", None)
        return {
            "summary": summary,
            "low_stock": arepo.low_stock(self.company_id),
            "best_sellers": arepo.top_products(self.company_id, days=days),
        }

    def tool_tasks_status(self, args: dict[str, Any]) -> Any:
        return {
            "counts": arepo.task_counts(self.company_id),
            "overdue": arepo.overdue_tasks(self.company_id),
        }

    def tool_attendance(self, args: dict[str, Any]) -> Any:
        raw = (args.get("date") or "").strip()
        if raw:
            try:
                day = date.fromisoformat(raw)
            except ValueError as exc:
                raise ai.ToolError("date must be YYYY-MM-DD.") from exc
        else:
            day = timezone.localdate()
        rows = arepo.attendance_on(self.company_id, day)
        counts: dict[str, int] = {}
        for row in rows:
            counts[row.get("status") or "not_marked"] = counts.get(row.get("status") or "not_marked", 0) + 1
        return {"date": day.isoformat(), "counts": counts, "people": rows}

    def tool_upcoming_events(self, args: dict[str, Any]) -> Any:
        days = _int(args, "days", 7, lo=1, hi=60)
        now = timezone.now()
        events = wrepo.list_events(self.company_id, start=now, end=now + timedelta(days=days), limit=60)
        names = {e["id"]: e.get("full_name") for e in b2b_repo.list_employees(self.company_id)}
        return [
            {
                "title": e.get("title"), "starts_at": e.get("starts_at"), "ends_at": e.get("ends_at"),
                "location": e.get("location"),
                "participants": [names.get(pid, str(pid)) for pid in (e.get("participant_ids") or [])],
            }
            for e in events
        ]

    def tool_recent_reports(self, args: dict[str, Any]) -> Any:
        limit = _int(args, "limit", 5, lo=1, hi=20)
        rows = reports_repo.list_reports(self.company_id, limit=limit)
        out = []
        for index, row in enumerate(rows):
            item = {
                "id": row["id"], "period": row.get("period"), "status": row.get("status"),
                "from": row.get("period_start"), "to": row.get("period_end"),
                "score": row.get("score"), "headline_uz": row.get("headline_uz"),
                "headline_ru": row.get("headline_ru"), "written_at": row.get("created_at"),
            }
            if index == 0 and row.get("status") == reports_repo.STATUS_READY:
                full = reports_repo.get_report(row["id"], self.company_id) or {}
                item["text_uz"] = full.get("text_uz")
                item["text_ru"] = full.get("text_ru")
            out.append(item)
        return out

    def tool_remember(self, args: dict[str, Any]) -> Any:
        text = str(args.get("text") or "").strip()
        if len(text) < 3:
            raise ai.ToolError("Nothing to remember.")
        row = arepo.add_note(self.company_id, self.user.id, text[:500])
        return {"saved": True, "note_id": (row or {}).get("id"), "text": text[:500]}

    def tool_forget(self, args: dict[str, Any]) -> Any:
        try:
            note_id = int(args.get("note_id"))
        except (TypeError, ValueError) as exc:
            raise ai.ToolError("note_id must be an integer.") from exc
        return {"deleted": arepo.delete_note(note_id, self.company_id), "note_id": note_id}


# ─── The prompt ──────────────────────────────────────────────────────────────

def notes_block(company_id: int) -> str:
    notes = arepo.list_notes(company_id)
    if not notes:
        return NO_NOTES
    lines = [NOTES_HEADER]
    for note in notes:
        when = note.get("created_at")
        stamp = when.date().isoformat() if hasattr(when, "date") else str(when or "")
        lines.append(f"- [id {note['id']}, {stamp}] {note['text']}")
    return "\n".join(lines)


def system_prompt(user, *, language: str) -> str:
    company = b2b_repo.get_company(user.company_id) or {}
    employee = user._data if hasattr(user, "_data") else {}
    return SYSTEM_PROMPT.format(
        language=LANGUAGE_NAMES.get(language, LANGUAGE_NAMES["uz"]),
        today=timezone.localdate().isoformat(),
        company=company.get("name") or "",
        industry=company.get("industry") or "—",
        city=company.get("city") or "—",
        name=user.full_name or "",
        position=employee.get("position") or "",
        role=user.role,
        notes=notes_block(user.company_id),
    )


# ─── Answering ───────────────────────────────────────────────────────────────

def turns_for(conversation_id: int) -> list[ai.Turn]:
    history = int(getattr(settings, "B2B_AI_HISTORY_TURNS", 40))
    turns = []
    for message in ai_repo.recent_messages(conversation_id, history):
        role, text = message["role"], message["text"]
        if role == ROLE_REPORT:
            role, text = "user", f"Weel AI report:\n\n{text}"
        if role in ("user", "assistant"):
            turns.append(ai.Turn(role=role, text=text))
    return turns


def answer(user, conversation: dict, *, vendor: analyst.Vendor, language: str) -> str:
    """The advisor's reply to the conversation as it stands. Raises
    `ai.AiError` the way `ai.complete` does; the view turns that into a
    response. The person's turn must already be stored."""
    max_tokens = int(getattr(settings, "B2B_ADVISOR_MAX_OUTPUT_TOKENS", 8000))
    return ai.complete_with_tools(
        vendor.provider, vendor.key, vendor.model, turns_for(conversation["id"]),
        tools=TOOLS,
        call=Toolbox(user),
        system=system_prompt(user, language=language),
        max_tokens=max_tokens,
    )
