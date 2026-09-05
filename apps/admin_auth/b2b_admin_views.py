from __future__ import annotations

from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

from shared.raw.db import fetch_all

from .authentication import AdminJWTAuthentication
from .permissions import IsAdminUser

from apps.b2b.workspace import repository as workspace_repo
from apps.b2b.workspace import access_repository as access_repo
from apps.b2b.workspace.serializers import (
    SupportMessageCreateSerializer,
    SupportMessageSerializer,
    SupportThreadSerializer,
)

from apps.b2b.repository import list_b2b_users, get_company, create_company, create_b2b_user, get_b2b_user_by_phone
from apps.b2b.serializers import B2BCompanySerializer, B2BUserSerializer


class AdminBaseView(APIView):
    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]


class AdminB2BCompaniesView(AdminBaseView):
    """List/create B2B companies — admin view"""

    @swagger_auto_schema(responses={200: B2BCompanySerializer(many=True)})
    def get(self, request):
        companies = fetch_all("SELECT * FROM b2b_company WHERE is_active = TRUE ORDER BY name ASC")
        return Response(B2BCompanySerializer(companies, many=True).data)

    @swagger_auto_schema(request_body=B2BCompanySerializer, responses={201: B2BCompanySerializer()})
    def post(self, request):
        serializer = B2BCompanySerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        company = create_company(**serializer.validated_data)
        return Response(B2BCompanySerializer(company).data, status=status.HTTP_201_CREATED)


class AdminB2BCompanyDetailView(AdminBaseView):

    @swagger_auto_schema(responses={200: B2BCompanySerializer()})
    def get(self, request, company_id):
        company = get_company(company_id)
        if not company:
            return Response({"detail": "Company not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(B2BCompanySerializer(company).data)


class AdminB2BUsersView(AdminBaseView):

    @swagger_auto_schema(responses={200: B2BUserSerializer(many=True)})
    def get(self, request, company_id):
        users = list_b2b_users(company_id)
        return Response(B2BUserSerializer(users, many=True).data)

    @swagger_auto_schema(request_body=B2BUserSerializer, responses={201: B2BUserSerializer()})
    def post(self, request, company_id):
        company = get_company(company_id)
        if not company:
            return Response({"detail": "Company not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = B2BUserSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        if get_b2b_user_by_phone(serializer.validated_data["phone"]):
            return Response(
                {"detail": "Bu telefon raqam bilan foydalanuvchi allaqachon mavjud."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = create_b2b_user(company_id=company_id, **serializer.validated_data)
        return Response(B2BUserSerializer(user).data, status=status.HTTP_201_CREATED)


class AdminB2BSupportThreadsView(AdminBaseView):
    """GET ``/api/admin-auth/b2b/support/`` — the inbox.

    One row per employee who has written in, newest first, with the count of
    their own lines nobody has answered yet. Across every company: this is
    WEEL's own desk, not a company's.
    """

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter(
                "search", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                description="Employee name, company name or phone.",
            ),
        ],
        responses={200: SupportThreadSerializer(many=True)},
    )
    def get(self, request):
        threads = workspace_repo.list_support_threads(
            search=(request.query_params.get("search") or "").strip() or None,
        )
        return Response(SupportThreadSerializer(threads, many=True).data)


class AdminB2BSupportThreadView(AdminBaseView):
    """GET/POST ``/api/admin-auth/b2b/support/<employee_id>/`` — one
    conversation, and the reply into it.

    Reading marks the employee's lines answered, which is what clears them off
    the inbox counter. A reply is stored with ``is_staff`` set here rather than
    taken from the body, the same way the app cannot claim to be support.
    """

    @swagger_auto_schema(responses={200: SupportMessageSerializer(many=True), 404: "Unknown employee"})
    def get(self, request, employee_id):
        if not workspace_repo.support_employee(employee_id):
            return Response({"detail": "Employee not found."}, status=status.HTTP_404_NOT_FOUND)

        messages = workspace_repo.list_support_messages(employee_id)
        workspace_repo.mark_support_answered(employee_id)
        return Response(SupportMessageSerializer(messages, many=True).data)

    @swagger_auto_schema(
        request_body=SupportMessageCreateSerializer,
        responses={201: SupportMessageSerializer(), 404: "Unknown employee"},
    )
    def post(self, request, employee_id):
        employee = workspace_repo.support_employee(employee_id)
        if not employee:
            return Response({"detail": "Employee not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = SupportMessageCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        message = workspace_repo.create_support_message(
            # The company comes off the employee, never off the request: a
            # reply must land in the same thread the question came from.
            company_id=employee["company_id"],
            employee_id=employee_id,
            text=serializer.validated_data["text"],
            is_staff=True,
            # Deliberately null. `author_user_id` references `b2b_user`, and the
            # person replying here is a WEEL admin from `users` — a different table
            # with its own id space. Passing `request.user.id` violated the foreign
            # key (a 500 on every reply) or, where the two ids happened to collide,
            # credited the message to an unrelated B2B user. `is_staff` is what marks
            # a line as support's; there is no correct value for this column.
            author_user_id=None,
        )
        workspace_repo.mark_support_answered(employee_id)
        return Response(
            SupportMessageSerializer(message).data, status=status.HTTP_201_CREATED,
        )


class OwnershipRequestDecisionSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=["approve", "reject"])
    note = serializers.CharField(required=False, allow_blank=True, default="")


class AdminB2BOwnershipRequestsView(AdminBaseView):
    """GET ``/api/admin-auth/b2b/ownership-requests/`` — every company asking
    to hand itself over or close, waiting on WEEL.

    An owner cannot transfer or close a Company by asking their own workspace
    — see `WorkspaceOwnershipRequestView` — precisely so that a decision this
    consequential always has someone outside the company looking at it. This
    is that someone's inbox.
    """

    def get(self, request):
        return Response({"results": access_repo.list_pending_ownership_requests()})


class AdminB2BOwnershipRequestView(AdminBaseView):
    """POST ``/api/admin-auth/b2b/ownership-requests/<id>/decide/`` —
    approve or reject one.

    Approving is what actually moves the `owner` role or closes the company;
    there is no separate "apply" step, because a request marked approved that
    had not yet been carried out is exactly the kind of row that survives a
    crash and quietly never happens.
    """

    @swagger_auto_schema(
        request_body=OwnershipRequestDecisionSerializer,
        responses={200: "Decided", 404: "Request not found or already decided"},
    )
    def post(self, request, request_id: int):
        serializer = OwnershipRequestDecisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        decided = access_repo.decide_ownership_request(
            request_id,
            approve=serializer.validated_data["action"] == "approve",
            reviewer_user_id=request.user.id,
            note=serializer.validated_data.get("note", ""),
        )
        if not decided:
            return Response(
                {"detail": "Not found, or already decided."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(decided)
