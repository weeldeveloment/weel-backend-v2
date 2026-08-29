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

import logging
import re

from django.utils.translation import gettext as _
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.b2b.workspace import access_repository as arepo
from apps.b2b.workspace import accounts
from apps.b2b.workspace import joining_repository as jrepo
from apps.b2b.workspace import repository as repo
from apps.b2b.workspace import storage
from apps.b2b.workspace.access import Module, Permission, Role
from apps.b2b.workspace.authentication import (
    AccountJWTAuthentication,
    WorkspaceAccount,
)
from apps.b2b.workspace.joining_repository import JoinStatus
from apps.b2b.workspace.permissions import IsWorkspaceManager, IsWorkspaceUser
from apps.b2b.workspace.tokens import create_workspace_tokens
from apps.b2b.workspace.views import WORKSPACE_TAG, WorkspaceAPIView

logger = logging.getLogger(__name__)


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

USERNAME_RE = accounts.USERNAME_RE


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
        tags=WORKSPACE_TAG, operation_summary="Delete this account"
    )
    def delete(self, request):
        """Erase the person, keep the work.

        Required of any app that lets somebody sign up — App Store guideline
        5.1.1(v) — and it has to be reachable from inside the app rather than
        by writing to support.

        It always succeeds. Owning a company cannot be handed over anywhere in
        this product, so refusing while somebody owns one would be refusing
        for good; instead the companies they solely own are closed with them,
        and `GET` on this endpoint is what lets the screen say which ones
        before anybody presses anything.
        """
        result = accounts.delete_account(request.user.id)
        return Response(result, status=status.HTTP_200_OK)

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


class AccountUsernameCheckView(AccountAPIView):
    """GET /api/b2b/workspace/account/username-check/?username=xusan_design

    Whether a handle is free, and what else to try if it is not.

    Answered as the field is typed rather than only on submit. A uniqueness
    rule that is enforced at the end of a form is a form people fill in twice,
    and the handle is the last screen of registration — the worst place to
    send somebody back to.

    Reading this tells the caller whether *some* handle exists, which is
    exactly what the screen after it does anyway; it needs an account session,
    so it is not an open directory probe.
    """

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Is this username free?",
        manual_parameters=[
            openapi.Parameter(
                "username",
                openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                description="The handle to test, with or without its @.",
            )
        ],
    )
    def get(self, request):
        raw = (request.query_params.get("username") or "").strip().lstrip("@").lower()

        def from_the_name(limit: int = 3) -> list[str]:
            return accounts.suggest_usernames(
                request.user.get("first_name"),
                request.user.get("last_name"),
                request.user.phone or "",
                limit=limit,
            )

        if not USERNAME_RE.fullmatch(raw):
            # Not a verdict on availability — the field is still being typed,
            # or holds something the serializer would refuse anyway. The
            # suggestions are the opening offer for an empty field.
            return Response({
                "username": raw,
                "valid": False,
                "available": False,
                "suggestions": from_the_name(),
            })

        taken = accounts.username_taken(raw, exclude_account_id=request.user.id)
        if not taken:
            return Response({
                "username": raw,
                "valid": True,
                "available": True,
                "suggestions": from_the_name(),
            })

        # Taken. Answer with handles that look like the one they wanted first,
        # and fill out the row from the name only if there is room left: they
        # have already said what they want to be called.
        suggestions = accounts.suggest_username_variants(raw)
        for candidate in from_the_name():
            if len(suggestions) >= 3:
                break
            if candidate not in suggestions:
                suggestions.append(candidate)
        return Response({
            "username": raw,
            "valid": True,
            "available": False,
            "suggestions": suggestions,
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
    description = serializers.CharField(
        max_length=500, required=False, allow_blank=True, trim_whitespace=True
    )
    #: One of `WorkspaceIcon`'s keys on the client. Free text here rather than
    #: an enum — the set of icons is the app's to grow without a migration on
    #: this side, and an unrecognised key just draws as the client's default.
    icon = serializers.CharField(
        max_length=20, required=False, allow_blank=True, allow_null=True
    )
    #: What the first workspace inside a brand-new company is called. Ignored
    #: when `org_id` is given — there `name` already names the workspace.
    workspace_name = serializers.CharField(
        max_length=200, required=False, allow_blank=True, trim_whitespace=True
    )
    #: The company's STIR / INN. Optional, and never validated as a checksum:
    #: the screen says "ixtiyoriy" and a wrong digit here should not stop
    #: somebody opening their company.
    tax_id = serializers.CharField(
        max_length=20, required=False, allow_blank=True, trim_whitespace=True
    )

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
            account=request.user._data,
            name=data["name"],
            org_id=org_id,
            description=data.get("description"),
            icon=data.get("icon") or None,
            workspace_name=data.get("workspace_name") or None,
            tax_id=data.get("tax_id") or None,
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
        org = created.get("org") or {}
        tokens = create_workspace_tokens(employee)
        return Response(
            {
                "access": tokens["access"],
                "refresh": tokens["refresh"],
                "employee_id": employee["id"],
                "company_id": company["id"],
                # `company_name` is the *workspace* — see the naming note in
                # `create_b2b_tables`. The screen that confirms this shows both
                # lines, so both are named here rather than left to be guessed.
                "company_name": company.get("name"),
                "company_slug": company.get("slug"),
                "org_id": org.get("id"),
                "org_name": org.get("name"),
                # Handed back at creation so the screen that confirms it can
                # show the owner what to give people.
                "org_join_code": org.get("join_code"),
                "workspace_name": company.get("name"),
                "role": created["role"],
                "role_label": Role.label(created["role"]),
            },
            status=status.HTTP_201_CREATED,
        )


class AccountOrgWorkspacesView(AccountAPIView):
    """GET /api/b2b/workspace/account/orgs/<org_id>/workspaces/ — every
    workspace under this company, for the "Workspace'lar" screen.

    Gated the same way `POST /account/workspaces/` gates opening one inside
    an org: holding any active roster row in it. An org's workspaces are
    already visible sideways to anyone on one of them — see
    `WorkspaceOrgPeopleView` — this is that same boundary applied to the list
    of workspaces rather than the list of people.
    """

    @swagger_auto_schema(
        tags=WORKSPACE_TAG, operation_summary="A company's workspaces"
    )
    def get(self, request, org_id: int):
        if org_id not in accounts.org_ids_for_account(request.user.id):
            return Response(
                {"detail": _("You do not belong to that company.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        mine = {row["company_id"]: row for row in accounts.list_memberships(request.user.id)}
        return Response({
            "results": [
                {
                    "id": row["id"],
                    "name": row["name"],
                    "description": row.get("description"),
                    "icon": row.get("icon"),
                    "member_count": row["member_count"],
                    "admin_name": row.get("admin_name"),
                    # Null when the caller has no roster row here — a
                    # workspace on the list they may not yet open.
                    "employee_id": mine[row["id"]]["employee_id"]
                    if row["id"] in mine
                    else None,
                }
                for row in accounts.list_org_workspaces(org_id)
            ],
        })


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
        # Resolved, not raw: the column holds a storage path — see
        # `storage.photo_url`. Shipped bare, the workspace picker and the
        # invite preview drew initials for somebody who has a photo.
        "photo": storage.photo_url(account.get("photo")),
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
            # The organisation this workspace groups under — see the naming
            # note in `create_b2b_tables.py`. `org_id` is never null once a
            # deployment has run `create_b2b_tables`: every workspace gets an
            # org of its own the first time that command runs.
            "org_id": row.get("org_id"),
            "org_name": row.get("org_name") or row["company_name"],
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
    #: Set to invite somebody to one conversation rather than to the
    #: workspace — the "Faqat suhbat" half of the invite sheet. The role and
    #: the module list are ignored when it is: a chat guest has neither.
    thread_id = serializers.IntegerField(required=False, allow_null=True)

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

        thread_id = data.get("thread_id")
        if thread_id is not None and not repo.get_thread_for_member(
            thread_id, request.user.company_id, request.user.id
        ):
            # A conversation this person is not in is not theirs to hand out,
            # and a thread id from another workspace is not a thread at all.
            return Response(
                {"thread_id": [_("This conversation is not yours to share.")]},
                status=status.HTTP_404_NOT_FOUND,
            )

        invite = jrepo.create_invite(
            company_id=request.user.company_id,
            created_by=request.user.id,
            role=data["role"],
            modules=data.get("modules"),
            permissions=data.get("permissions"),
            days=data.get("days"),
            thread_id=thread_id,
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
        "is_chat_only": bool(invite.get("is_chat_only")),
        "thread_id": invite.get("thread_id"),
        "thread_title": invite.get("thread_title"),
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

def _invite_preview(invite: dict, problem: str | None) -> dict:
    """What a link offers, as the app draws it.

    Its own function because two endpoints answer with it — the link's own
    preview, and the resolver that decides whether a typed string is a link at
    all — and the second must not describe an invitation differently from the
    first.
    """
    return {
        "company_name": invite["company_name"],
        "role": invite["role"],
        "role_label": Role.label(invite["role"]),
        "is_chat_only": bool(invite.get("is_chat_only")),
        "thread_id": invite.get("thread_id"),
        "thread_title": invite.get("thread_title"),
        # A chat link opens no modules at all, and resolving the role's would
        # promise access it does not carry.
        "modules": []
        if invite.get("is_chat_only")
        else (invite.get("modules") or _role_modules(invite)),
        "invited_by": invite.get("created_by_name"),
        "expires_at": invite.get("expires_at"),
        "is_usable": problem is None,
        "problem": problem,
    }


class JoinCodeView(AccountAPIView):
    """GET /api/b2b/workspace/account/join-code/?code= — what this string is.

    One field on the app, two things it can hold, and the server decides
    which — not the client. The two are genuinely different offers and telling
    them apart by shape is exactly the kind of rule that goes stale:

    * a **workspace invite link** was minted for one room, with a role and a
      set of modules already chosen. Taking it is immediate.
    * a **company join code** decides nothing. It names a company and lists
      the rooms inside it, and every one of them still has to be asked
      through.

    A string that is neither is one answer — `404` — whichever it failed to
    be. Guessing at five characters must not be able to learn that a code
    exists but its company is closed, or that a token was real but expired.
    """

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Resolve an invite link or company code",
        manual_parameters=[
            openapi.Parameter(
                "code",
                openapi.IN_QUERY,
                description="An invite link, an invite token, or a company code.",
                type=openapi.TYPE_STRING,
            )
        ],
    )
    def get(self, request):
        raw = (request.query_params.get("code") or "").strip()
        if not raw:
            return Response(
                {"detail": _("Enter a link or a code.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # The link is tried first. Its tokens are long and random, so a string
        # that matches one is not a company code that happens to collide.
        token = raw.split("/")[-1] if "/" in raw else raw
        invite = jrepo.get_invite_by_token(token)
        if invite:
            problem = jrepo.invite_problem(invite)
            return Response({
                "kind": "invite",
                "invite": _invite_preview(invite, problem),
            })

        org = accounts.find_org_by_join_code(raw)
        if not org:
            return Response(
                {"detail": _("No link or code like that.")},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response({
            "kind": "company",
            "company": {
                "id": org["id"],
                "name": org.get("name"),
                "join_code": org.get("join_code"),
            },
            "workspaces": [
                {
                    "id": row["id"],
                    "name": row["name"],
                    "slug": row["slug"],
                    "icon": row.get("icon"),
                    "member_count": int(row.get("member_count") or 0),
                    "is_member": bool(row.get("is_member")),
                    "has_pending_request": bool(row.get("has_pending_request")),
                }
                for row in accounts.org_workspaces_for_joining(
                    org["id"], account_id=request.user.id
                )
            ],
        })


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
        return Response(_invite_preview(invite, problem))

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

        is_chat_only = bool(invite.get("is_chat_only"))
        existing = accounts.employee_in_company(request.user.id, invite["company_id"])
        if existing and not existing.get("is_chat_only"):
            if not is_chat_only:
                return Response(
                    {"detail": _("You are already in this workspace.")},
                    status=status.HTTP_409_CONFLICT,
                )
            # A full member following a chat link is not an error: they simply
            # get added to the conversation, keeping the standing they have.

        # Claim the link before creating anything. Two taps a moment apart
        # would otherwise both pass and put the person on the roster twice.
        if not jrepo.mark_invite_accepted(invite["id"], request.user.id):
            return Response(
                {"detail": _("This link cannot be used."), "problem": "used"},
                status=status.HTTP_409_CONFLICT,
            )

        if is_chat_only and existing:
            # Already on the roster, one way or the other. The link only has
            # the conversation left to give.
            employee = existing
        else:
            employee = accounts.create_membership(
                account=request.user._data,
                company_id=invite["company_id"],
                role=invite["role"],
                modules=invite.get("modules") if not is_chat_only else None,
                permissions=(
                    invite.get("permissions") if not is_chat_only else None
                ),
                is_chat_only=is_chat_only,
            )
        if not employee:
            return Response(
                {"detail": _("Could not join the workspace.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if is_chat_only and invite.get("thread_id"):
            # The whole point of the link. Done after the roster row exists:
            # every message and membership references `b2b_employee(id)`.
            repo.add_thread_member(invite["thread_id"], employee["id"])

        arepo.record_audit(
            invite["company_id"],
            actor_employee_id=employee["id"],
            action="invite.accepted",
            target_type="employee",
            target_id=employee["id"],
            payload={
                "invite_id": invite["id"],
                "role": invite["role"],
                "chat_only": is_chat_only,
                "thread_id": invite.get("thread_id"),
            },
        )
        tokens = create_workspace_tokens(employee)
        return Response(
            {
                "access": tokens["access"],
                "refresh": tokens["refresh"],
                "employee_id": employee["id"],
                "company_id": employee["company_id"],
                "company_name": invite["company_name"],
                "is_chat_only": is_chat_only,
                "thread_id": invite.get("thread_id"),
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


def _queue_join_decision(request_id: int) -> None:
    """Tell the asker, off the request.

    Wrapped because a broker that is down must not turn an answered request
    into a 500 — the decision is already stored, and the app shows it on the
    asker's next launch either way. The push is the fast path, not the only
    one.
    """
    try:
        from apps.b2b.workspace.tasks import notify_join_request_decided

        notify_join_request_decided.delay(request_id)
    except Exception:  # noqa: BLE001 - the decision itself is stored
        logger.exception("Could not queue the answer to join request %s", request_id)


def _queue_join_request_created(request_id: int) -> None:
    """Tell whoever may decide it, off the request.

    Same reasoning as [_queue_join_decision]: the request is already on the
    roster's list either way, and a broker that is down must not turn asking
    to join into a 500 for the asker.
    """
    try:
        from apps.b2b.workspace.tasks import notify_join_request_created

        notify_join_request_created.delay(request_id)
    except Exception:  # noqa: BLE001 - the request itself is stored
        logger.exception("Could not queue the notice for join request %s", request_id)


class AccountDeletionPreviewView(AccountAPIView):
    """GET /api/b2b/workspace/account/me/deletion/ — what deleting would cost.

    Separate from the delete itself so the confirmation can be specific.
    "This cannot be undone" is a sentence people press through; naming the
    company that closes and the number of colleagues who lose their workspace
    is not.
    """

    @swagger_auto_schema(
        tags=WORKSPACE_TAG, operation_summary="What deleting this account closes"
    )
    def get(self, request):
        closed = accounts.companies_closed_by_deleting(request.user.id)
        return Response({
            "companies": [
                {
                    "id": org["id"],
                    "name": org.get("name"),
                    "other_members": int(org.get("other_members") or 0),
                }
                for org in closed
            ]
        })


class AccountDeviceTokenView(AccountAPIView):
    """POST /api/b2b/workspace/account/device-token/ — address this phone.

    The account-session twin of `/me/device-token/`. Registered as soon as
    registration finishes, before there is any workspace to belong to, so that
    somebody waiting on a join request can be told when it is answered — the
    roster's token cannot reach them, because they are not on a roster.

    An empty token clears the row, which is what signing out sends.
    """

    @swagger_auto_schema(
        tags=WORKSPACE_TAG, operation_summary="Register this phone (account)"
    )
    def post(self, request):
        token = (request.data.get("fcm_token") or "").strip() or None
        accounts.set_account_fcm_token(request.user.id, token)
        return Response({"ok": True})


class AccountJoinRequestView(AccountAPIView):
    """GET  /api/b2b/workspace/account/join-requests/ — what I have asked for.
    POST /api/b2b/workspace/account/join-requests/ — ask to be let in."""

    @swagger_auto_schema(
        tags=WORKSPACE_TAG, operation_summary="My join requests"
    )
    def get(self, request):
        return Response({
            "results": [
                {
                    "id": row["id"],
                    "company_id": row["company_id"],
                    "company_name": row.get("company_name"),
                    "company_slug": row.get("company_slug"),
                    "org_name": row.get("org_name"),
                    "status": row["status"],
                    # Only on a refusal, and only when one was given. What the
                    # role turned out to be is on the accepted one, because
                    # "you are in" without saying as what is half an answer.
                    "decline_reason": row.get("decline_reason"),
                    "granted_role": row.get("granted_role"),
                    "granted_role_label": (
                        Role.label(row["granted_role"])
                        if row.get("granted_role")
                        else None
                    ),
                    "created_at": row.get("created_at"),
                    "decided_at": row.get("decided_at"),
                }
                for row in jrepo.list_account_join_requests(request.user.id)
            ]
        })

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
        if created:
            _queue_join_request_created(created["id"])
        return Response(
            {
                "id": created["id"] if created else None,
                "company_name": company["name"],
                "status": JoinStatus.PENDING,
            },
            status=status.HTTP_201_CREATED,
        )


class WorkspaceSearchView(AccountAPIView):
    """GET /api/b2b/workspace/account/workspaces/search/?q= — find a workspace
    to ask to join.

    The one screen an account that belongs to nothing may look outward from,
    so it is kept to exactly that: at least two characters, a capped number of
    rows, and nothing on a row that is not already on the card the app draws.
    Workspaces this account is already on are filtered out on the way.
    """

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Search workspaces",
        manual_parameters=[
            openapi.Parameter(
                "q",
                openapi.IN_QUERY,
                description="Workspace name or handle, at least 2 characters.",
                type=openapi.TYPE_STRING,
            )
        ],
    )
    def get(self, request):
        rows = jrepo.search_companies(
            request.query_params.get("q", ""), account_id=request.user.id
        )
        return Response({
            "results": [
                {
                    "id": row["id"],
                    "name": row["name"],
                    "slug": row["slug"],
                    "icon": row.get("icon"),
                    # The company above this workspace, which is what tells
                    # two rooms of the same name apart.
                    "org_name": row.get("org_name"),
                    "member_count": int(row.get("member_count") or 0),
                }
                for row in rows
            ]
        })


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
    """GET /api/b2b/workspace/join-requests/ — who is asking to be let in.

    Whoever runs the workspace: the owner, an administrator, or a manager.
    Deliberately wider than `EMPLOYEE_INVITE`, which is what separates an
    administrator from a manager and gates *asking another workspace to lend
    somebody* — a commitment about who is allowed in that is not this. Somebody
    knocking at the door is the day-to-day of running a workspace, and a
    request only the owner can answer sits unanswered for as long as the owner
    is away.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser, IsWorkspaceManager]

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
                    "photo": storage.photo_url(row.get("photo")),
                    "message": row.get("message"),
                    "wanted_modules": row.get("wanted_modules"),
                    "status": row["status"],
                    "created_at": row.get("created_at"),
                }
                for row in jrepo.list_join_requests(request.user.company_id)
            ]
        })


class WorkspaceJoinRequestDecideView(WorkspaceAPIView):
    """POST /api/b2b/workspace/join-requests/<id>/<accept|decline>/

    Same audience as the list — see [WorkspaceJoinRequestListView]. What
    standing the person is let in with is chosen per request; changing a role
    afterwards is a different act and still needs `EMPLOYEE_CHANGE_ROLE`.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser, IsWorkspaceManager]

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
            _queue_join_decision(request_id)
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
        _queue_join_decision(request_id)
        return Response({"status": JoinStatus.ACCEPTED})
