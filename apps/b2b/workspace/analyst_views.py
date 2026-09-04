"""Weel AI's endpoints — the reports, and the bridge to the assistant.

All under `/api/b2b/workspace/analyst/`, and all for the people who run the
company: the report is a verdict on colleagues, and `sees_all_company_data`
is the flag that already decides who may read the company rather than
their own corner of it.
"""
from __future__ import annotations

import logging

from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import serializers, status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from apps.b2b.integrations import ai_repository as ai_repo
from apps.b2b.workspace import analyst
from apps.b2b.workspace import analyst_repository as repo
from apps.b2b.workspace import assistant
from apps.b2b.workspace.permissions import IsWorkspaceUser
from apps.b2b.workspace.views import WORKSPACE_TAG, WorkspaceAPIView

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


def status_payload(user) -> dict:
    latest = repo.latest_report(user.company_id)
    return {
        "name": "Weel AI",
        "available": analyst.is_available(user.company_id),
        "unseen_count": repo.unseen_count(user.company_id, user.id),
        "latest": _report_row(latest) if latest else None,
        # Whether the connected assistant can take a report and talk it
        # through — the "Qanday tuzatish mumkin?" button is drawn on that.
        "assistant_connected": assistant.connected_vendor(user.company_id)[2] is not None,
    }


class GenerateSerializer(serializers.Serializer):
    period = serializers.ChoiceField(choices=list(repo.PERIODS))


class DiscussSerializer(serializers.Serializer):
    #: What to ask about the report. Optional: the default is the question
    #: the button asks.
    question = serializers.CharField(required=False, allow_blank=True, max_length=4000)


class AnalystView(AnalystAPIView):
    """GET /analyst/ — the button: whether Weel AI runs here, how many reports
    are unread, and the latest one."""

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
    """POST /analyst/reports/<id>/discuss/ — hand the report to the assistant.

    The two AIs working together: Weel AI found it, the connected assistant
    explains how to fix it. The report goes into the caller's assistant chat
    as a card, their question under it, and the assistant's answer comes
    back — and the chat is then where the conversation carries on.
    """

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Ask the assistant about a report",
                         request_body=DiscussSerializer())
    def post(self, request, report_id: int):
        report = repo.get_report(report_id, request.user.company_id)
        if not report or report.get("status") != repo.STATUS_READY:
            return Response({"detail": _("Report not found.")}, status=status.HTTP_404_NOT_FOUND)
        serializer = DiscussSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        provider, integration, key = assistant.connected_vendor(request.user.company_id)
        if key is None:
            return Response(
                {"detail": _("Ask an owner to connect Claude or ChatGPT in Integrations first.")},
                status=status.HTTP_409_CONFLICT,
            )

        lang = _lang()
        question = (serializer.validated_data.get("question") or "").strip() or (
            "Qanday tuzatish mumkin? Aniq qadamlar bilan tushuntir."
            if lang == "uz" else
            "Как это исправить? Объясни конкретными шагами."
        )
        headline = report.get(f"headline_{lang}") or ""
        body = report.get(f"text_{lang}") or ""
        card = f"{headline}\n\n{body}".strip() if headline else body

        conversation = assistant.ensure_conversation(
            request.user.company_id, request.user.id, model=integration.get("ai_model")
        )
        ai_repo.append_message(conversation["id"], assistant.ROLE_REPORT, card)
        ai_repo.append_message(conversation["id"], "user", question)
        _answer, refusal = assistant.answer(request.user, conversation)
        if refusal is not None:
            return refusal
        return Response(assistant.conversation_payload(
            request.user,
            assistant.conversation_for(request.user.company_id, request.user.id),
        ))
