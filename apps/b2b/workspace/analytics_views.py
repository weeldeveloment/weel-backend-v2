"""«Hisobotlar» — the endpoints behind the redesigned report screen.

    GET  analytics/               one section: eight figures + employee table
    GET  analytics/items/         the rows behind one figure (detalizatsiya)
    GET  analytics/export/        the section as XLSX or CSV
    GET  analytics/subscription/  this person's standing order for the section
    PUT  analytics/subscription/  …and changing it

Who sees what is decided the way the rest of the workspace decides it: a
person with ``sees_all_company_data`` reads the company and may narrow to one
employee; everybody else reads their own work. The tabs on offer are the
modules this person opens — see ``_sections``.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.core.validators import validate_email
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from apps.b2b.mail import repository as mail_repo
from apps.b2b.raw.tables import B2B_EMPLOYEE_TABLE
from apps.b2b.workspace import analytics
from apps.b2b.workspace import analytics_io
from apps.b2b.workspace import analytics_repository as subs
from apps.b2b.workspace.access import Module, Permission, Role
from apps.b2b.workspace.permissions import IsWorkspaceUser
from apps.b2b.workspace.views import WORKSPACE_TAG, WorkspaceAPIView
from apps.shared.raw.db import fetch_one

logger = logging.getLogger(__name__)


def _lang() -> str:
    code = (get_language() or "uz").split("-")[0].lower()
    return "ru" if code == "ru" else "uz"


def _may(user, permission: str) -> bool:
    """One named permission, or the standing that outranks the role editor."""
    if Role.clean(getattr(user, "role", None)) in Role.ADMINISTRATIVE:
        return True
    try:
        return bool(user.may(permission))
    except Exception:  # noqa: BLE001 — a user object without the map
        return False


def _sees_all(user) -> bool:
    try:
        return bool(user.capabilities.get("sees_all_company_data"))
    except Exception:  # noqa: BLE001
        return False


def _sections(user) -> list[str]:
    """The tabs this person gets, in the order the screen shows them.

    A tab is a module: no sales board, no «Sotuv» tab. The stock tab also
    wants the stock permission — a salesperson who may not open the
    warehouse should not read its totals through a report either.
    """
    if user.get("is_chat_only"):
        return []
    out: list[str] = []
    if user.opens(Module.SALES):
        out.append(analytics.SECTION_SALES)
    if user.opens(Module.TASKS):
        out.append(analytics.SECTION_TASKS)
    if user.opens(Module.SALES) and _may(user, Permission.STOCK_VIEW):
        out.append(analytics.SECTION_STOCK)
    if user.opens(Module.TRIPS):
        out.append(analytics.SECTION_TRIPS)
    # Attendance has no module of its own (TZ §20) and `can_view_attendance`
    # is true for everybody; the scope keeps a plain employee on their own
    # days.
    out.append(analytics.SECTION_ATTENDANCE)
    return out


class CanReadReports(BasePermission):
    message = _("Reports are not shared with you.")

    def has_permission(self, request, view):
        user = request.user
        if user.get("is_chat_only"):
            return False
        return _may(user, Permission.REPORT_VIEW)


class AnalyticsAPIView(WorkspaceAPIView):
    permission_classes = [IsAuthenticated, IsWorkspaceUser, CanReadReports]

    # ── Shared parameter reading ────────────────────────────────────────────

    def _window(self, request) -> analytics.Window:
        anchor = parse_date(request.query_params.get("date") or "")
        return analytics.resolve_window(request.query_params.get("period") or "", anchor)

    def _section(self, request) -> str:
        offered = _sections(request.user)
        wanted = request.query_params.get("section") or ""
        if wanted and wanted not in analytics.SECTIONS:
            raise analytics.AnalyticsError(f"unknown section {wanted!r}")
        if wanted and wanted not in offered:
            raise PermissionError(wanted)
        if not wanted:
            if not offered:
                raise PermissionError("")
            wanted = offered[0]
        return wanted

    def _scope(self, request) -> tuple[int | None, dict | None]:
        """Whose work to count, and the picked employee if any.

        Returns ``(employee_id, employee)`` — ``None`` for the whole company.
        A plain employee is pinned to themselves whatever they ask for.
        """
        user = request.user
        if not _sees_all(user):
            return user.id, None
        raw = request.query_params.get("employee_id")
        if not raw:
            return None, None
        try:
            wanted = int(raw)
        except ValueError:
            return None, None
        row = fetch_one(
            f"SELECT id, full_name FROM {B2B_EMPLOYEE_TABLE} WHERE id = %s AND company_id = %s",
            [wanted, user.company_id],
        )
        if not row:
            return None, None
        return int(row["id"]), {"id": int(row["id"]), "full_name": row["full_name"]}

    def _refuse(self, exc: Exception) -> Response:
        if isinstance(exc, PermissionError):
            return Response(
                {"detail": _("This section is not shared with you."), "section": str(exc)},
                status=status.HTTP_403_FORBIDDEN,
            )
        return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)


_PARAMS = [
    openapi.Parameter("section", openapi.IN_QUERY, type=openapi.TYPE_STRING, enum=list(analytics.SECTIONS)),
    openapi.Parameter("period", openapi.IN_QUERY, type=openapi.TYPE_STRING, enum=list(analytics.PERIODS)),
    openapi.Parameter("date", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                      description="Anchor day, YYYY-MM-DD; the period containing it. Defaults to today."),
    openapi.Parameter("employee_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER,
                      description="Managers only: narrow the figures to one person."),
]


class AnalyticsReportView(AnalyticsAPIView):
    """GET /api/b2b/workspace/analytics/"""

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="One report section: KPIs and the employee table",
                         manual_parameters=_PARAMS)
    def get(self, request):
        try:
            section = self._section(request)
        except (analytics.AnalyticsError, PermissionError) as exc:
            return self._refuse(exc)
        window = self._window(request)
        employee_id, employee = self._scope(request)
        lang = _lang()
        report = analytics.section_report(
            request.user.company_id, section, window, employee_id=employee_id
        )
        return Response({
            "generated_at": timezone.localtime(timezone.now()).isoformat(),
            "window": window.as_json(),
            "label": analytics_io.window_label(window, lang),
            "compare_label": analytics_io.compare_label(window, lang),
            "scope": "company" if employee_id is None else "own",
            "sections": _sections(request.user),
            "section": section,
            "employee": employee,
            "can_export": _may(request.user, Permission.REPORT_EXPORT),
            "can_filter_employee": _sees_all(request.user),
            "metrics": report["metrics"],
            "employees": report["employees"],
        })


class AnalyticsItemsView(AnalyticsAPIView):
    """GET /api/b2b/workspace/analytics/items/"""

    @swagger_auto_schema(
        tags=WORKSPACE_TAG, operation_summary="The rows behind one KPI",
        manual_parameters=_PARAMS + [
            openapi.Parameter("metric", openapi.IN_QUERY, type=openapi.TYPE_STRING, required=True),
            openapi.Parameter("sort", openapi.IN_QUERY, type=openapi.TYPE_STRING, enum=["date", "amount"]),
            openapi.Parameter("limit", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter("offset", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
        ],
    )
    def get(self, request):
        try:
            section = self._section(request)
            window = self._window(request)
            employee_id, employee = self._scope(request)
            page = analytics.list_items(
                request.user.company_id, section,
                request.query_params.get("metric") or "",
                window,
                employee_id=employee_id,
                sort=request.query_params.get("sort") or "date",
                limit=_int(request.query_params.get("limit"), 50),
                offset=_int(request.query_params.get("offset"), 0),
            )
        except (analytics.AnalyticsError, PermissionError) as exc:
            return self._refuse(exc)
        lang = _lang()
        return Response({
            "window": window.as_json(),
            "label": analytics_io.window_label(window, lang),
            "section": section,
            "metric": request.query_params.get("metric"),
            "employee": employee,
            **page,
        })


class AnalyticsExportView(AnalyticsAPIView):
    """GET /api/b2b/workspace/analytics/export/?type=xlsx|csv

    ``type`` and not ``format``: DRF reads ``?format=`` as its own renderer
    switch and answers 404 for a name it does not know.
    """

    @swagger_auto_schema(
        tags=WORKSPACE_TAG, operation_summary="The section as a spreadsheet",
        manual_parameters=_PARAMS + [
            openapi.Parameter("type", openapi.IN_QUERY, type=openapi.TYPE_STRING, enum=["xlsx", "csv"]),
        ],
    )
    def get(self, request):
        if not _may(request.user, Permission.REPORT_EXPORT):
            return Response(
                {"detail": _("Your role does not allow exporting reports."),
                 "permission": Permission.REPORT_EXPORT},
                status=status.HTTP_403_FORBIDDEN,
            )
        try:
            section = self._section(request)
        except (analytics.AnalyticsError, PermissionError) as exc:
            return self._refuse(exc)
        window = self._window(request)
        employee_id, _employee = self._scope(request)
        fmt = "csv" if (request.query_params.get("type") or "").lower() == "csv" else "xlsx"
        lang = _lang()
        payload, content_type, name = build_export(
            request.user.company_id, section, window, employee_id=employee_id, fmt=fmt, lang=lang
        )
        response = HttpResponse(payload, content_type=content_type)
        response["Content-Disposition"] = f'attachment; filename="{name}"'
        response["X-Report-File"] = name
        return response


def build_export(company_id: int, section: str, window: analytics.Window, *,
                 employee_id: int | None, fmt: str, lang: str) -> tuple[bytes, str, str]:
    """The file the button downloads and the subscription mails — one place,
    so the two cannot drift apart."""
    report = analytics.section_report(company_id, section, window, employee_id=employee_id)
    items = analytics.list_items(
        company_id, section, analytics.metric_keys(section)[0], window,
        employee_id=employee_id, sort="date", limit=5000, offset=0, cap=5000,
    )
    if fmt == "csv":
        return (
            analytics_io.export_csv(section, report, items, window, lang),
            analytics_io.CSV_CONTENT_TYPE,
            analytics_io.file_name(section, window, "csv"),
        )
    return (
        analytics_io.export_xlsx(section, report, items, window, lang),
        analytics_io.XLSX_CONTENT_TYPE,
        analytics_io.file_name(section, window, "xlsx"),
    )


def _int(raw, default: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def platform_mail_configured() -> bool:
    """Whether this deployment can send mail on its own behalf.

    Django's ``EMAIL_HOST`` defaults to ``localhost``, so its presence proves
    nothing; a configured SMTP relay always comes with a login.
    """
    backend = getattr(settings, "EMAIL_BACKEND", "") or ""
    return bool(getattr(settings, "EMAIL_HOST_USER", "")) and "smtp" in backend


def _mail_available(user) -> bool:
    """Whether a mailed report has anything to leave through: the person's
    own connected inbox, or a deployment-level SMTP relay."""
    if platform_mail_configured():
        return True
    try:
        return any(a.get("is_active", True) for a in mail_repo.list_accounts(user.id))
    except Exception:  # noqa: BLE001 — the mail tables may be absent on an old database
        return False


class AnalyticsSubscriptionView(AnalyticsAPIView):
    """GET / PUT /api/b2b/workspace/analytics/subscription/?section=sales"""

    def _payload(self, request, section: str, row: dict | None) -> dict:
        row = row or {}
        return {
            "section": section,
            "is_enabled": bool(row.get("is_enabled", False)),
            "frequency": row.get("frequency") or subs.FREQUENCY_WEEKLY,
            "recipients": list(row.get("recipients") or []),
            "channels": list(row.get("channels") or [subs.CHANNEL_CHAT]),
            "last_sent_at": row["last_sent_at"].isoformat() if row.get("last_sent_at") else None,
            "last_error": row.get("last_error"),
            "frequencies": list(subs.FREQUENCIES),
            "channel_options": list(subs.CHANNELS),
            "mail_available": _mail_available(request.user),
            "own_email": request.user.get("email") or None,
            "max_recipients": subs.MAX_RECIPIENTS,
        }

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="This person's report subscription for a section",
                         manual_parameters=_PARAMS[:1])
    def get(self, request):
        try:
            section = self._section(request)
        except (analytics.AnalyticsError, PermissionError) as exc:
            return self._refuse(exc)
        row = subs.get_subscription(request.user.company_id, request.user.id, section)
        return Response(self._payload(request, section, row))

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Switch, schedule and address the subscription",
                         manual_parameters=_PARAMS[:1])
    def put(self, request):
        try:
            section = self._section(request)
        except (analytics.AnalyticsError, PermissionError) as exc:
            return self._refuse(exc)
        data = request.data or {}
        errors: dict[str, list[str]] = {}

        is_enabled = bool(data.get("is_enabled", False))
        frequency = str(data.get("frequency") or subs.FREQUENCY_WEEKLY)
        if frequency not in subs.FREQUENCIES:
            errors["frequency"] = [str(_("Pick daily, weekly or monthly."))]

        raw_channels = data.get("channels")
        channels = [c for c in (raw_channels if isinstance(raw_channels, list) else []) if c in subs.CHANNELS]
        if is_enabled and not channels:
            errors["channels"] = [str(_("Pick at least one way to deliver the report."))]

        raw_recipients = data.get("recipients")
        recipients: list[str] = []
        for value in (raw_recipients if isinstance(raw_recipients, list) else []):
            address = str(value or "").strip().lower()
            if not address:
                continue
            try:
                validate_email(address)
            except ValidationError:
                errors.setdefault("recipients", []).append(str(_("«%(address)s» is not an email address.") % {"address": address}))
                continue
            if address not in recipients:
                recipients.append(address)
        if len(recipients) > subs.MAX_RECIPIENTS:
            errors.setdefault("recipients", []).append(
                str(_("At most %(n)d recipients.") % {"n": subs.MAX_RECIPIENTS})
            )
        if is_enabled and subs.CHANNEL_EMAIL in channels and not recipients:
            errors.setdefault("recipients", []).append(str(_("Add at least one address for the mailed report.")))

        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        row = subs.upsert_subscription(
            request.user.company_id, request.user.id, section,
            is_enabled=is_enabled, frequency=frequency, recipients=recipients,
            channels=channels or [subs.CHANNEL_CHAT],
        )
        return Response(self._payload(request, section, row))
