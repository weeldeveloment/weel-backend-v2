"""API for the WEEL B2B mobile app (`weel-b2b-mobile`).

The app is the *employee-facing* side of a B2B company: the owner and their
managers hand out tasks, put things on the shared calendar and open chats;
plain employees work what they were given. Which of those a caller may do is
decided in one place — ``roles.capabilities_for`` — and reported to the client
via ``GET /me/`` so the UI and the API can never disagree.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from django.conf import settings
from django.core.files.storage import default_storage
from django.db import IntegrityError
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.translation import gettext_lazy as _
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

from apps.b2b.models import LeadKind, LeadSource, LeadStage, LeadStatus
from apps.b2b.repository import get_company, get_org
from apps.b2b.mail import repository as mail_repo
from apps.b2b.workspace import push_text
from apps.b2b.workspace.access import Permission, Role
from apps.b2b.workspace.secondment import Module
from apps.b2b.workspace import repository as repo
from apps.b2b.workspace import storage
from apps.b2b.workspace import presence
from apps.b2b.workspace import realtime
from apps.b2b.workspace.consumers import add_to_thread, remove_from_thread
from apps.b2b.workspace.realtime import broadcast_deletion, broadcast_message
from apps.b2b.workspace.authentication import (
    DashboardWorkspaceAuthentication,
    WorkspaceJWTAuthentication,
)
from apps.b2b.workspace.permissions import HasCapability, IsWorkspaceManager, IsWorkspaceUser
from apps.b2b.workspace.roles import capabilities_for, is_manager
from apps.b2b.workspace.serializers import (
    WorkspaceFilePatchSerializer,
    WorkspaceReportSerializer,
    WorkspaceFolderListSerializer,
    WorkspaceFolderSerializer,
    WorkspaceFolderWriteSerializer,
    AttendanceCheckInSerializer,
    AttendanceCheckOutSerializer,
    AttendanceDaySerializer,
    AttendanceLocationSerializer,
    AttendanceLocationUpdateSerializer,
    AttendanceMarkSerializer,
    AttendanceSelfAbsenceSerializer,
    CalendarEventSerializer,
    ChatGroupSerializer,
    ChatMessageSerializer,
    ChatThreadSerializer,
    CrmCustomerDetailSerializer,
    CrmCustomerListSerializer,
    CustomerListSerializer,
    EmployeeMonthlyStatSerializer,
    EmployeeOfMonthListSerializer,
    EmployeeOfMonthSelectSerializer,
    EmployeeOfMonthSerializer,
    EventPatchSerializer,
    EventWriteSerializer,
    LeadActivitySerializer,
    LeadAssignWriteSerializer,
    LeadCommentWriteSerializer,
    LeadDetailSerializer,
    LeadDueDateWriteSerializer,
    LeadQualityWriteSerializer,
    LeadItemSerializer,
    LeadItemWriteSerializer,
    LeadListSerializer,
    LeadSerializer,
    LeadStageWriteSerializer,
    LeadWriteSerializer,
    EmployeeStatsSerializer,
    MeSerializer,
    OwnProfileSerializer,
    MessageEditSerializer,
    MessageReactionSerializer,
    MessageWriteSerializer,
    NotePatchSerializer,
    NoteSerializer,
    NoteWriteSerializer,
    StorageUsageSerializer,
    SupportMessageCreateSerializer,
    SupportMessageSerializer,
    TaskCommentWriteSerializer,
    TaskListSerializer,
    TaskPatchSerializer,
    TaskSerializer,
    TaskStatusSerializer,
    TaskWriteSerializer,
    UsernameSerializer,
    TeamMemberSerializer,
    ThreadCreateSerializer,
    ThreadMemberRoleSerializer,
    ThreadMembersSerializer,
    ThreadUpdateSerializer,
    ThreadFlagsSerializer,
    WorkspaceFileListSerializer,
    WorkspaceFileSerializer,
    WorkspaceLoginSerializer,
    WorkspaceLoginVerifySerializer,
    WorkspaceRefreshSerializer,
)
from apps.b2b.workspace.geo import distance_meters
from apps.b2b.workspace import accounts
from apps.b2b.workspace.tokens import (
    create_account_tokens,
    create_workspace_tokens,
    rotate_account_tokens,
    rotate_workspace_tokens,
)
from users.models.logs import SmsPurpose
from users.services import EskizService, OTPRedisService
from users.tasks import send_otp_sms_eskiz
from users.tokens import CustomRefreshToken

logger = logging.getLogger(__name__)

WORKSPACE_TAG = ["B2B / Workspace (mobile)"]


class WorkspaceAPIView(APIView):
    """Base for every signed-in workspace endpoint.

    Two logins lead here — the mobile app's employee token and the web
    dashboard's ``b2b`` token — and both arrive as a ``WorkspaceUser``, so
    nothing below this line has to care which screen the request came from.
    The dashboard bridge is listed only here rather than in the project-wide
    authentication chain; see ``DashboardWorkspaceAuthentication``.
    """

    authentication_classes = [
        WorkspaceJWTAuthentication,
        DashboardWorkspaceAuthentication,
    ]

    #: Set on a view that belongs to one section of the workspace — the sales
    #: board, the task list, the calendar, chat. Left None the view is open to
    #: anybody signed in, which is what almost all of them are.
    required_module = None

    def get_permissions(self):
        """Every view's own permissions, plus the module gate.

        Appended here rather than added to forty `permission_classes` lists.
        Each of those lists is written per view and would have to remember
        this one — and the failure mode of forgetting is not a broken screen
        but a guest quietly reading a board that was never shared with them,
        which is exactly the kind of hole nobody notices.
        """
        from apps.b2b.workspace.permissions import HasModule, HasPermission

        return [*super().get_permissions(), HasModule(), HasPermission()]

    #: Which part of the workspace an endpoint belongs to, read off its URL
    #: name. This is what makes the app live everywhere rather than only in
    #: chat: any successful write announces the section it changed, and the
    #: screens built on that section reload.
    #:
    #: Longest prefix wins, so `ws-task-status` is a task and not something
    #: else that happens to start the same way. Chat is absent on purpose —
    #: its endpoints publish precise events (the message itself, a deletion,
    #: a read receipt) and a second, vaguer "chat changed" on top of them
    #: would have every open thread refetching what it had just been handed.
    LIVE_SECTIONS: tuple[tuple[str, str], ...] = (
        ("ws-task", realtime.EVENT_TASK),
        ("ws-subtask", realtime.EVENT_TASK),
        ("ws-event", realtime.EVENT_CALENDAR),
        ("ws-lead", realtime.EVENT_LEAD),
        ("ws-crm", realtime.EVENT_LEAD),
        ("ws-customers", realtime.EVENT_LEAD),
        ("ws-attendance", realtime.EVENT_ATTENDANCE),
        ("ws-join-request", realtime.EVENT_JOIN_REQUEST),
        ("ws-invite", realtime.EVENT_JOIN_REQUEST),
        ("ws-request", realtime.EVENT_REQUEST),
        ("ws-access", realtime.EVENT_ACCESS),
        ("ws-employee-access", realtime.EVENT_ACCESS),
        ("ws-file", realtime.EVENT_FILE),
        ("ws-folder", realtime.EVENT_FILE),
        ("ws-employee-of-month", realtime.EVENT_TEAM),
        ("ws-trash", realtime.EVENT_TEAM),
        ("ws-restore", realtime.EVENT_TEAM),
        ("ws-purge", realtime.EVENT_TEAM),
    )

    def _live_section(self) -> str | None:
        match = getattr(self.request, "resolver_match", None)
        name = getattr(match, "url_name", None) or ""
        best: tuple[str, str] | None = None
        for prefix, section in self.LIVE_SECTIONS:
            if name.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
                best = (prefix, section)
        return best[1] if best else None

    def finalize_response(self, request, response, *args, **kwargs):
        """Announces a successful write to everyone in the workspace.

        Done once here rather than at forty return statements. The failure
        mode of the alternative is not a broken screen but a section that is
        live on some actions and stale on others, which reads as the feature
        working badly rather than as one call site having been missed.
        """
        response = super().finalize_response(request, response, *args, **kwargs)
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return response
        if not (200 <= response.status_code < 300):
            return response

        section = self._live_section()
        company_id = getattr(getattr(request, "user", None), "company_id", None)
        if not section or not company_id:
            return response

        # Deliberately payload-free beyond the section and the actor. The
        # server decides ids, ordering and what a viewer is allowed to see, so
        # the honest answer to "what does the board look like now" is the one
        # the client's own reload comes back with — and a ping cannot leak a
        # row to somebody the list endpoint would have filtered out.
        realtime.publish_company(
            company_id,
            section,
            action="changed",
            by=getattr(request.user, "id", None),
        )
        return response


# ─── Auth ─────────────────────────────────────────────────────────────────────

def _resolve_employee(phone: str) -> dict | None:
    """Find the employee behind a phone number.

    Employees are matched on the roster; owners created by ``create_b2b_owner``
    exist only as a login row, so they are promoted into a roster entry on
    first use — see ``ensure_workspace_employee``.
    """
    employee = repo.find_employee_by_phone(phone)
    if employee:
        return employee

    b2b_user = repo.find_b2b_user_by_phone(phone)
    if b2b_user:
        return repo.ensure_workspace_employee(b2b_user)
    return None


class WorkspaceLoginView(APIView):
    """POST /api/b2b/workspace/auth/login/ — send a login code."""

    permission_classes = [AllowAny]
    throttle_scope = "otp_login_send"

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Send a login OTP to an employee's phone",
        request_body=WorkspaceLoginSerializer,
        responses={
            200: openapi.Response(description="OTP sent"),
            404: openapi.Response(description="No employee with this phone number"),
            429: openapi.Response(description="Wait before requesting another code"),
        },
    )
    def post(self, request):
        serializer = WorkspaceLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone = serializer.validated_data["phone"]

        is_test_phone = OTPRedisService.is_test_phone_for_purpose(phone, SmsPurpose.B2B_LOGIN)

        # A number nobody has seen before is a registration, not an error. The
        # TZ's flow starts here — phone, then OTP, then name and username —
        # and the account it produces is a member of nothing: getting into a
        # workspace still takes an invitation or an accepted request.
        #
        # Rejecting unknown numbers used to be the rule, and it was the right
        # one while there was no way to register at all.

        if is_test_phone:
            return Response({
                "detail": _("OTP sent successfully"),
                "phone": phone,
                "expires_in": OTPRedisService.OTP_EXPIRE,
            })

        try:
            if not OTPRedisService.can_resend(phone, SmsPurpose.B2B_LOGIN):
                return Response(
                    {"detail": _("Please wait before requesting a new OTP.")},
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )
            otp_code = OTPRedisService.create_otp(phone, SmsPurpose.B2B_LOGIN)
            OTPRedisService.mark_resend(phone, SmsPurpose.B2B_LOGIN)
        except Exception:
            logger.exception("Workspace login OTP cache is unavailable.")
            return Response(
                {"detail": _("OTP service is temporarily unavailable.")},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        if not EskizService.is_configured():
            logger.warning("Eskiz is not configured; skipping SMS for %s.", phone)
            if settings.DEBUG:
                # Nothing can deliver the code in dev, so hand it back rather
                # than leaving the developer locked out.
                return Response({
                    "detail": _("OTP sent successfully (dev mode)"),
                    "phone": phone,
                    "otp": otp_code,
                    "expires_in": OTPRedisService.OTP_EXPIRE,
                })
        else:
            try:
                send_otp_sms_eskiz.delay(phone, SmsPurpose.B2B_LOGIN, otp_code)
            except Exception:
                logger.warning("Could not queue the OTP SMS task for %s.", phone)

        return Response({
            "detail": _("OTP sent successfully"),
            "phone": phone,
            "expires_in": OTPRedisService.OTP_EXPIRE,
        })


class WorkspaceLoginVerifyView(APIView):
    """POST /api/b2b/workspace/auth/login/verify/ — exchange the code for tokens."""

    permission_classes = [AllowAny]
    throttle_scope = "otp_login_verify"

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Verify the OTP and receive workspace tokens",
        request_body=WorkspaceLoginVerifySerializer,
        responses={200: openapi.Response(description="Tokens and profile"),
                   400: openapi.Response(description="Wrong or expired OTP")},
    )
    def post(self, request):
        serializer = WorkspaceLoginVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        phone = serializer.validated_data["phone"]
        otp = serializer.validated_data["otp"]

        # The code is checked before anything is looked up or created. The
        # other way round, an unknown number would tell a caller whether it is
        # registered before proving they hold it — and would let anybody mint
        # accounts by guessing numbers.
        ok, message = OTPRedisService.verify_otp(phone, otp, SmsPurpose.B2B_LOGIN)
        if not ok:
            return Response({"otp": [message]}, status=status.HTTP_400_BAD_REQUEST)
        OTPRedisService.consume_otp(phone, SmsPurpose.B2B_LOGIN)

        account = accounts.ensure_account(phone)
        account_tokens = create_account_tokens(account) if account else None

        employee = _resolve_employee(phone)
        if not employee:
            # Registered, and a member of nothing yet. The app takes them on
            # to name and username, and from there to an invitation or a
            # request to join.
            return Response({
                "account": account_tokens,
                "user": None,
                "has_profile": bool(
                    account and account.get("first_name") and account.get("username")
                ),
            })

        tokens = create_workspace_tokens(employee)
        return Response({
            "access": tokens["access"],
            "refresh": tokens["refresh"],
            # Alongside the workspace session rather than instead of it: the
            # app needs this one to switch workspaces or accept an invitation,
            # and asking somebody to sign in twice for that would be absurd.
            "account": account_tokens,
            "user": _me_payload(repo.get_workspace_employee(employee["id"]) or employee),
        })


class WorkspaceTokenRefreshView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "token_refresh"

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Exchange a workspace refresh token for a new pair",
        request_body=WorkspaceRefreshSerializer,
        responses={200: openapi.Response(description="New token pair"),
                   401: openapi.Response(description="Invalid or expired refresh token")},
    )
    def post(self, request):
        serializer = WorkspaceRefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            tokens = rotate_workspace_tokens(serializer.validated_data["refresh"])
        except (TokenError, InvalidToken):
            return Response(
                {"detail": _("Invalid or expired refresh token")},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        return Response(tokens)


class AccountTokenRefreshView(APIView):
    """POST /api/b2b/workspace/account/token/refresh/

    The account session's half of the refresh above, and its own endpoint
    rather than a second branch inside it: the two token types are
    deliberately not interchangeable, and one view that answered for both
    would be the place that eventually hands a workspace token to a caller
    holding an account one.

    Without this the account session could not be renewed at all. It simply
    died one access lifetime after sign-in, which is what left somebody who
    had registered but not yet been let into a workspace stuck on "could not
    load your workspaces" with a Retry button that could never succeed.
    """

    permission_classes = [AllowAny]
    throttle_scope = "token_refresh"

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Exchange an account refresh token for a new pair",
        request_body=WorkspaceRefreshSerializer,
        responses={200: openapi.Response(description="New token pair"),
                   401: openapi.Response(description="Invalid or expired refresh token")},
    )
    def post(self, request):
        serializer = WorkspaceRefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            tokens = rotate_account_tokens(serializer.validated_data["refresh"])
        except (TokenError, InvalidToken):
            return Response(
                {"detail": _("Invalid or expired refresh token")},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        return Response(tokens)


class WorkspaceLogoutView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Revoke the refresh token",
        request_body=WorkspaceRefreshSerializer,
        responses={200: openapi.Response(description="Logged out")},
    )
    def post(self, request):
        serializer = WorkspaceRefreshSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            CustomRefreshToken(serializer.validated_data["refresh"]).blacklist()
        except TokenError:
            # An already-expired token is the normal case for "log me out";
            # reporting it as an error would only make the app retry.
            pass
        return Response({"detail": _("Successfully logged out")})


# ─── Me & team ────────────────────────────────────────────────────────────────

def _access_payload(employee: dict) -> dict:
    """What this person can open and do, as `access.resolve` answers it."""
    from apps.b2b.workspace.access import Role
    from apps.b2b.workspace.access_repository import access_for_employee

    modules, permissions = access_for_employee(employee)
    return {
        "role": Role.clean(employee.get("role")),
        "role_label": Role.label(employee.get("role")),
        "modules": modules,
        "permissions": permissions,
    }


def _me_payload(employee: dict, membership=None) -> dict:
    """Who this is, where they are, and what they may do here.

    `membership` is set when this identity is a guest — somebody lent to this
    workspace by another. It has to reach `capabilities_for`, or the app would
    build its tabs from a permission map wider than the one the endpoints
    enforce, and every screen it drew past the grant would 403 on open.
    """
    company = get_company(employee["company_id"]) or {}
    org = get_org(company.get("org_id")) or {}
    modules = list(membership.modules) if membership else None
    # The handle lives on the account now, not on the roster row — one person,
    # one username, wherever they work (see the account note in
    # `create_b2b_tables.py`). Registration writes it there and
    # `create_membership` no longer copies it down, so a freshly signed-up
    # member whose employee row was created without one still has a handle to
    # show. The employee column is kept only as a fallback for rows written
    # before the account existed.
    account = (
        accounts.get_account(employee["account_id"])
        if employee.get("account_id")
        else None
    )
    username = (account or {}).get("username") or employee.get("username")
    return {
        "id": employee["id"],
        "company_id": employee["company_id"],
        "company_name": company.get("name"),
        # The organisation this workspace belongs to — see the naming note in
        # `create_b2b_tables.py`. The profile screen's company switcher groups
        # by this, not by `company_id`.
        "org_id": company.get("org_id"),
        "org_name": org.get("name") or company.get("name"),
        # What the owner hands out so somebody can see this company's rooms
        # and ask through one. Carried on every session because the screen
        # that shows it is the invite sheet, which any admin may open.
        "org_join_code": org.get("join_code"),
        "full_name": employee.get("full_name"),
        "position": employee.get("position"),
        "role": employee.get("role") or "employee",
        "username": username,
        "phone": employee.get("phone"),
        "email": employee.get("email"),
        "photo": _photo_url(employee.get("photo")),
        "department_name": employee.get("department_name"),
        "completed_this_month": repo.completed_tasks_this_month(
            employee["id"], *_current_year_month()
        ),
        "permissions": capabilities_for(employee.get("role"), modules),
        # The TZ's model: which parts of the workspace are open, and what may
        # be done inside them. Sent alongside `permissions` rather than
        # instead of it while the older map still gates the existing
        # endpoints — see the note on `authentication.WorkspaceUser.access`.
        "access": _access_payload(employee),
        # Being here on loan is worth saying out loud: the app puts the host
        # workspace's name and the date it ends in the header, so nobody is
        # left wondering why their own leads are missing.
        "is_guest": bool(membership),
        "modules": modules,
        "guest_until": membership.ends_at if membership else None,
    }


class WorkspaceMeView(WorkspaceAPIView):
    """GET /api/b2b/workspace/me/ — profile plus the permission map the app
    builds its UI from."""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Signed-in employee and permissions",
                         responses={200: MeSerializer()})
    def get(self, request):
        employee = repo.get_workspace_employee(request.user.id)
        if not employee:
            return Response({"detail": _("Employee not found.")}, status=status.HTTP_404_NOT_FOUND)
        return Response(_me_payload(employee, request.user.membership))


class WorkspaceProfileView(WorkspaceAPIView):
    """PUT /api/b2b/workspace/me/profile/ — correct your own entry.

    Yours alone, and only the parts that are actually yours: the name people
    see and the address they write to. The position, the department and the
    role are the workspace's account of what you do here and are set by whoever
    runs it, so the app draws them greyed out with that said in words rather
    than leaving them off the screen — somebody looking for the field that
    fixes their job title should find the answer, not an absence.

    The phone is not editable here either. It is what the login is checked
    against, and moving it is a different act with an OTP behind it.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Edit your own profile",
        request_body=OwnProfileSerializer,
        responses={200: MeSerializer()},
    )
    def put(self, request):
        serializer = OwnProfileSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        first_name = data["first_name"]
        last_name = data.get("last_name") or None
        full_name = accounts.full_name_from(first_name, last_name)

        updated = repo.set_own_profile(
            request.user.id,
            full_name=full_name,
            email=data.get("email") or None,
        )
        if not updated:
            return Response(
                {"detail": _("Profile not found.")}, status=status.HTTP_404_NOT_FOUND
            )

        # The account holds the name in two halves, and this screen is the only
        # place they can be told apart. Writing the split back keeps the next
        # workspace this person joins from being seeded with the older one.
        if updated.get("account_id"):
            accounts.update_account(
                updated["account_id"], first_name=first_name, last_name=last_name
            )

        return Response(_me_payload(updated, request.user.membership))


class WorkspaceProfilePhotoView(WorkspaceAPIView):
    """PUT    /api/b2b/workspace/me/photo/ — set your own picture.
    DELETE /api/b2b/workspace/me/photo/ — go back to initials.

    Yours alone. There is no path here for changing somebody else's: a photo
    is the one thing on a roster entry that is unambiguously the person's own,
    and a workspace that could set it for them is a workspace that can put any
    face against their name.

    The bytes go through the same door everything else stored here goes
    through, so they are checked against the company's quota rather than being
    a way around it.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Set your own photo",
        consumes=["multipart/form-data"],
        manual_parameters=[
            openapi.Parameter("photo", openapi.IN_FORM, type=openapi.TYPE_FILE, required=True),
        ],
        responses={200: MeSerializer(), 413: "Storage limit reached"},
    )
    def put(self, request):
        upload = request.FILES.get("photo")
        if upload is None:
            return Response(
                {"photo": [_("Pick a picture.")]}, status=status.HTTP_400_BAD_REQUEST
            )

        content_type = (getattr(upload, "content_type", "") or "").lower()
        if not content_type.startswith("image/"):
            return Response(
                {"photo": [_("That is not a picture.")]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        employee = repo.get_workspace_employee(request.user.id)
        if not employee:
            return Response(
                {"detail": _("Profile not found.")}, status=status.HTTP_404_NOT_FOUND
            )
        previous = employee.get("photo")

        file, refusal = store_upload(request=request, upload=upload, kind="avatar")
        if refusal:
            return refusal

        updated = repo.set_own_photo(request.user.id, file["path"])
        if not updated:
            return Response(
                {"detail": _("Profile not found.")}, status=status.HTTP_404_NOT_FOUND
            )

        # The old picture goes only once the new one is recorded. Deleting it
        # first would leave somebody with no photo at all if the write failed.
        self._forget(previous)
        return Response(_me_payload(updated, request.user.membership))

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Remove your photo",
        responses={200: MeSerializer()},
    )
    def delete(self, request):
        employee = repo.get_workspace_employee(request.user.id)
        if not employee:
            return Response(
                {"detail": _("Profile not found.")}, status=status.HTTP_404_NOT_FOUND
            )

        previous = employee.get("photo")
        updated = repo.set_own_photo(request.user.id, None)
        self._forget(previous)
        return Response(_me_payload(updated or employee, request.user.membership))

    @staticmethod
    def _forget(path: str | None) -> None:
        """Drops the stored object, if there was one.

        Never fatal: a leftover file wastes disk, while a failure here would
        leave somebody unable to change their picture at all.
        """
        if not path or path.startswith("http"):
            return
        try:
            default_storage.delete(path)
        except Exception:  # noqa: BLE001
            logger.exception("Could not delete stored object %s", path)


class WorkspaceUsernameView(WorkspaceAPIView):
    """PUT /api/b2b/workspace/me/username/ — pick the handle people find you by.

    Yours alone. A roster is imported from passports and phone numbers, and a
    handle is the one part of somebody's entry they choose for themselves —
    which is also why nothing here lets one person set another's.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Set your own username",
        request_body=UsernameSerializer,
        responses={200: MeSerializer(), 409: openapi.Response(description="Taken")},
    )
    def put(self, request):
        serializer = UsernameSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        username = serializer.validated_data["username"]

        employee = repo.get_workspace_employee(request.user.id)
        account_id = (employee or {}).get("account_id")
        if not account_id:
            return Response(
                {"detail": _("Profile not found.")},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Global, per the TZ: one handle per person across every workspace they
        # work in, so the check and the write are against the account rather
        # than this one roster row.
        if username and accounts.username_taken(
            username, exclude_account_id=account_id
        ):
            return Response(
                {"username": [_("This handle is already taken.")]},
                status=status.HTTP_409_CONFLICT,
            )

        try:
            accounts.update_account(account_id, username=username or None)
        except IntegrityError:
            # Lost the race against somebody claiming the same handle between
            # the check above and the write; the unique index is the authority.
            return Response(
                {"username": [_("This handle is already taken.")]},
                status=status.HTTP_409_CONFLICT,
            )

        # Each roster row keeps its own copy so listing and search need no
        # join — see `sync_username_across_memberships`.
        repo.sync_username_across_memberships(account_id, username or None)

        return Response(
            _me_payload(
                repo.get_workspace_employee(request.user.id) or employee,
                request.user.membership,
            )
        )


def _with_presence(members: list[dict]) -> list[dict]:
    """Stamps `is_online` and `last_seen_at` onto roster rows.

    Two cache reads for the whole list rather than one per row, and it is done
    here rather than in the repository because presence is not in the database
    — see `presence.py`. Rows are copied: `list_team` hands back what a query
    returned, and a caller that reused it would be reading a green dot from a
    minute ago.
    """
    ids = [m["id"] for m in members]
    online = presence.online_ids(ids)
    seen = presence.last_seen(ids)
    return [
        {
            **m,
            "photo": _photo_url(m.get("photo")),
            "is_online": m["id"] in online,
            "last_seen_at": seen.get(m["id"]),
        }
        for m in members
    ]


class WorkspacePresenceView(WorkspaceAPIView):
    """GET /api/b2b/workspace/presence/ — who is online right now.

    The socket says so on connect and pushes every change after that, so this
    is for the case the socket cannot cover: an app that has just come back to
    the foreground and wants the current picture before its connection is up.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Who is online")
    def get(self, request):
        ids = repo.company_employee_ids(request.user.company_id)
        online = presence.online_ids(ids)
        seen = presence.last_seen(ids)
        return Response({
            "online": sorted(online),
            "last_seen": {str(k): v for k, v in seen.items()},
            "heartbeat_seconds": presence.HEARTBEAT_SECONDS,
        })


class WorkspaceTeamView(WorkspaceAPIView):
    """GET /api/b2b/workspace/team/ — the company roster.

    Everyone can read it: names are needed to render assignees, chat rows and
    event participants. Editing the roster stays in the web dashboard.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Company roster",
        manual_parameters=[openapi.Parameter("search", openapi.IN_QUERY, type=openapi.TYPE_STRING)],
        responses={200: TeamMemberSerializer(many=True)},
    )
    def get(self, request):
        members = repo.list_team(
            request.user.company_id,
            search=(request.query_params.get("search") or "").strip() or None,
        )
        return Response(TeamMemberSerializer(_with_presence(members), many=True).data)


class WorkspaceEmployeeStatsView(WorkspaceAPIView):
    """GET /api/b2b/workspace/employees/<id>/stats/ — what one colleague is
    carrying.

    The two numbers on the card the chat opens when you tap somebody's name.
    Its own call rather than fields on `/team/`: the roster is fetched to label
    rows all over the app — assignees, chat titles, event participants — and
    four counts per person would put a join over every task in the company
    behind every one of those screens, to draw numbers only this one page
    shows.

    Readable by anyone in the workspace, like the roster itself. It says how
    much work somebody has, never what the work is.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="One employee's task counts",
        responses={
            200: EmployeeStatsSerializer(),
            404: openapi.Response(description="Not on this roster"),
        },
    )
    def get(self, request, employee_id: int):
        employee = repo.get_workspace_employee(employee_id)
        # Checked against the caller's company and not only for existence: an
        # id from another workspace must read as "no such person", not as a
        # colleague with no work on.
        if not employee or employee.get("company_id") != request.user.company_id:
            return Response(
                {"detail": _("Employee not found.")}, status=status.HTTP_404_NOT_FOUND
            )

        counts = repo.employee_task_counters(request.user.company_id, employee_id)
        return Response({
            "employee_id": employee_id,
            "tasks_done": counts.get("done_count", 0),
            "tasks_in_progress": counts.get("in_progress_count", 0),
            "tasks_todo": counts.get("todo_count", 0),
            "tasks_overdue": counts.get("overdue_count", 0),
        })


# ─── Tasks ────────────────────────────────────────────────────────────────────

def _task_voice_payload(voice: dict | None) -> dict | None:
    """The task's voice note as the app reads it.

    Shaped rather than passed through: the row carries the storage path, which
    is an internal detail and not a URL anything can play. Same field names as
    a chat attachment, so one bubble renders either.
    """
    if not voice:
        return None
    return {
        "id": voice["id"],
        "name": voice["name"],
        "size": voice["size"],
        "content_type": voice.get("content_type"),
        "duration_ms": voice.get("duration_ms"),
        "url": default_storage.url(voice["path"]),
    }


def _task_file_payload(file: dict) -> dict:
    """One document attached to a task, as the app reads it.

    The same shape a voice note gets, minus the duration: the row's storage
    path never leaves the server, and `url` is the only way anything can
    fetch the bytes.
    """
    return {
        "id": file["id"],
        "name": file["name"],
        "size": file["size"],
        "content_type": file.get("content_type"),
        "url": default_storage.url(file["path"]),
    }


def _task_payload(task: dict, user) -> dict:
    """Adds the per-task permission flags the app uses to decide which buttons
    to render — the same rules the write endpoints enforce."""
    caps = user.capabilities
    is_assignee = user.id in (task.get("assignee_ids") or [])
    is_author = task.get("author_id") == user.id

    return {
        **task,
        "voice": _task_voice_payload(task.get("voice")),
        "files": [_task_file_payload(f) for f in (task.get("files") or [])],
        "can_edit": bool(caps["can_edit_task"]),
        "can_delete": bool(caps["can_delete_task"]),
        # An employee moves only their own work along the board.
        "can_change_status": bool(caps["can_edit_task"] or is_assignee or is_author),
        # Handing the task to somebody else. Wider than can_edit: the person
        # who raised it may reassign it — the colleague they gave it to is out
        # sick and it has to move — without the manager-wide edit right.
        "can_reassign": bool(caps["can_edit_task"] or is_author),
    }


class WorkspaceTaskListCreateView(WorkspaceAPIView):
    """GET  /api/b2b/workspace/tasks/ — the company's whole board, whatever
    the caller's role; the app's "Menikilar" toggle narrows it client-side.
    POST /api/b2b/workspace/tasks/ — managers only."""

    required_module = Module.TASKS
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="List tasks (every role sees the whole company board)",
        manual_parameters=[
            openapi.Parameter("status", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                              enum=list(repo.TASK_STATUSES)),
            openapi.Parameter("search", openapi.IN_QUERY, type=openapi.TYPE_STRING),
        ],
        responses={200: TaskListSerializer()},
    )
    def get(self, request):
        user = request.user
        scope = user.task_scope
        tasks = repo.list_tasks(
            user.company_id,
            visible_to=scope,
            status=request.query_params.get("status") or None,
            search=(request.query_params.get("search") or "").strip() or None,
        )
        return Response({
            "results": [_task_payload(task, user) for task in tasks],
            "counters": repo.task_counters(user.company_id, scope),
        })

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Create a task (owner/manager only)",
        request_body=TaskWriteSerializer,
        responses={201: TaskSerializer(), 403: openapi.Response(description="Employees cannot create tasks")},
    )
    def post(self, request):
        if not request.user.capabilities["can_create_task"]:
            return Response(
                {"detail": _("Your role does not allow creating tasks.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = TaskWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        assignees = _validated_employee_ids(request.user.company_id, data.get("assignee_ids", []))
        if assignees is None:
            return Response(
                {"assignee_ids": [_("Some of these employees are not in your company.")]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        task = repo.create_task(
            company_id=request.user.company_id,
            author_id=request.user.id,
            title=data["title"],
            description=data.get("description") or "",
            status=data.get("status") or "todo",
            priority=data.get("priority") or "medium",
            project=data.get("project") or None,
            due_date=data.get("due_date"),
            assignee_ids=assignees,
            subtasks=data.get("subtasks") or [],
        )
        _queue_task_assigned(task, request.user)
        return Response(_task_payload(task, request.user), status=status.HTTP_201_CREATED)


class WorkspaceTaskActivityFeedView(WorkspaceAPIView):
    """GET /api/b2b/workspace/tasks/activity/

    The company-wide feed the tasks page shows: every create/edit/status/
    assign/delete across every task, newest first — including tasks since
    deleted, since the log outlives the row it was written about.

    Everyone sees everyone's actions, the same boundary ``list_tasks`` draws
    for the task list itself (see ``WorkspaceUser.task_scope``).
    """

    required_module = Module.TASKS
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Company-wide task activity feed",
                         responses={200: openapi.Response(description="Activity feed")})
    def get(self, request):
        user = request.user
        activity = repo.list_company_task_activity(
            user.company_id,
            actor_id=user.id if user.task_scope is not None else None,
        )
        return Response({"results": activity})


def _queue_task_assigned(task: dict | None, user, employee_ids=None) -> None:
    """Tell the assignees, off the request.

    `employee_ids` limits it to the people an edit just added; left out, it is
    everyone the task is on.

    Swallows everything. A push is worth a manager's response being fast, not
    worth it failing: the task is already stored, and a broker that is briefly
    down must not turn a successful create into a 500.
    """
    if not task:
        return
    try:
        from apps.b2b.workspace.tasks import notify_task_assigned

        notify_task_assigned.delay(
            task["id"],
            user.id,
            user.company_id,
            sorted(employee_ids) if employee_ids is not None else None,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Could not queue the assignment push for task %s", task["id"])


def _queue_event_created(event: dict | None, user, employee_ids=None) -> None:
    """The same for a calendar entry somebody was invited to."""
    if not event:
        return
    try:
        from apps.b2b.workspace.tasks import notify_event_created

        notify_event_created.delay(
            event["id"],
            user.id,
            user.company_id,
            sorted(employee_ids) if employee_ids is not None else None,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Could not queue the invite push for event %s", event["id"])


def _validated_employee_ids(company_id: int, ids) -> list[int] | None:
    """Returns the ids if they all belong to the company, otherwise ``None``.

    Without this an id from another tenant would silently be written into
    ``b2b_task_assignee`` and leak the task into that company's list.
    """
    ids = [int(i) for i in dict.fromkeys(ids or [])]
    if not ids:
        return []
    valid = repo.employee_ids_in_company(company_id, ids)
    return ids if len(valid) == len(ids) else None


class WorkspaceTaskDetailView(WorkspaceAPIView):
    """GET / PATCH / DELETE a single task."""

    required_module = Module.TASKS
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    def _load(self, request, task_id: int):
        task = repo.get_task(task_id, request.user.company_id)
        if not task:
            return None
        if request.user.task_scope is not None:
            visible = (
                task["author_id"] == request.user.id
                or request.user.id in (task.get("assignee_ids") or [])
            )
            if not visible:
                return None
        return task

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Task detail",
                         responses={200: TaskSerializer(), 404: openapi.Response(description="Not found")})
    def get(self, request, task_id: int):
        task = self._load(request, task_id)
        if not task:
            return Response({"detail": _("Task not found.")}, status=status.HTTP_404_NOT_FOUND)
        return Response({
            **_task_payload(task, request.user),
            "activity": repo.list_task_activity(task_id),
        })

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Edit a task (owner/manager only)",
                         request_body=TaskPatchSerializer, responses={200: TaskSerializer()})
    def patch(self, request, task_id: int):
        task = self._load(request, task_id)
        if not task:
            return Response({"detail": _("Task not found.")}, status=status.HTTP_404_NOT_FOUND)

        # TZ §8: once a task is done, only the owner or an administrator may
        # still touch it — a manager who could edit it a minute ago cannot
        # edit it a minute after it closed, and neither can the author.
        if task.get("status") == "done" and Role.clean(request.user.role) not in Role.ADMINISTRATIVE:
            return Response(
                {"detail": _("Only the owner or an administrator may edit a completed task.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        can_edit = bool(request.user.capabilities["can_edit_task"])
        is_author = task["author_id"] == request.user.id

        # Anyone who is neither a manager nor the person who raised the task is
        # done here before the body is even looked at.
        if not can_edit and not is_author:
            return Response(
                {"detail": _("Your role does not allow editing tasks.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = TaskPatchSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        assignees = data.pop("assignee_ids", None)
        subtasks = data.pop("subtasks", None)
        other_fields = {key: value for key, value in data.items() if key in {
            "title", "description", "status", "priority", "project", "due_date",
        }}

        # A full edit stays manager-only. The author may still PATCH, but only
        # to change who the task is assigned to — reassigning work they handed
        # out when the person they gave it to falls through.
        reassign_only = (
            assignees is not None and subtasks is None and not other_fields
        )
        if not can_edit and not (is_author and reassign_only):
            return Response(
                {"detail": _("Your role does not allow editing tasks.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        if assignees is not None:
            checked = _validated_employee_ids(request.user.company_id, assignees)
            if checked is None:
                return Response(
                    {"assignee_ids": [_("Some of these employees are not in your company.")]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            added = set(checked) - set(task.get("assignee_ids") or [])
            repo.set_task_assignees(
                task_id, checked,
                company_id=request.user.company_id,
                actor_id=request.user.id,
                task_title=task["title"],
            )
            # Only when somebody new is on it. Editing a title, or dropping an
            # assignee, must not push the task at the people who already had
            # it — that reads as a second task rather than an edit.
            if added:
                _queue_task_assigned(task, request.user, employee_ids=added)

        if subtasks is not None:
            repo.replace_subtasks(task_id, subtasks)

        # Only the columns the caller actually sent — PATCH must not reset the
        # serializer's defaults over fields nobody touched.
        updated = repo.update_task(
            task_id, request.user.company_id, actor_id=request.user.id, **other_fields
        )
        return Response(_task_payload(updated, request.user))

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Delete a task (owner/manager only)",
                         responses={204: openapi.Response(description="Deleted")})
    def delete(self, request, task_id: int):
        if not request.user.capabilities["can_delete_task"]:
            return Response(
                {"detail": _("Your role does not allow deleting tasks.")},
                status=status.HTTP_403_FORBIDDEN,
            )
        if not repo.delete_task(task_id, request.user.company_id, actor_id=request.user.id):
            return Response({"detail": _("Task not found.")}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkspaceTaskStatusView(WorkspaceAPIView):
    """POST /api/b2b/workspace/tasks/<id>/status/

    The one write an employee always has: moving a task they were given from
    todo → in progress → done.
    """

    required_module = Module.TASKS
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Change a task's status",
                         request_body=TaskStatusSerializer, responses={200: TaskSerializer()})
    def post(self, request, task_id: int):
        task = repo.get_task(task_id, request.user.company_id)
        if not task:
            return Response({"detail": _("Task not found.")}, status=status.HTTP_404_NOT_FOUND)

        allowed = (
            request.user.capabilities["can_edit_task"]
            or task["author_id"] == request.user.id
            or request.user.id in (task.get("assignee_ids") or [])
        )
        if not allowed:
            return Response(
                {"detail": _("You can only update tasks assigned to you.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = TaskStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        new_status = serializer.validated_data["status"]

        # `updated_at` moves on every edit, so it can't say whether a done task
        # was ever touched again after finishing — employee-of-the-month's
        # on-time rate needs a timestamp that only changes when the task
        # actually finishes. Reopening a task (done -> anything else) clears
        # it, so a task re-completed later is judged on that completion, not
        # a stale one.
        completed_at = timezone.now() if new_status == "done" else None
        updated = repo.update_task(
            task_id, request.user.company_id, actor_id=request.user.id,
            status=new_status, completed_at=completed_at,
        )
        return Response(_task_payload(updated, request.user))


class WorkspaceSubtaskToggleView(WorkspaceAPIView):
    """POST /api/b2b/workspace/tasks/<id>/subtasks/<sid>/toggle/"""

    required_module = Module.TASKS
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Tick or untick a checklist step",
                         responses={200: TaskSerializer()})
    def post(self, request, task_id: int, subtask_id: int):
        task = repo.get_task(task_id, request.user.company_id)
        if not task:
            return Response({"detail": _("Task not found.")}, status=status.HTTP_404_NOT_FOUND)

        allowed = (
            request.user.capabilities["can_edit_task"]
            or task["author_id"] == request.user.id
            or request.user.id in (task.get("assignee_ids") or [])
        )
        if not allowed:
            return Response(
                {"detail": _("You can only update tasks assigned to you.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        if not repo.toggle_subtask(task_id, subtask_id):
            return Response({"detail": _("Subtask not found.")}, status=status.HTTP_404_NOT_FOUND)

        # Ticking the last step leaves the task where it is. It used to be
        # nudged into "review", a status that no longer exists — and moving
        # somebody's task for them was never the checklist's job anyway:
        # finishing the steps is what the person does, saying it is done is a
        # decision they make afterwards.
        updated = repo.get_task(task_id, request.user.company_id)
        return Response(_task_payload(updated, request.user))


class WorkspaceTaskCommentView(WorkspaceAPIView):
    """POST /api/b2b/workspace/tasks/<id>/comments/"""

    required_module = Module.TASKS
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Comment on a task",
                         request_body=TaskCommentWriteSerializer, responses={201: TaskSerializer()})
    def post(self, request, task_id: int):
        task = repo.get_task(task_id, request.user.company_id)
        if not task:
            return Response({"detail": _("Task not found.")}, status=status.HTTP_404_NOT_FOUND)

        if request.user.task_scope is not None and not (
            task["author_id"] == request.user.id
            or request.user.id in (task.get("assignee_ids") or [])
        ):
            return Response({"detail": _("Task not found.")}, status=status.HTTP_404_NOT_FOUND)

        serializer = TaskCommentWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        repo.add_task_comment(task_id, request.user.id, serializer.validated_data["text"])

        updated = repo.get_task(task_id, request.user.company_id)
        return Response(_task_payload(updated, request.user), status=status.HTTP_201_CREATED)


class WorkspaceTaskVoiceView(WorkspaceAPIView):
    """POST/DELETE /api/b2b/workspace/tasks/<id>/voice/ — the task's voice note.

    Its own endpoint rather than a field on the create call: a task is created
    as JSON and a clip is multipart, and folding the two together would mean
    every task write carried a file parser it does not need. The app posts the
    task, gets its id, and sends the recording straight after.

    A task carries at most one clip. Posting a second replaces the first,
    bytes and all — re-recording is the common case, and leaving the earlier
    attempt on the company's quota is not what "replace" means.
    """

    required_module = Module.TASKS
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Attach a voice note to a task",
        consumes=["multipart/form-data"],
        manual_parameters=[
            openapi.Parameter("file", openapi.IN_FORM, type=openapi.TYPE_FILE, required=True),
            openapi.Parameter("duration_ms", openapi.IN_FORM, type=openapi.TYPE_INTEGER),
        ],
        responses={201: TaskSerializer()},
    )
    def post(self, request, task_id: int):
        task = self._writable_task(request, task_id)
        if task is None:
            return Response({"detail": _("Task not found.")}, status=status.HTTP_404_NOT_FOUND)

        upload = request.FILES.get("file")
        if not upload:
            return Response({"detail": _("No file provided.")}, status=status.HTTP_400_BAD_REQUEST)

        self._discard_existing(task_id)

        voice, refusal = store_upload(
            request=request,
            upload=upload,
            kind="task",
            task_id=task_id,
            # From the recorder, which is the only thing that knows how long
            # the clip runs — see the chat send for why the server does not
            # work it out itself.
            duration_ms=_int_or_none(request.data.get("duration_ms")),
        )
        if refusal:
            return refusal

        updated = repo.get_task(task_id, request.user.company_id)
        return Response(_task_payload(updated, request.user), status=status.HTTP_201_CREATED)

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Remove a task's voice note",
        responses={200: TaskSerializer()},
    )
    def delete(self, request, task_id: int):
        task = self._writable_task(request, task_id)
        if task is None:
            return Response({"detail": _("Task not found.")}, status=status.HTTP_404_NOT_FOUND)

        self._discard_existing(task_id)
        updated = repo.get_task(task_id, request.user.company_id)
        return Response(_task_payload(updated, request.user))

    def _writable_task(self, request, task_id: int) -> dict | None:
        """The task, if this caller may attach to it.

        Author, assignee or a manager — narrower than the comment endpoint on
        purpose, even though everyone can now *read* every task: a task holds
        one clip, so attaching replaces whatever was there. Reading the board
        company-wide must not let a bystander overwrite somebody's recording. Anything else gets a 404 rather than a 403: a company's task
        ids are not something to confirm to somebody who cannot see them.
        """
        task = repo.get_task(task_id, request.user.company_id)
        if not task:
            return None
        if request.user.visible_scope is not None and not (
            task["author_id"] == request.user.id
            or request.user.id in (task.get("assignee_ids") or [])
        ):
            return None
        return task

    @staticmethod
    def _discard_existing(task_id: int) -> None:
        """Drops the clip a task already had, object and row together."""
        previous = repo.delete_task_voice(task_id)
        if previous:
            default_storage.delete(previous["path"])


def _writable_task_or_none(request, task_id: int) -> dict | None:
    """The task, if this caller may attach to or detach from it.

    Author, assignee or a manager. Narrower than reading — every role sees the
    whole company board — because attaching spends the company's storage quota
    and detaching destroys somebody else's file. Anything else gets a 404
    rather than a 403: a company's task ids are not something to confirm to
    somebody who cannot act on them.
    """
    task = repo.get_task(task_id, request.user.company_id)
    if not task:
        return None
    if request.user.visible_scope is not None and not (
        task["author_id"] == request.user.id
        or request.user.id in (task.get("assignee_ids") or [])
    ):
        return None
    return task


class WorkspaceTaskFilesView(WorkspaceAPIView):
    """POST /api/b2b/workspace/tasks/<id>/files/ — attach a document to a task.

    Its own endpoint for the same reason the voice note has one: a task is
    written as JSON and a document is multipart, so the app posts the task,
    gets its id, and sends the files straight after.

    Unlike the voice note a task carries as many documents as were attached —
    a brief, its annexes and a photographed receipt are three files and
    replacing one with the next would be a data loss, not a correction. One
    request carries one file; several are several requests, which is what lets
    the app report and retry them one at a time instead of losing a whole
    batch to the one that was over the limit.
    """

    required_module = Module.TASKS
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Attach a document to a task",
        consumes=["multipart/form-data"],
        manual_parameters=[
            openapi.Parameter("file", openapi.IN_FORM, type=openapi.TYPE_FILE, required=True),
        ],
        responses={201: TaskSerializer()},
    )
    def post(self, request, task_id: int):
        task = _writable_task_or_none(request, task_id)
        if task is None:
            return Response({"detail": _("Task not found.")}, status=status.HTTP_404_NOT_FOUND)

        upload = request.FILES.get("file")
        if not upload:
            return Response({"detail": _("No file provided.")}, status=status.HTTP_400_BAD_REQUEST)

        # Not `_, refusal = ...`: `_` is gettext in this module, and binding
        # it locally makes every translated string in this method a
        # NameError.
        _stored, refusal = store_upload(
            request=request,
            upload=upload,
            kind=repo.TASK_FILE_KIND,
            task_id=task_id,
        )
        if refusal:
            return refusal

        updated = repo.get_task(task_id, request.user.company_id)
        return Response(_task_payload(updated, request.user), status=status.HTTP_201_CREATED)


class WorkspaceTaskFileDetailView(WorkspaceAPIView):
    """DELETE /api/b2b/workspace/tasks/<id>/files/<file_id>/ — detach one.

    The bytes go with the row. There is no trash for a task attachment: the
    drive is where files are kept, and something attached to a task is part of
    the task rather than a document in its own right.
    """

    required_module = Module.TASKS
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Detach a document from a task",
        responses={200: TaskSerializer()},
    )
    def delete(self, request, task_id: int, file_id: int):
        task = _writable_task_or_none(request, task_id)
        if task is None:
            return Response({"detail": _("Task not found.")}, status=status.HTTP_404_NOT_FOUND)

        removed = repo.delete_task_file(task_id, file_id)
        if not removed:
            return Response({"detail": _("File not found.")}, status=status.HTTP_404_NOT_FOUND)
        default_storage.delete(removed["path"])

        updated = repo.get_task(task_id, request.user.company_id)
        return Response(_task_payload(updated, request.user))


# ─── Calendar ─────────────────────────────────────────────────────────────────

def _event_payload(event: dict, user) -> dict:
    return {
        **event,
        "can_edit": bool(
            user.capabilities["can_edit_any_event"] or event.get("author_id") == user.id
        ),
    }


class WorkspaceEventListCreateView(WorkspaceAPIView):
    """GET  /api/b2b/workspace/events/ — the calendar window.
    POST /api/b2b/workspace/events/ — managers create shared events; employees
    may create personal ones for themselves."""

    required_module = Module.CALENDAR
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="List calendar events",
        manual_parameters=[
            openapi.Parameter("start", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                              format=openapi.FORMAT_DATETIME),
            openapi.Parameter("end", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                              format=openapi.FORMAT_DATETIME),
        ],
        responses={200: CalendarEventSerializer(many=True)},
    )
    def get(self, request):
        from django.utils.dateparse import parse_datetime
        from django.utils import timezone as dj_timezone

        def _window(name: str, fallback):
            raw = request.query_params.get(name)
            parsed = parse_datetime(raw) if raw else None
            if parsed and dj_timezone.is_naive(parsed):
                parsed = dj_timezone.make_aware(parsed)
            return parsed or fallback

        now = dj_timezone.now()
        # A default window keeps a long-lived company from shipping years of
        # history to a phone that only renders one month.
        start = _window("start", now - timedelta(days=90))
        end = _window("end", now + timedelta(days=365))

        events = repo.list_events(
            request.user.company_id,
            visible_to=request.user.visible_scope,
            start=start,
            end=end,
        )
        return Response([_event_payload(event, request.user) for event in events])

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Create a calendar event",
                         request_body=EventWriteSerializer, responses={201: CalendarEventSerializer()})
    def post(self, request):
        serializer = EventWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        caps = request.user.capabilities
        participants = data.get("participant_ids") or []

        if not caps["can_create_event"]:
            # An employee keeps a private calendar: their own entries, no
            # invitations to anyone else.
            if participants and set(participants) != {request.user.id}:
                return Response(
                    {"detail": _("Your role does not allow inviting other people to an event.")},
                    status=status.HTTP_403_FORBIDDEN,
                )
            participants = [request.user.id]

        checked = _validated_employee_ids(request.user.company_id, participants)
        if checked is None:
            return Response(
                {"participant_ids": [_("Some of these employees are not in your company.")]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        event = repo.create_event(
            company_id=request.user.company_id,
            author_id=request.user.id,
            title=data["title"],
            event_type=data.get("event_type") or "meeting",
            starts_at=data["starts_at"],
            ends_at=data["ends_at"],
            all_day=data.get("all_day", False),
            location=data.get("location") or None,
            notes=data.get("notes") or None,
            participant_ids=checked,
        )
        _queue_event_created(event, request.user)
        return Response(_event_payload(event, request.user), status=status.HTTP_201_CREATED)


class WorkspaceEventDetailView(WorkspaceAPIView):
    required_module = Module.CALENDAR
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    def _load(self, request, event_id: int):
        event = repo.get_event(event_id, request.user.company_id)
        if not event:
            return None
        if request.user.visible_scope is not None:
            visible = (
                event["author_id"] == request.user.id
                or request.user.id in (event.get("participant_ids") or [])
            )
            if not visible:
                return None
        return event

    def _may_edit(self, request, event: dict) -> bool:
        return request.user.capabilities["can_edit_any_event"] or event["author_id"] == request.user.id

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Event detail",
                         responses={200: CalendarEventSerializer()})
    def get(self, request, event_id: int):
        event = self._load(request, event_id)
        if not event:
            return Response({"detail": _("Event not found.")}, status=status.HTTP_404_NOT_FOUND)
        return Response(_event_payload(event, request.user))

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Edit an event",
                         request_body=EventPatchSerializer, responses={200: CalendarEventSerializer()})
    def patch(self, request, event_id: int):
        event = self._load(request, event_id)
        if not event:
            return Response({"detail": _("Event not found.")}, status=status.HTTP_404_NOT_FOUND)
        if not self._may_edit(request, event):
            return Response(
                {"detail": _("You can only edit events you created.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = EventPatchSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        participants = data.pop("participant_ids", None)
        if participants is not None:
            checked = _validated_employee_ids(request.user.company_id, participants)
            if checked is None:
                return Response(
                    {"participant_ids": [_("Some of these employees are not in your company.")]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            repo.set_event_participants(event_id, checked)

        fields = {key: value for key, value in data.items() if key in {
            "title", "event_type", "starts_at", "ends_at", "all_day", "location", "notes",
        }}
        updated = repo.update_event(event_id, request.user.company_id, **fields)

        # Moved to another time, so the reminders are due again. Without this
        # the 30-minute row is still claimed from the old time and a meeting
        # pushed from 10:00 to 16:00 warns nobody at all.
        if "starts_at" in fields:
            repo.clear_event_reminders(event_id)

        # Somebody newly invited is told, the same as on create. The people
        # who were already on it are not: `only` narrows it to the difference.
        if participants is not None:
            added = set(checked) - set(event.get("participant_ids") or [])
            if added:
                _queue_event_created(updated, request.user, employee_ids=added)

        return Response(_event_payload(updated, request.user))

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Delete an event",
                         responses={204: openapi.Response(description="Deleted")})
    def delete(self, request, event_id: int):
        event = self._load(request, event_id)
        if not event:
            return Response({"detail": _("Event not found.")}, status=status.HTTP_404_NOT_FOUND)
        if not self._may_edit(request, event):
            return Response(
                {"detail": _("You can only delete events you created.")},
                status=status.HTTP_403_FORBIDDEN,
            )
        repo.delete_event(event_id, request.user.company_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─── Quick notes ──────────────────────────────────────────────────────────────


def _note_voice_payload(voice: dict | None) -> dict | None:
    """A note's recording as the app plays it.

    Shaped rather than passed through for the same reason a task's clip is:
    the row carries a storage path, which is an internal detail and not
    something a phone can open.
    """
    if not voice:
        return None
    return {
        "id": voice["id"],
        "name": voice["name"],
        "size": voice["size"],
        "content_type": voice.get("content_type"),
        "duration_ms": voice.get("duration_ms"),
        "url": default_storage.url(voice["path"]),
    }


def _note_payload(note: dict, user) -> dict:
    """A note plus whether this caller may change it.

    Authorship, not role: a shared note is something a colleague let you read,
    and a manager's edit rights over the board do not extend to rewriting
    somebody else's note in place.
    """
    return {
        **note,
        "voice": _note_voice_payload(note.get("voice")),
        "can_edit": note.get("author_id") == user.id,
    }


class WorkspaceNoteListCreateView(WorkspaceAPIView):
    """GET  /api/b2b/workspace/notes/ — the strip above the calendar.
    POST /api/b2b/workspace/notes/ — a typed note, or the empty shell a
    recording is then attached to.

    Filed under the calendar module because that is the only screen that shows
    them: somebody without the Taqvim tab has nowhere to read a note, so an
    endpoint they could still reach would be a gap in the same gate.
    """

    required_module = Module.CALENDAR
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="List quick notes (own, plus what the workspace shared)",
        responses={200: NoteSerializer(many=True)},
    )
    def get(self, request):
        notes = repo.list_notes(
            request.user.company_id, employee_id=request.user.id
        )
        return Response([_note_payload(note, request.user) for note in notes])

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Create a quick note",
        request_body=NoteWriteSerializer,
        responses={201: NoteSerializer()},
    )
    def post(self, request):
        serializer = NoteWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        note = repo.create_note(
            company_id=request.user.company_id,
            author_id=request.user.id,
            kind=data["kind"],
            title=data["title"].strip(),
            body=data["body"].strip(),
            color=data["color"],
            is_shared=data["is_shared"],
        )
        if not note:
            return Response(
                {"detail": _("Could not create the note.")},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(_note_payload(note, request.user), status=status.HTTP_201_CREATED)


class WorkspaceNoteDetailView(WorkspaceAPIView):
    """PATCH/DELETE /api/b2b/workspace/notes/<id>/ — the author's own note.

    There is no GET: the strip loads every note the caller can see in one
    request and the detail screen is drawn from that, so a per-note fetch would
    only be a second way for the same row to arrive.
    """

    required_module = Module.CALENDAR
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    def _own_note(self, request, note_id: int) -> dict | None:
        """The note, if this caller wrote it.

        A shared note somebody else wrote gets a 404 rather than a 403: the
        caller can read it, but confirming which ids are writable is not
        something a refusal needs to tell them.
        """
        note = repo.get_note(note_id, request.user.company_id)
        if not note or note["author_id"] != request.user.id:
            return None
        return note

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Edit a note — text, colour, pinned, shared",
        request_body=NotePatchSerializer,
        responses={200: NoteSerializer()},
    )
    def patch(self, request, note_id: int):
        note = self._own_note(request, note_id)
        if not note:
            return Response({"detail": _("Note not found.")}, status=status.HTTP_404_NOT_FOUND)

        serializer = NotePatchSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        fields = {
            key: (value.strip() if isinstance(value, str) else value)
            for key, value in serializer.validated_data.items()
        }
        updated = repo.update_note(note_id, request.user.company_id, **fields)
        return Response(_note_payload(updated, request.user))

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Delete a note",
        responses={204: openapi.Response(description="Deleted")},
    )
    def delete(self, request, note_id: int):
        note = self._own_note(request, note_id)
        if not note:
            return Response({"detail": _("Note not found.")}, status=status.HTTP_404_NOT_FOUND)

        removed = repo.delete_note(note_id, request.user.company_id)
        # The file row cascades away with the note; the object it points at
        # does not, so it is removed here or the company pays quota forever for
        # bytes nothing can reach.
        voice = (removed or {}).get("voice")
        if voice:
            default_storage.delete(voice["path"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkspaceNoteVoiceView(WorkspaceAPIView):
    """POST/DELETE /api/b2b/workspace/notes/<id>/voice/ — the recording.

    Its own endpoint rather than a field on the create call, for the reason
    [WorkspaceTaskVoiceView] gives: a note is created as JSON and a clip is
    multipart. The app posts the note, gets its id, and sends the recording
    straight after.

    A note carries at most one clip, and posting a second replaces the first,
    bytes and all.
    """

    required_module = Module.CALENDAR
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Attach a recording to a note",
        consumes=["multipart/form-data"],
        manual_parameters=[
            openapi.Parameter("file", openapi.IN_FORM, type=openapi.TYPE_FILE, required=True),
            openapi.Parameter("duration_ms", openapi.IN_FORM, type=openapi.TYPE_INTEGER),
        ],
        responses={201: NoteSerializer()},
    )
    def post(self, request, note_id: int):
        note = self._own_note(request, note_id)
        if not note:
            return Response({"detail": _("Note not found.")}, status=status.HTTP_404_NOT_FOUND)

        upload = request.FILES.get("file")
        if not upload:
            return Response({"detail": _("No file provided.")}, status=status.HTTP_400_BAD_REQUEST)

        self._discard_existing(note_id)

        # `stored` rather than `_`: that name is gettext's in this module, and
        # binding it here makes it a local for the whole method — so every
        # `_("...")` above it raises instead of translating. The row itself is
        # not needed, since the payload below re-reads the note with its clip.
        stored, refusal = store_upload(
            request=request,
            upload=upload,
            kind=repo.NOTE_VOICE_KIND,
            note_id=note_id,
            # From the recorder, which is the only thing that knows how long
            # the clip runs — see the chat send for why the server does not
            # work it out itself.
            duration_ms=_int_or_none(request.data.get("duration_ms")),
        )
        if refusal:
            return refusal

        updated = repo.get_note(note_id, request.user.company_id)
        return Response(_note_payload(updated, request.user), status=status.HTTP_201_CREATED)

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Remove a note's recording",
        responses={200: NoteSerializer()},
    )
    def delete(self, request, note_id: int):
        note = self._own_note(request, note_id)
        if not note:
            return Response({"detail": _("Note not found.")}, status=status.HTTP_404_NOT_FOUND)

        self._discard_existing(note_id)
        updated = repo.get_note(note_id, request.user.company_id)
        return Response(_note_payload(updated, request.user))

    def _own_note(self, request, note_id: int) -> dict | None:
        note = repo.get_note(note_id, request.user.company_id)
        if not note or note["author_id"] != request.user.id:
            return None
        return note

    @staticmethod
    def _discard_existing(note_id: int) -> None:
        """Drops the clip a note already had, object and row together."""
        previous = repo.delete_note_voice(note_id)
        if previous:
            default_storage.delete(previous["path"])


# ─── Chat ─────────────────────────────────────────────────────────────────────

#: A stored picture as a URL.
#:
#: Moved to `storage` rather than kept here: the join-request and secondment
#: payloads live in their own modules and carry the same column, and they were
#: still shipping the bare path — see the note on `storage.photo_url`.
_photo_url = storage.photo_url


def _thread_photo_url(thread: dict) -> str | None:
    """A group's picture as something the app can load.

    The column holds a storage path — the same convention every other stored
    object here uses — and turning it into a URL is the server's job, because
    only the server knows which backend the bytes are on.
    """
    return _photo_url(thread.get("photo"))


def _thread_payload(thread: dict) -> dict:
    last_id = thread.get("last_message_id")
    return {
        "id": thread["id"],
        "group_name": thread.get("group_name"),
        "photo": _thread_photo_url(thread),
        "participant_ids": thread.get("participant_ids") or [],
        "unread": int(thread.get("unread") or 0),
        "is_pinned": bool(thread.get("is_pinned")),
        "is_muted": bool(thread.get("is_muted")),
        "last_message": {
            "id": last_id,
            "sender_id": thread.get("last_message_sender_id"),
            "text": thread.get("last_message_text"),
            "created_at": thread.get("last_message_created_at"),
        } if last_id else None,
    }


class WorkspaceThreadListCreateView(WorkspaceAPIView):
    """GET  /api/b2b/workspace/chats/ — the caller's conversations.
    POST /api/b2b/workspace/chats/ — open a direct chat, or a group (managers)."""

    required_module = Module.CHAT
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="List chat threads",
                         responses={200: ChatThreadSerializer(many=True)})
    def get(self, request):
        threads = repo.list_threads(request.user.company_id, request.user.id)
        return Response({
            "results": [_thread_payload(thread) for thread in threads],
            "total_unread": repo.total_unread(request.user.company_id, request.user.id),
        })

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Start a chat",
                         request_body=ThreadCreateSerializer, responses={200: ChatThreadSerializer()})
    def post(self, request):
        serializer = ThreadCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        group_name = (data.get("group_name") or "").strip() or None
        members = [m for m in data["member_ids"] if m != request.user.id]
        if not members:
            return Response(
                {"member_ids": [_("Pick at least one other person.")]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if group_name and not request.user.capabilities["can_create_group_chat"]:
            return Response(
                {"detail": _("Your role does not allow creating group chats.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        checked = _validated_employee_ids(request.user.company_id, members)
        if checked is None:
            return Response(
                {"member_ids": [_("Some of these employees are not in your company.")]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not group_name:
            # Opening a chat with someone twice must land in the same room, not
            # fork a second empty thread.
            existing = repo.find_direct_thread(request.user.company_id, request.user.id, checked[0])
            if existing:
                thread = repo.get_thread_for_member(
                    existing["id"], request.user.company_id, request.user.id
                )
                return Response(_thread_payload(thread or existing))

        thread = repo.create_thread(
            company_id=request.user.company_id,
            created_by=request.user.id,
            member_ids=checked,
            group_name=group_name,
        )
        # Everyone in the new room starts listening to it now, rather than on
        # their next reconnect. Without this a group chat opened while all its
        # members are online is silent for all of them but its creator, which
        # looks exactly like the feature being broken.
        add_to_thread([request.user.id, *checked], thread["id"])
        realtime.publish_employees(
            checked,
            realtime.EVENT_THREAD,
            action="created",
            thread_id=thread["id"],
        )
        return Response(_thread_payload(thread), status=status.HTTP_201_CREATED)


# ─── Group chats ──────────────────────────────────────────────────────────────
#
# A group has a screen of its own — a name, a picture and the people in it —
# and everything on that screen is written through the four views below. They
# share one rule about who may write, stated once in [_may_manage_group]: an
# admin of *this room*, or somebody who runs the company. The room's own admin
# comes first because a group is not a company asset — it is a conversation,
# and the person who started it is the one who knows who belongs in it.


def _may_manage_group(thread: dict, user, member: dict | None) -> bool:
    """Whether this caller may rename the room, repaper it, or move people.

    A manager is included so a room does not become unmaintainable when its
    admins leave the company — which is exactly when somebody needs to get
    into it and cannot ask its owner.
    """
    if member is None:
        return False
    if member.get("role") == "admin":
        return True
    return bool(user.capabilities["can_manage_team"])


def _group_payload(thread: dict, user, member: dict | None) -> dict:
    members = _with_presence(repo.list_thread_members(thread["id"]))
    return {
        "id": thread["id"],
        "group_name": thread.get("group_name"),
        "photo": _thread_photo_url(thread),
        "created_by": thread.get("created_by"),
        "created_at": thread.get("created_at"),
        "member_count": len(members),
        "my_role": (member or {}).get("role") or "member",
        "can_manage": _may_manage_group(thread, user, member),
        "members": members,
    }


def _group_for_request(request, thread_id: int):
    """The room, the caller's membership in it, and the refusal to send back
    when there is nothing to work with.

    Returns ``(thread, member, None)`` or ``(None, None, Response)``. Every one
    of the four views starts with these same three checks, and writing them out
    four times is how the fourth one ends up missing the tenant check.
    """
    thread = repo.get_thread(thread_id, request.user.company_id)
    if not thread:
        return None, None, Response(
            {"detail": _("Chat not found.")}, status=status.HTTP_404_NOT_FOUND
        )

    member = repo.thread_member(thread_id, request.user.id)
    if not member:
        # Not "forbidden": somebody who is not in the room should not be able
        # to learn that it exists.
        return None, None, Response(
            {"detail": _("Chat not found.")}, status=status.HTTP_404_NOT_FOUND
        )

    if not thread.get("group_name"):
        return None, None, Response(
            {"detail": _("This is not a group chat.")},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return thread, member, None


def _announce_group(thread_id: int, action: str, **payload) -> None:
    """Tells the room that something about it changed.

    Sent to the thread rather than to a list of employees, so a member who is
    reading the room right now sees the new name or the new picture without
    reopening it — and so the message reaches whoever is in the room *after*
    the change, which a pre-computed recipient list would get wrong exactly
    when somebody was added or removed.
    """
    # `publish_thread` puts the thread id on the wire itself — a client holds
    # one socket per room and a frame that does not say which room it belongs
    # to cannot be placed.
    realtime.publish_thread(thread_id, realtime.EVENT_THREAD, action=action, **payload)


class WorkspaceGroupView(WorkspaceAPIView):
    """GET   /api/b2b/workspace/chats/<id>/group/ — the group's own screen.
    PATCH /api/b2b/workspace/chats/<id>/group/ — rename it, or change its picture.

    The picture arrives as multipart, the same door every other upload uses, so
    it is quota-checked and accounted for like any other stored object.
    """

    required_module = Module.CHAT
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Group detail with its members",
        responses={200: ChatGroupSerializer(), 404: "Not found"},
    )
    def get(self, request, thread_id: int):
        thread, member, refusal = _group_for_request(request, thread_id)
        if refusal:
            return refusal
        return Response(_group_payload(thread, request.user, member))

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Rename a group or set its picture",
        consumes=["application/json", "multipart/form-data"],
        request_body=ThreadUpdateSerializer,
        manual_parameters=[
            openapi.Parameter("photo", openapi.IN_FORM, type=openapi.TYPE_FILE, required=False),
        ],
        responses={200: ChatGroupSerializer(), 403: "Not an admin", 413: "Storage limit reached"},
    )
    def patch(self, request, thread_id: int):
        thread, member, refusal = _group_for_request(request, thread_id)
        if refusal:
            return refusal
        if not _may_manage_group(thread, request.user, member):
            return Response(
                {"detail": _("Only a group admin can change this.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = ThreadUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        changes: dict = {}
        if "group_name" in serializer.validated_data:
            changes["group_name"] = serializer.validated_data["group_name"]

        upload = request.FILES.get("photo")
        previous_photo = thread.get("photo")
        if upload is not None:
            file, upload_refusal = store_upload(
                request=request, upload=upload, kind="chat"
            )
            if upload_refusal:
                return upload_refusal
            changes["photo"] = file["path"]

        if not changes:
            return Response(_group_payload(thread, request.user, member))

        updated = repo.update_thread(thread_id, request.user.company_id, **changes)
        if not updated:
            return Response(
                {"detail": _("Chat not found.")}, status=status.HTTP_404_NOT_FOUND
            )

        # The old picture goes only once the new one is recorded. Deleting it
        # first would leave the room with no picture at all if the write failed.
        if upload is not None and previous_photo:
            try:
                default_storage.delete(previous_photo)
            except Exception:  # noqa: BLE001
                logger.exception("Could not delete stored object %s", previous_photo)

        payload = _group_payload(updated, request.user, member)
        _announce_group(
            thread_id,
            "updated",
            group_name=payload["group_name"],
            photo=payload["photo"],
        )
        return Response(payload)


class WorkspaceGroupMembersView(WorkspaceAPIView):
    """POST /api/b2b/workspace/chats/<id>/members/ — add people to a group."""

    required_module = Module.CHAT
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Add members to a group",
        request_body=ThreadMembersSerializer,
        responses={200: ChatGroupSerializer(), 403: "Not an admin"},
    )
    def post(self, request, thread_id: int):
        thread, member, refusal = _group_for_request(request, thread_id)
        if refusal:
            return refusal
        if not _may_manage_group(thread, request.user, member):
            return Response(
                {"detail": _("Only a group admin can add people.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = ThreadMembersSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        checked = _validated_employee_ids(
            request.user.company_id, serializer.validated_data["member_ids"]
        )
        if checked is None:
            return Response(
                {"member_ids": [_("Some of these employees are not in your company.")]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        for employee_id in checked:
            repo.add_thread_member(thread_id, employee_id)

        # Same reason the create endpoint does it: without this, somebody added
        # to a room while they have the app open hears nothing from it until
        # they reconnect, which reads as the room being broken for them.
        add_to_thread(checked, thread_id)
        realtime.publish_employees(
            checked, realtime.EVENT_THREAD, action="created", thread_id=thread_id
        )
        _announce_group(thread_id, "members")
        return Response(_group_payload(thread, request.user, member))


class WorkspaceGroupMemberView(WorkspaceAPIView):
    """PATCH  /api/b2b/workspace/chats/<id>/members/<employee_id>/ — admin or member.
    DELETE /api/b2b/workspace/chats/<id>/members/<employee_id>/ — take them out.

    Removing yourself through this endpoint is how leaving works, and it is the
    one case that needs no admin rights: nobody can be held in a conversation.
    """

    required_module = Module.CHAT
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Make a member an admin, or an admin an ordinary member",
        request_body=ThreadMemberRoleSerializer,
        responses={200: ChatGroupSerializer(), 403: "Not an admin", 409: "Last admin"},
    )
    def patch(self, request, thread_id: int, employee_id: int):
        thread, member, refusal = _group_for_request(request, thread_id)
        if refusal:
            return refusal
        if not _may_manage_group(thread, request.user, member):
            return Response(
                {"detail": _("Only a group admin can change roles.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        target = repo.thread_member(thread_id, employee_id)
        if not target:
            return Response(
                {"detail": _("That person is not in this group.")},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = ThreadMemberRoleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        role = serializer.validated_data["role"]

        # A group with no admin cannot be renamed, added to or repaired from
        # inside the app, so the last one may not step down. Promote somebody
        # else first — which is what the app's own screen tells them to do.
        if role == "member" and target.get("role") == "admin":
            if len(repo.thread_admin_ids(thread_id)) <= 1:
                return Response(
                    {"detail": _("A group needs at least one admin.")},
                    status=status.HTTP_409_CONFLICT,
                )

        repo.set_thread_member_role(thread_id, employee_id, role)
        # Re-read: the caller may have just changed their own standing, and the
        # payload has to say what is true after the write, not before it.
        member = repo.thread_member(thread_id, request.user.id)
        _announce_group(thread_id, "members")
        return Response(_group_payload(thread, request.user, member))

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Remove somebody from a group, or leave it yourself",
        responses={200: ChatGroupSerializer(), 204: "You left", 403: "Not an admin"},
    )
    def delete(self, request, thread_id: int, employee_id: int):
        thread, member, refusal = _group_for_request(request, thread_id)
        if refusal:
            return refusal

        leaving = employee_id == request.user.id
        if not leaving and not _may_manage_group(thread, request.user, member):
            return Response(
                {"detail": _("Only a group admin can remove people.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        target = repo.thread_member(thread_id, employee_id)
        if not target:
            return Response(
                {"detail": _("That person is not in this group.")},
                status=status.HTTP_404_NOT_FOUND,
            )
        # An admin is not above another admin. Without this the person who was
        # promoted a minute ago can remove the one who promoted them.
        if not leaving and target.get("role") == "admin" and member.get("role") != "admin":
            return Response(
                {"detail": _("Only a group admin can remove another admin.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        repo.remove_thread_member(thread_id, employee_id)
        remove_from_thread([employee_id], thread_id)

        # The room must not be left ownerless — see
        # `promote_longest_standing_member` for why this is not simply refused.
        if target.get("role") == "admin" and not repo.thread_admin_ids(thread_id):
            repo.promote_longest_standing_member(thread_id)

        realtime.publish_employees(
            [employee_id], realtime.EVENT_THREAD, action="removed", thread_id=thread_id
        )
        _announce_group(thread_id, "members")

        if leaving:
            # Nothing to hand back: the caller is no longer in the room and
            # would have no right to read its members.
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(_group_payload(thread, request.user, member))


# What a quoted message shows in the bubble above a reply. Deliberately short:
# the quote is a pointer, and shipping the whole original doubles the size of
# every reply in a page of history.
_QUOTE_LENGTH = 120


def _int_or_none(value) -> int | None:
    """Form fields arrive as strings, and a client that sends nonsense should
    lose the label rather than the message."""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _quote_payload(original: dict | None, attachment: dict | None = None) -> dict | None:
    """What the strip above a reply shows about the message being answered.

    The attachment rides along because a voice message has no text at all: a
    quote carrying only `text` renders as an empty strip, which says nothing
    about what was replied to. No URL — the strip labels the original, it does
    not play it — so this stays the pointer it is meant to be.
    """
    if not original:
        return None
    text = original.get("text") or ""
    return {
        "id": original["id"],
        "sender_id": original["sender_id"],
        "text": text[:_QUOTE_LENGTH],
        "is_truncated": len(text) > _QUOTE_LENGTH,
        "attachment": (
            {
                "name": attachment["name"],
                "content_type": attachment.get("content_type"),
                # What tells a voice message from any other file, here as in
                # the message payload itself.
                "duration_ms": attachment.get("duration_ms"),
            }
            if attachment
            else None
        ),
    }


def _reaction_payload(reactions: list[dict] | None, viewer_id: int) -> list[dict]:
    """The reactions on one message, folded into one entry per emoji.

    `mine` rides along because the bubble needs it and a count cannot carry
    it: tapping a reaction you already left is how it is taken back, so the
    button has to know which of them are yours.
    """
    if not reactions:
        return []
    folded: dict[str, dict] = {}
    for row in reactions:
        entry = folded.setdefault(
            row["emoji"], {"emoji": row["emoji"], "count": 0, "mine": False}
        )
        entry["count"] += 1
        if row["employee_id"] == viewer_id:
            entry["mine"] = True
    # Most-reacted first, and stable after that: a list that reorders itself
    # every time somebody taps is one nobody can aim at.
    return sorted(folded.values(), key=lambda r: (-r["count"], r["emoji"]))


def _message_payload(
    message: dict,
    attachment: dict | None = None,
    replied_to: dict | None = None,
    quoted_attachment: dict | None = None,
    reactions: list[dict] | None = None,
    viewer_id: int | None = None,
    names: dict[int, str] | None = None,
) -> dict:
    """One message as the app reads it.

    The attachment and the quote are nested rather than flattened: a message
    has at most one attachment today, but a client that reads `attachment.url`
    keeps working when that becomes a list, whereas one reading
    `attachment_url` does not.
    """
    data = ChatMessageSerializer(message).data
    # Who wrote the original, by name. Null when the row is not a forward, and
    # when the person's row is gone — the bubble then falls back to the plain
    # "Yuborilgan xabar", which is the one case where naming nobody is right.
    data["forwarded_from_name"] = (names or {}).get(message.get("forwarded_from_id"))
    data["attachment"] = (
        {
            "id": attachment["id"],
            "name": attachment["name"],
            "size": attachment["size"],
            "content_type": attachment.get("content_type"),
            # Null for anything that is not audio. Present for a voice message,
            # so the bubble can be labelled before the clip is fetched.
            "duration_ms": attachment.get("duration_ms"),
            "url": default_storage.url(attachment["path"]),
        }
        if attachment
        else None
    )
    # Null once the original is deleted — the reply survives and simply stops
    # quoting, which is what the ON DELETE SET NULL on the column produces.
    data["reply_to"] = _quote_payload(replied_to, quoted_attachment)
    data["reactions"] = _reaction_payload(reactions, viewer_id or 0)
    return data


def _forward_names(messages) -> dict[int, str]:
    """The names a page of bubbles needs for its forward labels, in one query.

    Empty when nothing on the page is a forward, which is the common case and
    costs no query at all.
    """
    return repo.employee_names([m.get("forwarded_from_id") for m in messages])


def _everyone_read_at(read_state: dict, participant_ids) -> str | None:
    """The moment by which every other member had read the room.

    `read_state` holds only members who have ever opened it, so a shorter map
    than the roster means somebody has not — and a double tick then would be
    saying something that is not true. `participant_ids` is already "everyone
    except the viewer", which is the same population the read state covers.
    """
    others = [i for i in participant_ids]
    if not others or len(read_state) < len(others):
        return None
    return min(str(value) for value in read_state.values())


class WorkspaceMessageView(WorkspaceAPIView):
    """GET / POST messages in a thread the caller belongs to."""

    required_module = Module.CHAT
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    def _thread(self, request, thread_id: int):
        return repo.get_thread_for_member(thread_id, request.user.company_id, request.user.id)

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Message history (oldest first, paged from the newest end)",
        manual_parameters=[
            openapi.Parameter("before_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter("limit", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
        ],
        responses={200: ChatMessageSerializer(many=True)},
    )
    def get(self, request, thread_id: int):
        thread = self._thread(request, thread_id)
        if not thread:
            return Response({"detail": _("Chat not found.")}, status=status.HTTP_404_NOT_FOUND)

        try:
            limit = min(int(request.query_params.get("limit", 50)), 200)
        except (TypeError, ValueError):
            limit = 50
        try:
            before_id = int(request.query_params["before_id"])
        except (KeyError, TypeError, ValueError):
            before_id = None

        search = (request.query_params.get("search") or "").strip() or None
        messages = repo.list_messages(
            thread_id, before_id=before_id, limit=limit, search=search
        )
        quoted = repo.messages_by_ids(
            [m["reply_to_id"] for m in messages if m.get("reply_to_id")]
        )
        # One query for both: a quoted message is usually already on the page,
        # and asking for its attachment separately would fetch the same rows
        # twice to render one screen.
        attachments = repo.attachments_for_messages(
            [m["id"] for m in messages] + list(quoted)
        )
        reactions = repo.reactions_for_messages([m["id"] for m in messages])
        pinned = repo.list_pinned_messages(thread_id)
        # Both lists at once: a pinned message is usually on the page too.
        forward_names = _forward_names(messages + pinned)
        # Opening a room is what marks it read, so it happens here rather than
        # costing the phone a second round trip. Only the newest page counts —
        # scrolling back through history must not clear newer messages. The
        # path is exempt from the GET cache (see core/middleware/cache.py), so
        # this write is never skipped by a cache hit.
        # A search is a look through history, not a visit to the room. Marking
        # it read would clear a backlog somebody was searching *for*.
        if before_id is None and search is None:
            read_at = repo.mark_thread_read(thread_id, request.user.id)
            # The other side's ticks move on this, not only on the socket's
            # own `read` frame: opening the room over HTTP is the commonest
            # way a thread gets read, and a sender whose app is open should
            # not have to wait for the reader to type something before their
            # message stops looking undelivered.
            realtime.publish_thread(
                thread_id,
                realtime.EVENT_READ,
                employee_id=request.user.id,
                read_at=read_at,
                last_message_id=messages[-1]["id"] if messages else None,
            )

        # Whose ticks are whose: when each *other* member last read the room.
        # The client turns this into one or two ticks per bubble by comparing
        # it against the message's own timestamp, which is the only way a
        # group chat can say "everybody has seen this" without a row per
        # message per member.
        read_state = repo.thread_read_state(thread_id, request.user.id)

        return Response({
            "results": [
                _message_payload(
                    m,
                    attachments.get(m["id"]),
                    quoted.get(m.get("reply_to_id")),
                    attachments.get(m.get("reply_to_id")),
                    reactions.get(m["id"]),
                    request.user.id,
                    forward_names,
                )
                for m in messages
            ],
            "has_more": len(messages) == limit,
            "members_read_at": {str(k): v for k, v in read_state.items()},
            # The moment by which *everyone* else had read. Null while any
            # member has never opened the room — which is exactly when a
            # double tick would be a lie.
            "read_at": _everyone_read_at(read_state, thread.get("participant_ids") or []),
            # What is pinned in this room, whatever page of history is open.
            # A pin nobody can reach from the top of the room is not a pin.
            "pinned": [
                _message_payload(m, viewer_id=request.user.id, names=forward_names)
                for m in pinned
            ],
        })

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Send a message, optionally with a photo or video",
        consumes=["application/json", "multipart/form-data"],
        request_body=MessageWriteSerializer,
        manual_parameters=[
            openapi.Parameter("file", openapi.IN_FORM, type=openapi.TYPE_FILE, required=False),
        ],
        responses={201: ChatMessageSerializer(), 413: "Storage limit reached"},
    )
    def post(self, request, thread_id: int):
        if not self._thread(request, thread_id):
            return Response({"detail": _("Chat not found.")}, status=status.HTTP_404_NOT_FOUND)

        upload = request.FILES.get("file")
        serializer = MessageWriteSerializer(
            data=request.data,
            # An attachment carries its own meaning, so a photo with no caption
            # is a real message — and so does a forward, whose text the server
            # copies from the original after this runs. Passing a forward on
            # without typing anything is the whole gesture, and requiring text
            # here refused every one of them with "Message cannot be empty".
            # Text stays required when there is nothing else in the envelope.
            context={
                "allow_empty_text": upload is not None
                or bool(request.data.get("forward_message_id")),
            },
        )
        serializer.is_valid(raise_exception=True)

        # The quota is checked before the message row exists, so a refused
        # upload leaves no half-sent message in the thread.
        if upload is not None:
            try:
                storage.assert_can_store(request.user.company_id, upload.size)
            except storage.UploadTooLarge as exc:
                return _too_large_response(exc)
            except storage.StorageQuotaExceeded as exc:
                return _quota_response(exc)

        # A reply has to point at a message in *this* thread. Without the
        # check, a valid id from another company's room would be quoted into
        # one the caller can read, leaking its text.
        reply_to_id = serializer.validated_data.get("reply_to_id")
        replied_to = None
        quoted_attachment = None
        if reply_to_id:
            replied_to = repo.get_message(reply_to_id, thread_id)
            if not replied_to:
                return Response(
                    {"reply_to_id": [_("That message is not in this chat.")]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            # Only after the message is known to be in this thread: the lookup
            # is by message id alone, and running it first would read a row the
            # caller has no business seeing.
            quoted_attachment = repo.attachments_for_messages([reply_to_id]).get(reply_to_id)

        # A forward copies the original's text on the server. Trusting the
        # client's would let anyone put words in a colleague's mouth and have
        # the bubble attribute them.
        forward_id = serializer.validated_data.get("forward_message_id")
        forwarded_from_id = None
        text = serializer.validated_data["text"]
        if forward_id:
            original = repo.message_visible_to(
                forward_id, request.user.company_id, request.user.id
            )
            if not original:
                return Response(
                    {"forward_message_id": [_("That message is not available.")]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            text = original["text"]
            # The person, not the message: the label has to keep saying who
            # wrote it after the original room is gone.
            # A forward of a forward keeps pointing at whoever wrote the
            # words, not at the colleague who passed them on — otherwise the
            # label re-attributes the message at every hop.
            forwarded_from_id = original.get("forwarded_from_id") or original["sender_id"]

        message = repo.send_message(
            thread_id,
            request.user.id,
            text,
            reply_to_id=reply_to_id,
            forwarded_from_id=forwarded_from_id,
        )

        attachment = None
        if upload is not None:
            attachment, refusal = store_upload(
                request=request,
                upload=upload,
                kind="chat",
                message_id=message["id"],
                # Sent by the recorder, which is the only thing that knows how
                # long the clip is — the server would have to decode the audio
                # to find out, and a wrong number here is cosmetic.
                duration_ms=_int_or_none(request.data.get("duration_ms")),
            )
            if refusal:
                # Racing another upload between the check and here. The message
                # goes with it rather than being left in the thread as an empty
                # bubble the sender cannot explain.
                repo.delete_message(message["id"], thread_id)
                return refusal

        # Off the request: a slow FCM call must not hold up the sender's own
        # screen, and a push that fails is not a reason to report the message
        # as unsent — it is already in the thread.
        try:
            from apps.b2b.mail.tasks import notify_chat_message

            notify_chat_message.delay(
                thread_id,
                request.user.id,
                getattr(request.user, "full_name", "") or "",
                text,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Could not queue chat notification for thread %s", thread_id)

        payload = _message_payload(
            message,
            attachment,
            replied_to,
            quoted_attachment,
            viewer_id=request.user.id,
            names=_forward_names([message]),
        )
        broadcast_message(thread_id, payload)
        return Response(payload, status=status.HTTP_201_CREATED)


class WorkspaceMessageDetailView(WorkspaceAPIView):
    """PATCH  /api/b2b/workspace/chats/<thread_id>/messages/<message_id>/
    DELETE /api/b2b/workspace/chats/<thread_id>/messages/<message_id>/

    Your own message, always. Anyone else's only if you run the company —
    a manager has to be able to take down something posted in a shared room,
    and an employee must not be able to edit the record of what was said.

    Editing is narrower than deleting: only the author, never a manager. A
    manager removing something is a visible act; a manager rewriting what
    somebody said is a forgery, and no role should be able to do it.
    """

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Edit your own message",
        request_body=MessageEditSerializer,
        responses={200: ChatMessageSerializer(), 403: "Not yours", 404: "Not found"},
    )
    def patch(self, request, thread_id: int, message_id: int):
        if not repo.get_thread_for_member(thread_id, request.user.company_id, request.user.id):
            return Response({"detail": _("Chat not found.")}, status=status.HTTP_404_NOT_FOUND)

        message = repo.get_message(message_id, thread_id)
        if not message:
            return Response({"detail": _("Message not found.")}, status=status.HTTP_404_NOT_FOUND)
        if message["sender_id"] != request.user.id:
            return Response(
                {"detail": _("You can only edit your own messages.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = MessageEditSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated = repo.edit_message(
            message_id, thread_id, serializer.validated_data["text"]
        )
        if not updated:
            return Response({"detail": _("Message not found.")}, status=status.HTTP_404_NOT_FOUND)

        attachment = repo.attachments_for_messages([message_id]).get(message_id)
        payload = _message_payload(
            updated, attachment, viewer_id=request.user.id,
            names=_forward_names([updated]),
        )
        # Everyone in the room swaps the text in place rather than showing the
        # old one until the next refetch.
        realtime.broadcast_edit(thread_id, payload)
        return Response(payload)

    required_module = Module.CHAT
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Delete a message",
        responses={204: "Deleted", 403: "Not yours", 404: "Not found"},
    )
    def delete(self, request, thread_id: int, message_id: int):
        if not repo.get_thread_for_member(thread_id, request.user.company_id, request.user.id):
            return Response({"detail": _("Chat not found.")}, status=status.HTTP_404_NOT_FOUND)

        message = repo.get_message(message_id, thread_id)
        if not message:
            return Response({"detail": _("Message not found.")}, status=status.HTTP_404_NOT_FOUND)

        own = message["sender_id"] == request.user.id
        if not own and not is_manager(getattr(request.user, "role", None)):
            return Response(
                {"detail": _("You can only delete your own messages.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        # The attachment row cascades with the message, which is what hands its
        # bytes back to the quota. The stored object is removed separately —
        # failing there wastes disk but must not fail the delete, or the
        # message stays in the room with no way to remove it.
        attachment = repo.attachments_for_messages([message_id]).get(message_id)
        repo.delete_message(message_id, thread_id)
        if attachment:
            try:
                default_storage.delete(attachment["path"])
            except Exception:  # noqa: BLE001
                logger.exception("Could not delete stored object %s", attachment["path"])

        # Everyone in the room drops the bubble without refetching.
        broadcast_deletion(thread_id, message_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkspaceMessagePinView(WorkspaceAPIView):
    """POST   /api/b2b/workspace/chats/<thread_id>/messages/<message_id>/pin/
    DELETE the same path — unpin.

    A pin is about the room, not about the message's author: anybody in it can
    put something at the top, and anybody in it can take it down again. That
    is the same rule Telegram uses in a group, and the alternative — only the
    author may pin their own — makes the feature useless for the case it
    exists for, which is somebody else's address or meeting time.
    """

    required_module = Module.CHAT
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    def _message(self, request, thread_id: int, message_id: int):
        if not repo.get_thread_for_member(thread_id, request.user.company_id, request.user.id):
            return None
        return repo.get_message(message_id, thread_id)

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Pin a message",
                         responses={200: ChatMessageSerializer()})
    def post(self, request, thread_id: int, message_id: int):
        return self._set(request, thread_id, message_id, pinned_by=request.user.id)

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Unpin a message",
                         responses={200: ChatMessageSerializer()})
    def delete(self, request, thread_id: int, message_id: int):
        return self._set(request, thread_id, message_id, pinned_by=None)

    def _set(self, request, thread_id: int, message_id: int, *, pinned_by: int | None):
        if not self._message(request, thread_id, message_id):
            return Response({"detail": _("Message not found.")}, status=status.HTTP_404_NOT_FOUND)

        updated = repo.set_message_pinned(message_id, thread_id, pinned_by=pinned_by)
        if not updated:
            return Response({"detail": _("Message not found.")}, status=status.HTTP_404_NOT_FOUND)

        payload = _message_payload(
            updated, viewer_id=request.user.id, names=_forward_names([updated])
        )
        realtime.publish_thread(
            thread_id,
            realtime.EVENT_PINNED,
            message=payload,
            pinned=pinned_by is not None,
        )
        return Response(payload)


class WorkspaceMessageReactionView(WorkspaceAPIView):
    """POST /api/b2b/workspace/chats/<thread_id>/messages/<message_id>/reactions/

    One endpoint for both directions, because the app has one gesture for
    both: tapping a reaction you already left is how it comes off.
    """

    required_module = Module.CHAT
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="React to a message, or take the reaction back",
        request_body=MessageReactionSerializer,
        responses={200: ChatMessageSerializer()},
    )
    def post(self, request, thread_id: int, message_id: int):
        if not repo.get_thread_for_member(thread_id, request.user.company_id, request.user.id):
            return Response({"detail": _("Chat not found.")}, status=status.HTTP_404_NOT_FOUND)

        message = repo.get_message(message_id, thread_id)
        if not message:
            return Response({"detail": _("Message not found.")}, status=status.HTTP_404_NOT_FOUND)

        serializer = MessageReactionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        added = repo.toggle_reaction(
            message_id, request.user.id, serializer.validated_data["emoji"]
        )

        reactions = repo.reactions_for_messages([message_id]).get(message_id)
        attachment = repo.attachments_for_messages([message_id]).get(message_id)
        payload = _message_payload(
            message, attachment, reactions=reactions, viewer_id=request.user.id,
            names=_forward_names([message]),
        )
        # Broadcast without `mine`: whose reaction it is depends on who is
        # reading, and the room is one group. Each client recomputes its own
        # from `employee_id`.
        realtime.publish_thread(
            thread_id,
            realtime.EVENT_REACTION,
            message_id=message_id,
            employee_id=request.user.id,
            emoji=serializer.validated_data["emoji"],
            # Which way it went. Without it a listener has to guess whether to
            # add or subtract, and a guess that is wrong leaves a count that
            # never comes back.
            on=added,
        )
        return Response(payload)


class WorkspaceThreadFlagsView(WorkspaceAPIView):
    """POST /api/b2b/workspace/chats/<id>/flags/ — pin / mute for this member."""

    required_module = Module.CHAT
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Pin or mute a chat",
                         request_body=ThreadFlagsSerializer, responses={200: ChatThreadSerializer()})
    def post(self, request, thread_id: int):
        if not repo.get_thread_for_member(thread_id, request.user.company_id, request.user.id):
            return Response({"detail": _("Chat not found.")}, status=status.HTTP_404_NOT_FOUND)

        serializer = ThreadFlagsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        repo.set_thread_flags(thread_id, request.user.id, **serializer.validated_data)

        thread = repo.get_thread_for_member(thread_id, request.user.company_id, request.user.id)
        return Response(_thread_payload(thread))


class WorkspaceThreadReadView(WorkspaceAPIView):
    """POST /api/b2b/workspace/chats/<id>/read/"""

    required_module = Module.CHAT
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Mark a chat as read",
                         responses={200: openapi.Response(description="Marked read")})
    def post(self, request, thread_id: int):
        if not repo.get_thread_for_member(thread_id, request.user.company_id, request.user.id):
            return Response({"detail": _("Chat not found.")}, status=status.HTTP_404_NOT_FOUND)
        repo.mark_thread_read(thread_id, request.user.id)
        return Response({"detail": _("Marked as read")})


# ─── Leads ────────────────────────────────────────────────────────────────────

def _works_lead(lead: dict, user) -> bool:
    """Whether this person is the one working the deal.

    A lead belongs to exactly one employee — whoever claimed it or was handed
    it. Nobody else touches it: not another employee, and not the owner or a
    manager over their head. Management posts leads, hands them out and watches
    the board; the work itself is one person's, so the history reads as one
    person's account of one deal.

    A manager who took the lead themselves is its claimant like anyone else,
    and works it as one.
    """
    claimed_by = lead.get("claimed_by_id")
    return claimed_by is not None and claimed_by == user.id


def _lead_activity_payload(row: dict) -> dict:
    """One history row as the app reads it, with the document filed against it.

    Nested rather than flattened — `attachment.url` survives a lead event that
    one day carries two files, `attachment_url` does not — and the same shape a
    chat message already uses, so the phone has one attachment reader and not
    two.
    """
    data = {k: v for k, v in row.items() if not k.startswith("attachment_")}
    data["attachment"] = (
        {
            "id": row["attachment_id"],
            "name": row["attachment_name"],
            "size": row["attachment_size"],
            "content_type": row.get("attachment_content_type"),
            "url": default_storage.url(row["attachment_path"]),
        }
        if row.get("attachment_id")
        else None
    )
    return data


def _lead_activity_feed(lead_id: int) -> list[dict]:
    return [_lead_activity_payload(row) for row in repo.list_lead_activity(lead_id)]


def _lead_payload(lead: dict, user) -> dict:
    """Shapes a lead for one viewer.

    Once someone claims a lead, the contact — the person and phone number an
    employee would actually call — is only sent back to whoever claimed it (and
    to the manager who posted it). Everyone else on the board still sees the
    row (company, product, status) so they know it is taken, just not who to
    call, which is what stops two employees from working the same contact.
    """
    is_owner = _works_lead(lead, user)
    can_view_details = is_owner or user.is_manager
    payload = {
        **lead,
        "can_claim": lead.get("status") == LeadStatus.NEW,
        "can_complete": lead.get("status") == LeadStatus.IN_PROGRESS and is_owner,
        "can_view_details": can_view_details,
        # Everything the detail screen writes — the stage, the notes, the line
        # items, the tasks raised off the deal — is the claimant's alone. One
        # flag rather than four, because the rule behind them is one rule.
        "can_work": is_owner,
        # Moving a lead along the funnel is the claimant's job, and a manager
        # does not do it over their head. Nobody moves a closed lead.
        "can_change_stage": (
            is_owner and lead.get("status") != LeadStatus.COMPLETED
        ),
        # Reassigning is a manager's call only — see `WorkspaceLeadAssignView`.
        "can_assign": bool(user.is_manager),
        # Deleting is irreversible, so it is limited the same way moving the
        # lead is — the owner, or a manager over their head — see
        # `WorkspaceLeadDetailView.delete`.
        "can_delete": is_owner or user.is_manager,
    }
    if not can_view_details:
        # The whole contact card, not just the two original fields: an address
        # and an email are as much a way to reach the contact as the phone is.
        payload["contact_full_name"] = None
        payload["contact_phone"] = None
        payload["contact_position"] = None
        payload["contact_email"] = None
        payload["contact_address"] = None
        # And the raw form the customer filled in. A Meta lead-ad form asks
        # whatever the marketer wrote, which routinely includes a second phone
        # number or an address — blanking the five columns above while leaving
        # the answers they came from in plain sight would hand the whole board
        # the contact the rule exists to withhold.
        payload["external_data"] = None
    return payload


class WorkspaceDeviceTokenView(WorkspaceAPIView):
    """POST /api/b2b/workspace/me/device-token/ — register this device for push."""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Register the FCM token for push notifications")
    def post(self, request):
        token = (request.data.get("fcm_token") or "").strip() or None
        repo.set_employee_fcm_token(request.user.id, token)
        return Response({"detail": _("Saved")})


class WorkspaceCustomerSearchView(WorkspaceAPIView):
    """GET /api/b2b/workspace/customers/?q= — the company's customer directory.

    What step 1 of the "Yangi lead" sheet searches. Any employee may look a
    customer up: the point of the search is to stop the same buyer being typed
    in twice, and a directory only half the company can see would not.

    Deliberately not paged. It answers a search box the moment somebody stops
    typing, and twenty matches is already more than anyone reads before
    narrowing the query.
    """

    required_module = Module.SALES
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Search the customer directory",
        manual_parameters=[
            openapi.Parameter(
                "q", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                description="Name, company or phone. Blank returns the most recent.",
            ),
        ],
        responses={200: CustomerListSerializer()},
    )
    def get(self, request):
        customers = repo.search_customers(
            request.user.company_id,
            query=request.query_params.get("q") or "",
        )
        return Response({"results": customers})


def _parse_active(raw: str | None) -> bool | None:
    if raw == "active":
        return True
    if raw == "inactive":
        return False
    return None


class WorkspaceCrmCustomerListView(WorkspaceAPIView):
    """GET /api/b2b/workspace/crm/customers/ — the CRM directory.

    Every customer the company has ever raised a lead against, with their
    deal count, lifetime value and last-touched date, so the CRM list screen
    can render straight off one response.
    """

    required_module = Module.SALES
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="List CRM customers",
        manual_parameters=[
            openapi.Parameter(
                "q", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                description="Name, company or phone.",
            ),
            openapi.Parameter(
                "active", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                enum=["active", "inactive"],
                description="Faol mijozlar / Nofaol mijozlar. Omit for Barchasi.",
            ),
        ],
        responses={200: CrmCustomerListSerializer()},
    )
    def get(self, request):
        customers = repo.list_crm_customers(
            request.user.company_id,
            query=request.query_params.get("q") or "",
            active=_parse_active(request.query_params.get("active")),
        )
        return Response({"results": customers})


class WorkspaceCrmCustomerDetailView(WorkspaceAPIView):
    """GET /api/b2b/workspace/crm/customers/<id>/ — one customer's CRM card.

    The contact card, the lifetime totals, the trailing six months of deal
    value for the chart, and the deal history — everything the detail screen
    draws, in one response.
    """

    required_module = Module.SALES
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="CRM customer detail",
        responses={200: CrmCustomerDetailSerializer(), 404: openapi.Response(description="Not found")},
    )
    def get(self, request, customer_id: int):
        customer = repo.get_customer_detail(customer_id, request.user.company_id)
        if not customer:
            return Response({"detail": _("Customer not found.")}, status=status.HTTP_404_NOT_FOUND)
        return Response(customer)


class WorkspaceLeadListCreateView(WorkspaceAPIView):
    """GET  /api/b2b/workspace/leads/ — every lead in the company, any employee
    may see the board and claim an open one.
    POST /api/b2b/workspace/leads/ — owner/performer only."""

    required_module = Module.SALES
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="List leads",
        manual_parameters=[
            openapi.Parameter("status", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                              enum=list(repo.LEAD_STATUSES)),
            # `unmarked` is not a quality — it asks for the leads nobody has
            # judged, which is the list a manager chasing the board wants.
            openapi.Parameter("quality", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                              enum=list(repo.LEAD_QUALITY_FILTERS)),
            # Omitted, this is the funnel and answers with leads only. Quick
            # sales never belong on the board — they are asked for by name, or
            # with `any` by a reader counting deals rather than working them.
            openapi.Parameter("kind", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                              enum=list(repo.LEAD_KIND_FILTERS)),
        ],
        responses={200: LeadListSerializer()},
    )
    def get(self, request):
        leads = repo.list_leads(
            request.user.company_id,
            status=request.query_params.get("status") or None,
            stage=request.query_params.get("stage") or None,
            quality=request.query_params.get("quality") or None,
            kind=request.query_params.get("kind") or LeadKind.LEAD,
        )
        # Counted in two queries for the whole board rather than two per lead:
        # the funnel shows every lead in the company, and N+1 here is the
        # difference between one screen and forty round trips.
        items = repo.count_lead_items([lead["id"] for lead in leads])
        tasks = repo.count_lead_tasks(
            request.user.company_id, [lead["id"] for lead in leads]
        )
        return Response({
            "results": [
                {
                    **_lead_payload(lead, request.user),
                    "item_count": items.get(lead["id"], 0),
                    "task_count": tasks.get(lead["id"], 0),
                }
                for lead in leads
            ]
        })

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Create a lead (owner/manager only)",
        request_body=LeadWriteSerializer,
        responses={201: LeadSerializer(), 403: openapi.Response(description="Only owners and managers create leads")},
    )
    def post(self, request):
        serializer = LeadWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Raising a lead is management's act in both of its forms — posting one
        # to the board for somebody to take, and entering one already claimed.
        # An employee works the deals they are handed; they do not open the
        # funnel with rows of their own.
        if not request.user.is_manager:
            return Response(
                {"detail": _("Your role does not allow creating leads.")},
                status=status.HTTP_403_FORBIDDEN,
            )
        # A sale that has already happened is not posted to the board for
        # somebody to take — there is nothing left to take. It belongs to
        # whoever recorded it, whatever the sheet said about assignment.
        kind = data.get("kind") or LeadKind.LEAD
        is_quick_sale = kind == LeadKind.QUICK_SALE
        claim_for_author = is_quick_sale or bool(data.get("assign_to_me", True))

        customer_id = data.get("customer_id")
        if customer_id and not repo.get_customer(customer_id, request.user.company_id):
            return Response(
                {"detail": _("Customer not found.")}, status=status.HTTP_404_NOT_FOUND
            )

        lead = repo.create_lead(
            company_id=request.user.company_id,
            author_id=request.user.id,
            company_name=data["company_name"],
            contact_full_name=data["contact_full_name"],
            contact_phone=data["contact_phone"],
            product_name=data["product_name"],
            quantity=data["quantity"],
            contact_position=data.get("contact_position") or None,
            contact_email=data.get("contact_email") or None,
            contact_address=data.get("contact_address") or None,
            source=data.get("source") or LeadSource.MANUAL,
            items=data.get("items") or (),
            customer_id=customer_id,
            amount=data.get("amount"),
            due_date=data.get("due_date"),
            note=data.get("note"),
            claim_for_author=claim_for_author,
            kind=kind,
            payment_method=data.get("payment_method"),
        )

        # Only a lead left on the board is news to the rest of the company —
        # one its author already holds is not up for grabs, and telling
        # everyone about it is a notification nobody can act on.
        recipients = (
            []
            if claim_for_author
            else repo.list_company_recipients(
                request.user.company_id, exclude_employee_id=request.user.id
            )
        )

        # The row in each colleague's notification list. Written for the whole
        # roster rather than only the reachable half: a lead sitting on the
        # board unclaimed is exactly the thing somebody should still find when
        # they open the app, whether or not their phone was pushed at.
        body = f"{data['company_name']} — {data['product_name']} ({data['quantity']})"
        for recipient in recipients:
            try:
                mail_repo.create_notification(
                    company_id=recipient["company_id"],
                    employee_id=recipient["employee_id"],
                    kind="lead",
                    title=push_text.LEAD_TITLE,
                    body=body,
                    payload={"lead_id": lead["id"]},
                )
            except Exception:  # noqa: BLE001 - the lead itself is stored
                logger.exception(
                    "Could not record the new-lead notification for employee %s.",
                    recipient["employee_id"],
                )

        tokens = [r["fcm_token"] for r in recipients if r.get("fcm_token")]
        if tokens:
            try:
                from apps.notification.service import (
                    B2B_ANDROID_CHANNEL,
                    FCMService,
                    b2b_firebase_app,
                )

                FCMService.send_to_tokens(
                    tokens=tokens,
                    # Uzbek, not `gettext`: there is no recipient locale to
                    # translate into here, and the active language in this
                    # process is `en` — see `push_text`.
                    title=push_text.LEAD_TITLE,
                    body=body,
                    data={"type": "lead", "lead_id": str(lead["id"])},
                    app=b2b_firebase_app(),
                    android_channel_id=B2B_ANDROID_CHANNEL,
                    deactivate_invalid=repo.clear_employee_fcm_tokens,
                )
            except Exception:
                logger.exception("Failed to push new-lead notification for lead %s.", lead["id"])

        return Response(_lead_payload(lead, request.user), status=status.HTTP_201_CREATED)


class WorkspaceLeadClaimView(WorkspaceAPIView):
    """POST /api/b2b/workspace/leads/<id>/claim/ — any employee takes a 'new' lead."""

    required_module = Module.SALES
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Claim a lead", responses={200: LeadSerializer()})
    def post(self, request, lead_id: int):
        if not repo.get_lead(lead_id, request.user.company_id):
            return Response({"detail": _("Lead not found.")}, status=status.HTTP_404_NOT_FOUND)

        lead = repo.claim_lead(lead_id, request.user.company_id, request.user.id)
        if not lead:
            return Response(
                {"detail": _("This lead has already been claimed.")},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(_lead_payload(lead, request.user))


class WorkspaceLeadCompleteView(WorkspaceAPIView):
    """POST /api/b2b/workspace/leads/<id>/complete/ — the claiming employee
    marks it resolved."""

    required_module = Module.SALES
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Complete a lead", responses={200: LeadSerializer()})
    def post(self, request, lead_id: int):
        lead = repo.get_lead(lead_id, request.user.company_id)
        if not lead:
            return Response({"detail": _("Lead not found.")}, status=status.HTTP_404_NOT_FOUND)
        if lead.get("claimed_by_id") != request.user.id:
            return Response(
                {"detail": _("Only the employee who claimed this lead can complete it.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        updated = repo.complete_lead(lead_id, request.user.company_id, request.user.id)
        if not updated:
            return Response(
                {"detail": _("Lead is not in progress.")}, status=status.HTTP_409_CONFLICT
            )
        return Response(_lead_payload(updated, request.user))


class WorkspaceLeadDetailView(WorkspaceAPIView):
    """GET /api/b2b/workspace/leads/<id>/ — the whole lead in one response.

    The detail screen shows the lead, its priced lines, its history and the
    tasks raised off it all at once, so it fetches them together: four small
    queries on the server beats four round trips from a phone.
    """

    required_module = Module.SALES
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Lead detail with items, activity and linked tasks",
        responses={200: LeadDetailSerializer(), 404: openapi.Response(description="Not found")},
    )
    def get(self, request, lead_id: int):
        lead = repo.get_lead(lead_id, request.user.company_id)
        if not lead:
            return Response({"detail": _("Lead not found.")}, status=status.HTTP_404_NOT_FOUND)

        payload = _lead_payload(lead, request.user)
        tasks = repo.list_lead_tasks(lead_id, request.user.company_id)
        return Response({
            **payload,
            "item_count": len(repo.list_lead_items(lead_id)),
            "task_count": len(tasks),
            "items": repo.list_lead_items(lead_id),
            "activity": _lead_activity_feed(lead_id),
            # Hydrated the same way the tasks tab does it, so a checkbox on this
            # screen and the same task on Vazifa agree about who may tick it.
            "tasks": [
                _task_payload(repo.get_task(task["id"], request.user.company_id), request.user)
                for task in tasks
            ],
        })

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Delete a lead (owner or administrator only)",
        responses={
            204: openapi.Response(description="Deleted"),
            403: openapi.Response(description="Only the owner or an administrator may delete this lead"),
            404: openapi.Response(description="Not found"),
        },
    )
    def delete(self, request, lead_id: int):
        lead = repo.get_lead(lead_id, request.user.company_id)
        if not lead:
            return Response({"detail": _("Lead not found.")}, status=status.HTTP_404_NOT_FOUND)
        # TZ §11: deleting a lead is the owner's or the administrator's call —
        # not even the manager who posted it, and not the claimant working it.
        # [_works_lead] answers a different question ("whose deal is this to
        # work") and is deliberately not consulted here.
        if not request.user.may(Permission.DEAL_DELETE):
            return Response(
                {"detail": _("Only the owner or an administrator may delete this lead.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        repo.delete_lead(lead_id, request.user.company_id, actor_id=request.user.id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkspaceLeadStageView(WorkspaceAPIView):
    """POST /api/b2b/workspace/leads/<id>/stage/ — move the lead along the funnel.

    The claimant only, and never on a closed lead. Reaching ``won`` or ``lost``
    completes it; that rule lives in the repository so this view does not have
    to know which stages are terminal.

    Takes JSON or ``multipart/form-data``: a move can carry one document — the
    signed contract behind "Yutdik", the offer behind "Taklif yuborildi" — and
    it is filed against the history row the move writes, so the feed shows it
    beside the event it belongs to rather than loose on the drive.
    """

    required_module = Module.SALES
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Change a lead's funnel stage",
        consumes=["multipart/form-data", "application/json"],
        request_body=LeadStageWriteSerializer,
        manual_parameters=[
            openapi.Parameter(
                "file",
                openapi.IN_FORM,
                type=openapi.TYPE_FILE,
                required=False,
                description="Optional document filed with the move (multipart only).",
            ),
        ],
        responses={200: LeadSerializer(), 403: openapi.Response(description="Not yours")},
    )
    def post(self, request, lead_id: int):
        lead = repo.get_lead(lead_id, request.user.company_id)
        if not lead:
            return Response({"detail": _("Lead not found.")}, status=status.HTTP_404_NOT_FOUND)

        if not _works_lead(lead, request.user):
            return Response(
                {"detail": _("Only the employee who claimed this lead can move it.")},
                status=status.HTTP_403_FORBIDDEN,
            )
        if lead.get("status") == LeadStatus.COMPLETED:
            return Response(
                {"detail": _("This lead is already closed.")},
                status=status.HTTP_409_CONFLICT,
            )

        serializer = LeadStageWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        stage = serializer.validated_data["stage"]

        # A move to where the lead already is writes nothing — the repository
        # returns early on it — so an upload here would leave a file hanging
        # off no history row at all: bytes against the company's quota that no
        # screen can show and nobody can delete.
        if lead.get("stage") == stage:
            return Response(_lead_payload(lead, request.user))

        file = None
        if upload := request.FILES.get("file"):
            file, refusal = store_upload(request=request, upload=upload, kind="lead")
            if refusal:
                return refusal

        updated = repo.set_lead_stage(
            lead_id,
            request.user.company_id,
            stage=stage,
            employee_id=request.user.id,
            lost_reason=serializer.validated_data.get("lost_reason"),
            note=serializer.validated_data.get("note"),
            attachment_file_id=file["id"] if file else None,
        )
        return Response(_lead_payload(updated or lead, request.user))


class WorkspaceLeadDueDateView(WorkspaceAPIView):
    """POST /api/b2b/workspace/leads/<id>/due-date/ — set, move or clear the
    deal's deadline.

    The claimant's, like every other write on the deal, and a manager's over
    their head — a deadline is as often the manager's call as the
    salesperson's, which is the one place this differs from
    ``WorkspaceLeadStageView``.

    A closed lead keeps whatever date it had. Putting a deadline on a deal that
    is already won or lost sets a clock nothing can run down.
    """

    required_module = Module.SALES
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Set or clear a lead's deadline",
        request_body=LeadDueDateWriteSerializer,
        responses={200: LeadSerializer(), 403: openapi.Response(description="Not yours")},
    )
    def post(self, request, lead_id: int):
        lead = repo.get_lead(lead_id, request.user.company_id)
        if not lead:
            return Response({"detail": _("Lead not found.")}, status=status.HTTP_404_NOT_FOUND)

        if not (_works_lead(lead, request.user) or request.user.is_manager):
            return Response(
                {"detail": _("Only the employee working this lead can set its deadline.")},
                status=status.HTTP_403_FORBIDDEN,
            )
        if lead.get("status") == LeadStatus.COMPLETED:
            return Response(
                {"detail": _("This lead is already closed.")},
                status=status.HTTP_409_CONFLICT,
            )

        serializer = LeadDueDateWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated = repo.set_lead_due_date(
            lead_id,
            request.user.company_id,
            due_date=serializer.validated_data["due_date"],
            employee_id=request.user.id,
        )
        return Response(_lead_payload(updated or lead, request.user))


class WorkspaceLeadQualityView(WorkspaceAPIView):
    """POST /api/b2b/workspace/leads/<id>/quality/ — mark the enquiry good or
    bad, or take the mark off.

    Who may: whoever is working the deal, and a manager over their head — the
    same pair as the deadline, and for the same reason. The salesperson who
    rang the number is the one who knows it was a wrong number; the manager
    reading the board is the one who has to be able to correct a lead written
    off too quickly.

    A closed lead is *not* refused here, unlike the deadline. A deadline on a
    finished deal sets a clock nothing runs down, but "that enquiry was never
    real" is a judgement most often made about a deal that has already been
    lost — refusing it there would put the mark out of reach on exactly the
    leads it is for.
    """

    required_module = Module.SALES
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Mark a lead good or bad, or clear the mark",
        request_body=LeadQualityWriteSerializer,
        responses={200: LeadSerializer(), 403: openapi.Response(description="Not yours")},
    )
    def post(self, request, lead_id: int):
        lead = repo.get_lead(lead_id, request.user.company_id)
        if not lead:
            return Response({"detail": _("Lead not found.")}, status=status.HTTP_404_NOT_FOUND)

        if not (_works_lead(lead, request.user) or request.user.is_manager):
            return Response(
                {"detail": _("Only the employee working this lead can rate it.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = LeadQualityWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated = repo.set_lead_quality(
            lead_id,
            request.user.company_id,
            quality=serializer.validated_data["quality"],
            employee_id=request.user.id,
        )
        return Response(_lead_payload(updated or lead, request.user))


class WorkspaceLeadAssignView(WorkspaceAPIView):
    """POST /api/b2b/workspace/leads/<id>/assign/ — hand the lead to somebody.

    Managers only, and distinct from claiming: claiming is first-come and
    self-service, this takes a lead off one employee and gives it to another.
    """

    required_module = Module.SALES
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Reassign a lead (managers only)",
        request_body=LeadAssignWriteSerializer,
        responses={200: LeadSerializer(), 403: openapi.Response(description="Managers only")},
    )
    def post(self, request, lead_id: int):
        if not request.user.is_manager:
            return Response(
                {"detail": _("Your role does not allow reassigning leads.")},
                status=status.HTTP_403_FORBIDDEN,
            )
        if not repo.get_lead(lead_id, request.user.company_id):
            return Response({"detail": _("Lead not found.")}, status=status.HTTP_404_NOT_FOUND)

        serializer = LeadAssignWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        employee_id = serializer.validated_data["employee_id"]

        # Checked against this company's roster rather than trusted: an id from
        # the client could otherwise attach a lead to somebody else's employee.
        if employee_id not in repo.employee_ids_in_company(
            request.user.company_id, [employee_id]
        ):
            return Response(
                {"detail": _("Employee not found.")}, status=status.HTTP_404_NOT_FOUND
            )

        updated = repo.assign_lead(
            lead_id,
            request.user.company_id,
            employee_id=employee_id,
            actor_id=request.user.id,
        )
        return Response(_lead_payload(updated, request.user))


class WorkspaceLeadCommentView(WorkspaceAPIView):
    """POST /api/b2b/workspace/leads/<id>/comments/ — add a note to the history.

    The claimant's alone. Management reads the history — that is the point of
    it — but the account of the calls is written by the person who made them.
    """

    required_module = Module.SALES
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Comment on a lead",
        request_body=LeadCommentWriteSerializer,
        responses={201: LeadActivitySerializer()},
    )
    def post(self, request, lead_id: int):
        lead = repo.get_lead(lead_id, request.user.company_id)
        if not lead:
            return Response({"detail": _("Lead not found.")}, status=status.HTTP_404_NOT_FOUND)

        if not _works_lead(lead, request.user):
            return Response(
                {"detail": _("Only the employee who claimed this lead can comment.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = LeadCommentWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        activity = repo.add_lead_comment(
            lead_id, author_id=request.user.id, text=serializer.validated_data["text"]
        )
        return Response(
            _lead_activity_payload(activity) if activity else activity,
            status=status.HTTP_201_CREATED,
        )


class WorkspaceLeadItemsView(WorkspaceAPIView):
    """POST   /api/b2b/workspace/leads/<id>/items/ — add a priced line.
    PUT    /api/b2b/workspace/leads/<id>/items/ — replace the whole list.

    Either way the lead's ``amount`` is re-totalled, so the board's money never
    disagrees with the lines it came from.
    """

    required_module = Module.SALES
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    def _guard(self, request, lead_id: int):
        lead = repo.get_lead(lead_id, request.user.company_id)
        if not lead:
            return None, Response(
                {"detail": _("Lead not found.")}, status=status.HTTP_404_NOT_FOUND
            )
        if not _works_lead(lead, request.user):
            return None, Response(
                {"detail": _("Only the employee who claimed this lead can edit it.")},
                status=status.HTTP_403_FORBIDDEN,
            )
        return lead, None

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Add a line item to a lead",
        request_body=LeadItemWriteSerializer,
        responses={201: LeadItemSerializer()},
    )
    def post(self, request, lead_id: int):
        _, error = self._guard(request, lead_id)
        if error:
            return error

        serializer = LeadItemWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        item = repo.add_lead_item(
            lead_id,
            name=data["name"],
            unit=data.get("unit") or "",
            amount=data.get("amount") or 0,
        )
        return Response(item, status=status.HTTP_201_CREATED)

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Replace a lead's line items",
        request_body=LeadItemWriteSerializer(many=True),
        responses={200: LeadItemSerializer(many=True)},
    )
    def put(self, request, lead_id: int):
        _, error = self._guard(request, lead_id)
        if error:
            return error

        serializer = LeadItemWriteSerializer(data=request.data, many=True)
        serializer.is_valid(raise_exception=True)
        repo.replace_lead_items(lead_id, serializer.validated_data)
        return Response(repo.list_lead_items(lead_id))


class WorkspaceLeadItemDetailView(WorkspaceAPIView):
    """DELETE /api/b2b/workspace/leads/<id>/items/<item_id>/."""

    required_module = Module.SALES
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Delete a lead's line item")
    def delete(self, request, lead_id: int, item_id: int):
        lead = repo.get_lead(lead_id, request.user.company_id)
        if not lead:
            return Response({"detail": _("Lead not found.")}, status=status.HTTP_404_NOT_FOUND)
        if not _works_lead(lead, request.user):
            return Response(
                {"detail": _("Only the employee who claimed this lead can edit it.")},
                status=status.HTTP_403_FORBIDDEN,
            )
        if not repo.delete_lead_item(lead_id, item_id):
            return Response({"detail": _("Item not found.")}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkspaceLeadTasksView(WorkspaceAPIView):
    """POST /api/b2b/workspace/leads/<id>/tasks/ — raise a task off this lead.

    Deliberately not gated on `can_create_task`: the point of the button on the
    lead screen is that the person working the deal can write down the next
    thing they have to do, and that is an employee more often than a manager.
    The task is created assigned to them.
    """

    required_module = Module.SALES
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Create a task linked to a lead",
        request_body=TaskWriteSerializer,
        responses={201: TaskSerializer()},
    )
    def post(self, request, lead_id: int):
        lead = repo.get_lead(lead_id, request.user.company_id)
        if not lead:
            return Response({"detail": _("Lead not found.")}, status=status.HTTP_404_NOT_FOUND)

        if not _works_lead(lead, request.user):
            return Response(
                {"detail": _("Only the employee who claimed this lead can add tasks.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = TaskWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        task = repo.create_task(
            company_id=request.user.company_id,
            author_id=request.user.id,
            title=data["title"],
            description=data.get("description") or "",
            status=data.get("status") or "todo",
            priority=data.get("priority") or "medium",
            project=data.get("project") or lead.get("company_name"),
            due_date=data.get("due_date"),
            assignee_ids=data.get("assignee_ids") or [request.user.id],
            subtasks=data.get("subtasks") or (),
            lead_id=lead_id,
        )
        return Response(_task_payload(task, request.user), status=status.HTTP_201_CREATED)


# ─── Files ────────────────────────────────────────────────────────────────────

def _file_payload(file: dict) -> dict:
    return {**file, "url": default_storage.url(file["path"])}


def _quota_response(exc: storage.StorageQuotaExceeded) -> Response:
    """413 rather than 400.

    A validation error means "fix your request"; this one means "the request
    was fine, there is no room". The client shows a different screen for each,
    and the numbers go in the body so it can say how much is left.
    """
    return Response(
        {
            "detail": _("Storage limit reached."),
            "code": "storage_quota_exceeded",
            "used_bytes": exc.used,
            "quota_bytes": exc.quota,
            "available_bytes": exc.available,
            "incoming_bytes": exc.incoming,
        },
        status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
    )


def _too_large_response(exc: storage.UploadTooLarge) -> Response:
    return Response(
        {
            "detail": _("File is too large."),
            "code": "upload_too_large",
            "size_bytes": exc.size,
            "max_upload_bytes": exc.limit,
        },
        status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
    )


def store_upload(*, request, upload, kind: str, message_id: int | None = None,
                 trip_id: int | None = None, task_id: int | None = None,
                 folder_id: int | None = None, note_id: int | None = None,
                 duration_ms: int | None = None):
    """Quota-check, write the object, and record the row that owns its bytes.

    The single door for everything the workspace stores. The check happens
    *before* the write: reject afterwards and the bytes are already on disk,
    and any failure between the two leaves an orphan the quota can never see.

    Returns the file row, or a [Response] to return as-is when it was refused.
    """
    try:
        storage.assert_can_store(request.user.company_id, upload.size)
    except storage.UploadTooLarge as exc:
        return None, _too_large_response(exc)
    except storage.StorageQuotaExceeded as exc:
        return None, _quota_response(exc)

    path = default_storage.save(
        f"b2b/workspace/{request.user.company_id}/{kind}/{upload.name}", upload
    )
    file = repo.create_file(
        company_id=request.user.company_id,
        author_id=request.user.id,
        name=upload.name,
        path=path,
        size=upload.size,
        kind=kind,
        content_type=getattr(upload, "content_type", None),
        message_id=message_id,
        trip_id=trip_id,
        task_id=task_id,
        folder_id=folder_id,
        note_id=note_id,
        duration_ms=duration_ms,
    )
    if not file:
        # The row is what makes the bytes accountable. Without it the object
        # would sit in storage forever, invisible to both the drive and the
        # quota, so it is removed rather than left behind.
        default_storage.delete(path)
        return None, Response(
            {"detail": _("Could not store the file.")},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return file, None


def _keep_extension(current: str, requested: str) -> str:
    """[requested], wearing [current]'s extension.

    Enforced on the server and not only hidden in the app: the field a phone
    draws is not what protects the file, and this endpoint is reachable
    without it.
    """
    stem_now, dot_now, extension = current.rpartition(".")
    if not dot_now or not extension or not stem_now:
        # Nothing to preserve: the file never had an extension, or its whole
        # name is one.
        return requested

    stem, dot, _ = requested.rpartition(".")
    base = (stem if dot else requested).strip()
    # ".pdf" as a whole name leaves nothing to call the file, so it keeps the
    # stem it already had.
    return f"{base or stem_now}.{extension}"


def _folder_payload(folder: dict) -> dict:
    """A folder as the drive screen draws it: its name and what it holds."""
    return {
        "id": folder["id"],
        "name": folder["name"],
        "author_id": folder.get("author_id"),
        "file_count": int(folder.get("file_count") or 0),
        "size_bytes": int(folder.get("size_bytes") or 0),
        "created_at": folder.get("created_at"),
    }


class WorkspaceFolderListCreateView(WorkspaceAPIView):
    """GET/POST /api/b2b/workspace/folders/ — the drive's own folders.

    Anyone in the company may make one, the same as anyone may add a file: the
    drive is shared, and a folder is how somebody decided to arrange it.
    """

    required_module = Module.FILES
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="List folders",
        responses={200: WorkspaceFolderListSerializer()},
    )
    def get(self, request):
        folders = repo.list_folders(request.user.company_id)
        return Response({"results": [_folder_payload(f) for f in folders]})

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Create a folder",
        request_body=WorkspaceFolderWriteSerializer,
        responses={201: WorkspaceFolderSerializer()},
    )
    def post(self, request):
        serializer = WorkspaceFolderWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        folder = repo.create_folder(
            company_id=request.user.company_id,
            author_id=request.user.id,
            name=serializer.validated_data["name"],
        )
        if not folder:
            return Response(
                {"detail": _("Could not create the folder.")},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        # Fresh, so its counts are the same shape the list returns rather than
        # absent on the one payload a client reads right after creating.
        return Response(
            _folder_payload({**folder, "file_count": 0, "size_bytes": 0}),
            status=status.HTTP_201_CREATED,
        )


class WorkspaceFolderDetailView(WorkspaceAPIView):
    """DELETE /api/b2b/workspace/folders/<id>/

    The folder goes; the files in it go back to the drive. Deleting somebody's
    arrangement is not deleting the company's documents, and the two should
    never be the same tap.
    """

    required_module = Module.FILES
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Delete a folder (its files return to the drive)",
        responses={204: openapi.Response(description="Deleted")},
    )
    def delete(self, request, folder_id: int):
        folder = repo.get_folder(folder_id, request.user.company_id)
        if not folder:
            return Response({"detail": _("Folder not found.")}, status=status.HTTP_404_NOT_FOUND)

        # The person who made it, or somebody who runs the company.
        if not (
            folder["author_id"] == request.user.id
            or is_manager(getattr(request.user, "role", None))
        ):
            return Response(
                {"detail": _("You can only delete folders you created.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        repo.delete_folder(folder_id, request.user.company_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkspaceStorageView(WorkspaceAPIView):
    """GET /api/b2b/workspace/storage/ — how much of the 5 GB is gone.

    Read by everyone, not just the owner: an employee about to upload a video
    needs to know it will be refused before they spend the data sending it.
    """

    required_module = Module.FILES
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Company storage usage and quota",
        responses={200: StorageUsageSerializer()},
    )
    def get(self, request):
        return Response(storage.usage(request.user.company_id))


class WorkspaceFileListCreateView(WorkspaceAPIView):
    """GET/POST /api/b2b/workspace/files/ — the company's shared folder.

    Everyone may read and add; nothing here is scoped to a role, so a driver
    can send a photographed waybill without asking anyone to do it for them.
    """

    required_module = Module.FILES
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="List files",
        manual_parameters=[
            openapi.Parameter(
                "kind", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                description="file (default), chat, or voucher",
            ),
        ],
        responses={200: WorkspaceFileListSerializer()},
    )
    def get(self, request):
        folder_id = _int_or_none(request.query_params.get("folder_id"))
        if folder_id is not None:
            if not repo.get_folder(folder_id, request.user.company_id):
                return Response(
                    {"detail": _("Folder not found.")}, status=status.HTTP_404_NOT_FOUND
                )
            files = repo.list_files(request.user.company_id, folder_id=folder_id)
            return Response({"results": [_file_payload(f) for f in files]})

        kind = request.query_params.get("kind", "file")
        if kind not in ("file", "chat", "voucher"):
            return Response({"detail": _("Invalid kind.")}, status=status.HTTP_400_BAD_REQUEST)
        files = repo.list_files(request.user.company_id, kind=kind)
        return Response({"results": [_file_payload(f) for f in files]})

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Upload a file",
        consumes=["multipart/form-data"],
        manual_parameters=[
            openapi.Parameter("file", openapi.IN_FORM, type=openapi.TYPE_FILE, required=True),
        ],
        responses={201: WorkspaceFileSerializer()},
    )
    def post(self, request):
        upload = request.FILES.get("file")
        if not upload:
            return Response({"detail": _("No file provided.")}, status=status.HTTP_400_BAD_REQUEST)

        # Straight into a folder, when the screen that sent it was inside one.
        folder_id = _int_or_none(request.data.get("folder_id"))
        if folder_id is not None and not repo.get_folder(
            folder_id, request.user.company_id
        ):
            return Response(
                {"folder_id": [_("Folder not found.")]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        file, refusal = store_upload(
            request=request, upload=upload, kind="file", folder_id=folder_id
        )
        if refusal:
            return refusal
        return Response(_file_payload(file), status=status.HTTP_201_CREATED)


class WorkspaceFileDetailView(WorkspaceAPIView):
    """PATCH / DELETE /api/b2b/workspace/files/<id>/"""

    required_module = Module.FILES
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Rename a file or move it to another folder",
        request_body=WorkspaceFilePatchSerializer,
        responses={200: WorkspaceFileSerializer()},
    )
    def patch(self, request, file_id: int):
        file = repo.get_file(file_id, request.user.company_id)
        if not file:
            return Response({"detail": _("File not found.")}, status=status.HTTP_404_NOT_FOUND)

        serializer = WorkspaceFilePatchSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        fields = dict(serializer.validated_data)

        # A rename may not change what kind of file this is. The extension is
        # how every reader on the other side — a phone, a browser, Excel —
        # decides how to open the bytes, and the bytes are not being rewritten
        # here: a .xlsx renamed to .pdf is a file that no longer opens
        # anywhere. Whatever the client sent, the stored extension is kept.
        if "name" in fields:
            fields["name"] = _keep_extension(file["name"], fields["name"])

        # A folder id is a way into the drive, so it is checked against this
        # company before anything is written. Explicit null is the one value
        # that needs no check: it means the drive itself.
        if fields.get("folder_id") is not None and not repo.get_folder(
            fields["folder_id"], request.user.company_id
        ):
            return Response(
                {"folder_id": [_("Folder not found.")]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not fields:
            return Response(_file_payload(file))

        updated = repo.update_file(file_id, request.user.company_id, **fields)
        return Response(_file_payload(updated))

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Delete a file", responses={204: "Deleted"})
    def delete(self, request, file_id: int):
        file = repo.get_file(file_id, request.user.company_id)
        if not file:
            return Response({"detail": _("File not found.")}, status=status.HTTP_404_NOT_FOUND)

        # Row first: it is what the quota counts, so dropping it is what frees
        # the space. A storage delete that fails afterwards leaves an orphan
        # object, which wastes disk but never blocks the company from
        # uploading — the other order would.
        repo.delete_file(file_id, request.user.company_id)
        try:
            default_storage.delete(file["path"])
        except Exception:  # noqa: BLE001
            logger.exception("Could not delete stored object %s", file["path"])
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─── Attendance ───────────────────────────────────────────────────────────────

def _work_date(request):
    """The day being asked about.

    Defaults to today in the *server's* timezone, which is the company's — an
    employee travelling with a phone set to another zone must not check in
    against yesterday.
    """
    raw = request.query_params.get("date") if hasattr(request, "query_params") else None
    if raw:
        parsed = parse_date(raw)
        if parsed:
            return parsed
    return timezone.localdate()


def _attendance_payload(company_id: int, work_date, viewer_id: int) -> dict:
    rows = repo.attendance_for_date(company_id, work_date)

    present = sum(1 for r in rows if r["status"] in ("present", "late", "remote"))
    absent = sum(1 for r in rows if r["status"] == "absent")
    mine = next((r for r in rows if r["employee_id"] == viewer_id), None)

    return {
        "date": work_date,
        "present": present,
        "absent": absent,
        # Counted, not inferred by the client from the two above — the roster
        # can change under a day, so present + absent + unmarked is the only
        # sum that adds up.
        "unmarked": sum(1 for r in rows if r["status"] is None),
        "my_status": (mine or {}).get("status"),
        "my_reason": (mine or {}).get("reason"),
        "my_checked_in_at": (mine or {}).get("checked_in_at"),
        "my_checked_out_at": (mine or {}).get("checked_out_at"),
        "entries": rows,
    }


class WorkspaceAttendanceView(WorkspaceAPIView):
    """GET /api/b2b/workspace/attendance/ — today's roll call.

    Readable by everyone: it is on the chat home screen, and the point of it is
    knowing who is around. `?date=YYYY-MM-DD` reads another day.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Attendance for a day",
        manual_parameters=[
            openapi.Parameter("date", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                              description="YYYY-MM-DD, defaults to today"),
        ],
        responses={200: AttendanceDaySerializer()},
    )
    def get(self, request):
        payload = _attendance_payload(
            request.user.company_id, _work_date(request), request.user.id
        )
        return Response(AttendanceDaySerializer(payload).data)


class WorkspaceAttendanceCheckInView(WorkspaceAPIView):
    """POST /api/b2b/workspace/attendance/check-in/ — "I'm here".

    Needs no capability: it only ever writes the caller's own row. The arrival
    time is taken from the server rather than the request, so a wrong device
    clock cannot become an arrival time nobody can argue with.

    When the company has a geofence on, the phone's coordinates are checked
    against it *here*, server-side — a client-side "close enough" is a check
    a modified app could always pass.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Check yourself in for today",
        request_body=AttendanceCheckInSerializer,
        responses={200: AttendanceDaySerializer(), 400: "Location required or too far"},
    )
    def post(self, request):
        serializer = AttendanceCheckInSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        latitude = serializer.validated_data.get("latitude")
        longitude = serializer.validated_data.get("longitude")

        location = repo.get_attendance_location(request.user.company_id)
        if location and location["is_enabled"]:
            if latitude is None or longitude is None:
                return Response(
                    {"detail": _("Location is required to check in.")},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            distance = distance_meters(
                float(latitude), float(longitude),
                float(location["latitude"]), float(location["longitude"]),
            )
            if distance > location["radius_meters"]:
                return Response(
                    {
                        "detail": _(
                            "You are too far from the workplace to check in. "
                            "Report your absence with a reason instead."
                        ),
                        "code": "too_far_from_workplace",
                        "distance_meters": round(distance),
                        "radius_meters": location["radius_meters"],
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

        today = timezone.localdate()
        existing = repo.attendance_row(request.user.id, today)

        repo.upsert_attendance(
            company_id=request.user.company_id,
            employee_id=request.user.id,
            work_date=today,
            status="present",
            # Only on the first check-in of the day. Tapping again must not
            # rewrite the time you actually arrived to the time you tapped.
            checked_in_at=(existing or {}).get("checked_in_at") or timezone.now(),
            reason=None,
            marked_by_id=None,
            check_in_latitude=latitude,
            check_in_longitude=longitude,
        )
        return Response(
            AttendanceDaySerializer(
                _attendance_payload(request.user.company_id, today, request.user.id)
            ).data
        )


class WorkspaceAttendanceCheckOutView(WorkspaceAPIView):
    """POST /api/b2b/workspace/attendance/check-out/ — "Ketdim".

    The other end of the day from check-in. Needs no capability: it only ever
    writes the caller's own row. The departure time is the server's, not the
    request's, for the same reason the arrival time is.

    Unlike check-in, the geofence is not enforced here — the whole point of
    checking out is that the person is leaving, so being outside the radius is
    the expected case. Coordinates, if the phone sends them, are stored for
    audit parity with the check-in pair.

    You can only check out of a day you checked into: without an arrival on
    file there is nothing to close, and the tap is refused rather than
    inventing a departure with no matching arrival.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Check yourself out for today",
        request_body=AttendanceCheckOutSerializer,
        responses={200: AttendanceDaySerializer(), 400: "Not checked in"},
    )
    def post(self, request):
        serializer = AttendanceCheckOutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        latitude = serializer.validated_data.get("latitude")
        longitude = serializer.validated_data.get("longitude")

        today = timezone.localdate()
        existing = repo.attendance_row(request.user.id, today)
        if not existing or existing.get("status") not in ("present", "late", "remote"):
            return Response(
                {
                    "detail": _("Check in before you check out."),
                    "code": "not_checked_in",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        repo.upsert_attendance(
            company_id=request.user.company_id,
            employee_id=request.user.id,
            work_date=today,
            status=existing["status"],
            # Kept from the row — this is a check-out, not a re-check-in.
            checked_in_at=existing.get("checked_in_at"),
            # Only on the first check-out of the day. Tapping again must not
            # push your departure later than when you actually left.
            checked_out_at=existing.get("checked_out_at") or timezone.now(),
            reason=existing.get("reason"),
            marked_by_id=existing.get("marked_by_id"),
            check_out_latitude=latitude,
            check_out_longitude=longitude,
        )
        return Response(
            AttendanceDaySerializer(
                _attendance_payload(request.user.company_id, today, request.user.id)
            ).data
        )


class WorkspaceAttendanceAbsenceView(WorkspaceAPIView):
    """POST /api/b2b/workspace/attendance/absence/ — "I am not coming in".

    The other half of the check-in button. Somebody outside the geofence
    cannot mark themselves present, and the alternative to letting them say
    why is a day that stays unmarked — which reads as nobody having looked at
    them rather than as an absence they declared.

    Needs no capability for the same reason check-in does not: it only ever
    writes the caller's own row. `marked_by_id` stays null, which is what
    tells this apart from a manager marking them absent.

    No coordinates are taken. The point of this endpoint is that the person is
    somewhere else, and recording where they were when they said so would
    collect a location for no purpose it serves.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Report yourself absent, with a reason",
        request_body=AttendanceSelfAbsenceSerializer,
        responses={200: AttendanceDaySerializer(), 400: "Reason required"},
    )
    def post(self, request):
        serializer = AttendanceSelfAbsenceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        today = timezone.localdate()
        work_date = data.get("date") or today
        # Only today or a day already gone. Filing tomorrow's absence in
        # advance would let somebody pre-empt a roll call that has not
        # happened, and there is no screen that asks for it.
        if work_date > today:
            return Response(
                {"date": [_("You cannot report an absence for a future day.")]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        repo.upsert_attendance(
            company_id=request.user.company_id,
            employee_id=request.user.id,
            work_date=work_date,
            status="absent",
            checked_in_at=None,
            reason=data["reason"].strip(),
            marked_by_id=None,
        )
        return Response(
            AttendanceDaySerializer(
                _attendance_payload(request.user.company_id, work_date, request.user.id)
            ).data
        )


class WorkspaceAttendanceLocationView(WorkspaceAPIView):
    """GET / PUT ``attendance/location/`` — the office geofence.

    Read by everyone: the app needs `is_enabled` before it knows whether a
    check-in has to carry coordinates at all. Only the owner may change it —
    gated by `can_manage_attendance_location` rather than the manager-level
    `can_manage_attendance`, since this is company policy, not one person's
    day.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    def get_permissions(self):
        if self.request.method == "GET":
            return [IsAuthenticated(), IsWorkspaceUser()]
        return [IsAuthenticated(), IsWorkspaceUser(), HasCapability()]

    required_capability = "can_manage_attendance_location"

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="The office geofence attendance is checked against",
        responses={200: AttendanceLocationSerializer()},
    )
    def get(self, request):
        location = repo.get_attendance_location(request.user.company_id) or {
            "is_enabled": False,
            "latitude": None,
            "longitude": None,
            "radius_meters": 200,
            "updated_at": None,
        }
        return Response(AttendanceLocationSerializer(location).data)

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Set or toggle the office geofence",
        request_body=AttendanceLocationUpdateSerializer,
        responses={200: AttendanceLocationSerializer(), 403: "Not the owner"},
    )
    def put(self, request):
        serializer = AttendanceLocationUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        existing = repo.get_attendance_location(request.user.company_id)
        latitude = data.get("latitude")
        longitude = data.get("longitude")
        if latitude is None and longitude is None and existing:
            # Re-enabling (or just changing the radius) without resending the
            # point — reuse what is already on file.
            latitude = existing["latitude"]
            longitude = existing["longitude"]

        if data["is_enabled"] and (latitude is None or longitude is None):
            return Response(
                {"detail": _("latitude and longitude are required to enable location-based attendance.")},
                status=status.HTTP_400_BAD_REQUEST,
            )

        location = repo.upsert_attendance_location(
            company_id=request.user.company_id,
            is_enabled=data["is_enabled"],
            latitude=latitude,
            longitude=longitude,
            radius_meters=data.get("radius_meters") or (existing or {}).get("radius_meters", 200),
            updated_by_id=request.user.id,
        )
        return Response(AttendanceLocationSerializer(location).data)


class WorkspaceAttendanceMarkView(WorkspaceAPIView):
    """POST /api/b2b/workspace/attendance/<employee_id>/ — record someone's day.

    A manager's screen. Marking yourself goes through check-in instead, which
    is why this does not special-case it.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser, HasCapability]
    required_capability = "can_manage_attendance"

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Mark an employee present or absent",
        request_body=AttendanceMarkSerializer,
        responses={200: AttendanceDaySerializer(), 403: "Not a manager"},
    )
    def post(self, request, employee_id: int):
        serializer = AttendanceMarkSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # Scoped to the company, so one company cannot write attendance for
        # another's staff by guessing an id.
        if not repo.employee_ids_in_company(request.user.company_id, [employee_id]):
            return Response(
                {"detail": _("Employee not found.")},
                status=status.HTTP_404_NOT_FOUND,
            )

        work_date = data.get("date") or timezone.localdate()
        marked_present = data["status"] in ("present", "late", "remote")
        existing = repo.attendance_row(employee_id, work_date)

        repo.upsert_attendance(
            company_id=request.user.company_id,
            employee_id=employee_id,
            work_date=work_date,
            status=data["status"],
            # A manager marking someone present records an arrival time only if
            # there is not one already — the employee's own check-in is the
            # better record of when they actually got in.
            checked_in_at=(
                (existing or {}).get("checked_in_at") or timezone.now()
                if marked_present
                else None
            ),
            reason=(data.get("reason") or "").strip() or None,
            marked_by_id=request.user.id,
        )
        return Response(
            AttendanceDaySerializer(
                _attendance_payload(request.user.company_id, work_date, request.user.id)
            ).data
        )


# ─── Employee of the month ──────────────────────────────────────────────────

def _current_year_month() -> tuple[int, int]:
    now = timezone.localtime(timezone.now())
    return now.year, now.month


class WorkspaceEmployeeMonthlyStatsView(WorkspaceAPIView):
    """GET /api/b2b/workspace/employee-of-month/stats/ — owner only.

    Every active employee's completed-task count and on-time rate for the
    current calendar month, sorted best-first — what the owner picks the
    winner from.
    """

    permission_classes = [IsAuthenticated, HasCapability]
    required_capability = "can_pick_employee_of_month"

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Monthly task stats per employee (owner only)",
        responses={200: EmployeeMonthlyStatSerializer(many=True)},
    )
    def get(self, request):
        year, month = _current_year_month()
        stats = repo.monthly_employee_stats(request.user.company_id, year, month)
        return Response(EmployeeMonthlyStatSerializer(stats, many=True).data)


def _employees_of_month_payload(winners: list[dict]) -> dict:
    """The month's award, with the first pick repeated at the top level.

    Two shipped clients read this response as a single winner —
    `dashboard_weel_uz` and the first B2B mobile app — and neither knows the
    badge can name more than one person. Repeating the first row flat is what
    lets them keep working, showing one name instead of failing to parse a
    list; `results` is the real answer and what the current app reads.
    """
    first = winners[0]
    return {
        "results": EmployeeOfMonthSerializer(winners, many=True).data,
        **EmployeeOfMonthSerializer(first).data,
    }


class WorkspaceEmployeeOfMonthView(WorkspaceAPIView):
    """GET/POST /api/b2b/workspace/employee-of-month/

    Anyone can see this month's pick; only the owner can make or change it.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="This month's employees of the month",
        responses={200: EmployeeOfMonthListSerializer(), 204: "Not chosen yet this month"},
    )
    def get(self, request):
        year, month = _current_year_month()
        winners = repo.list_employees_of_month(request.user.company_id, year, month)
        if not winners:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(_employees_of_month_payload(winners))

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Pick this month's employees of the month (owner or administrator)",
        request_body=EmployeeOfMonthSelectSerializer,
        responses={200: EmployeeOfMonthListSerializer(), 403: openapi.Response(description="Owner or administrator only")},
    )
    def post(self, request):
        if not request.user.capabilities["can_pick_employee_of_month"]:
            return Response(
                {"detail": _("Only the owner or an administrator can pick the employee of the month.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = EmployeeOfMonthSelectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        employee_ids = serializer.validated_data["employee_ids"]

        # The whole list checked in one query rather than one query per name:
        # the owner picks a handful at a time, and a round trip each would be
        # a round trip each.
        known = set(
            repo.employee_ids_in_company(request.user.company_id, employee_ids)
        )
        outsiders = [eid for eid in employee_ids if eid not in known]
        if outsiders:
            return Response(
                {"employee_ids": [_("This employee is not in your company.")]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        year, month = _current_year_month()
        winners = repo.set_employees_of_month(
            company_id=request.user.company_id,
            year=year,
            month=month,
            employee_ids=employee_ids,
            selected_by_id=request.user.id,
        )
        # 204 rather than an object with nothing in it: an owner who saved an
        # empty list has taken the month's badge back, and that is the same
        # state GET reports when nobody has been named at all.
        if not winners:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(_employees_of_month_payload(winners))


# ─── Yordam markazi ───────────────────────────────────────────────────────────

class WorkspaceSupportView(WorkspaceAPIView):
    """GET/POST ``support/`` — the employee's own thread with WEEL support.

    No capability and no thread id. Every employee has exactly one
    conversation, it is scoped to whoever is calling, and it is created by the
    first message rather than by an explicit "open a ticket" step — a help desk
    that asks you to file a ticket before you can describe the problem is one
    people give up on.

    Reading it also marks support's replies seen, because opening the screen is
    what seeing them means; there is no separate "read" call for the app to
    forget to make.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="The caller's support conversation",
        responses={200: SupportMessageSerializer(many=True)},
    )
    def get(self, request):
        messages = repo.list_support_messages(request.user.id)
        repo.mark_support_read(request.user.id)
        return Response(SupportMessageSerializer(messages, many=True).data)

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Write to support",
        request_body=SupportMessageCreateSerializer,
        responses={201: SupportMessageSerializer()},
    )
    def post(self, request):
        serializer = SupportMessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        message = repo.create_support_message(
            company_id=request.user.company_id,
            employee_id=request.user.id,
            text=serializer.validated_data["text"],
        )
        return Response(
            SupportMessageSerializer(message).data,
            status=status.HTTP_201_CREATED,
        )


# --- The release gate --------------------------------------------------------


def _version_tuple(value: str) -> tuple[int, ...]:
    """``"1.10.2"`` → ``(1, 10, 2)``, so versions sort by number and not by text.

    A plain string comparison puts ``1.10`` *below* ``1.9``, which is exactly
    the case a force-update rule has to get right — the tenth patch of a
    release is the one that usually carries the fix being forced.

    Anything unparseable degrades to a zero, so a build name the app was not
    expecting reads as old rather than raising: the caller is a phone on a
    launch screen and there is nothing useful to hand it but a verdict.
    """
    parts: list[int] = []
    for chunk in str(value or "").split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def _is_older(version: str, than: str) -> bool:
    left, right = _version_tuple(version), _version_tuple(than)
    # Padded, so 1.1 and 1.1.0 compare equal rather than by length.
    width = max(len(left), len(right))
    left += (0,) * (width - len(left))
    right += (0,) * (width - len(right))
    return left < right


class WorkspaceAppVersionView(APIView):
    """GET /api/b2b/workspace/app-version/ — may this build still run?

    The one endpoint in the workspace API that answers before there is a
    session: the app asks it on launch, before the token storage is even read,
    so a phone stuck on an old build is stopped at the door rather than after
    signing in. Hence ``authentication_classes = []`` — an expired token must
    not turn the gate into a 401, because a locked-out user cannot refresh it.

    Two verdicts, not one. ``update_required`` blocks the app; it is what the
    store rollout of a breaking change turns on. ``update_available`` is a
    dismissible nudge — same information, no lock — and the app shows it once
    per version so a user who said "keyinroq" is not asked again on the next
    launch.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Whether the installed mobile build may still run",
        manual_parameters=[
            openapi.Parameter(
                "platform",
                openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                enum=["android", "ios"],
                required=True,
            ),
            openapi.Parameter(
                "version",
                openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                description="The installed version name, e.g. 1.1.0",
                required=True,
            ),
        ],
        responses={200: openapi.Response(description="The release verdict")},
    )
    def get(self, request):
        platform = (request.query_params.get("platform") or "").strip().lower()
        release = settings.B2B_APP_RELEASES.get(platform)
        current = (request.query_params.get("version") or "").strip()

        # An unknown platform — the web build, a desktop run — is not an error
        # to report to a user: nothing is being rolled out there, so it is
        # simply never out of date.
        if release is None or not current:
            return Response({
                "platform": platform,
                "current_version": current,
                "update_required": False,
                "update_available": False,
            })

        latest = release["latest_version"]
        minimum = release["min_version"]
        return Response({
            "platform": platform,
            "current_version": current,
            "latest_version": latest,
            "min_version": minimum,
            "update_required": _is_older(current, minimum),
            "update_available": _is_older(current, latest),
            "store_url": release["store_url"],
            "release_notes": settings.B2B_APP_RELEASE_NOTES,
        })


# ─── Hisobot va analitika ───────────────────────────────────────────────────

class WorkspaceReportView(WorkspaceAPIView):
    """GET /api/b2b/workspace/reports/

    The profile screen's "Hisobot va analitika": the sales funnel, the task
    board and the calendar over one window, in one response.

    One endpoint and not three, because the screen is one screen. Three would
    mean three round trips on open, three spinners, and — since each would
    take its own `NOW()` — three windows that do not quite line up.

    Who sees what is decided twice over:

    * **Scope.** A manager reads the company; everybody else reads their own
      work. Not a permission check but the honest reading of the question — a
      salesperson's report is about their month, and a company total on it
      would be a number they cannot act on.
    * **Sections.** A guest lent only the sales board gets `sales` and two
      nulls. `HasModule` guards one module per view and this view spans three,
      so the gate is applied per section here rather than on the class.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    def _may_read(self, request, module: str) -> bool:
        """Whether this caller may see one section of the report.

        The same rule as [HasModule], asked three times instead of once: a
        permanent employee has no module list and sees everything, a guest
        sees what their secondment named, and a chat-only member sees none of
        it.
        """
        if request.user.get("is_chat_only"):
            return False
        modules = request.user.modules
        return modules is None or module in modules

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Sales, tasks and calendar over one window",
        manual_parameters=[
            openapi.Parameter(
                "period",
                openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                enum=list(repo.REPORT_PERIODS),
                description=(
                    "How far back to count. Defaults to "
                    f"'{repo.DEFAULT_REPORT_PERIOD}'; an unknown value falls "
                    "back to it rather than failing."
                ),
            ),
        ],
        responses={200: WorkspaceReportSerializer()},
    )
    def get(self, request):
        window = repo.report_window(request.query_params.get("period") or "")
        # One clock for all three sections — see `report_window`.
        span = {
            "start": window["start"],
            "end": window["end"],
            "bucket": window["bucket"],
        }
        company_id = request.user.company_id
        employee_id = None if request.user.is_manager else request.user.id

        return Response({
            "period": window,
            "scope": "company" if employee_id is None else "own",
            "sales": (
                repo.sales_report(company_id, employee_id=employee_id, **span)
                if self._may_read(request, Module.SALES)
                else None
            ),
            "tasks": (
                repo.task_report(company_id, employee_id=employee_id, **span)
                if self._may_read(request, Module.TASKS)
                else None
            ),
            "calendar": (
                repo.calendar_report(company_id, employee_id=employee_id, **span)
                if self._may_read(request, Module.CALENDAR)
                else None
            ),
        })
