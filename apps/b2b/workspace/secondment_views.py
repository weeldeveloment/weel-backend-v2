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
from apps.b2b.workspace import accounts, realtime
from apps.b2b.workspace import repository as repo
from apps.b2b.workspace import storage
from apps.b2b.workspace import secondment_repository as srepo
from apps.b2b.workspace.access import Permission
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
    """Who may ask somebody elsewhere in the org for help.

    The owner, a lider or a manager — `can_send_request`, as the owner set it
    on 2026-09-10. An employee may search the org (see
    [WorkspaceOrgPeopleView]) but not send, and a guest does neither.
    """
    return bool(user.capabilities.get("can_send_request"))


def _join_requests_received(company_id: int) -> list[tuple]:
    """Somebody outside asking to join, in the same wire shape as a
    secondment ask — see [WorkspaceRequestListCreateView.get].

    Answered by `Permission.EMPLOYEE_INVITE` here rather than `_may_send`:
    deciding a join request and asking for help are different actions with
    different defaults, and this is the one the join-request endpoints
    themselves gate on.
    """
    from apps.b2b.workspace import joining_repository as jrepo

    rows = []
    for row in jrepo.list_join_requests(company_id):
        full_name = " ".join(
            part for part in [row.get("first_name"), row.get("last_name")] if part
        ).strip()
        rows.append((
            row["created_at"],
            {
                "kind": "join",
                "id": row["id"],
                "company_id": company_id,
                "from_full_name": full_name,
                "from_photo": storage.photo_url(row.get("photo")),
                "phone": row.get("phone"),
                "message": row.get("message") or "",
                "status": row["status"],
                "decline_reason": row.get("decline_reason"),
                "created_at": row["created_at"],
            },
        ))
    return rows


#: The mobile app's `RequestRole` enum speaks a different wire vocabulary
#: than the workspace's own `Role` — "lider" and "ghost" rather than "admin"
#: and "guest", left over from the secondment feature's own history (see
#: `Role.ALIASES`). A join request's granted role has to be translated into
#: it, or `_roleLabel` on the other end simply fails to match and drops it.
_ROLE_TO_REQUEST_WIRE = {
    "admin": "lider",
    "manager": "manager",
    "employee": "employee",
    "guest": "ghost",
}


def _join_requests_sent(account_id: int) -> list[tuple]:
    """What this account itself has asked to join — the "Jo'natgan" half of a
    join request, addressed to the company rather than to a person."""
    from apps.b2b.workspace import joining_repository as jrepo
    from apps.b2b.workspace.access import Role

    rows = []
    for row in jrepo.list_account_join_requests(account_id):
        granted = row.get("granted_role")
        rows.append((
            row["created_at"],
            {
                "kind": "join",
                "id": row["id"],
                "to_company_name": row.get("company_name"),
                "status": row["status"],
                "decline_reason": row.get("decline_reason"),
                "role": _ROLE_TO_REQUEST_WIRE.get(Role.clean(granted)) if granted else None,
                "created_at": row["created_at"],
            },
        ))
    return rows


class WorkspaceOrgPeopleView(WorkspaceAPIView):
    """GET /api/b2b/workspace/org/people/?search= — anyone in the org.

    The picker on "So'rov yuborish" searches this rather than `/team/`: it
    spans every workspace under the same owner, this one included, so a name,
    handle or phone finds the person wherever they sit. Only the searcher is
    left out. Restricted to the org — never the whole of WEEL.
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
        # Anybody on the roster may look — an employee finds a colleague and
        # is told on the send button that asking is not theirs to do. Only a
        # guest is kept out: this is the host org's directory, not theirs.
        if request.user.is_guest:
            return Response(
                {"detail": _("Your role does not allow sending requests.")},
                status=status.HTTP_403_FORBIDDEN,
            )
        org_id = srepo.org_id_for_company(request.user.company_id)
        people = srepo.search_org_persons(
            org_id,
            # One row per person across the whole org, this workspace included,
            # each saying whether they are already here — see
            # `search_org_persons`. The searcher is dropped with every seat of
            # theirs: a request has to go *to* somebody else.
            here_company_id=request.user.company_id,
            exclude_employee_id=request.user.id,
            exclude_account_id=request.user.get("account_id"),
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

        # A join request is the same question from the other direction —
        # somebody outside asking in rather than a colleague asking for help —
        # and the app draws both as one card in one list. See
        # `WorkspaceRequest.kind` on the mobile side: splitting them would
        # mean two inboxes to check for the exact people who decide both.
        received_rows = [
            (row["created_at"], {**SecondmentRequestSerializer(row).data, "kind": "message"})
            for row in received
        ]
        sent_rows = [
            (row["created_at"], {**SecondmentRequestSerializer(row).data, "kind": "message"})
            for row in sent
        ]

        if request.user.may(Permission.EMPLOYEE_INVITE):
            received_rows += _join_requests_received(request.user.company_id)

        account_id = request.user.get("account_id")
        if account_id:
            sent_rows += _join_requests_sent(account_id)

        received_rows.sort(key=lambda pair: pair[0], reverse=True)
        sent_rows.sort(key=lambda pair: pair[0], reverse=True)

        return Response({
            "received": [row for _, row in received_rows],
            "sent": [row for _, row in sent_rows],
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
        # Looked up by id rather than found in "the first thirty": the org can
        # be bigger than one page of the picker.
        candidates = srepo.search_org_people(
            org_id,
            exclude_employee_id=request.user.id,
            exclude_account_id=request.user.get("account_id"),
            employee_id=data["to_employee_id"],
            limit=None,
        )
        target = next(
            (p for p in candidates if p["id"] == data["to_employee_id"]), None
        )
        if not target:
            # Covers all of: not in this org, a guest row, deactivated. One
            # answer for all of them on purpose — a 404 that distinguished them
            # would be a way to probe the org.
            return Response(
                {"to_employee_id": [_("This person cannot be asked from here.")]},
                status=status.HTTP_404_NOT_FOUND,
            )
        if target["company_id"] == request.user.company_id or accounts.person_seated_in(
            request.user.company_id, target["id"]
        ):
            # The picker lists the whole org, this workspace included, so you
            # can look anyone up — but a secondment brings somebody *in*, and
            # this person is already here. Said plainly rather than folded into
            # the 404 above: it is not a probe, it is a normal mistake.
            return Response(
                {"to_employee_id": [_("This person is already in your workspace.")]},
                status=status.HTTP_400_BAD_REQUEST,
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
        _announce(created)
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
        # Against every seat of the person: the request names one of their
        # seats, but it asks the human — whichever workspace they answer from,
        # and as a guest of a third one through their *home* row.
        return ask["to_employee_id"] in srepo.person_seat_ids(
            request.user.home_employee_id
        )

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
        _announce(ask)
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
        _announce(ask)
        return Response(SecondmentRequestSerializer(srepo.get_request(ask["id"])).data)

    def _accept(self, request, ask):
        if not self._is_recipient(request, ask):
            return Response(
                {"detail": _("This request was not sent to you.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        if accounts.person_seated_in(ask["company_id"], ask["to_employee_id"]):
            # Already there — on its staff, or lent to it by another request.
            # A second seat would be a second copy of that workspace in their
            # switcher, so the request is closed as the one that is not needed.
            srepo.close_request(
                ask["id"],
                status=RequestStatus.DECLINED,
                decline_reason=_("Already in this workspace."),
            )
            return Response(
                {
                    "detail": _("You are already in this workspace."),
                    "code": "already_in_workspace",
                },
                status=status.HTTP_409_CONFLICT,
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
        _announce(ask, team=True)
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


def _announce(ask: dict, *, team: bool = False) -> None:
    """Tell both ends of a request that it moved, on the live feed.

    `WorkspaceAPIView.finalize_response` already announces a write — but to
    the workspace of whoever made it, and a request always has its other end
    somewhere else. Accepted from Toshkent, the Samarqand office that asked
    kept "kutilmoqda" on its sent list, and the person it had just gained
    stayed off its roster, until the app was closed and opened again. So the
    asking workspace hears `request` (and `team`, when somebody joined it),
    and the person asked hears `request` on every seat of theirs.
    """
    try:
        realtime.publish_company(ask["company_id"], realtime.EVENT_REQUEST, action="changed")
        if team:
            realtime.publish_company(ask["company_id"], realtime.EVENT_TEAM, action="changed")
        realtime.publish_employees(
            srepo.person_seat_ids(ask["to_employee_id"]),
            realtime.EVENT_REQUEST,
            action="changed",
        )
    except Exception:  # noqa: BLE001 - the request itself is stored
        logger.exception("Could not announce request %s", ask.get("id"))


def _queue(request_id: int, event: str) -> None:
    """Tell whoever is waiting on this, off the request."""
    try:
        from apps.b2b.workspace.tasks import notify_secondment_request

        notify_secondment_request.delay(request_id, event)
    except Exception:  # noqa: BLE001 - the request itself is stored
        logger.exception("Could not queue the %s notification for request %s", event, request_id)
