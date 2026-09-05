"""Call Center Desk (`weelccd`) endpoints — `/api/admin-auth/ccd/…`.

The desk is a separate FastAPI service with its own UI, 2FA and operator records. It
holds no copy of the B2B data: companies, workspaces, rosters, calls, audit and the
approval queues are read from here, so this backend stays the single source of truth.

Everything is behind the same admin JWT the rest of `admin_auth` uses, so a desk
operator is an ordinary WEEL admin and every action is attributed to that account
rather than to a shared service identity.
"""

from __future__ import annotations

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import serializers, status
from rest_framework.response import Response

from . import ccd_repository as repo
from .b2b_admin_views import AdminBaseView

_SEARCH = openapi.Parameter(
    "q", openapi.IN_QUERY, type=openapi.TYPE_STRING,
    description="Free-text search.",
)
_COMPANY = openapi.Parameter(
    "company_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER,
    description="Restrict to one company (a b2b_org id).",
)


def _q(request) -> str | None:
    return (request.query_params.get("q") or "").strip() or None


def _int_param(request, name: str) -> int | None:
    raw = request.query_params.get(name)
    try:
        return int(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _limit(request, default: int, ceiling: int) -> int:
    value = _int_param(request, "limit") or default
    return max(1, min(value, ceiling))


class CcdCompaniesView(AdminBaseView):
    """GET — every company (a `b2b_org`) with the counts the desk's table shows."""

    @swagger_auto_schema(manual_parameters=[_SEARCH], tags=["CCD"])
    def get(self, request):
        return Response(repo.list_companies(search=_q(request)))


class CcdCompanyDetailView(AdminBaseView):
    @swagger_auto_schema(tags=["CCD"])
    def get(self, request, company_id: int):
        company = repo.get_company(company_id)
        if not company:
            return Response({"detail": "Company not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(company)


class CcdWorkspacesView(AdminBaseView):
    """GET — workspaces (`b2b_company` rows), optionally for one company."""

    @swagger_auto_schema(manual_parameters=[_COMPANY], tags=["CCD"])
    def get(self, request):
        return Response(repo.list_workspaces(org_id=_int_param(request, "company_id")))


class CcdEmployeesView(AdminBaseView):
    """GET — the roster across every company, which is what the desk's Users screen is."""

    @swagger_auto_schema(manual_parameters=[_SEARCH, _COMPANY], tags=["CCD"])
    def get(self, request):
        return Response(
            repo.list_employees(search=_q(request), org_id=_int_param(request, "company_id"))
        )


class CcdCallsView(AdminBaseView):
    @swagger_auto_schema(tags=["CCD"])
    def get(self, request):
        return Response(repo.list_calls(limit=_limit(request, 200, 1000)))


class CcdAuditView(AdminBaseView):
    @swagger_auto_schema(tags=["CCD"])
    def get(self, request):
        return Response(repo.list_audit(limit=_limit(request, 300, 1000)))


class CcdApprovalsView(AdminBaseView):
    """GET — delete, ownership and join requests as one queue."""

    @swagger_auto_schema(tags=["CCD"])
    def get(self, request):
        return Response(repo.list_approvals())


class CcdTicketsView(AdminBaseView):
    """GET — the support inbox. Same threads as `/b2b/support/`, but carrying the org id
    the desk links a ticket to; see `ccd_repository.list_support_threads`."""

    @swagger_auto_schema(manual_parameters=[_SEARCH], tags=["CCD"])
    def get(self, request):
        return Response(repo.list_support_threads(search=_q(request), limit=_limit(request, 200, 1000)))


class CcdTicketMessagesView(AdminBaseView):
    """GET — one conversation, oldest first.

    Reading does *not* mark the employee's lines answered here: that is what clears the
    inbox counter, and it belongs to actually replying (`/b2b/support/<id>/`), not to an
    operator glancing at a thread.
    """

    @swagger_auto_schema(tags=["CCD"])
    def get(self, request, employee_id: int):
        return Response(repo.support_messages(employee_id))


class _ActiveSerializer(serializers.Serializer):
    active = serializers.BooleanField()


class CcdEmployeeActiveView(AdminBaseView):
    """POST — block or unblock a person. `{"active": false}` is the desk's Block."""

    @swagger_auto_schema(request_body=_ActiveSerializer, tags=["CCD"])
    def post(self, request, employee_id: int):
        serializer = _ActiveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        row = repo.set_employee_active(employee_id, active=serializer.validated_data["active"])
        if not row:
            return Response({"detail": "Employee not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(row)


class CcdWorkspaceActiveView(AdminBaseView):
    """POST — freeze or unfreeze a workspace. `{"active": false}` is the desk's Freeze."""

    @swagger_auto_schema(request_body=_ActiveSerializer, tags=["CCD"])
    def post(self, request, workspace_id: int):
        serializer = _ActiveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        row = repo.set_workspace_active(workspace_id, active=serializer.validated_data["active"])
        if not row:
            return Response({"detail": "Workspace not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(row)
