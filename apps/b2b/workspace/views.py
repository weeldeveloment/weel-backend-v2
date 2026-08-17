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

from apps.b2b.models import LeadStatus
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
    CalendarEventSerializer,
    ChatMessageSerializer,
    ChatThreadSerializer,
    EmployeeMonthlyStatSerializer,
    EmployeeOfMonthSelectSerializer,
    EmployeeOfMonthSerializer,
    EventPatchSerializer,
    EventWriteSerializer,
    LeadListSerializer,
    LeadSerializer,
    LeadWriteSerializer,
    MeSerializer,
    MessageWriteSerializer,
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
    StorageUsageSerializer,
    AttendanceCheckInSerializer,
    AttendanceDaySerializer,
    AttendanceMarkSerializer,
)
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

def _task_payload(task: dict, user) -> dict:
    """Adds the per-task permission flags the app uses to decide which buttons
    to render — the same rules the write endpoints enforce."""
    caps = user.capabilities
    is_assignee = user.id in (task.get("assignee_ids") or [])
    is_author = task.get("author_id") == user.id

    return {
        **task,
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
        return Response(_task_payload(task, request.user))

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
            repo.set_task_assignees(task_id, checked)

        if subtasks is not None:
            repo.replace_subtasks(task_id, subtasks)

        # Only the columns the caller actually sent — PATCH must not reset the
        # serializer's defaults over fields nobody touched.
        fields = {key: value for key, value in data.items() if key in {
            "title", "description", "status", "priority", "project", "due_date",
        }}
        updated = repo.update_task(task_id, request.user.company_id, **fields)
        return Response(_task_payload(updated, request.user))

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Delete a task (owner/manager only)",
                         responses={204: openapi.Response(description="Deleted")})
    def delete(self, request, task_id: int):
        if not request.user.capabilities["can_delete_task"]:
            return Response(
                {"detail": _("Your role does not allow deleting tasks.")},
                status=status.HTTP_403_FORBIDDEN,
            )
        if not repo.delete_task(task_id, request.user.company_id):
            return Response({"detail": _("Task not found.")}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


class WorkspaceTaskStatusView(WorkspaceAPIView):
    """POST /api/b2b/workspace/tasks/<id>/status/

    The one write an employee always has: moving a task they were given from
    todo → in progress → review → done.
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
            task_id, request.user.company_id, status=new_status, completed_at=completed_at
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

        # Finishing the last step nudges the task into review rather than
        # leaving it visually complete but still "in progress".
        updated = repo.get_task(task_id, request.user.company_id)
        steps = updated.get("subtasks") or []
        if steps and all(step["is_done"] for step in steps) and updated["status"] == "in_progress":
            updated = repo.update_task(task_id, request.user.company_id, status="review")

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


def _quote_payload(original: dict | None) -> dict | None:
    if not original:
        return None
    text = original.get("text") or ""
    return {
        "id": original["id"],
        "sender_id": original["sender_id"],
        "text": text[:_QUOTE_LENGTH],
        "is_truncated": len(text) > _QUOTE_LENGTH,
    }


def _message_payload(
    message: dict,
    attachment: dict | None = None,
    replied_to: dict | None = None,
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
    data["reply_to"] = _quote_payload(replied_to)
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
        attachments = repo.attachments_for_messages([m["id"] for m in messages])
        quoted = repo.messages_by_ids(
            [m["reply_to_id"] for m in messages if m.get("reply_to_id")]
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
        if reply_to_id:
            replied_to = repo.get_message(reply_to_id, thread_id)
            if not replied_to:
                return Response(
                    {"reply_to_id": [_("That message is not in this chat.")]},
                    status=status.HTTP_400_BAD_REQUEST,
                )

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

        payload = _message_payload(message, attachment, replied_to)
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

def _lead_payload(lead: dict, user) -> dict:
    """Shapes a lead for one viewer.

    Once someone claims a lead, the contact — the person and phone number an
    employee would actually call — is only sent back to whoever claimed it (and
    to the manager who posted it). Everyone else on the board still sees the
    row (company, product, status) so they know it is taken, just not who to
    call, which is what stops two employees from working the same contact.
    """
    is_owner = lead.get("claimed_by_id") == user.id
    can_view_details = is_owner or user.is_manager
    payload = {
        **lead,
        "can_claim": lead.get("status") == LeadStatus.NEW,
        "can_complete": lead.get("status") == LeadStatus.IN_PROGRESS and is_owner,
        "can_view_details": can_view_details,
    }
    if not can_view_details:
        payload["contact_full_name"] = None
        payload["contact_phone"] = None
    return payload


class WorkspaceDeviceTokenView(WorkspaceAPIView):
    """POST /api/b2b/workspace/me/device-token/ — register this device for push."""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Register the FCM token for push notifications")
    def post(self, request):
        token = (request.data.get("fcm_token") or "").strip() or None
        repo.set_employee_fcm_token(request.user.id, token)
        return Response({"detail": _("Saved")})


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
        )
        return Response({"results": [_lead_payload(lead, request.user) for lead in leads]})

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Create a lead (owner/manager only)",
        request_body=LeadWriteSerializer,
        responses={201: LeadSerializer(), 403: openapi.Response(description="Employees cannot create leads")},
    )
    def post(self, request):
        if not request.user.is_manager:
            return Response(
                {"detail": _("Your role does not allow creating leads.")},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = LeadWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        lead = repo.create_lead(
            company_id=request.user.company_id,
            author_id=request.user.id,
            company_name=data["company_name"],
            contact_full_name=data["contact_full_name"],
            contact_phone=data["contact_phone"],
            product_name=data["product_name"],
            quantity=data["quantity"],
        )

        tokens = repo.list_employee_fcm_tokens(
            request.user.company_id, exclude_employee_id=request.user.id
        )
        if tokens:
            try:
                from apps.notification.service import FCMService

                FCMService.send_to_tokens(
                    tokens=tokens,
                    title=_("New lead"),
                    body=f"{data['company_name']} — {data['product_name']} ({data['quantity']})",
                    data={"type": "lead", "lead_id": str(lead["id"])},
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
                 trip_id: int | None = None, duration_ms: int | None = None):
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

        file, refusal = store_upload(request=request, upload=upload, kind="file")
        if refusal:
            return refusal
        return Response(_file_payload(file), status=status.HTTP_201_CREATED)


class WorkspaceFileDetailView(WorkspaceAPIView):
    """DELETE /api/b2b/workspace/files/<id>/"""

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

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
    """

    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Check yourself in for today",
        request_body=AttendanceCheckInSerializer,
        responses={200: AttendanceDaySerializer()},
    )
    def post(self, request):
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
        )
        return Response(
            AttendanceDaySerializer(
                _attendance_payload(request.user.company_id, today, request.user.id)
            ).data
        )


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
