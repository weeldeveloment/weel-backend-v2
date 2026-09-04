"""``/api/b2b/workspace/calls/`` — the endpoints of TZ §7.

Thin on purpose: every rule lives in `calls.py`, and a view here only reads
the call, checks the caller is signed in, and turns a `CallError` into the
status code it names. The module gate is applied per call rather than per
view, because a sales guest with no chat grant may still ring a lead.
"""
from __future__ import annotations

from django.utils.translation import gettext_lazy as _
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.b2b.workspace import calls
from apps.b2b.workspace import calls_repository as calls_repo
from apps.b2b.workspace.calls_repository import CallSource, CallType
from apps.b2b.workspace.permissions import HasModule, IsWorkspaceUser
from apps.b2b.workspace.secondment import Module
from apps.b2b.workspace.views import WORKSPACE_TAG, WorkspaceAPIView


class CallCreateSerializer(serializers.Serializer):
    type = serializers.ChoiceField(choices=CallType.CHOICES, default=CallType.VIDEO)
    source_module = serializers.ChoiceField(choices=CallSource.CHOICES, default=CallSource.CHAT)
    thread_id = serializers.IntegerField(required=False, allow_null=True)
    target_employee_id = serializers.IntegerField(required=False, allow_null=True)
    lead_id = serializers.IntegerField(required=False, allow_null=True)
    customer_id = serializers.IntegerField(required=False, allow_null=True)


class CallSerializer(serializers.Serializer):
    """For the schema only — the payload is built in `calls.payload`."""

    id = serializers.IntegerField()
    room_name = serializers.CharField()
    type = serializers.CharField()
    source_module = serializers.CharField()
    status = serializers.CharField()
    thread_id = serializers.IntegerField(allow_null=True)
    lead_id = serializers.IntegerField(allow_null=True)
    customer_id = serializers.IntegerField(allow_null=True)
    started_at = serializers.DateTimeField()
    answered_at = serializers.DateTimeField(allow_null=True)
    ended_at = serializers.DateTimeField(allow_null=True)
    duration_seconds = serializers.IntegerField(allow_null=True)
    server_url = serializers.CharField()
    ring_timeout_seconds = serializers.IntegerField()
    token = serializers.CharField(allow_null=True)
    token_expires_at = serializers.DateTimeField(allow_null=True)
    guest_link = serializers.CharField(allow_null=True)


def _refused(error: calls.CallError) -> Response:
    return Response({"detail": error.detail}, status=error.status)


def _module_for(source_module: str) -> str:
    return Module.CHAT if source_module == CallSource.CHAT else Module.SALES


class _CallView(WorkspaceAPIView):
    permission_classes = [IsAuthenticated, IsWorkspaceUser]

    def _call(self, request, call_id: int):
        call = calls_repo.get_call(call_id, request.user.company_id)
        if not call or not calls.is_participant(call, request.user.id):
            return None
        # Settled before it is read: a ring that outlived its window is a
        # missed call whatever the row still says.
        return calls.settle(call)


class WorkspaceCallListCreateView(_CallView):
    """POST /calls/ — start a call and ring the other side."""

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Start a video/audio call (Jitsi room + JWT)",
        request_body=CallCreateSerializer,
        responses={201: CallSerializer(), 409: "Busy", 503: "Jitsi not configured"},
    )
    def post(self, request):
        serializer = CallCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # The module gate, by hand: the view cannot declare one module when
        # the call may belong to either of two.
        request_module = _module_for(data["source_module"])
        gate = HasModule()
        self.required_module = request_module
        if not gate.has_permission(request, self):
            return Response({"detail": gate.message}, status=status.HTTP_403_FORBIDDEN)

        try:
            payload = calls.start(
                user=request.user,
                call_type=data["type"],
                source_module=data["source_module"],
                thread_id=data.get("thread_id"),
                target_employee_id=data.get("target_employee_id"),
                lead_id=data.get("lead_id"),
                customer_id=data.get("customer_id"),
            )
        except calls.CallError as error:
            return _refused(error)
        return Response(payload, status=status.HTTP_201_CREATED)


class WorkspaceCallDetailView(_CallView):
    """GET /calls/<id>/ — where the call stands now. The phone polls this
    when its socket is down, and on resume for a ring it may have missed."""

    @swagger_auto_schema(tags=WORKSPACE_TAG, responses={200: CallSerializer()})
    def get(self, request, call_id: int):
        call = self._call(request, call_id)
        if not call:
            return Response({"detail": _("Call not found.")}, status=status.HTTP_404_NOT_FOUND)
        return Response(calls.payload(call))


class WorkspaceCallAcceptView(_CallView):
    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Accept an incoming call — returns this side's JWT",
        responses={200: CallSerializer(), 409: "Already settled"},
    )
    def post(self, request, call_id: int):
        call = self._call(request, call_id)
        if not call:
            return Response({"detail": _("Call not found.")}, status=status.HTTP_404_NOT_FOUND)
        try:
            return Response(calls.accept(call, request.user))
        except calls.CallError as error:
            return _refused(error)


class WorkspaceCallDeclineView(_CallView):
    @swagger_auto_schema(tags=WORKSPACE_TAG, responses={200: CallSerializer()})
    def post(self, request, call_id: int):
        call = self._call(request, call_id)
        if not call:
            return Response({"detail": _("Call not found.")}, status=status.HTTP_404_NOT_FOUND)
        try:
            return Response(calls.decline(call, request.user))
        except calls.CallError as error:
            return _refused(error)


class WorkspaceCallEndView(_CallView):
    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        operation_summary="Hang up (cancels a call that is still ringing)",
        responses={200: CallSerializer()},
    )
    def post(self, request, call_id: int):
        call = self._call(request, call_id)
        if not call:
            return Response({"detail": _("Call not found.")}, status=status.HTTP_404_NOT_FOUND)
        try:
            return Response(calls.end(call, request.user))
        except calls.CallError as error:
            return _refused(error)


class WorkspaceCallTokenView(_CallView):
    """GET /calls/<id>/token/ — a fresh JWT for a call still in progress."""

    @swagger_auto_schema(tags=WORKSPACE_TAG, responses={200: CallSerializer()})
    def get(self, request, call_id: int):
        call = self._call(request, call_id)
        if not call:
            return Response({"detail": _("Call not found.")}, status=status.HTTP_404_NOT_FOUND)
        try:
            return Response(calls.fresh_token(call, request.user))
        except calls.CallError as error:
            return _refused(error)


class WorkspaceCallIncomingView(_CallView):
    """GET /calls/incoming/ — the call ringing at me right now, if any.

    Asked on every app resume. A push can be dropped and a socket can be
    down; this is the third path, and the one that cannot be missed.
    """

    @swagger_auto_schema(tags=WORKSPACE_TAG, responses={200: CallSerializer(), 204: "Nothing ringing"})
    def get(self, request):
        call = calls_repo.ringing_for(request.user.id)
        if call:
            call = calls.settle(call)
        if not call or call["status"] != calls_repo.CallStatus.RINGING:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(calls.payload(call))


class WorkspaceCallHistoryView(_CallView):
    """GET /calls/history/?thread_id= | lead_id= | customer_id= — newest first.
    With no filter, the caller's own calls."""

    @swagger_auto_schema(
        tags=WORKSPACE_TAG,
        manual_parameters=[
            openapi.Parameter("thread_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter("lead_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter("customer_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter("before_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter("limit", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
        ],
        responses={200: CallSerializer(many=True)},
    )
    def get(self, request):
        def _int(name):
            raw = request.query_params.get(name)
            try:
                return int(raw) if raw not in (None, "") else None
            except (TypeError, ValueError):
                return None

        thread_id, lead_id, customer_id = _int("thread_id"), _int("lead_id"), _int("customer_id")
        limit = min(_int("limit") or 50, 200)
        # A card's own history, or mine. Never the whole company's: a call
        # between two colleagues is theirs.
        employee_id = None if (thread_id or lead_id or customer_id) else request.user.id
        rows = calls_repo.list_history(
            request.user.company_id,
            thread_id=thread_id,
            lead_id=lead_id,
            customer_id=customer_id,
            employee_id=employee_id,
            before_id=_int("before_id"),
            limit=limit,
        )
        if thread_id:
            # A thread's calls are visible only to its members.
            rows = [r for r in rows if calls.is_participant(r, request.user.id)]
        cards = calls_repo.employee_cards(
            [i for r in rows for i in (r.get("initiator_id"), r.get("target_employee_id"))]
        )
        return Response({
            "results": [calls.payload(calls.settle(r), cards=cards) for r in rows],
            "has_more": len(rows) == limit,
        })
