"""Endpoints for lending somebody to another workspace.

Their own module rather than another thousand lines in `views.py`, and for the
same reason `secondment_repository` is separate: everything else under
`/workspace/` answers within one `company_id`, and these five views are the
ones that deliberately reach past it. A reviewer asking "where can one
workspace touch another?" should have one file to read.
"""
from __future__ import annotations

import logging

from django.utils import timezone
from django.utils.translation import gettext as _
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.b2b.repository import get_company
from apps.b2b.workspace import repository as repo
from apps.b2b.workspace import secondment_repository as srepo
from apps.b2b.workspace.permissions import IsWorkspaceUser
from apps.b2b.workspace.secondment import Module, RequestRole, RequestStatus
from apps.b2b.workspace.serializers import (
    OrgPersonSerializer,
    SecondmentDeclineSerializer,
    SecondmentRequestCreateSerializer,
    SecondmentRequestSerializer,
)
from apps.b2b.workspace.tokens import create_workspace_tokens
from apps.b2b.workspace.views import WORKSPACE_TAG, WorkspaceAPIView

logger = logging.getLogger(__name__)


def _may_send(user) -> bool:
    """Who may ask another workspace for help.

    An owner or a lider — see `roles.REQUEST_ROLES`. Not every manager: this
    commits the workspace to letting an outsider in, with a role and a set of
    modules, for a stretch of time.
    """
    return bool(user.capabilities.get("can_request_help"))


class WorkspaceOrgPeopleView(WorkspaceAPIView):
    """GET /api/b2b/workspace/org/people/?search= — who else is in the org.

    The picker on "So'rov yuborish" searches this rather than `/team/`: the
    whole point is to reach somebody who is *not* in this workspace. Restricted
    to the org, so a workspace can only ever ask people who share an owner
    with it — never the whole of WEEL.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Search people in the org's other workspaces",
        manual_parameters=[
            openapi.Parameter("search", openapi.IN_QUERY, type=openapi.TYPE_STRING),
        ],
        responses={200: OrgPersonSerializer(many=True)},
    )
    def get(self, request):
        if not _may_send(request.user):
            return Response(
                {"detail": _("Your role does not allow sending requests.")},
                status=status.HTTP_403_FORBIDDEN,
            )
        org_id = srepo.org_id_for_company(request.user.company_id)
        people = srepo.search_org_people(
            org_id,
            exclude_company_id=request.user.company_id,
            search=(request.query_params.get("search") or "").strip() or None,
        )
        return Response({"results": OrgPersonSerializer(people, many=True).data})


class WorkspaceRequestListCreateView(WorkspaceAPIView):
    """GET  /api/b2b/workspace/requests/ — the inbox and the sent list.
    POST /api/b2b/workspace/requests/ — ask somebody to come and help."""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Requests received and sent",
        responses={200: SecondmentRequestSerializer(many=True)},
    )
    def get(self, request):
        # Received is per person and sent is per workspace, deliberately. An
        # ask is made *to* a human and *by* an office: whoever picks up the
        # reply on the sending side needs to see what a colleague sent while
        # they were out.
        received = srepo.list_requests_for_employee(request.user.home_employee_id)
        sent = (
            srepo.list_requests_from_company(request.user.company_id)
            if _may_send(request.user)
            else []
        )
        return Response({
            "received": SecondmentRequestSerializer(received, many=True).data,
            "sent": SecondmentRequestSerializer(sent, many=True).data,
        })

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Ask somebody from another workspace for help",
        request_body=SecondmentRequestCreateSerializer,
        responses={201: SecondmentRequestSerializer()},
    )
    def post(self, request):
        if not _may_send(request.user):
            return Response(
                {"detail": _("Your role does not allow sending requests.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = SecondmentRequestCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        org_id = srepo.org_id_for_company(request.user.company_id)
        candidates = srepo.search_org_people(
            org_id, exclude_company_id=request.user.company_id
        )
        target = next(
            (p for p in candidates if p["id"] == data["to_employee_id"]), None
        )
        if not target:
            # Covers all of: not in this org, already in this workspace, a
            # guest row, deactivated. One answer for all of them on purpose —
            # a 404 that distinguished them would be a way to probe the org.
            return Response(
                {"to_employee_id": [_("This person cannot be asked from here.")]},
                status=status.HTTP_404_NOT_FOUND,
            )

        existing = srepo.pending_request_between(
            request.user.company_id, data["to_employee_id"]
        )
        if existing:
            # Not an error. The second tap of a button that felt slow means
            # the same thing as the first, and the caller wants the request.
            return Response(
                SecondmentRequestSerializer(existing).data,
                status=status.HTTP_200_OK,
            )

        created = srepo.create_request(
            company_id=request.user.company_id,
            from_employee_id=request.user.id,
            to_employee_id=data["to_employee_id"],
            message=(data.get("message") or "").strip(),
            role=data["role"],
            modules=data.get("modules") or [],
            starts_at=data.get("starts_at"),
            ends_at=data.get("ends_at"),
        )
        if not created:
            return Response(
                {"detail": _("Could not create the request.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        _queue(created["id"], "sent")
        return Response(
            SecondmentRequestSerializer(created).data, status=status.HTTP_201_CREATED
        )


class WorkspaceRequestRespondView(WorkspaceAPIView):
    """POST /api/b2b/workspace/requests/<id>/<accept|decline|cancel>/"""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Accept, decline or cancel a request",
        request_body=SecondmentDeclineSerializer,
        responses={200: SecondmentRequestSerializer()},
    )
    def post(self, request, request_id: int, action: str):
        ask = srepo.get_request(request_id)
        if not ask:
            return Response(
                {"detail": _("Request not found.")}, status=status.HTTP_404_NOT_FOUND
            )

        if action == "cancel":
            return self._cancel(request, ask)
        if action == "accept":
            return self._accept(request, ask)
        return self._decline(request, ask)

    # -- who may do what ----------------------------------------------------

    def _is_recipient(self, request, ask) -> bool:
        # Against the *home* row: a person reading their inbox while signed in
        # as a guest of a third workspace is still the person being asked.
        return ask["to_employee_id"] == request.user.home_employee_id

    def _closed(self, ask):
        return Response(
            {"detail": _("This request has already been answered.")},
            status=status.HTTP_409_CONFLICT,
        )

    # -- the three endings --------------------------------------------------

    def _cancel(self, request, ask):
        if ask["company_id"] != request.user.company_id or not _may_send(request.user):
            return Response(
                {"detail": _("Only the workspace that sent it can withdraw it.")},
                status=status.HTTP_403_FORBIDDEN,
            )
        if not srepo.close_request(ask["id"], status=RequestStatus.CANCELLED):
            return self._closed(ask)
        return Response(SecondmentRequestSerializer(srepo.get_request(ask["id"])).data)

    def _decline(self, request, ask):
        if not self._is_recipient(request, ask):
            return Response(
                {"detail": _("This request was not sent to you.")},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = SecondmentDeclineSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        if not srepo.close_request(
            ask["id"],
            status=RequestStatus.DECLINED,
            decline_reason=serializer.validated_data["reason"],
        ):
            return self._closed(ask)

        _queue(ask["id"], "declined")
        return Response(SecondmentRequestSerializer(srepo.get_request(ask["id"])).data)

    def _accept(self, request, ask):
        if not self._is_recipient(request, ask):
            return Response(
                {"detail": _("This request was not sent to you.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Claim the request first. Everything after this creates rows, and
        # losing the race *after* creating them would leave a workspace with
        # two guest rows for one person.
        if not srepo.close_request(ask["id"], status=RequestStatus.ACCEPTED):
            return self._closed(ask)

        home = repo.get_workspace_employee(ask["to_employee_id"])
        if not home:
            return Response(
                {"detail": _("Your employee record could not be read.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        guest = srepo.create_guest_employee(
            company_id=ask["company_id"], home=home, role=ask["role"]
        )
        if not guest:
            return Response(
                {"detail": _("Could not join the workspace.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        srepo.create_membership(
            company_id=ask["company_id"],
            employee_id=guest["id"],
            home_employee_id=home["id"],
            request_id=ask["id"],
            role=ask["role"],
            modules=ask.get("modules") or [],
            # An acceptance with no start named begins now rather than at some
            # unstated future point — the person said yes and the workspace
            # asking is short-handed today.
            starts_at=ask.get("starts_at") or timezone.now(),
            ends_at=ask.get("ends_at"),
        )

        _queue(ask["id"], "accepted")
        return Response(SecondmentRequestSerializer(srepo.get_request(ask["id"])).data)


class WorkspaceSwitchView(WorkspaceAPIView):
    """GET  /api/b2b/workspace/switch/ — the workspaces this person can open.
    POST /api/b2b/workspace/switch/ — tokens for one of them.

    Signing in always lands on the workspace that hired you; this is how
    somebody gets to one they were lent to. A separate token per workspace
    rather than one token that carries a workspace header: every row in this
    schema references `b2b_employee(id)`, so "which workspace am I in" and
    "which employee am I" are the same question, and answering it once at
    sign-in is what keeps the other two hundred queries honest.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG, operation_summary="Workspaces this person can open"
    )
    def get(self, request):
        home_id = request.user.home_employee_id
        home = repo.get_workspace_employee(home_id) or {}
        home_company = get_company(home.get("company_id")) or {}

        places = [{
            "employee_id": home_id,
            "company_id": home.get("company_id"),
            "company_name": home_company.get("name"),
            "is_home": True,
            "role": home.get("role"),
            "modules": None,
            "ends_at": None,
        }]
        for membership in srepo.list_memberships_for_person(home_id):
            places.append({
                "employee_id": membership["employee_id"],
                "company_id": membership["company_id"],
                "company_name": membership.get("company_name"),
                "is_home": False,
                "role": membership.get("role"),
                "modules": membership.get("modules") or [],
                "ends_at": membership.get("ends_at"),
            })
        return Response({"results": places, "current_id": request.user.id})

    @swagger_auto_schema(
        tags=WORKSPACE_TAG, operation_summary="Get tokens for another workspace"
    )
    def post(self, request):
        target_id = request.data.get("employee_id")
        try:
            target_id = int(target_id)
        except (TypeError, ValueError):
            return Response(
                {"employee_id": [_("Which workspace?")]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        home_id = request.user.home_employee_id
        allowed = {home_id} | {
            m["employee_id"] for m in srepo.list_memberships_for_person(home_id)
        }
        if target_id not in allowed:
            return Response(
                {"detail": _("You do not have access to that workspace.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        employee = repo.get_workspace_employee(target_id)
        if not employee:
            return Response(
                {"detail": _("That workspace access has ended.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        tokens = create_workspace_tokens(employee)
        return Response({
            "access": tokens["access"],
            "refresh": tokens["refresh"],
            "employee_id": target_id,
            "company_id": employee["company_id"],
        })


def _queue(request_id: int, event: str) -> None:
    """Tell whoever is waiting on this, off the request."""
    try:
        from apps.b2b.workspace.tasks import notify_secondment_request

        notify_secondment_request.delay(request_id, event)
    except Exception:  # noqa: BLE001 - the request itself is stored
        logger.exception("Could not queue the %s notification for request %s", event, request_id)
