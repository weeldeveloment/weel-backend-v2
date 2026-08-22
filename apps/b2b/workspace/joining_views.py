"""Registration, and the three ways into a workspace.

Two kinds of session meet here, and which one an endpoint takes is the whole
design (see `accounts.py`):

* the **account** endpoints — finishing registration, listing where you work,
  opening a link, asking to join, creating a workspace — are what somebody who
  belongs to nothing can reach;
* the **workspace** endpoints — minting links, answering requests — are the
  administrative half, and need a workspace session and the permission.
"""
from __future__ import annotations

import re

from django.utils.translation import gettext as _
from drf_yasg.utils import swagger_auto_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.b2b.workspace import access_repository as arepo
from apps.b2b.workspace import accounts
from apps.b2b.workspace import joining_repository as jrepo
from apps.b2b.workspace.access import Module, Permission, Role
from apps.b2b.workspace.authentication import (
    AccountJWTAuthentication,
    WorkspaceAccount,
)
from apps.b2b.workspace.joining_repository import JoinStatus
from apps.b2b.workspace.permissions import IsWorkspaceUser
from apps.b2b.workspace.tokens import create_workspace_tokens
from apps.b2b.workspace.views import WORKSPACE_TAG, WorkspaceAPIView


class IsAccount(IsAuthenticated):
    """An account session, and specifically not a workspace one."""

    message = "This endpoint needs a Weel account session."

    def has_permission(self, request, view) -> bool:
        return isinstance(request.user, WorkspaceAccount)


class AccountAPIView(APIView):
    """Base for everything an account can reach before choosing a workspace."""

    authentication_classes = [AccountJWTAuthentication]
    permission_classes = [IsAccount]


# ─── Finishing registration ───────────────────────────────────────────────────

USERNAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,49}$")


class ProfileSerializer(serializers.Serializer):
    """The name and handle the TZ asks for after the OTP.

    Both required together: the flow is one screen, and an account with a name
    and no handle cannot be found by anybody, which is most of what a handle
    is for.
    """

    first_name = serializers.CharField(max_length=100, trim_whitespace=True)
    last_name = serializers.CharField(
        max_length=100, required=False, allow_blank=True, trim_whitespace=True
    )
    username = serializers.CharField(max_length=50, trim_whitespace=True)

    def validate_username(self, value: str) -> str:
        handle = value.strip().lstrip("@").lower()
        if not USERNAME_RE.fullmatch(handle):
            raise serializers.ValidationError(
                _(
                    "3–50 characters: lowercase letters, numbers and underscore, "
                    "starting with a letter."
                )
            )
        return handle


class AccountMeView(AccountAPIView):
    """GET  /api/b2b/workspace/account/me/ — who this is, and where they work.
    PUT  /api/b2b/workspace/account/me/ — finish registration."""

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="The Weel account")
    def get(self, request):
        return Response(_account_payload(request.user))

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Set name and username",
        request_body=ProfileSerializer,
    )
    def put(self, request):
        serializer = ProfileSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if accounts.username_taken(
            data["username"], exclude_account_id=request.user.id
        ):
            return Response(
                {"username": [_("This name is taken.")]},
                status=status.HTTP_409_CONFLICT,
            )

        updated = accounts.update_account(
            request.user.id,
            first_name=data["first_name"],
            last_name=data.get("last_name") or None,
            username=data["username"],
        )
        if not updated:
            # Lost the race for the handle between the check and the write.
            return Response(
                {"username": [_("This name is taken.")]},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(_account_payload(WorkspaceAccount(updated)))


class AccountUsernameSuggestionView(AccountAPIView):
    """GET /api/b2b/workspace/account/username-suggestion/

    The TZ says the system may propose a free handle. Offering one is most of
    what gets somebody past this screen — a blank field with a uniqueness rule
    is where registrations stop.
    """

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="A free username")
    def get(self, request):
        return Response({
            "username": accounts.suggest_username(
                request.user.get("first_name"),
                request.user.get("last_name"),
                request.user.phone or "",
            )
        })


class WorkspaceCreateSerializer(serializers.Serializer):
    """Opening a new workspace.

    A name and nothing else. The handle other people find it by is derived
    from the name and numbered if it has to be — asking somebody to invent a
    unique slug on the screen where they are naming their company is asking
    them to abandon it.
    """

    name = serializers.CharField(max_length=200, trim_whitespace=True)
    #: Which organisation to open it under. Omitted, it goes under the one
    #: this account already belongs to, or a new one if it belongs to none.
    org_id = serializers.IntegerField(required=False, allow_null=True)

    def validate_name(self, value: str) -> str:
        if len(value.strip()) < 2:
            raise serializers.ValidationError(_("Please give it a name."))
        return value.strip()


class AccountWorkspacesView(AccountAPIView):
    """GET  /api/b2b/workspace/account/workspaces/ — where this account works.
    POST /api/b2b/workspace/account/workspaces/ — open a new one."""

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="My workspaces")
    def get(self, request):
        return Response({"results": _workspaces(request.user.id)})

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Create a workspace",
        request_body=WorkspaceCreateSerializer,
    )
    def post(self, request):
        if not request.user.has_profile:
            # A workspace whose creator has no name is one nobody can identify
            # the administrator of.
            return Response(
                {"detail": _("Finish your profile first."), "problem": "no_profile"},
                status=status.HTTP_409_CONFLICT,
            )

        serializer = WorkspaceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        org_id = data.get("org_id")
        if org_id is not None and org_id not in accounts.org_ids_for_account(
            request.user.id
        ):
            # Opening a workspace inside somebody else's organisation would be
            # a way in through the back door.
            return Response(
                {"org_id": [_("You do not belong to that company.")]},
                status=status.HTTP_403_FORBIDDEN,
            )

        created = accounts.create_workspace(
            account=request.user._data, name=data["name"], org_id=org_id
        )
        if not created or not created.get("employee"):
            return Response(
                {"detail": _("Could not create the workspace.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        employee = created["employee"]
        company = created["company"]
        arepo.record_audit(
            company["id"],
            actor_employee_id=employee["id"],
            action="workspace.created",
            target_type="company",
            target_id=company["id"],
            payload={"name": company.get("name"), "role": created["role"]},
        )
        tokens = create_workspace_tokens(employee)
        return Response(
            {
                "access": tokens["access"],
                "refresh": tokens["refresh"],
                "employee_id": employee["id"],
                "company_id": company["id"],
                "company_name": company.get("name"),
                "company_slug": company.get("slug"),
                "role": created["role"],
            },
            status=status.HTTP_201_CREATED,
        )


class AccountOpenWorkspaceView(AccountAPIView):
    """POST /api/b2b/workspace/account/workspaces/<employee_id>/open/"""

    @swagger_auto_schema(
        tags=WORKSPACE_TAG, operation_summary="Open a workspace session"
    )
    def post(self, request, employee_id: int):
        allowed = {row["employee_id"] for row in accounts.list_memberships(request.user.id)}
        if employee_id not in allowed:
            return Response(
                {"detail": _("You are not a member of that workspace.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        from apps.b2b.workspace.repository import get_workspace_employee

        employee = get_workspace_employee(employee_id)
        if not employee:
            return Response(
                {"detail": _("That workspace access has ended.")},
                status=status.HTTP_403_FORBIDDEN,
            )
        tokens = create_workspace_tokens(employee)
        return Response({
            "access": tokens["access"],
            "refresh": tokens["refresh"],
            "employee_id": employee_id,
            "company_id": employee["company_id"],
        })


def _account_payload(account: WorkspaceAccount) -> dict:
    return {
        "id": account.id,
        "phone": account.phone,
        "username": account.username,
        "first_name": account.get("first_name"),
        "last_name": account.get("last_name"),
        "photo": account.get("photo"),
        # What the app routes on: somebody who stopped after the OTP is sent
        # back to the name screen rather than into an empty workspace list.
        "has_profile": account.has_profile,
        "workspaces": _workspaces(account.id),
    }


def _workspaces(account_id: int) -> list[dict]:
    return [
        {
            "employee_id": row["employee_id"],
            "company_id": row["company_id"],
            "company_name": row["company_name"],
            "company_slug": row.get("company_slug"),
            "role": Role.clean(row.get("role")),
            "role_label": Role.label(row.get("role")),
            "is_guest": bool(row.get("is_guest")),
        }
        for row in accounts.list_memberships(account_id)
    ]


# ─── Invitations: the workspace's half ────────────────────────────────────────

class InviteCreateSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=Role.CHOICES)
    #: Null is "by role"; a list is "configure" and replaces it.
    modules = serializers.ListField(
        child=serializers.CharField(), required=False, allow_null=True
    )
    permissions = serializers.ListField(
        child=serializers.CharField(), required=False, allow_null=True
    )
    days = serializers.IntegerField(
        required=False, min_value=1, max_value=jrepo.MAX_INVITE_DAYS
    )

    def validate_role(self, value: str) -> str:
        if value == Role.OWNER:
            # A company has one owner and it is not handed out on a link.
            raise serializers.ValidationError(_("An owner cannot be invited."))
        return value


class WorkspaceInviteListCreateView(WorkspaceAPIView):
    """GET/POST /api/b2b/workspace/invites/"""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]
    required_permission = Permission.EMPLOYEE_INVITE

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Invite links")
    def get(self, request):
        return Response({
            "results": [
                _invite_payload(invite)
                for invite in jrepo.list_invites(request.user.company_id)
            ]
        })

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Create an invite link",
        request_body=InviteCreateSerializer,
    )
    def post(self, request):
        serializer = InviteCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        invite = jrepo.create_invite(
            company_id=request.user.company_id,
            created_by=request.user.id,
            role=data["role"],
            modules=data.get("modules"),
            permissions=data.get("permissions"),
            days=data.get("days"),
        )
        if not invite:
            return Response(
                {"detail": _("Could not create the invite.")},
                status=status.HTTP_400_BAD_REQUEST,
            )
        arepo.record_audit(
            request.user.company_id,
            actor_employee_id=request.user.id,
            action="invite.created",
            target_type="invite",
            target_id=invite["id"],
            payload={"role": invite["role"], "modules": invite.get("modules")},
        )
        return Response(_invite_payload(invite), status=status.HTTP_201_CREATED)


class WorkspaceInviteRevokeView(WorkspaceAPIView):
    """POST /api/b2b/workspace/invites/<id>/revoke/"""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]
    required_permission = Permission.EMPLOYEE_INVITE

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Revoke an invite link")
    def post(self, request, invite_id: int):
        if not jrepo.revoke_invite(invite_id, request.user.company_id):
            return Response(
                {"detail": _("This link is already closed.")},
                status=status.HTTP_409_CONFLICT,
            )
        arepo.record_audit(
            request.user.company_id,
            actor_employee_id=request.user.id,
            action="invite.revoked",
            target_type="invite",
            target_id=invite_id,
        )
        return Response({"revoked": True})


def _invite_payload(invite: dict) -> dict:
    problem = jrepo.invite_problem(invite)
    return {
        "id": invite["id"],
        "token": invite["token"],
        "role": invite["role"],
        "role_label": Role.label(invite["role"]),
        "modules": invite.get("modules"),
        "permissions": invite.get("permissions"),
        "expires_at": invite.get("expires_at"),
        "revoked_at": invite.get("revoked_at"),
        "accepted_at": invite.get("accepted_at"),
        "accepted_by_username": invite.get("accepted_by_username"),
        "created_by_name": invite.get("created_by_name"),
        "created_at": invite.get("created_at"),
        "is_usable": problem is None,
        "problem": problem,
    }


# ─── Invitations: the invitee's half ──────────────────────────────────────────

class InvitePreviewView(AccountAPIView):
    """GET  /api/b2b/workspace/account/invites/<token>/ — what this link offers.
    POST /api/b2b/workspace/account/invites/<token>/ — take it."""

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Preview an invite")
    def get(self, request, token: str):
        invite = jrepo.get_invite_by_token(token)
        problem = jrepo.invite_problem(invite)
        if problem == "not_found":
            return Response(
                {"detail": _("This link is not valid.")},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response({
            "company_name": invite["company_name"],
            "role": invite["role"],
            "role_label": Role.label(invite["role"]),
            "modules": invite.get("modules") or _role_modules(invite),
            "invited_by": invite.get("created_by_name"),
            "expires_at": invite.get("expires_at"),
            "is_usable": problem is None,
            "problem": problem,
        })

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Accept an invite")
    def post(self, request, token: str):
        invite = jrepo.get_invite_by_token(token)
        problem = jrepo.invite_problem(invite)
        if problem:
            return Response(
                {"detail": _("This link cannot be used."), "problem": problem},
                status=status.HTTP_404_NOT_FOUND
                if problem == "not_found"
                else status.HTTP_409_CONFLICT,
            )

        if not request.user.has_profile:
            # A member with no name is a row nobody can identify. The TZ puts
            # name and username before joining anything for that reason.
            return Response(
                {"detail": _("Finish your profile first."), "problem": "no_profile"},
                status=status.HTTP_409_CONFLICT,
            )

        existing = accounts.employee_in_company(request.user.id, invite["company_id"])
        if existing and not existing.get("is_chat_only"):
            return Response(
                {"detail": _("You are already in this workspace.")},
                status=status.HTTP_409_CONFLICT,
            )

        # Claim the link before creating anything. Two taps a moment apart
        # would otherwise both pass and put the person on the roster twice.
        if not jrepo.mark_invite_accepted(invite["id"], request.user.id):
            return Response(
                {"detail": _("This link cannot be used."), "problem": "used"},
                status=status.HTTP_409_CONFLICT,
            )

        employee = accounts.create_membership(
            account=request.user._data,
            company_id=invite["company_id"],
            role=invite["role"],
            modules=invite.get("modules"),
            permissions=invite.get("permissions"),
        )
        if not employee:
            return Response(
                {"detail": _("Could not join the workspace.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        arepo.record_audit(
            invite["company_id"],
            actor_employee_id=employee["id"],
            action="invite.accepted",
            target_type="employee",
            target_id=employee["id"],
            payload={"invite_id": invite["id"], "role": invite["role"]},
        )
        tokens = create_workspace_tokens(employee)
        return Response(
            {
                "access": tokens["access"],
                "refresh": tokens["refresh"],
                "employee_id": employee["id"],
                "company_id": employee["company_id"],
                "company_name": invite["company_name"],
            },
            status=status.HTTP_201_CREATED,
        )


def _role_modules(invite: dict) -> list[str]:
    """What "by role" actually opens, for the preview.

    Resolved rather than left null: somebody deciding whether to accept should
    be told what they are accepting, and "by role" tells them nothing.
    """
    modules, _permissions = arepo.role_access(invite["company_id"], invite["role"])
    return modules


# ─── Asking to join ───────────────────────────────────────────────────────────

class JoinRequestSerializer(serializers.Serializer):
    slug = serializers.CharField(max_length=50)
    message = serializers.CharField(
        max_length=1000, required=False, allow_blank=True, trim_whitespace=True
    )
    #: What they would like. A request for access, not a grant — the workspace
    #: decides, which is exactly what the TZ says about this field.
    modules = serializers.ListField(
        child=serializers.CharField(), required=False, allow_null=True
    )


class AccountJoinRequestView(AccountAPIView):
    """POST /api/b2b/workspace/account/join-requests/ — ask to be let in."""

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Ask to join a workspace",
        request_body=JoinRequestSerializer,
    )
    def post(self, request):
        serializer = JoinRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        company = jrepo.find_company_by_slug(data["slug"])
        if not company:
            return Response(
                {"slug": [_("No workspace with that name.")]},
                status=status.HTTP_404_NOT_FOUND,
            )
        if not request.user.has_profile:
            return Response(
                {"detail": _("Finish your profile first."), "problem": "no_profile"},
                status=status.HTTP_409_CONFLICT,
            )
        if accounts.employee_in_company(request.user.id, company["id"]):
            return Response(
                {"detail": _("You are already in this workspace.")},
                status=status.HTTP_409_CONFLICT,
            )

        created = jrepo.create_join_request(
            company_id=company["id"],
            account_id=request.user.id,
            message=(data.get("message") or "").strip(),
            wanted_modules=data.get("modules"),
        )
        return Response(
            {
                "id": created["id"] if created else None,
                "company_name": company["name"],
                "status": JoinStatus.PENDING,
            },
            status=status.HTTP_201_CREATED,
        )


class JoinDecisionSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=Role.CHOICES, required=False)
    modules = serializers.ListField(
        child=serializers.CharField(), required=False, allow_null=True
    )
    reason = serializers.CharField(
        max_length=1000, required=False, allow_blank=True, trim_whitespace=True
    )

    def validate_role(self, value: str) -> str:
        if value == Role.OWNER:
            raise serializers.ValidationError(_("An owner cannot be added this way."))
        return value


class WorkspaceJoinRequestListView(WorkspaceAPIView):
    """GET /api/b2b/workspace/join-requests/ — who is asking to be let in."""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]
    required_permission = Permission.EMPLOYEE_INVITE

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Join requests")
    def get(self, request):
        return Response({
            "results": [
                {
                    "id": row["id"],
                    "account_id": row["account_id"],
                    "username": row.get("username"),
                    "phone": row.get("phone"),
                    "full_name": " ".join(
                        part
                        for part in [row.get("last_name"), row.get("first_name")]
                        if part
                    ).strip(),
                    "photo": row.get("photo"),
                    "message": row.get("message"),
                    "wanted_modules": row.get("wanted_modules"),
                    "status": row["status"],
                    "created_at": row.get("created_at"),
                }
                for row in jrepo.list_join_requests(request.user.company_id)
            ]
        })


class WorkspaceJoinRequestDecideView(WorkspaceAPIView):
    """POST /api/b2b/workspace/join-requests/<id>/<accept|decline>/"""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]
    required_permission = Permission.EMPLOYEE_INVITE

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Answer a join request",
        request_body=JoinDecisionSerializer,
    )
    def post(self, request, request_id: int, action: str):
        ask = jrepo.get_join_request(request_id)
        if not ask or ask["company_id"] != request.user.company_id:
            return Response(
                {"detail": _("Request not found.")}, status=status.HTTP_404_NOT_FOUND
            )

        serializer = JoinDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if action == "decline":
            if not jrepo.close_join_request(
                request_id,
                status=JoinStatus.DECLINED,
                decided_by=request.user.id,
                decline_reason=(data.get("reason") or "").strip() or None,
            ):
                return Response(
                    {"detail": _("This request has already been answered.")},
                    status=status.HTTP_409_CONFLICT,
                )
            return Response({"status": JoinStatus.DECLINED})

        # The workspace decides the standing, not the asker. What they asked
        # for is only a request — `wanted_modules` is never read here.
        role = data.get("role") or Role.EMPLOYEE
        modules = data.get("modules")

        if not jrepo.close_join_request(
            request_id,
            status=JoinStatus.ACCEPTED,
            decided_by=request.user.id,
            granted_role=role,
            granted_modules=modules,
        ):
            return Response(
                {"detail": _("This request has already been answered.")},
                status=status.HTTP_409_CONFLICT,
            )

        account = accounts.get_account(ask["account_id"])
        if not account:
            return Response(
                {"detail": _("That account no longer exists.")},
                status=status.HTTP_410_GONE,
            )
        employee = accounts.create_membership(
            account=account,
            company_id=ask["company_id"],
            role=role,
            modules=modules,
        )
        arepo.record_audit(
            request.user.company_id,
            actor_employee_id=request.user.id,
            action="join_request.accepted",
            target_type="employee",
            target_id=employee["id"] if employee else None,
            payload={"role": Role.clean(role), "modules": Module.clean(modules) if modules else None},
        )
        return Response({"status": JoinStatus.ACCEPTED})
