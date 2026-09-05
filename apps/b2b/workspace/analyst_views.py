"""Weel AI's endpoints — the reports, and the chat.

All under `/api/b2b/workspace/analyst/`. The reports are for the people who
run the company: a report is a verdict on colleagues, and
`sees_all_company_data` is the flag that already decides who may read the
company rather than their own corner of it. The chat is everybody's — see
`WeelAiChatView` for who gets which Weel AI.
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta

from django.conf import settings
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import serializers, status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from apps.b2b import repository as b2b_repo
from apps.b2b.integrations import ai
from apps.b2b.integrations import ai_repository as ai_repo
from apps.b2b.integrations.ai_views import _message_payload
from apps.b2b.integrations.serializers import AiSendSerializer
from apps.b2b.workspace import advisor, analyst
from apps.b2b.workspace import analyst_repository as repo
from apps.b2b.workspace import repository as wrepo
from apps.b2b.workspace.permissions import IsWorkspaceUser
from apps.b2b.workspace.views import WORKSPACE_TAG, WorkspaceAPIView

#: The `provider` column of each person's Weel AI chat. Not a vendor — the
#: chat belongs to Weel AI whichever model answers it.
WEEL_AI_PROVIDER = "weel_ai"

logger = logging.getLogger(__name__)


class CanReadAnalyst(BasePermission):
    """Owner, administrator, manager — whoever reads the whole company."""

    message = _("Weel AI reports are for the people who run the company.")

    def has_permission(self, request, view):
        user = request.user
        return bool(getattr(user, "capabilities", {}).get("sees_all_company_data"))


class AnalystAPIView(WorkspaceAPIView):
    permission_classes = [IsAuthenticated, IsWorkspaceUser, CanReadAnalyst]


def _lang() -> str:
    code = (get_language() or "uz").split("-")[0].lower()
    return "ru" if code == "ru" else "uz"


def _report_row(report: dict) -> dict:
    """A list row: both headlines, the reader picks. The bodies are sent by
    the detail endpoint only — a list of thirty is thirty reports."""
    return {
        "id": report["id"],
        "period": report["period"],
        "period_start": report["period_start"],
        "period_end": report["period_end"],
        "status": report.get("status") or repo.STATUS_READY,
        "score": report.get("score"),
        "headline_uz": report.get("headline_uz") or "",
        "headline_ru": report.get("headline_ru") or "",
        "provider": report.get("provider"),
        "model": report.get("model"),
        "error": report.get("error"),
        "created_at": report.get("created_at"),
    }


def _report_detail(report: dict) -> dict:
    return {
        **_report_row(report),
        "text_uz": report.get("text_uz") or "",
        "text_ru": report.get("text_ru") or "",
        "data": report.get("data"),
    }


def _reads_reports(user) -> bool:
    return bool(getattr(user, "capabilities", {}).get("sees_all_company_data"))


def status_payload(user) -> dict:
    reads_reports = _reads_reports(user)
    latest = repo.latest_report(user.company_id) if reads_reports else None
    conversation = ai_repo.find_owned_conversation(user.company_id, WEEL_AI_PROVIDER, user.id)
    last = None
    if conversation and conversation.get("message_count"):
        recent = ai_repo.recent_messages(conversation["id"], 1)
        if recent:
            last = _message_payload(recent[-1])
    return {
        "name": "Weel AI",
        "available": analyst.is_available(user.company_id),
        # The reports are about the whole company and stay with the people
        # who run it; the chat under `analyst/chat/` is everybody's.
        "can_read_reports": reads_reports,
        # Whether this person gets the business advisor — the chat that
        # reads the whole company through tools (`advisor.py`) — rather
        # than the employee's own-work chat. Same people as the reports.
        "advisor": reads_reports,
        "unseen_count": repo.unseen_count(user.company_id, user.id) if reads_reports else 0,
        "latest": _report_row(latest) if latest else None,
        "message_count": int((conversation or {}).get("message_count") or 0),
        "last_message": last,
    }


class GenerateSerializer(serializers.Serializer):
    period = serializers.ChoiceField(choices=list(repo.PERIODS))


class DiscussSerializer(serializers.Serializer):
    #: What to ask about the report. Optional: the default is the question
    #: the button asks.
    question = serializers.CharField(required=False, allow_blank=True, max_length=4000)


class AnalystView(WorkspaceAPIView):
    """GET /analyst/ — the row: whether Weel AI runs here, how many reports
    are unread (for whoever may read them), and the last thing said in the
    chat. Every role: the row is drawn for everybody now that the chat is."""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Weel AI status")
    def get(self, request):
        return Response(status_payload(request.user))


class AnalystSeenView(AnalystAPIView):
    """POST /analyst/seen/ — the reader opened the list; the dot goes."""

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Mark Weel AI reports seen")
    def post(self, request):
        repo.mark_seen(request.user.id)
        return Response(status_payload(request.user))


class AnalystReportListView(AnalystAPIView):
    """GET  /analyst/reports/?period=&limit= — newest first.
    POST /analyst/reports/ {period} — write one now."""

    @swagger_auto_schema(
        tags=WORKSPACE_TAG, operation_summary="Weel AI reports",
        manual_parameters=[
            openapi.Parameter("period", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                              enum=list(repo.PERIODS)),
            openapi.Parameter("limit", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
        ],
    )
    def get(self, request):
        try:
            limit = int(request.query_params.get("limit") or 30)
        except (TypeError, ValueError):
            limit = 30
        rows = repo.list_reports(
            request.user.company_id,
            period=request.query_params.get("period") or None,
            limit=limit,
        )
        return Response({"results": [_report_row(r) for r in rows]})

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Write a Weel AI report now",
                         request_body=GenerateSerializer())
    def post(self, request):
        serializer = GenerateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        period = serializer.validated_data["period"]
        company_id = request.user.company_id

        if not analyst.is_available(company_id):
            return Response(
                {"detail": _("Weel AI has no key to run on. Connect Claude or ChatGPT "
                             "in Integrations, or ask Weel to enable it.")},
                status=status.HTTP_409_CONFLICT,
            )
        if repo.report_made_recently(
            company_id, period, within_minutes=analyst.RERUN_COOLDOWN_MINUTES
        ):
            return Response(
                {"detail": _("This report was written less than %(n)d minutes ago.")
                 % {"n": analyst.RERUN_COOLDOWN_MINUTES}},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        try:
            report = analyst.generate(company_id, period, requested_by_id=request.user.id)
        except analyst.AnalystUnavailable:
            return Response({"detail": _("Weel AI has no key to run on.")},
                            status=status.HTTP_409_CONFLICT)
        if report.get("status") == repo.STATUS_FAILED:
            return Response(
                {"detail": report.get("error") or _("Weel AI could not write the report.")},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        return Response(_report_detail(report), status=status.HTTP_201_CREATED)


class AnalystReportView(AnalystAPIView):
    """GET /analyst/reports/<id>/ — the report, in both languages."""

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="One Weel AI report")
    def get(self, request, report_id: int):
        report = repo.get_report(report_id, request.user.company_id)
        if not report:
            return Response({"detail": _("Report not found.")}, status=status.HTTP_404_NOT_FOUND)
        return Response(_report_detail(report))


class AnalystDiscussView(AnalystAPIView):
    """POST /analyst/reports/<id>/discuss/ — talk a report through with
    Weel AI.

    The report goes into the caller's Weel AI chat as a card, their
    question under it, and the advisor's answer comes back — and the chat
    is then where the conversation carries on. Managers only, like the
    report itself, so the answer is always the advisor's, with the tools.
    """

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Ask Weel AI about a report",
                         request_body=DiscussSerializer())
    def post(self, request, report_id: int):
        report = repo.get_report(report_id, request.user.company_id)
        if not report or report.get("status") != repo.STATUS_READY:
            return Response({"detail": _("Report not found.")}, status=status.HTTP_404_NOT_FOUND)
        serializer = DiscussSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        vendor = analyst.vendor_for(request.user.company_id)
        if vendor is None or not analyst.is_available(request.user.company_id):
            return Response({"detail": NOT_ENABLED_MESSAGE}, status=status.HTTP_409_CONFLICT)

        lang = _lang()
        question = (serializer.validated_data.get("question") or "").strip() or (
            "Qanday tuzatish mumkin? Aniq qadamlar bilan tushuntir."
            if lang == "uz" else
            "Как это исправить? Объясни конкретными шагами."
        )
        headline = report.get(f"headline_{lang}") or ""
        body = report.get(f"text_{lang}") or ""
        card = f"{headline}\n\n{body}".strip() if headline else body

        conversation = _ensure_weel_ai_conversation(request.user, vendor)
        ai_repo.append_message(conversation["id"], advisor.ROLE_REPORT, card)
        ai_repo.append_message(conversation["id"], "user", question)
        try:
            answer = advisor.answer(request.user, conversation, vendor=vendor, language=lang)
        except ai.AiError as exc:
            code = status.HTTP_400_BAD_REQUEST if exc.is_key_problem else status.HTTP_502_BAD_GATEWAY
            return Response({"detail": str(exc)}, status=code)
        if answer:
            ai_repo.append_message(conversation["id"], "assistant", answer)
        return Response(_weel_ai_payload(request.user))


# ─── Weel AI as a chat, for everybody ────────────────────────────────────────

WEEL_AI_SYSTEM = """You are Weel AI, the assistant built into the Weel workspace app, talking to one employee of a company.

Language: answer in the language the person writes in. The app's languages are Uzbek (Latin script) and Russian; if the message is ambiguous or very short, answer in {language}. Never mix languages in one answer, and never switch to English unless the person writes in English.

You help with their everyday work: answer questions, draft messages, letters and documents, plan the day, think a problem through. You also see this person's own numbers below — their tasks, deals and attendance for the last 30 days, and their open tasks. When they ask about their work, use them: say what is going well, what is behind, what to do today and this week. Be concrete and reasonably short; plain paragraphs and simple lists, no headings. Do not invent numbers that are not in the data; if something is missing, say what to check in the app.

The person: {name}, {position}, {role} at {company}.

Data (JSON):
{data}"""


def _weel_ai_context(user) -> dict:
    """What Weel AI knows about this person: their month, their open tasks,
    and — for whoever runs the company — the company line as well."""
    window = analyst.window_for("month")
    rows = repo.employee_window_stats(user.company_id, window.start, window.end)
    mine = next((r for r in rows if int(r.get("employee_id") or 0) == int(user.id)), None)
    person = None
    if mine:
        person = {
            "tasks_completed": int(mine.get("completed_count") or 0),
            "tasks_on_time_rate": analyst._rate(int(mine.get("on_time_count") or 0), int(mine.get("due_count") or 0)),
            "tasks_open": int(mine.get("open_count") or 0),
            "tasks_overdue": int(mine.get("overdue_count") or 0),
            "deals_won": int(mine.get("won_count") or 0),
            "deals_won_amount": analyst._num(mine.get("won_amount")) or 0,
            "deals_lost": int(mine.get("lost_count") or 0),
            "days_present": int(mine.get("present_days") or 0),
            "days_late": int(mine.get("late_days") or 0),
            "days_absent": int(mine.get("absent_days") or 0),
        }
    tasks = []
    try:
        for task in wrepo.list_tasks(user.company_id, visible_to=user.id, limit=60):
            if task.get("status") == "done":
                continue
            tasks.append({
                "title": task.get("title"),
                "status": task.get("status"),
                "priority": task.get("priority"),
                "due_date": task["due_date"].isoformat() if task.get("due_date") else None,
                "project": task.get("project"),
            })
            if len(tasks) >= 15:
                break
    except Exception:  # noqa: BLE001 - the chat is worth more than one list
        pass
    data = {
        "window": {"start": window.start_date.isoformat(), "end": (window.end_date - timedelta(days=1)).isoformat()},
        "me": person,
        "open_tasks": tasks,
    }
    if _reads_reports(user):
        try:
            whole = analyst.gather(user.company_id, window)
            data["company_totals"] = whole.get("company_totals")
            data["departments"] = whole.get("departments")
        except Exception:  # noqa: BLE001
            pass
        latest = repo.latest_report(user.company_id)
        if latest and latest.get("status") == repo.STATUS_READY:
            lang = _lang()
            data["latest_report"] = {
                "period": latest.get("period"),
                "headline": latest.get(f"headline_{lang}") or "",
            }
    return data


def _weel_ai_system(user) -> str:
    company = b2b_repo.get_company(user.company_id) or {}
    employee = user._data if hasattr(user, "_data") else {}
    return WEEL_AI_SYSTEM.format(
        language=advisor.LANGUAGE_NAMES[_lang()],
        name=user.full_name or "",
        position=employee.get("position") or "",
        role=user.role,
        company=company.get("name") or "",
        data=json.dumps(_weel_ai_context(user), ensure_ascii=False, default=analyst._json_default),
    )


#: What the app is told when there is no key for Weel AI to run on.
NOT_ENABLED_MESSAGE = _("Weel AI is not enabled on this server yet.")


def _weel_ai_conversation(user) -> dict | None:
    return ai_repo.find_owned_conversation(user.company_id, WEEL_AI_PROVIDER, user.id)


def _ensure_weel_ai_conversation(user, vendor: analyst.Vendor) -> dict:
    conversation = _weel_ai_conversation(user)
    if conversation:
        return conversation
    row = ai_repo.create_conversation(
        company_id=user.company_id,
        provider=WEEL_AI_PROVIDER,
        title="Weel AI",
        model=vendor.model,
        project_id=None,
        created_by_id=user.id,
    )
    return ai_repo.get_conversation(row["id"], user.company_id, WEEL_AI_PROVIDER) or row


def _weel_ai_payload(user) -> dict:
    conversation = _weel_ai_conversation(user)
    messages = ai_repo.list_messages(conversation["id"]) if conversation else []
    return {
        **status_payload(user),
        "id": conversation["id"] if conversation else None,
        "messages": [_message_payload(m) for m in messages],
    }


class WeelAiChatView(WorkspaceAPIView):
    """GET    /analyst/chat/ — this person's chat with Weel AI.
    POST   /analyst/chat/ {text} — ask, and get the answer.
    DELETE /analyst/chat/ — start over.

    Every role, two readers. An employee gets their own month and open
    tasks in the prompt and help with their own work. Whoever runs the
    company (`sees_all_company_data`) gets the business advisor instead —
    `advisor.py` — which reads the whole company through tools as it
    answers and keeps what it is told between conversations. Both on the
    deployment's key (`B2B_ANALYST_API_KEY`, or the workspace's as the
    fallback).
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="My chat with Weel AI")
    def get(self, request):
        return Response(_weel_ai_payload(request.user))

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Ask Weel AI", request_body=AiSendSerializer())
    def post(self, request):
        serializer = AiSendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        text = serializer.validated_data["text"].strip()
        vendor = analyst.vendor_for(request.user.company_id)
        if vendor is None or not analyst.is_available(request.user.company_id):
            return Response({"detail": NOT_ENABLED_MESSAGE}, status=status.HTTP_409_CONFLICT)
        conversation = _ensure_weel_ai_conversation(request.user, vendor)
        ai_repo.append_message(conversation["id"], "user", text)

        try:
            if _reads_reports(request.user):
                answer = advisor.answer(
                    request.user, conversation, vendor=vendor, language=_lang(),
                )
            else:
                answer = ai.complete(
                    vendor.provider, vendor.key, vendor.model,
                    advisor.turns_for(conversation["id"]),
                    system=_weel_ai_system(request.user),
                )
        except ai.AiError as exc:
            code = status.HTTP_400_BAD_REQUEST if exc.is_key_problem else status.HTTP_502_BAD_GATEWAY
            return Response({"detail": str(exc)}, status=code)
        if answer:
            ai_repo.append_message(conversation["id"], "assistant", answer)
        return Response(_weel_ai_payload(request.user))

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Clear my Weel AI chat")
    def delete(self, request):
        conversation = _weel_ai_conversation(request.user)
        if conversation:
            ai_repo.delete_conversation(conversation["id"], request.user.company_id, WEEL_AI_PROVIDER)
        return Response(status=status.HTTP_204_NO_CONTENT)
