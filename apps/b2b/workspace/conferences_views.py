"""``/api/b2b/workspace/conferences/`` — the endpoints behind the "+" menu.

Thin like `calls_views`: every rule lives in `conferences.py`, and a view
here only loads the row, checks the person is signed in, and turns a
`CallError` into the status code it names.

Opening one needs no capability at all, by the owner's decision: anybody in
the workspace may call a meeting. It used to require `can_create_group_chat`,
on the reasoning that a conference *is* a group — which is true of the row it
writes and false of what the feature is for. The group it opens is the room
the invitation lands in, not a group the person went out to create.
"""
from __future__ import annotations

from drf_yasg.utils import swagger_auto_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.b2b.workspace import conferences
from apps.b2b.workspace import conferences_repository as conf_repo
from apps.b2b.workspace.calls import CallError
from apps.b2b.workspace.conferences_repository import ConferenceScope
from apps.b2b.workspace.permissions import IsWorkspaceUser
from apps.b2b.workspace.secondment import Module
from apps.b2b.workspace.views import WORKSPACE_TAG, WorkspaceAPIView


class ConferenceCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200, required=False, allow_blank=True)
    scope = serializers.ChoiceField(choices=ConferenceScope.CHOICES)
    department_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, allow_empty=True
    )
    employee_ids = serializers.ListField(
        child=serializers.IntegerField(), required=False, allow_empty=True
    )


class ConferenceSerializer(serializers.Serializer):
    """For the schema only — the payload is built in `conferences.payload`."""

    id = serializers.IntegerField()
    room_name = serializers.CharField()
    title = serializers.CharField()
    thread_id = serializers.IntegerField()
    message_id = serializers.IntegerField(allow_null=True)
    scope = serializers.CharField()
    status = serializers.CharField()
    created_by = serializers.IntegerField()
    started_at = serializers.DateTimeField()
    ended_at = serializers.DateTimeField(allow_null=True)
    provider = serializers.CharField()
    server_url = serializers.CharField()
    token = serializers.CharField(allow_null=True)
    token_expires_at = serializers.DateTimeField(allow_null=True)


def _refused(error: CallError) -> Response:
    return Response({"detail": error.detail}, status=error.status)


class _ConferenceView(WorkspaceAPIView):
    required_module = Module.CHAT
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    def _conference(self, request, conference_id: int):
        conference = conf_repo.get_conference(conference_id, request.user.company_id)
        if not conference:
            return None
        # Settled before it is read: one that outlived the maximum duration is
        # over whatever the row still says.
        return conferences.settle(conference)


class WorkspaceConferenceListCreateView(_ConferenceView):
    """POST /conferences/ — open a conference and invite people into it."""

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Open a conference (group + room + invitation card)",
        request_body=ConferenceCreateSerializer,
        responses={201: ConferenceSerializer(), 403: "Not allowed", 503: "Not configured"},
    )
    def post(self, request):
        serializer = ConferenceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            payload = conferences.create(
                request.user,
                title=data.get("title") or "",
                scope=data["scope"],
                department_ids=data.get("department_ids"),
                employee_ids=data.get("employee_ids"),
            )
        except CallError as error:
            return _refused(error)
        return Response(payload, status=status.HTTP_201_CREATED)


class WorkspaceConferenceDetailView(_ConferenceView):
    """GET /conferences/<id>/ — where it stands now. The app asks on resume,
    and when its socket has been down long enough to have missed the ending."""

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="One conference",
        responses={200: ConferenceSerializer(), 404: "Not found"},
    )
    def get(self, request, conference_id: int):
        conference = self._conference(request, conference_id)
        if not conference:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(conferences.payload(conference))


class WorkspaceConferenceJoinView(_ConferenceView):
    """POST /conferences/<id>/join/ — this person's own token for the room."""

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Join a conference",
        request_body=None,
        responses={200: ConferenceSerializer(), 409: "Already over", 403: "Not invited"},
    )
    def post(self, request, conference_id: int):
        conference = self._conference(request, conference_id)
        if not conference:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            return Response(conferences.join(conference, request.user))
        except CallError as error:
            return _refused(error)


class WorkspaceConferenceEndView(_ConferenceView):
    """POST /conferences/<id>/end/ — the organiser closes it for everybody."""

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="End a conference",
        request_body=None,
        responses={200: ConferenceSerializer(), 403: "Not the organiser"},
    )
    def post(self, request, conference_id: int):
        conference = self._conference(request, conference_id)
        if not conference:
            return Response(status=status.HTTP_404_NOT_FOUND)
        try:
            payload = conferences.end(conference, request.user)
        except CallError as error:
            return _refused(error)
        # Already closed by the sweep or another device: the room is shut
        # either way, so the answer is the row rather than a refusal.
        return Response(payload or conferences.payload(conference))
