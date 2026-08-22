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

from apps.b2b.models import LeadSource, LeadStage, LeadStatus
from apps.b2b.repository import get_company
from apps.b2b.workspace import repository as repo
from apps.b2b.workspace import storage
from apps.b2b.workspace.consumers import broadcast_deletion, broadcast_message
from apps.b2b.workspace.authentication import (
    DashboardWorkspaceAuthentication,
    WorkspaceJWTAuthentication,
)
from apps.b2b.workspace.permissions import HasCapability, IsWorkspaceManager, IsWorkspaceUser
from apps.b2b.workspace.roles import capabilities_for, is_manager
from apps.b2b.workspace.serializers import (
    WorkspaceFilePatchSerializer,
    WorkspaceFolderListSerializer,
    WorkspaceFolderSerializer,
    WorkspaceFolderWriteSerializer,
    AttendanceCheckInSerializer,
    AttendanceDaySerializer,
    AttendanceLocationSerializer,
    AttendanceLocationUpdateSerializer,
    AttendanceMarkSerializer,
    AttendanceSelfAbsenceSerializer,
    CalendarEventSerializer,
    ChatMessageSerializer,
    ChatThreadSerializer,
    CrmCustomerDetailSerializer,
    CrmCustomerListSerializer,
    CustomerListSerializer,
    EmployeeMonthlyStatSerializer,
    EmployeeOfMonthSelectSerializer,
    EmployeeOfMonthSerializer,
    EventPatchSerializer,
    EventWriteSerializer,
    LeadActivitySerializer,
    LeadAssignWriteSerializer,
    LeadCommentWriteSerializer,
    LeadDetailSerializer,
    LeadItemSerializer,
    LeadItemWriteSerializer,
    LeadListSerializer,
    LeadSerializer,
    LeadStageWriteSerializer,
    LeadWriteSerializer,
    MeSerializer,
    MessageWriteSerializer,
    StorageUsageSerializer,
    SupportMessageCreateSerializer,
    SupportMessageSerializer,
    TaskCommentWriteSerializer,
    TaskListSerializer,
    TaskPatchSerializer,
    TaskSerializer,
    TaskStatusSerializer,
    TaskWriteSerializer,
    TeamMemberSerializer,
    ThreadCreateSerializer,
    ThreadFlagsSerializer,
    WorkspaceFileListSerializer,
    WorkspaceFileSerializer,
    WorkspaceLoginSerializer,
    WorkspaceLoginVerifySerializer,
    WorkspaceRefreshSerializer,
)
from apps.b2b.workspace.geo import distance_meters
from apps.b2b.workspace.tokens import create_workspace_tokens, rotate_workspace_tokens
from apps.hotels.repository import search_hotels
from apps.hotels.serializers import HotelCardSerializer
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

        employee = _resolve_employee(phone)
        is_test_phone = OTPRedisService.is_test_phone_for_purpose(phone, SmsPurpose.B2B_LOGIN)

        if not employee and not is_test_phone:
            return Response(
                {"detail": _("No employee is registered with this phone number.")},
                status=status.HTTP_404_NOT_FOUND,
            )

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

        employee = _resolve_employee(phone)
        if not employee:
            return Response(
                {"phone": [_("No employee is registered with this phone number.")]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ok, message = OTPRedisService.verify_otp(phone, otp, SmsPurpose.B2B_LOGIN)
        if not ok:
            return Response({"otp": [message]}, status=status.HTTP_400_BAD_REQUEST)
        OTPRedisService.consume_otp(phone, SmsPurpose.B2B_LOGIN)

        tokens = create_workspace_tokens(employee)
        return Response({
            "access": tokens["access"],
            "refresh": tokens["refresh"],
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

def _me_payload(employee: dict) -> dict:
    company = get_company(employee["company_id"]) or {}
    return {
        "id": employee["id"],
        "company_id": employee["company_id"],
        "company_name": company.get("name"),
        "full_name": employee.get("full_name"),
        "position": employee.get("position"),
        "role": employee.get("role") or "employee",
        "phone": employee.get("phone"),
        "email": employee.get("email"),
        "photo": employee.get("photo"),
        "department_name": employee.get("department_name"),
        "completed_this_month": repo.completed_tasks_this_month(
            employee["id"], *_current_year_month()
        ),
        "permissions": capabilities_for(employee.get("role")),
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
        return Response(_me_payload(employee))


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
        return Response(TeamMemberSerializer(members, many=True).data)


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


def _task_payload(task: dict, user) -> dict:
    """Adds the per-task permission flags the app uses to decide which buttons
    to render — the same rules the write endpoints enforce."""
    caps = user.capabilities
    is_assignee = user.id in (task.get("assignee_ids") or [])
    is_author = task.get("author_id") == user.id

    return {
        **task,
        "voice": _task_voice_payload(task.get("voice")),
        "can_edit": bool(caps["can_edit_task"]),
        "can_delete": bool(caps["can_delete_task"]),
        # An employee moves only their own work along the board.
        "can_change_status": bool(caps["can_edit_task"] or is_assignee or is_author),
    }


class WorkspaceTaskListCreateView(WorkspaceAPIView):
    """GET  /api/b2b/workspace/tasks/ — tasks the caller may see.
    POST /api/b2b/workspace/tasks/ — managers only."""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="List tasks (employees see only their own)",
        manual_parameters=[
            openapi.Parameter("status", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                              enum=list(repo.TASK_STATUSES)),
            openapi.Parameter("search", openapi.IN_QUERY, type=openapi.TYPE_STRING),
        ],
        responses={200: TaskListSerializer()},
    )
    def get(self, request):
        user = request.user
        scope = user.visible_scope
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
        return Response(_task_payload(task, request.user), status=status.HTTP_201_CREATED)


class WorkspaceTaskActivityFeedView(WorkspaceAPIView):
    """GET /api/b2b/workspace/tasks/activity/

    The company-wide feed the tasks page shows: every create/edit/status/
    assign/delete across every task, newest first — including tasks since
    deleted, since the log outlives the row it was written about.

    Managers (``visible_scope is None``) see everyone's actions; an employee
    sees only their own, the same boundary ``list_tasks`` draws for the task
    list itself.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Company-wide task activity feed",
                         responses={200: openapi.Response(description="Activity feed")})
    def get(self, request):
        user = request.user
        activity = repo.list_company_task_activity(
            user.company_id,
            actor_id=user.id if user.visible_scope is not None else None,
        )
        return Response({"results": activity})


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

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    def _load(self, request, task_id: int):
        task = repo.get_task(task_id, request.user.company_id)
        if not task:
            return None
        if request.user.visible_scope is not None:
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
        if not request.user.capabilities["can_edit_task"]:
            return Response(
                {"detail": _("Your role does not allow editing tasks.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = TaskPatchSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        assignees = data.pop("assignee_ids", None)
        subtasks = data.pop("subtasks", None)

        if assignees is not None:
            checked = _validated_employee_ids(request.user.company_id, assignees)
            if checked is None:
                return Response(
                    {"assignee_ids": [_("Some of these employees are not in your company.")]},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            repo.set_task_assignees(
                task_id, checked,
                company_id=request.user.company_id,
                actor_id=request.user.id,
                task_title=task["title"],
            )

        if subtasks is not None:
            repo.replace_subtasks(task_id, subtasks)

        # Only the columns the caller actually sent — PATCH must not reset the
        # serializer's defaults over fields nobody touched.
        fields = {key: value for key, value in data.items() if key in {
            "title", "description", "status", "priority", "project", "due_date",
        }}
        updated = repo.update_task(
            task_id, request.user.company_id, actor_id=request.user.id, **fields
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

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Comment on a task",
                         request_body=TaskCommentWriteSerializer, responses={201: TaskSerializer()})
    def post(self, request, task_id: int):
        task = repo.get_task(task_id, request.user.company_id)
        if not task:
            return Response({"detail": _("Task not found.")}, status=status.HTTP_404_NOT_FOUND)

        if request.user.visible_scope is not None and not (
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

        Author, assignee or a manager — the same reach the comment endpoint
        allows, because a clip explaining a task is a comment that happens to
        be audio. Anything else gets a 404 rather than a 403: a company's task
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
        return Response(_event_payload(event, request.user), status=status.HTTP_201_CREATED)


class WorkspaceEventDetailView(WorkspaceAPIView):
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


# ─── Chat ─────────────────────────────────────────────────────────────────────

def _thread_payload(thread: dict) -> dict:
    last_id = thread.get("last_message_id")
    return {
        "id": thread["id"],
        "group_name": thread.get("group_name"),
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
        return Response(_thread_payload(thread), status=status.HTTP_201_CREATED)


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


def _message_payload(
    message: dict,
    attachment: dict | None = None,
    replied_to: dict | None = None,
    quoted_attachment: dict | None = None,
) -> dict:
    """One message as the app reads it.

    The attachment and the quote are nested rather than flattened: a message
    has at most one attachment today, but a client that reads `attachment.url`
    keeps working when that becomes a list, whereas one reading
    `attachment_url` does not.
    """
    data = ChatMessageSerializer(message).data
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
    return data


class WorkspaceMessageView(WorkspaceAPIView):
    """GET / POST messages in a thread the caller belongs to."""

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
        if not self._thread(request, thread_id):
            return Response({"detail": _("Chat not found.")}, status=status.HTTP_404_NOT_FOUND)

        try:
            limit = min(int(request.query_params.get("limit", 50)), 200)
        except (TypeError, ValueError):
            limit = 50
        try:
            before_id = int(request.query_params["before_id"])
        except (KeyError, TypeError, ValueError):
            before_id = None

        messages = repo.list_messages(thread_id, before_id=before_id, limit=limit)
        quoted = repo.messages_by_ids(
            [m["reply_to_id"] for m in messages if m.get("reply_to_id")]
        )
        # One query for both: a quoted message is usually already on the page,
        # and asking for its attachment separately would fetch the same rows
        # twice to render one screen.
        attachments = repo.attachments_for_messages(
            [m["id"] for m in messages] + list(quoted)
        )
        # Opening a room is what marks it read, so it happens here rather than
        # costing the phone a second round trip. Only the newest page counts —
        # scrolling back through history must not clear newer messages. The
        # path is exempt from the GET cache (see core/middleware/cache.py), so
        # this write is never skipped by a cache hit.
        if before_id is None:
            repo.mark_thread_read(thread_id, request.user.id)

        return Response({
            "results": [
                _message_payload(
                    m,
                    attachments.get(m["id"]),
                    quoted.get(m.get("reply_to_id")),
                    attachments.get(m.get("reply_to_id")),
                )
                for m in messages
            ],
            "has_more": len(messages) == limit,
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
            # is a real message. Text stays required when there is nothing else
            # in the envelope.
            context={"allow_empty_text": upload is not None},
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

        message = repo.send_message(
            thread_id,
            request.user.id,
            serializer.validated_data["text"],
            reply_to_id=reply_to_id,
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
                serializer.validated_data["text"],
            )
        except Exception:  # noqa: BLE001
            logger.exception("Could not queue chat notification for thread %s", thread_id)

        payload = _message_payload(message, attachment, replied_to, quoted_attachment)
        broadcast_message(thread_id, payload)
        return Response(payload, status=status.HTTP_201_CREATED)


class WorkspaceMessageDetailView(WorkspaceAPIView):
    """DELETE /api/b2b/workspace/chats/<thread_id>/messages/<message_id>/

    Your own message, always. Anyone else's only if you run the company —
    a manager has to be able to take down something posted in a shared room,
    and an employee must not be able to edit the record of what was said.
    """

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


class WorkspaceThreadFlagsView(WorkspaceAPIView):
    """POST /api/b2b/workspace/chats/<id>/flags/ — pin / mute for this member."""

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

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="List leads",
        manual_parameters=[
            openapi.Parameter("status", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                              enum=list(repo.LEAD_STATUSES)),
        ],
        responses={200: LeadListSerializer()},
    )
    def get(self, request):
        leads = repo.list_leads(
            request.user.company_id,
            status=request.query_params.get("status") or None,
            stage=request.query_params.get("stage") or None,
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
        claim_for_author = bool(data.get("assign_to_me", True))

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
            note=data.get("note"),
            claim_for_author=claim_for_author,
        )

        # Only a lead left on the board is news to the rest of the company —
        # one its author already holds is not up for grabs, and telling
        # everyone about it is a notification nobody can act on.
        tokens = (
            []
            if claim_for_author
            else repo.list_employee_fcm_tokens(
                request.user.company_id, exclude_employee_id=request.user.id
            )
        )
        if tokens:
            try:
                from apps.notification.service import FCMService, b2b_firebase_app

                FCMService.send_to_tokens(
                    tokens=tokens,
                    title=_("New lead"),
                    body=f"{data['company_name']} — {data['product_name']} ({data['quantity']})",
                    data={"type": "lead", "lead_id": str(lead["id"])},
                    app=b2b_firebase_app(),
                    deactivate_invalid=repo.clear_employee_fcm_tokens,
                )
            except Exception:
                logger.exception("Failed to push new-lead notification for lead %s.", lead["id"])

        return Response(_lead_payload(lead, request.user), status=status.HTTP_201_CREATED)


class WorkspaceLeadClaimView(WorkspaceAPIView):
    """POST /api/b2b/workspace/leads/<id>/claim/ — any employee takes a 'new' lead."""

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
            "activity": repo.list_lead_activity(lead_id),
            # Hydrated the same way the tasks tab does it, so a checkbox on this
            # screen and the same task on Vazifa agree about who may tick it.
            "tasks": [
                _task_payload(repo.get_task(task["id"], request.user.company_id), request.user)
                for task in tasks
            ],
        })

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Delete a lead (owner or manager)",
        responses={
            204: openapi.Response(description="Deleted"),
            403: openapi.Response(description="Only the owner or a manager may delete this lead"),
            404: openapi.Response(description="Not found"),
        },
    )
    def delete(self, request, lead_id: int):
        lead = repo.get_lead(lead_id, request.user.company_id)
        if not lead:
            return Response({"detail": _("Lead not found.")}, status=status.HTTP_404_NOT_FOUND)
        if not (_works_lead(lead, request.user) or request.user.is_manager):
            return Response(
                {"detail": _("Only the owner or a manager may delete this lead.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        repo.delete_lead(lead_id, request.user.company_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkspaceLeadStageView(WorkspaceAPIView):
    """POST /api/b2b/workspace/leads/<id>/stage/ — move the lead along the funnel.

    The claimant only, and never on a closed lead. Reaching ``won`` or ``lost``
    completes it; that rule lives in the repository so this view does not have
    to know which stages are terminal.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Change a lead's funnel stage",
        request_body=LeadStageWriteSerializer,
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
        updated = repo.set_lead_stage(
            lead_id,
            request.user.company_id,
            stage=serializer.validated_data["stage"],
            employee_id=request.user.id,
            lost_reason=serializer.validated_data.get("lost_reason"),
            note=serializer.validated_data.get("note"),
        )
        return Response(_lead_payload(updated or lead, request.user))


class WorkspaceLeadAssignView(WorkspaceAPIView):
    """POST /api/b2b/workspace/leads/<id>/assign/ — hand the lead to somebody.

    Managers only, and distinct from claiming: claiming is first-come and
    self-service, this takes a lead off one employee and gives it to another.
    """

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
        return Response(activity, status=status.HTTP_201_CREATED)


class WorkspaceLeadItemsView(WorkspaceAPIView):
    """POST   /api/b2b/workspace/leads/<id>/items/ — add a priced line.
    PUT    /api/b2b/workspace/leads/<id>/items/ — replace the whole list.

    Either way the lead's ``amount`` is re-totalled, so the board's money never
    disagrees with the lines it came from.
    """

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
                 folder_id: int | None = None, duration_ms: int | None = None):
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


# ─── Hotels ───────────────────────────────────────────────────────────────────

def _hotel_status(hotel: dict) -> str:
    """Collapses the platform's several hotel flags into the three states the
    mobile list filters on."""
    if hotel.get("is_archived") or not hotel.get("is_active", True):
        return "paused"
    if not hotel.get("is_verified"):
        return "pending"
    return "active"


class WorkspaceHotelListView(WorkspaceAPIView):
    """GET /api/b2b/workspace/hotels/ — partner hotels, shaped for the phone.

    A thin projection of the platform hotel card: the mobile list only renders
    a name, a location, a rating and a starting price, and shipping the full
    card (policies, rate plans, legal info) over mobile data for a list of 20
    would cost far more than it shows.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Partner hotels",
        manual_parameters=[
            openapi.Parameter("city", openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter("search", openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter("limit", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter("offset", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
        ],
        responses={200: openapi.Response(description="Hotel cards")},
    )
    def get(self, request):
        def _int(name: str, default: int, ceiling: int) -> int:
            try:
                return min(max(int(request.query_params.get(name, default)), 0), ceiling)
            except (TypeError, ValueError):
                return default

        limit = _int("limit", 30, 100)
        offset = _int("offset", 0, 10_000)
        city = (request.query_params.get("city") or "").strip() or None
        search = (request.query_params.get("search") or "").strip().lower()

        rows = search_hotels(city=city, sort_by="weel_recommended", limit=limit, offset=offset)

        # Project from the serialized card, never from the raw row. The raw row
        # calls the name `name`, the images `photos`, and carries a description
        # per language — reading `title`, `img` and `description` off it, as
        # this view used to, produced a list where every hotel was nameless and
        # imageless, and where searching by name matched nothing.
        hotels = HotelCardSerializer(rows, many=True, context={"request": request}).data

        if search:
            hotels = [
                hotel for hotel in hotels
                if search in (hotel.get("title") or "").lower()
                or search in (hotel.get("city") or "").lower()
                or search in (hotel.get("full_address") or hotel.get("address") or "").lower()
            ]

        return Response({"results": [{
            "id": hotel.get("guid") or str(hotel.get("id")),
            "name": hotel.get("title"),
            "city": hotel.get("city"),
            "address": hotel.get("full_address") or hotel.get("address"),
            "description": hotel.get("description"),
            "status": _hotel_status(hotel),
            "stars": hotel.get("star_rating") or 0,
            "rating": float(hotel.get("rating") or 0),
            "review_count": hotel.get("review_count") or 0,
            "available_rooms": hotel.get("available_rooms") or 0,
            "min_price": float(hotel["min_price"]) if hotel.get("min_price") is not None else None,
            "amenities": hotel.get("amenities") or [],
            "images": hotel.get("img") or [],
            "is_recommended": bool(hotel.get("is_recommended")),
            "latitude": hotel.get("latitude"),
            "longitude": hotel.get("longitude"),
        } for hotel in hotels]})


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


class WorkspaceEmployeeOfMonthView(WorkspaceAPIView):
    """GET/POST /api/b2b/workspace/employee-of-month/

    Anyone can see this month's pick; only the owner can make or change it.
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="This month's employee of the month",
        responses={200: EmployeeOfMonthSerializer(), 204: "Not chosen yet this month"},
    )
    def get(self, request):
        year, month = _current_year_month()
        winner = repo.get_employee_of_month(request.user.company_id, year, month)
        if not winner:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(EmployeeOfMonthSerializer(winner).data)

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Pick this month's employee of the month (owner only)",
        request_body=EmployeeOfMonthSelectSerializer,
        responses={200: EmployeeOfMonthSerializer(), 403: openapi.Response(description="Owner only")},
    )
    def post(self, request):
        if not request.user.capabilities["can_pick_employee_of_month"]:
            return Response(
                {"detail": _("Only the owner can pick the employee of the month.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = EmployeeOfMonthSelectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        employee_id = serializer.validated_data["employee_id"]

        if employee_id not in repo.employee_ids_in_company(request.user.company_id, [employee_id]):
            return Response(
                {"employee_id": [_("This employee is not in your company.")]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        year, month = _current_year_month()
        winner = repo.set_employee_of_month(
            company_id=request.user.company_id,
            year=year,
            month=month,
            employee_id=employee_id,
            selected_by_id=request.user.id,
        )
        return Response(EmployeeOfMonthSerializer(winner).data)


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
