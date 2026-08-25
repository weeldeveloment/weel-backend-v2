from __future__ import annotations

import hashlib
import logging
import random
import uuid
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from django.core.cache import cache
from django.core.files.storage import default_storage
from django.db import IntegrityError
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from shared.throttles import ResilientScopedRateThrottle

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema

from apps.b2b.repository import (
    add_trip_employee,
    create_budget_request,
    create_company,
    create_b2b_user,
    create_department,
    create_employee,
    create_lead_request,
    create_policy_rule,
    create_trip,
    create_voucher,
    delete_department,
    delete_employee,
    delete_policy_rule,
    delete_trip,
    count_department_employees,
    deactivate_department_employees,
    move_department_employees,
    update_department,
    get_company,
    get_dashboard_summary,
    get_department_monthly_spending,
    list_dashboard_notifications,
    get_department_spending,
    get_employee,
    get_trip_status_summary,
    get_or_create_travel_policy,
    get_policy_rule,
    get_rule_used_amount,
    get_monthly_spending_chart,
    get_spending_chart,
    get_weekly_spending_chart,
    get_spending_overview,
    get_top_employees_by_trip_count,
    get_trip,
    get_voucher,
    count_active_trip_employees,
    list_active_trip_employees,
    list_budget_requests,
    list_transactions,
    list_departments,
    list_departments_with_budget,
    list_employees,
    list_employees_with_individual_limit,
    list_policy_rules,
    list_policy_rules_by_type,
    list_recent_trip_employees,
    list_trip_employees,
    list_trips,
    review_budget_request,
    revoke_b2b_login_by_phone,
    sync_performer_b2b_login,
    update_company,
    update_employee,
    update_policy_rule,
    update_travel_policy,
    update_trip,
)
from apps.b2b.models import B2BUserRole, EmployeeRole, TripEmployeeStatus, compute_budget_status
from apps.b2b.permissions import IsB2BOwner, IsB2BOwnerOrPerformer
from apps.b2b.tasks import _send_b2b_lead_telegram_notification, run_passport_ocr_job
from apps.b2b.serializers import (
    ActiveTripEmployeeSerializer,
    ActiveTripEmployeesResponseSerializer,
    B2BCompanySerializer,
    B2BDepartmentSerializer,
    B2BDepartmentSummarySerializer,
    B2BDepartmentUpdateSerializer,
    B2BDepartmentMoveEmployeesSerializer,
    B2BEmployeeCreateSerializer,
    B2BEmployeeLimitSerializer,
    B2BEmployeePassportPreviewSerializer,
    B2BEmployeeSerializer,
    B2BUserSerializer,
    BudgetRequestListResponseSerializer,
    TransactionSerializer,
    TransactionListResponseSerializer,
    BudgetRequestSerializer,
    BusinessTripSerializer,
    DashboardNotificationSerializer,
    DashboardSummarySerializer,
    DepartmentMonthlySpendingSerializer,
    StatisticsResponseSerializer,
    MonthlySpendingChartResponseSerializer,
    StatisticsChartResponseSerializer,
    WeeklySpendingChartResponseSerializer,
    B2BLeadRequestSerializer,
    RecentTripEmployeeSerializer,
    ReviewBudgetRequestSerializer,
    TopEmployeeByTripsSerializer,
    TripStatusSummarySerializer,
    TravelPolicyRuleCreateSerializer,
    TravelPolicyRuleSerializer,
    TravelPolicyRuleUpdateSerializer,
    TravelPolicySerializer,
    TravelVoucherSerializer,
    TripEmployeeSerializer,
)

logger = logging.getLogger(__name__)


def _get_company_id(request) -> int | None:
    user = getattr(request, "user", None)
    if isinstance(user, dict):
        return user.get("company_id")
    return getattr(user, "company_id", None)


def _get_user_id(request) -> int | None:
    user = getattr(request, "user", None)
    if isinstance(user, dict):
        return user.get("id")
    return getattr(user, "id", None)


def _get_b2b_user_id(request) -> int | None:
    """The caller's ``b2b_user.id``, or None when they signed in through the
    workspace.

    Two different accounts reach these endpoints. A ``B2BAuthUser`` is a row in
    ``b2b_user``; a ``WorkspaceUser`` is an employee, and its ``id`` is a
    ``b2b_employee`` row — the two id spaces are unrelated, and no column links
    them. ``created_by``/``requested_by``/``reviewed_by`` are foreign keys into
    ``b2b_user``, so handing them an employee id is a constraint violation
    rather than attribution: the mobile workspace app died with a 500 on every
    hotel booking, at the trip it creates first. All three columns are
    nullable, so an unattributed row is the honest answer here.
    """
    user = getattr(request, "user", None)
    # WorkspaceUser is the only account carrying employee_id; B2BAuthUser and
    # the dict form (both b2b_user-backed) do not.
    if getattr(user, "employee_id", None) is not None:
        return None
    return _get_user_id(request)


class B2BCompanyView(APIView):
    """Company settings — owner-only. A performer has no business reason to
    view or change company-wide settings, so this is locked down at the API
    level too (not just hidden in the sidebar)."""
    permission_classes = [IsAuthenticated, IsB2BOwner]

    @swagger_auto_schema(responses={200: B2BCompanySerializer()})
    def get(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        company = get_company(company_id)
        if not company:
            return Response({"detail": "Company not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(B2BCompanySerializer(company).data)

    @swagger_auto_schema(request_body=B2BCompanySerializer, responses={200: B2BCompanySerializer()})
    def patch(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        serializer = B2BCompanySerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        company = update_company(company_id, **serializer.validated_data)
        if not company:
            return Response({"detail": "Company not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(B2BCompanySerializer(company).data)


class B2BDepartmentListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="List departments",
        operation_description=(
            "Return each department's owner-defined budget limit "
            "(`budget_limit`), used amount (`used_amount`), remaining amount "
            "(`remaining_amount`), status (`status`), and assigned employees "
            "(`employees`). `status` is derived from the remaining budget: "
            "`high` means more than 25% remains, `low` means 25% or less "
            "(but not zero), `empty` means nothing remains or the limit was "
            "exceeded, and `no_limit` means no limit is set for the department."
        ),
        manual_parameters=[
            openapi.Parameter("search", openapi.IN_QUERY, type=openapi.TYPE_STRING, required=False),
            openapi.Parameter("month", openapi.IN_QUERY, type=openapi.TYPE_STRING, required=False, description="YYYY-MM; scopes used_amount to that month"),
        ],
        responses={200: B2BDepartmentSummarySerializer(many=True)},
    )
    def get(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)

        search = request.query_params.get("search")
        month = request.query_params.get("month")
        depts = list_departments_with_budget(company_id, search=search, month=month)
        employees_by_dept: dict[int, list[dict[str, Any]]] = {}
        for emp in list_employees(company_id):
            employees_by_dept.setdefault(emp["department_id"], []).append(emp)

        payload = []
        for d in depts:
            budget_limit = d["budget_limit"]
            used_amount = d["used_amount"]
            remaining_amount = None if budget_limit is None else budget_limit - used_amount
            dept_status = compute_budget_status(budget_limit, used_amount)
            payload.append({
                "id": d["department_id"],
                "company_id": d["company_id"],
                "name": d["department_name"],
                "color": d["color"],
                "budget_limit": budget_limit,
                "used_amount": used_amount,
                "on_trip_amount": d["on_trip_amount"],
                "remaining_amount": remaining_amount,
                "status": dept_status,
                "employees": employees_by_dept.get(d["department_id"], []),
                "created_at": d["created_at"],
            })
        return Response(B2BDepartmentSummarySerializer(payload, many=True).data)

    @swagger_auto_schema(request_body=B2BDepartmentSerializer, responses={201: B2BDepartmentSerializer()})
    def post(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        serializer = B2BDepartmentSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        dept = create_department(
            company_id=company_id,
            name=serializer.validated_data["name"],
            color=serializer.validated_data.get("color"),
        )
        if not dept:
            return Response({"detail": "Failed to create department."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(B2BDepartmentSerializer(dept).data, status=status.HTTP_201_CREATED)


class B2BDepartmentDetailView(APIView):
    """PATCH / DELETE /b2b/departments/<id>/ — owner or performer.

    PATCH renames the department and/or changes its color badge.
    DELETE removes it, but only once it has no active employees left — the
    FK is ``ON DELETE SET NULL``, not cascade, so deleting a department that
    still has people in it would silently orphan them. Use
    ``POST /b2b/departments/<id>/move-employees/`` to relocate them first.
    """
    permission_classes = [IsAuthenticated, IsB2BOwnerOrPerformer]

    @swagger_auto_schema(
        operation_summary="Rename or recolor a department",
        request_body=B2BDepartmentUpdateSerializer,
        responses={200: B2BDepartmentSerializer(), 404: openapi.Response(description="Department not found.")},
    )
    def patch(self, request, department_id):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        serializer = B2BDepartmentUpdateSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        if not serializer.validated_data:
            return Response({"detail": "Nothing to update."}, status=status.HTTP_400_BAD_REQUEST)
        dept = update_department(department_id, company_id, **serializer.validated_data)
        if not dept:
            return Response({"detail": "Department not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(B2BDepartmentSerializer(dept).data)

    @swagger_auto_schema(
        operation_summary="Delete a department",
        operation_description=(
            "Fails with 400 if the department still has active employees, "
            "unless `with_employees=true` is passed — that also deactivates "
            "every employee still in it (same as `DELETE /b2b/employees/<id>/` "
            "would, just for the whole department at once) before removing "
            "the department itself. To keep the employees instead, move them "
            "first via `POST /b2b/departments/<id>/move-employees/`."
        ),
        manual_parameters=[
            openapi.Parameter(
                "with_employees", openapi.IN_QUERY, type=openapi.TYPE_BOOLEAN, required=False,
                description="Also deactivate the department's employees instead of blocking the delete.",
            ),
        ],
        responses={
            204: openapi.Response(description="Deleted."),
            400: openapi.Response(description="Department still has employees."),
            404: openapi.Response(description="Department not found."),
        },
    )
    def delete(self, request, department_id):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        if not update_department(department_id, company_id):
            return Response({"detail": "Department not found."}, status=status.HTTP_404_NOT_FOUND)
        with_employees = str(request.query_params.get("with_employees", "")).lower() in {"1", "true", "yes"}
        employee_count = count_department_employees(department_id)
        if employee_count > 0:
            if not with_employees:
                return Response(
                    {"detail": "Department still has employees.", "employee_count": employee_count},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            deactivate_department_employees(department_id, company_id)
        delete_department(department_id, company_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class B2BDepartmentMoveEmployeesView(APIView):
    """POST /b2b/departments/<id>/move-employees/ — owner or performer.

    Reassigns every employee of department <id> to `target_department_id`,
    then deletes <id> — the "delete a department without losing its
    employees" flow: move everyone out first, source department goes away
    right after since there's nothing left to keep it around for.
    """
    permission_classes = [IsAuthenticated, IsB2BOwnerOrPerformer]

    @swagger_auto_schema(
        operation_summary="Move a department's employees out, then delete it",
        request_body=B2BDepartmentMoveEmployeesSerializer,
        responses={
            200: openapi.Response(description="Employees moved and department deleted."),
            400: openapi.Response(description="Validation error."),
            404: openapi.Response(description="Source or target department not found."),
        },
    )
    def post(self, request, department_id):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        serializer = B2BDepartmentMoveEmployeesSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        target_id = serializer.validated_data["target_department_id"]
        if target_id == department_id:
            return Response({"detail": "Target department must be different."}, status=status.HTTP_400_BAD_REQUEST)
        source = update_department(department_id, company_id)
        if not source:
            return Response({"detail": "Department not found."}, status=status.HTTP_404_NOT_FOUND)
        target = update_department(target_id, company_id)
        if not target:
            return Response({"detail": "Target department not found."}, status=status.HTTP_404_NOT_FOUND)
        moved_count = move_department_employees(
            from_department_id=department_id, to_department_id=target_id, company_id=company_id
        )
        delete_department(department_id, company_id)
        return Response({
            "moved_count": moved_count,
            "source_name": source["name"],
            "target_name": target["name"],
        })


class B2BEmployeeLimitsView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="List employees with a personal limit",
        operation_description=(
            "Return every active employee who has a personal budget "
            "(`individual_limit`) set, together with how much of it has "
            "been used (`used_amount`, scoped to `month` when given), the "
            "remaining amount, and a status derived the same way as "
            "department status: `high` (more than 25% remains), `low` "
            "(25% or less), `empty` (nothing remains)."
        ),
        manual_parameters=[
            openapi.Parameter("search", openapi.IN_QUERY, type=openapi.TYPE_STRING, required=False),
            openapi.Parameter("month", openapi.IN_QUERY, type=openapi.TYPE_STRING, required=False, description="YYYY-MM; scopes used_amount to that month"),
        ],
        responses={200: B2BEmployeeLimitSerializer(many=True)},
    )
    def get(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)

        search = request.query_params.get("search")
        month = request.query_params.get("month")
        employees = list_employees_with_individual_limit(company_id, search=search, month=month)

        payload = []
        for e in employees:
            limit = e["individual_limit"]
            used = e["used_amount"]
            payload.append({
                **e,
                "remaining_amount": limit - used,
                "status": compute_budget_status(limit, used),
            })
        return Response(B2BEmployeeLimitSerializer(payload, many=True).data)


# Employees are now entered by hand, so nothing on the create path runs OCR.
# The passport-preview endpoints below stay as a standalone service (they
# store nothing and create nobody), and this is how long their job result is
# kept for the client to poll.
PASSPORT_OCR_CACHE_TTL_SECONDS = 600


def _file_digest(file_obj) -> str:
    file_obj.seek(0)
    digest = hashlib.sha256(file_obj.read()).hexdigest()
    file_obj.seek(0)
    return digest


def _is_permanent_performer(employee: dict) -> bool:
    """Whether this row is the workspace's own manager.

    A workspace has one — the employee who also holds the dashboard login, so
    a second cannot be created. Guests are excluded from that count on
    purpose: somebody lent here for a fortnight with the "Manager" standing is
    working this workspace's board, not holding its web login, and counting
    them would leave the workspace unable to hire a manager of its own until
    the secondment ended.
    """
    return employee.get("role") == EmployeeRole.PERFORMER and not employee.get(
        "is_guest"
    )


class B2BEmployeeListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(responses={200: B2BEmployeeSerializer(many=True)})
    def get(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        search = request.query_params.get("search")
        employees = list_employees(company_id, search=search)
        return Response(B2BEmployeeSerializer(employees, many=True).data)

    @swagger_auto_schema(
        operation_summary="Add a new employee",
        operation_description=(
            "Adds a new employee to the company. All of `first_name`, "
            "`last_name`, `passport_series`, `passport_pinfl`, "
            "`department_id`, `email` and `phone` are required and entered "
            "by hand. `first_name` and `last_name` are stored joined as "
            "`full_name`."
        ),
        consumes=["multipart/form-data"],
        manual_parameters=[
            openapi.Parameter("last_name", openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="Surname (required)"),
            openapi.Parameter("first_name", openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="Given name (required)"),
            openapi.Parameter("passport_series", openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="ID card / passport number, format AA1234567"),
            openapi.Parameter("passport_pinfl", openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="PINFL — 14 digits"),
            openapi.Parameter("department_id", openapi.IN_FORM, type=openapi.TYPE_INTEGER, required=True, description="Department ID (required)"),
            openapi.Parameter("email", openapi.IN_FORM, type=openapi.TYPE_STRING, format=openapi.FORMAT_EMAIL, required=True, description="Email address (required)"),
            openapi.Parameter("phone", openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="Phone number (required)"),
            openapi.Parameter("photo", openapi.IN_FORM, type=openapi.TYPE_FILE, required=False, description="Employee profile photo (jpg, png; max 5MB, optional)"),
            openapi.Parameter("position", openapi.IN_FORM, type=openapi.TYPE_STRING, required=False, description="Job title"),
            openapi.Parameter("individual_limit", openapi.IN_FORM, type=openapi.TYPE_NUMBER, required=False, description="Individual limit for the employee"),
            openapi.Parameter("status", openapi.IN_FORM, type=openapi.TYPE_STRING, enum=["available", "on_trip", "blocked"], required=False, description="Employee status (default: available)"),
            openapi.Parameter("role", openapi.IN_FORM, type=openapi.TYPE_STRING, enum=["owner", "performer", "lider", "employee"], required=False, description="Employee role (default: employee)"),
        ],
        responses={
            201: B2BEmployeeSerializer(),
            400: openapi.Response(description="Validation error / Company context required."),
        },
    )
    def post(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        serializer = B2BEmployeeCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        validated = serializer.validated_data
        department_id = validated.get("department_id")
        if department_id and not any(d["id"] == department_id for d in list_departments(company_id)):
            return Response({"detail": "Department not found."}, status=status.HTTP_404_NOT_FOUND)

        role = validated.get("role", EmployeeRole.EMPLOYEE)
        if role == EmployeeRole.OWNER:
            return Response({"detail": "Owner role cannot be assigned to an employee."}, status=status.HTTP_400_BAD_REQUEST)
        if role == EmployeeRole.PERFORMER:
            has_performer = any(
                _is_permanent_performer(e) for e in list_employees(company_id)
            )
            if has_performer:
                return Response(
                    {"detail": "Company already has a performer employee."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        photo_file = validated.pop("photo", None)
        photo_url = None
        if photo_file:
            photo_path = default_storage.save(f"b2b/employees/photos/{photo_file.name}", photo_file)
            photo_url = default_storage.url(photo_path)

        # Ism va familiya alohida kiritiladi, lekin jadval, hisobot va
        # vaucherlarning hammasi bitta `full_name` ustunidan o'qiydi.
        first_name = validated.pop("first_name")
        last_name = validated.pop("last_name")
        employee = create_employee(
            company_id=company_id,
            full_name=f"{last_name} {first_name}",
            photo=photo_url,
            **{k: v for k, v in validated.items() if k not in ("company_id", "department_name")},
        )
        if not employee:
            return Response({"detail": "Failed to create employee."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        if employee["role"] == EmployeeRole.PERFORMER:
            sync_performer_b2b_login(company_id, employee)
        return Response(B2BEmployeeSerializer(employee).data, status=status.HTTP_201_CREATED)


class B2BEmployeePassportPreviewView(APIView):
    """Passport old/orqa skanidan OCR orqali ma'lumotlarni oldindan
    ko'rsatish uchun. OCR sekin ishlaydi, shu sababli bu endpoint uni darhol
    fon vazifasi (Celery) sifatida navbatga qo'yadi va ``job_id`` bilan
    202 qaytaradi — klient natijani ``B2BEmployeePassportPreviewStatusView``
    orqali so'rab turadi (polling). Xodim o'zi bu yerda saqlanmaydi; rasmlar
    vaqtinchalik joylashtiriladi va vazifa tugagach (muvaffaqiyatli yoki
    xato bilan) o'chiriladi. Natijadagi ``ocr_token``ni klient yakuniy
    saqlashda (``B2BEmployeeListCreateView.post``) xuddi shu ikkita rasm
    bilan birga qaytarsa, OCR ikkinchi marta ishga tushmaydi."""

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Start passport OCR extraction (async)",
        operation_description=(
            "Accepts the front and back scans of an ID document, saves them "
            "temporarily, and queues OCR extraction as a background job. "
            "Poll GET /b2b/employees/passport-preview/{job_id}/ for the "
            "result (full_name, date_of_birth, passport_series, "
            "passport_pinfl)."
        ),
        consumes=["multipart/form-data"],
        manual_parameters=[
            openapi.Parameter("passport_upload_front", openapi.IN_FORM, type=openapi.TYPE_FILE, required=True, description="Front side of the ID document"),
            openapi.Parameter("passport_upload_back", openapi.IN_FORM, type=openapi.TYPE_FILE, required=True, description="Back side of the ID document with MRZ code"),
        ],
        responses={
            202: openapi.Response(description="Job queued — returns {job_id}."),
            400: openapi.Response(description="Validation error."),
        },
    )
    def post(self, request):
        serializer = B2BEmployeePassportPreviewSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        front_file = serializer.validated_data["passport_upload_front"]
        back_file = serializer.validated_data["passport_upload_back"]

        job_id = uuid.uuid4().hex
        front_digest = _file_digest(front_file)
        back_digest = _file_digest(back_file)
        front_path = default_storage.save(f"b2b/employees/passports_pending/{job_id}_front", front_file)
        back_path = default_storage.save(f"b2b/employees/passports_pending/{job_id}_back", back_file)

        cache.set(
            f"passport_ocr_job:{job_id}",
            {"status": "pending", "front_digest": front_digest, "back_digest": back_digest},
            PASSPORT_OCR_CACHE_TTL_SECONDS,
        )
        run_passport_ocr_job.delay(job_id, front_path, back_path)
        return Response({"job_id": job_id}, status=status.HTTP_202_ACCEPTED)


class B2BEmployeePassportPreviewStatusView(APIView):
    """``B2BEmployeePassportPreviewView`` navbatga qo'ygan fon vazifasining
    natijasini so'rash uchun (polling)."""

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Poll passport OCR job status",
        responses={200: openapi.Response(description="{status: pending|done|error, ...}")},
    )
    def get(self, request, job_id):
        job = cache.get(f"passport_ocr_job:{job_id}")
        if job is None:
            return Response(
                {"detail": "So'rov topilmadi yoki muddati tugagan. Rasmlarni qayta yuklang."},
                status=status.HTTP_404_NOT_FOUND,
            )
        if job["status"] == "pending":
            return Response({"status": "pending"})
        if job["status"] == "error":
            return Response({"status": "error", "detail": job["detail"]})
        return Response({"status": "done", "ocr_token": job_id, **job["data"]})


class B2BEmployeeRetrieveUpdateView(APIView):
    """A performer can view employees but not modify or remove them — only
    the owner edits/deletes."""

    def get_permissions(self):
        if self.request.method == "GET":
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsB2BOwner()]

    @swagger_auto_schema(responses={200: B2BEmployeeSerializer()})
    def get(self, request, employee_id):
        company_id = _get_company_id(request)
        employee = get_employee(employee_id, company_id)
        if not employee:
            return Response({"detail": "Employee not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(B2BEmployeeSerializer(employee).data)

    @swagger_auto_schema(
        request_body=B2BEmployeeSerializer,
        operation_description=(
            """The 'owner' role is never assigned (resulting in a 400 error). If a user is designated as the 'performer', the company's current performer is automatically reassigned to the 'employee' role, and the new user becomes the performer."""
        ),
        responses={200: B2BEmployeeSerializer()},
    )
    def patch(self, request, employee_id):
        company_id = _get_company_id(request)
        existing = get_employee(employee_id, company_id)
        if not existing:
            return Response({"detail": "Employee not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = B2BEmployeeSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        department_id = serializer.validated_data.get("department_id")
        if department_id and not any(d["id"] == department_id for d in list_departments(company_id)):
            return Response({"detail": "Department not found."}, status=status.HTTP_404_NOT_FOUND)

        role = serializer.validated_data.get("role")
        if role == EmployeeRole.OWNER:
            return Response({"detail": "Owner role cannot be assigned to an employee."}, status=status.HTTP_400_BAD_REQUEST)
        if role == EmployeeRole.PERFORMER:
            for e in list_employees(company_id):
                if _is_permanent_performer(e) and e["id"] != employee_id:
                    update_employee(e["id"], role=EmployeeRole.EMPLOYEE)
                    revoke_b2b_login_by_phone(company_id, e["phone"])
        elif role == EmployeeRole.EMPLOYEE and existing["role"] == EmployeeRole.PERFORMER:
            revoke_b2b_login_by_phone(company_id, existing["phone"])

        new_phone = serializer.validated_data.get("phone")
        if (
            existing["role"] == EmployeeRole.PERFORMER
            and role != EmployeeRole.EMPLOYEE
            and new_phone
            and new_phone != existing["phone"]
        ):
            revoke_b2b_login_by_phone(company_id, existing["phone"])

        employee = update_employee(employee_id, **{
            k: v for k, v in serializer.validated_data.items()
            if k not in ("company_id", "department_name")
        })
        if not employee:
            return Response({"detail": "Employee not found."}, status=status.HTTP_404_NOT_FOUND)

        if employee["role"] == EmployeeRole.PERFORMER:
            sync_performer_b2b_login(company_id, employee)
        return Response(B2BEmployeeSerializer(employee).data)

    @swagger_auto_schema(response={204: "No Content"})
    def delete(self, request, employee_id):
        company_id = _get_company_id(request)
        employee = get_employee(employee_id, company_id)
        if not employee:
            return Response({"detail": "Employee not found."}, status=status.HTTP_404_NOT_FOUND)
        deleted = delete_employee(employee_id, company_id)
        if not deleted:
            return Response({"detail": "Employee not found."}, status=status.HTTP_404_NOT_FOUND)
        if employee["role"] == EmployeeRole.PERFORMER:
            revoke_b2b_login_by_phone(company_id, employee["phone"])
        return Response(status=status.HTTP_204_NO_CONTENT)

class BusinessTripListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(responses={200: BusinessTripSerializer(many=True)})
    def get(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        trip_status = request.query_params.get("status")
        trips = list_trips(company_id, status=trip_status)
        return Response(BusinessTripSerializer(trips, many=True).data)

    @swagger_auto_schema(request_body=BusinessTripSerializer, responses={201: BusinessTripSerializer()})
    def post(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        serializer = BusinessTripSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        validated = serializer.validated_data
        trip = create_trip(
            company_id=company_id,
            name=validated.pop("name"),
            created_by=_get_b2b_user_id(request),
            **{k: v for k, v in validated.items() if k not in ("company_id", "created_by")},
        )
        if not trip:
            return Response({"detail": "Failed to create trip."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(BusinessTripSerializer(trip).data, status=status.HTTP_201_CREATED)


class BusinessTripRetrieveUpdateView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(responses={200: BusinessTripSerializer()})
    def get(self, request, trip_id):
        company_id = _get_company_id(request)
        trip = get_trip(trip_id, company_id)
        if not trip:
            return Response({"detail": "Trip not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(BusinessTripSerializer(trip).data)

    @swagger_auto_schema(request_body=BusinessTripSerializer, responses={200: BusinessTripSerializer()})
    def patch(self, request, trip_id):
        company_id = _get_company_id(request)
        serializer = BusinessTripSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        trip = update_trip(trip_id, **{
            k: v for k, v in serializer.validated_data.items()
            if k not in ("company_id", "created_by")
        })
        if not trip:
            return Response({"detail": "Trip not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(BusinessTripSerializer(trip).data)

    @swagger_auto_schema(response={204: "No Content"})
    def delete(self, request, trip_id):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        deleted = delete_trip(trip_id, company_id)
        if not deleted:
            return Response(
                {"detail": "Trip not found or cannot be deleted."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


class TripEmployeeListCreateView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(responses={200: TripEmployeeSerializer(many=True)})
    def get(self, request, trip_id):
        employees = list_trip_employees(trip_id)
        return Response(TripEmployeeSerializer(employees, many=True).data)

    @swagger_auto_schema(request_body=TripEmployeeSerializer, responses={201: TripEmployeeSerializer()})
    def post(self, request, trip_id):
        serializer = TripEmployeeSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        validated = serializer.validated_data
        te = add_trip_employee(
            trip_id=trip_id,
            employee_id=validated.pop("employee_id"),
            **{k: v for k, v in validated.items() if k not in ("trip_id", "full_name", "position", "phone", "email")},
        )
        if not te:
            return Response({"detail": "Failed to add employee to trip."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(TripEmployeeSerializer(te).data, status=status.HTTP_201_CREATED)


class TravelPolicyView(APIView):
    """Travel Policy — viewable by owner and performer, but only the owner
    can change it (performer gets a read-only view on the frontend, and is
    blocked here too in case the request bypasses the UI)."""

    def get_permissions(self):
        if self.request.method == "GET":
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsB2BOwner()]

    @swagger_auto_schema(responses={200: TravelPolicySerializer()})
    def get(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        policy = get_or_create_travel_policy(company_id)
        return Response(TravelPolicySerializer(policy).data)

    @swagger_auto_schema(request_body=TravelPolicySerializer, responses={200: TravelPolicySerializer()})
    def patch(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        serializer = TravelPolicySerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        policy = update_travel_policy(company_id, **{
            k: v for k, v in serializer.validated_data.items()
            if k not in ("id", "company_id", "updated_at")
        })
        if not policy:
            return Response({"detail": "Failed to update policy."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(TravelPolicySerializer(policy).data)


class B2BLeadRequestCreateView(APIView):
    """POST /b2b/lead-requests/

    Public 'become a partner' application form — a prospective business
    owner submits their name, company and contact details to request
    onboarding. Unauthenticated (no B2B account exists yet). Notifies the
    sales team's Telegram channel on submission; staff review the request
    and onboard the company manually via ``create_b2b_owner``.
    """
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ResilientScopedRateThrottle]
    throttle_scope = "b2b_lead_request"

    @swagger_auto_schema(
        operation_summary="Submit a partnership request (new business owners)",
        operation_description=(
            "A business owner who is not yet a B2B client can submit their "
            "name, company name, email, and phone number to request "
            "partnership. Authentication is not required."
        ),
        request_body=B2BLeadRequestSerializer,
        responses={
            201: B2BLeadRequestSerializer(),
            400: openapi.Response(description="Validation error."),
        },
    )
    def post(self, request):
        serializer = B2BLeadRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        validated = serializer.validated_data

        lead = create_lead_request(
            full_name=validated["full_name"],
            company_name=validated["company_name"],
            email=validated["email"],
            phone_number=validated["phone_number"],
        )
        if not lead:
            return Response({"detail": "Failed to create lead request."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        try:
            _send_b2b_lead_telegram_notification(
                lead["id"], lead["full_name"], lead["company_name"], lead["email"], lead["phone_number"],
            )
        except Exception:
            logger.exception("Failed to send B2B lead Telegram notification for lead #%s", lead["id"])

        return Response(B2BLeadRequestSerializer(lead).data, status=status.HTTP_201_CREATED)


class BudgetRequestListCreateView(APIView):
    """GET/POST /b2b/budget-requests/

    A budget request is how an executer (performer, e.g. the one who sends an
    employee on a business trip) asks the owner for extra budget — either for
    a single employee (``employee_id``) or for a whole department
    (``department_id``); exactly one of the two must be given. ``trip_id`` is
    optional context. Every request is created ``pending`` and tagged with
    the executer as ``requested_by``; the owner reviews it via
    ``GET ?status=pending`` and approves/rejects via
    ``POST /budget-requests/<id>/review/``.
    """

    def get_permissions(self):
        # Owners get the company-wide review queue ("Заявки"); performers get
        # the same endpoint but scoped to the requests they raised themselves,
        # so they can see whether the owner approved or rejected them.
        return [IsAuthenticated()]

    @swagger_auto_schema(
        operation_summary="List budget requests (owner)",
        operation_description=(
            "Return all budget requests for the company. Filter with "
            "`status=pending` to see requests submitted by performers "
            "for an employee or department and waiting for owner approval."
        ),
        manual_parameters=[
            openapi.Parameter(
                "status", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                enum=["pending", "approved", "rejected"],
                description="Filter by status. For owners this is usually `pending`.",
            ),
        ],
        responses={200: BudgetRequestListResponseSerializer()},
    )
    def get(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        req_status = request.query_params.get("status")
        user = request.user
        role = user.get("role") if isinstance(user, dict) else getattr(user, "role", None)
        is_owner = role == B2BUserRole.OWNER
        requests = list_budget_requests(
            company_id,
            status=req_status,
            requested_by=None if is_owner else _get_user_id(request),
        )
        return Response({
            "count": len(requests),
            "results": BudgetRequestSerializer(requests, many=True).data,
        })

    @swagger_auto_schema(
        operation_summary="Submit a budget request (employee or department)",
        operation_description=(
            "A performer can request additional budget for either a single "
            "employee (`employee_id`) or an entire department "
            "(`department_id`), but exactly one of them must be provided. "
            "`trip_id` is optional; if present, the request is linked to that "
            "business trip. Every request is saved as `pending`, then the "
            "owner reviews it via `GET ?status=pending` and approves or "
            "rejects it with `POST /budget-requests/<id>/review/`."
        ),
        request_body=BudgetRequestSerializer,
        responses={
            201: BudgetRequestSerializer(),
            400: openapi.Response(description="Validation error / Company context required."),
            404: openapi.Response(description="Employee, department or trip not found."),
        },
    )
    def post(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        serializer = BudgetRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        validated = serializer.validated_data

        employee_id = validated.get("employee_id")
        department_id = validated.get("department_id")
        trip_id = validated.get("trip_id")

        if employee_id and not get_employee(employee_id, company_id):
            return Response({"detail": "Employee not found."}, status=status.HTTP_404_NOT_FOUND)
        if department_id and not any(d["id"] == department_id for d in list_departments(company_id)):
            return Response({"detail": "Department not found."}, status=status.HTTP_404_NOT_FOUND)
        if trip_id and not get_trip(trip_id, company_id):
            return Response({"detail": "Trip not found."}, status=status.HTTP_404_NOT_FOUND)

        budget_req = create_budget_request(
            trip_id=trip_id,
            employee_id=employee_id,
            department_id=department_id,
            requested_by=_get_b2b_user_id(request),
            amount=validated["amount"],
            description=validated.get("description"),
        )
        if not budget_req:
            return Response({"detail": "Failed to create budget request."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(BudgetRequestSerializer(budget_req).data, status=status.HTTP_201_CREATED)


class TransactionListView(APIView):
    """GET /b2b/transactions/?search=&page=&page_size=

    Paginated transaction history for the analytics page: one row per
    budget request. `search` matches employee or department name.
    `status` is the raw budget-request status
    (`pending` / `approved` / `rejected`).
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Transaction history",
        operation_description=(
            "Paginated list of budget-request transactions, newest first. "
            "`search` filters by employee or department name."
        ),
        manual_parameters=[
            openapi.Parameter("search", openapi.IN_QUERY, type=openapi.TYPE_STRING, description="Search by employee/department name."),
            openapi.Parameter("page", openapi.IN_QUERY, type=openapi.TYPE_INTEGER, description="Page number (default 1)."),
            openapi.Parameter("page_size", openapi.IN_QUERY, type=openapi.TYPE_INTEGER, description="Rows per page (default 10, max 100)."),
        ],
        responses={200: TransactionListResponseSerializer()},
        tags=["B2B / Statistics"],
    )
    def get(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)

        search = request.query_params.get("search") or None
        try:
            page = max(int(request.query_params.get("page", 1)), 1)
        except (TypeError, ValueError):
            page = 1
        try:
            page_size = int(request.query_params.get("page_size", 10))
        except (TypeError, ValueError):
            page_size = 10
        page_size = min(max(page_size, 1), 100)

        data = list_transactions(company_id, search=search, page=page, page_size=page_size)
        return Response({
            "count": data["count"],
            "page": page,
            "page_size": page_size,
            "results": TransactionSerializer(data["results"], many=True).data,
        })


class BudgetRequestReviewView(APIView):
    """POST /b2b/budget-requests/<id>/review/

    Owner-only: approve or reject a budget request, optionally with a
    ``description`` explaining the decision.
    """
    permission_classes = [IsAuthenticated, IsB2BOwner]

    @swagger_auto_schema(
        operation_summary="Approve or reject a budget request (owner only)",
        operation_description=(
            "The owner marks the budget request as `approved` or `rejected`. "
            "`description` is an optional reason for the decision."
        ),
        request_body=ReviewBudgetRequestSerializer,
        responses={
            200: BudgetRequestSerializer(),
            403: openapi.Response(description="Only company owners can perform this action."),
            404: openapi.Response(description="Budget request not found."),
        },
    )
    def post(self, request, request_id):
        serializer = ReviewBudgetRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        result = review_budget_request(
            request_id=request_id,
            status=serializer.validated_data["status"],
            reviewed_by=_get_b2b_user_id(request),
            review_description=serializer.validated_data.get("description"),
        )
        if not result:
            return Response({"detail": "Budget request not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(BudgetRequestSerializer(result).data)


class TripVoucherView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(responses={200: TravelVoucherSerializer()})
    def get(self, request, trip_id):
        voucher = get_voucher(trip_id)
        if not voucher:
            return Response({"detail": "Voucher not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(TravelVoucherSerializer(voucher).data)

    @swagger_auto_schema(responses={201: TravelVoucherSerializer()})
    def post(self, request, trip_id):
        company_id = _get_company_id(request)
        trip = get_trip(trip_id, company_id)
        if not trip:
            return Response({"detail": "Trip not found."}, status=status.HTTP_404_NOT_FOUND)

        existing = get_voucher(trip_id)
        if existing:
            return Response(TravelVoucherSerializer(existing).data)

        voucher_number = f"V-{trip_id}-{random.randint(10000, 99999)}"
        voucher = create_voucher(trip_id=trip_id, voucher_number=voucher_number)
        if not voucher:
            return Response({"detail": "Failed to create voucher."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(TravelVoucherSerializer(voucher).data, status=status.HTTP_201_CREATED)


_VALID_PERIODS = {"1h", "1d", "14d", "1m", "3m", "1y", "all"}

_PERIOD_DELTAS: dict[str, timedelta] = {
    "1h": timedelta(hours=1),
    "1d": timedelta(days=1),
    "14d": timedelta(days=14),
    "1m": timedelta(days=30),
    "3m": timedelta(days=90),
    "1y": timedelta(days=365),
}


class B2BStatisticsView(APIView):
    """
    GET /b2b/statistics/?period=1h|1d|14d|1m|3m|1y|all

    Returns:
    - `periods`: spending summary for every time window
    - `by_department`: spending per department for the selected period (defaults to `all`)

    Each period entry:
      total_budget           – sum of trip budgets created in that window
      total_trips            – number of trips created in that window
      approved_spend         – sum of approved budget-request amounts in that window
      remaining_limit        – company-wide travel-policy limit minus approved_spend
                                (floored at 0)
      requested_extra_limit  – how much approved_spend exceeds the company-wide
                                travel-policy limit (floored at 0) — money that
                                had to be requested because the given limit fell short
    """

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Company spending statistics",
        operation_description=(
            "Returns spending summaries grouped by time window (`periods`) and "
            "by department (`by_department`). The `period` query parameter "
            "selects the window for the department breakdown."
        ),
        manual_parameters=[
            openapi.Parameter(
                "period",
                openapi.IN_QUERY,
                description="Time window: 1h, 1d, 14d, 1m, 3m, 1y, or all (default: all)",
                type=openapi.TYPE_STRING,
                enum=["1h", "1d", "14d", "1m", "3m", "1y", "all"],
                default="all",
            ),
        ],
        responses={200: StatisticsResponseSerializer()},
        tags=["B2B / Statistics"],
    )
    def get(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)

        period = request.query_params.get("period", "all")
        if period not in _VALID_PERIODS:
            return Response(
                {"detail": f"Invalid period. Choose from: 1h, 1d, 14d, 1m, 3m, 1y, all"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        since = (timezone.now() - _PERIOD_DELTAS[period]) if period != "all" else None

        periods_data = get_spending_overview(company_id)
        departments = get_department_spending(company_id, since=since)

        return Response({
            "period": period,
            "periods": periods_data,
            "by_department": [
                {
                    "department_id": d["department_id"],
                    "department_name": d["department_name"],
                    "total_trips": d["total_trips"] or 0,
                    "total_employees": d["total_employees"] or 0,
                    "approved_spend": str(d["approved_spend"] or "0"),
                }
                for d in departments
            ],
        })


class B2BStatisticsChartView(APIView):
    """
    GET /b2b/statistics/chart/?period=1h|1d|14d|1m|3m|1y|all

    Returns a date-bucketed series of approved spend for the "Общие расходы"
    chart, plus the period total and the percent change versus the preceding
    period of equal length.
    """

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Company spending chart",
        operation_description=(
            "Returns a date-bucketed approved-spend series for the selected "
            "time window, along with the period total and percent change "
            "versus the preceding equal-length period."
        ),
        manual_parameters=[
            openapi.Parameter(
                "period",
                openapi.IN_QUERY,
                description="Time window: 1h, 1d, 14d, 1m, 3m, 1y, or all (default: 14d)",
                type=openapi.TYPE_STRING,
                enum=["1h", "1d", "14d", "1m", "3m", "1y", "all"],
                default="14d",
            ),
        ],
        responses={200: StatisticsChartResponseSerializer()},
        tags=["B2B / Statistics"],
    )
    def get(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)

        period = request.query_params.get("period", "14d")
        if period not in _VALID_PERIODS:
            return Response(
                {"detail": f"Invalid period. Choose from: 1h, 1d, 14d, 1m, 3m, 1y, all"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(get_spending_chart(company_id, period))


_VALID_MONTHLY_CHART_MONTHS = {3, 6, 12}


class B2BMonthlySpendingChartView(APIView):
    """GET /b2b/statistics/monthly-chart/?months=3|6|12

    Returns a calendar-month-bucketed approved-spend series (always exactly
    `months` points, oldest first, one point per month including the
    current one), each carrying its own month-over-month `change_percent`.
    Powers the "Аналитика затрат" dashboard chart.
    """

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Monthly company spending chart",
        operation_description=(
            "Return a month-by-month approved-spend series for the last "
            "`months` calendar months (default 12), each point carrying "
            "its own month-over-month `change_percent`."
        ),
        manual_parameters=[
            openapi.Parameter(
                "months",
                openapi.IN_QUERY,
                description="Number of months: 3, 6, or 12 (default: 12)",
                type=openapi.TYPE_INTEGER,
                enum=[3, 6, 12],
                default=12,
            ),
        ],
        responses={200: MonthlySpendingChartResponseSerializer()},
        tags=["B2B / Statistics"],
    )
    def get(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            months = int(request.query_params.get("months", 12))
        except (TypeError, ValueError):
            months = 12
        if months not in _VALID_MONTHLY_CHART_MONTHS:
            months = 12

        return Response(get_monthly_spending_chart(company_id, months))


class B2BWeeklySpendingChartView(APIView):
    """GET /b2b/statistics/weekly-chart/?month=YYYY-MM

    Returns a week-bucketed approved-spend series for a single calendar
    month (weeks 1-7, 8-14, 15-21, 22-28, 29-end), each with its own
    week-over-week `change_percent`. Defaults to the current month.
    """

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Weekly company spending chart for one month",
        operation_description=(
            "Return a week-by-week approved-spend series for the selected "
            "calendar month, each point carrying its own week-over-week "
            "`change_percent`. If `month` is omitted, the current month is used."
        ),
        manual_parameters=[
            openapi.Parameter(
                "month",
                openapi.IN_QUERY,
                description="Month in YYYY-MM format. Defaults to the current month.",
                type=openapi.TYPE_STRING,
                format=openapi.FORMAT_DATE,
                example="2026-06",
            ),
        ],
        responses={200: WeeklySpendingChartResponseSerializer()},
        tags=["B2B / Statistics"],
    )
    def get(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)

        year, month = _parse_year_month(request)
        return Response(get_weekly_spending_chart(company_id, year, month))


# ─── Dashboard summary ──────────────────────────────────────────────────────

class DashboardSummaryView(APIView):
    """GET /api/b2b/dashboard/summary/

    Returns the 4 top-line numbers shown on the company dashboard:
      monthly_limit          – owner-set overall monthly budget limit
      spent_this_month       – approved spend for the current calendar month
      active_employees       – distinct employees currently on/about to go on a trip
      pending_limit_requests – budget-requests awaiting owner review
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Four main dashboard statistics",
        operation_description=(
            "Return the overall monthly limit (`monthly_limit`), amount spent "
            "this month (`spent_this_month`), number of employees on or about "
            "to go on a business trip (`active_employees`), and the number of "
            "limit increase requests waiting for owner review "
            "(`pending_limit_requests`)."
        ),
        responses={
            200: DashboardSummarySerializer(),
            400: openapi.Response(description="Company context required."),
        },
    )
    def get(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        data = get_dashboard_summary(company_id)
        return Response(DashboardSummarySerializer(data).data)


# ─── Recent trip employees ─────────────────────────────────────────────────

class RecentTripEmployeesView(APIView):
    """GET /api/b2b/recent-trips/employees/?limit=5

    Returns the most recent employees who have been assigned to a business
    trip (past, current or future). Defaults to the latest 5.
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Employees assigned to the most recent trips",
        operation_description=(
            "Return the employees assigned to the company's most recent "
            "business trips. Default `limit=5`, maximum 100."
        ),
        manual_parameters=[
            openapi.Parameter(
                "limit",
                openapi.IN_QUERY,
                description="Number of employees to return (1-100). Default 5.",
                type=openapi.TYPE_INTEGER,
                default=5,
            ),
        ],
        responses={
            200: RecentTripEmployeeSerializer(many=True),
            400: openapi.Response(description="Company context required."),
        },
    )
    def get(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            limit = int(request.query_params.get("limit", 5))
        except (TypeError, ValueError):
            limit = 5
        if limit <= 0 or limit > 100:
            limit = 5
        rows = list_recent_trip_employees(company_id, limit=limit)
        return Response(RecentTripEmployeeSerializer(rows, many=True).data)


# ─── Top employees by trip count ───────────────────────────────────────────

class TopEmployeesByTripsView(APIView):
    """GET /api/b2b/employees/top-by-trips/?limit=5

    Returns the employees with the most business-trip (komandirovka)
    assignments for the company, ordered by trip count descending.
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Top employees by trip count",
        operation_description=(
            "Return the employees with the highest number of business-trip "
            "assignments for the company, ordered by `trip_count` descending. "
            "Default `limit=5`, maximum 100."
        ),
        manual_parameters=[
            openapi.Parameter(
                "limit",
                openapi.IN_QUERY,
                description="Number of employees to return (1-100). Default 5.",
                type=openapi.TYPE_INTEGER,
                default=5,
            ),
        ],
        responses={
            200: TopEmployeeByTripsSerializer(many=True),
            400: openapi.Response(description="Company context required."),
        },
    )
    def get(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            limit = int(request.query_params.get("limit", 5))
        except (TypeError, ValueError):
            limit = 5
        if limit <= 0 or limit > 100:
            limit = 5
        rows = get_top_employees_by_trip_count(company_id, limit=limit)
        return Response(TopEmployeeByTripsSerializer(rows, many=True).data)


class TripStatusSummaryView(APIView):
    """GET /api/b2b/trips/status-summary/

    For trips whose start_date falls in the current calendar month, returns
    the count of distinct employees per trip status: active (currently away),
    pending (date set, awaiting departure), completed (went and came back),
    cancelled (booking was cancelled).
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="This month's trip status breakdown",
        operation_description=(
            "Counts distinct employees per trip status for trips starting "
            "this calendar month: `active`, `pending`, `completed`, `cancelled`."
        ),
        responses={200: TripStatusSummarySerializer()},
        tags=["B2B / Statistics"],
    )
    def get(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)

        now = timezone.now()
        summary = get_trip_status_summary(company_id, now.year, now.month)
        return Response({
            "year": now.year,
            "month": now.month,
            **summary,
        })


# ─── Active / upcoming trip employees (yolda / borgan / tugagan) ───────────

_VALID_ACTIVE_TRIP_TYPES = {"yolda", "borgan", "all", "tugagan"}


class ActiveTripEmployeesView(APIView):
    """GET /api/b2b/trips/active-employees/?type=yolda|borgan|all|tugagan

    Returns employees that are currently on a business trip or about to go
    on one. Scoped to the authenticated company.

    Query params:
        type: ``"yolda"``   – today is between the trip's ``start_date`` and
                               ``end_date`` (currently travelling).
              ``"borgan"``  – trip starts in the future (upcoming trip).
              ``"all"``     – both groups combined (default).
              ``"tugagan"`` – archive: trips that have already ended.

    Trip must be in ``active`` or ``pending`` status (also ``completed`` for
    ``type=tugagan``) and the trip-employee assignment must not be
    ``cancelled`` or ``checked_out``.
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Employees on a trip, about to depart, or archived",
        operation_description=(
            "Return employees attached to active (`active` or `pending`, plus "
            "`completed` for `type=tugagan`) trips whose assignments are not "
            "`cancelled` or `checked_out` (unless `status` is given explicitly). "
            "`type=yolda` returns employees whose trip dates include today, "
            "`type=borgan` returns employees whose trip starts in the future, "
            "`type=all` (default) combines both groups, and `type=tugagan` "
            "returns the archive of trips that have already ended. "
            "Pass `page` to paginate (`count` becomes the total row count "
            "across all pages instead of the page size); omit it to keep the "
            "legacy behaviour of returning every matching row (optionally "
            "capped by `limit`)."
        ),
        manual_parameters=[
            openapi.Parameter(
                "type",
                openapi.IN_QUERY,
                description="Filter type: yolda | borgan | all | tugagan (default: all)",
                type=openapi.TYPE_STRING,
                enum=["yolda", "borgan", "all", "tugagan"],
                default="all",
            ),
            openapi.Parameter(
                "search",
                openapi.IN_QUERY,
                description="Filter by employee full name (partial, case-insensitive).",
                type=openapi.TYPE_STRING,
            ),
            openapi.Parameter(
                "department_id",
                openapi.IN_QUERY,
                description="Filter to a single department.",
                type=openapi.TYPE_INTEGER,
            ),
            openapi.Parameter(
                "status",
                openapi.IN_QUERY,
                description="Filter to a single trip-employee status.",
                type=openapi.TYPE_STRING,
                enum=TripEmployeeStatus.CHOICES,
            ),
            openapi.Parameter(
                "date_from",
                openapi.IN_QUERY,
                description="Only include trips ending on/after this date (YYYY-MM-DD).",
                type=openapi.TYPE_STRING,
            ),
            openapi.Parameter(
                "date_to",
                openapi.IN_QUERY,
                description="Only include trips starting on/before this date (YYYY-MM-DD).",
                type=openapi.TYPE_STRING,
            ),
            openapi.Parameter(
                "page",
                openapi.IN_QUERY,
                description="1-indexed page number. Enables pagination.",
                type=openapi.TYPE_INTEGER,
            ),
            openapi.Parameter(
                "page_size",
                openapi.IN_QUERY,
                description="Rows per page (1-100, default 10). Only used with `page`.",
                type=openapi.TYPE_INTEGER,
            ),
            openapi.Parameter(
                "limit",
                openapi.IN_QUERY,
                description="Max number of employees to return (1-100). Omit for no limit. Ignored when `page` is set.",
                type=openapi.TYPE_INTEGER,
            ),
        ],
        responses={200: ActiveTripEmployeesResponseSerializer()},
    )
    def get(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)

        type_ = request.query_params.get("type", "all")
        if type_ not in _VALID_ACTIVE_TRIP_TYPES:
            return Response(
                {"detail": "Invalid type. Choose from: yolda, borgan, all, tugagan."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        search = request.query_params.get("search") or None

        department_id: int | None = None
        raw_department_id = request.query_params.get("department_id")
        if raw_department_id:
            try:
                department_id = int(raw_department_id)
            except (TypeError, ValueError):
                return Response({"detail": "Invalid department_id."}, status=status.HTTP_400_BAD_REQUEST)

        status_param = request.query_params.get("status") or None
        if status_param and status_param not in TripEmployeeStatus.CHOICES:
            return Response(
                {"detail": f"Invalid status. Choose from: {', '.join(TripEmployeeStatus.CHOICES)}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        date_from_str = request.query_params.get("date_from") or None
        date_to_str = request.query_params.get("date_to") or None
        try:
            date_from = date.fromisoformat(date_from_str) if date_from_str else None
            date_to = date.fromisoformat(date_to_str) if date_to_str else None
        except ValueError:
            return Response(
                {"detail": "Invalid date_from/date_to format. Use YYYY-MM-DD."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        filters: dict[str, Any] = dict(
            type_=type_,
            search=search,
            department_id=department_id,
            status=status_param,
            date_from=date_from,
            date_to=date_to,
        )

        raw_page = request.query_params.get("page")
        if raw_page:
            try:
                page = max(1, int(raw_page))
            except (TypeError, ValueError):
                page = 1
            try:
                page_size = int(request.query_params.get("page_size") or 10)
            except (TypeError, ValueError):
                page_size = 10
            page_size = max(1, min(page_size, 100))

            rows = list_active_trip_employees(
                company_id,
                limit=page_size,
                offset=(page - 1) * page_size,
                **filters,
            )
            total = count_active_trip_employees(company_id, **filters)
            return Response({
                "type": type_,
                "count": total,
                "results": ActiveTripEmployeeSerializer(rows, many=True).data,
            })

        limit: int | None = None
        raw_limit = request.query_params.get("limit")
        if raw_limit:
            try:
                limit = int(raw_limit)
            except (TypeError, ValueError):
                limit = None
            if limit is not None and (limit <= 0 or limit > 100):
                limit = None

        rows = list_active_trip_employees(company_id, limit=limit, **filters)
        return Response({
            "type": type_,
            "count": len(rows),
            "results": ActiveTripEmployeeSerializer(rows, many=True).data,
        })


# ─── Department monthly spending ───────────────────────────────────────────

def _parse_year_month(request) -> tuple[int, int]:
    """Return (year, month) for the request, defaulting to the current month."""
    raw = request.query_params.get("month")
    today = timezone.now().date()
    if raw:
        try:
            parts = raw.split("-")
            year = int(parts[0])
            month = int(parts[1])
            if month < 1 or month > 12:
                raise ValueError
            return year, month
        except (ValueError, IndexError):
            pass
    return today.year, today.month


class DepartmentMonthlySpendingView(APIView):
    """GET /api/b2b/departments/monthly-spending/?month=YYYY-MM

    Returns each department's total spend (approved budget-request amounts)
    and trip count for the requested month. Defaults to the current month
    when no ``month`` query parameter is supplied.
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Monthly spending by department",
        operation_description=(
            "Return each department's approved budget-request totals and trip "
            "count for the selected month. If `month` is omitted, the current "
            "month is used."
        ),
        manual_parameters=[
            openapi.Parameter(
                "month",
                openapi.IN_QUERY,
                description="Month in YYYY-MM format. Defaults to the current month.",
                type=openapi.TYPE_STRING,
                format=openapi.FORMAT_DATE,
                example="2026-06",
            ),
        ],
        responses={
            200: openapi.Response(
                description="Monthly spending for each department",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "year": openapi.Schema(type=openapi.TYPE_INTEGER),
                        "month": openapi.Schema(type=openapi.TYPE_INTEGER),
                        "departments": openapi.Schema(
                            type=openapi.TYPE_ARRAY,
                            items=openapi.Schema(type=openapi.TYPE_OBJECT),
                        ),
                    },
                ),
            ),
            400: openapi.Response(description="Company context required."),
        },
    )
    def get(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        year, month = _parse_year_month(request)
        rows = get_department_monthly_spending(company_id, year, month)
        payload = []
        for r in rows:
            month_spend = Decimal(str(r["month_spend"] or "0"))
            prev_month_spend = Decimal(str(r["prev_month_spend"] or "0"))
            if prev_month_spend > 0:
                change_percent = float((month_spend - prev_month_spend) / prev_month_spend * 100)
            else:
                change_percent = 100.0 if month_spend > 0 else 0.0
            payload.append({
                "department_id": r["department_id"],
                "department_name": r["department_name"],
                "month_trips": r["month_trips"] or 0,
                "total_employees": r["total_employees"] or 0,
                "budget_limit": str(r["budget_limit"] or "0"),
                "month_spend": str(month_spend),
                "change_percent": round(change_percent, 1),
            })
        return Response({
            "year": year,
            "month": month,
            "departments": DepartmentMonthlySpendingSerializer(payload, many=True).data,
        })


# ─── Dashboard notifications ────────────────────────────────────────────────

class DashboardNotificationsView(APIView):
    """GET /api/b2b/dashboard/notifications/?limit=8

    Returns the dashboard's notification feed, derived on the fly from
    existing data (no dedicated notification model exists yet):
      limit_exceeded     – employee approved spend this month > individual_limit
      budget_threshold   – department has used >= 90% of its budget_limit
      trip_approved      – a trip-linked budget request was approved
      documents_uploaded – employee has a passport scan on file
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Dashboard notification feed",
        operation_description=(
            "Return the most recent dashboard notifications across 4 event "
            "types: `limit_exceeded`, `budget_threshold`, `trip_approved`, "
            "`documents_uploaded`. Default `limit=8`, maximum 50."
        ),
        manual_parameters=[
            openapi.Parameter(
                "limit",
                openapi.IN_QUERY,
                description="Number of notifications to return (1-50). Default 8.",
                type=openapi.TYPE_INTEGER,
                default=8,
            ),
        ],
        responses={
            200: openapi.Response(
                description="Notification feed",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "notifications": openapi.Schema(
                            type=openapi.TYPE_ARRAY,
                            items=openapi.Schema(type=openapi.TYPE_OBJECT),
                        ),
                    },
                ),
            ),
            400: openapi.Response(description="Company context required."),
        },
    )
    def get(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            limit = int(request.query_params.get("limit", 8))
        except (TypeError, ValueError):
            limit = 8
        if limit <= 0 or limit > 50:
            limit = 8
        rows = list_dashboard_notifications(company_id, limit=limit)
        return Response({"notifications": DashboardNotificationSerializer(rows, many=True).data})


# ─── Travel limit helpers ───────────────────────────────────────────────────

def _hydrate_rule_with_target(company_id: int, rule: dict[str, Any]) -> dict[str, Any]:
    """Decorate a TravelPolicyRule row with the human-readable name of the
    department / employee it targets (if any), and how much of it has
    actually been spent (``used_amount``, real confirmed-booking money)."""
    out = dict(rule)
    out["target_name"] = None
    applies_to = rule.get("applies_to")
    target_id = rule.get("target_id")
    out["used_amount"] = get_rule_used_amount(company_id, applies_to, target_id)
    if not target_id:
        return out
    if applies_to == "department":
        dept = next((d for d in list_departments(company_id) if d["id"] == target_id), None)
        if dept:
            out["target_name"] = dept.get("name")
    elif applies_to == "employee":
        emp = get_employee(target_id, company_id)
        if emp:
            out["target_name"] = emp.get("full_name")
    return out


def _validate_limit_target(company_id: int, applies_to: str, target_id: int | None) -> Response | None:
    """Return ``None`` on success, otherwise a ready-to-return 4xx Response.

    ``all`` has no target. ``department``/``employee`` require ``target_id``
    to point at a real row in the company.
    """
    if applies_to == "all":
        return None
    if not target_id:
        return Response({"detail": f"target_id ({applies_to}_id) is required."}, status=status.HTTP_400_BAD_REQUEST)
    if applies_to == "department":
        dept = next((d for d in list_departments(company_id) if d["id"] == target_id), None)
        if not dept:
            return Response({"detail": "Department not found."}, status=status.HTTP_404_NOT_FOUND)
    elif applies_to == "employee":
        emp = get_employee(target_id, company_id)
        if not emp:
            return Response({"detail": "Employee not found."}, status=status.HTTP_404_NOT_FOUND)
    return None


class TravelLimitsView(APIView):
    """GET / POST /api/b2b/travel-policy/limits/?applies_to=all|department|employee

    Single endpoint for all three limit tiers: the company-wide default
    (``all``, no target), per-department, and per-employee. ``applies_to``
    selects the tier — as a query param for GET, in the body for POST.

    Read-only for a performer — only the owner can add limit rules.
    """

    def get_permissions(self):
        if self.request.method == "GET":
            return [IsAuthenticated()]
        return [IsAuthenticated(), IsB2BOwner()]

    @swagger_auto_schema(
        operation_summary="List limit rules",
        operation_description=(
            "Use `applies_to` to choose which limit rules to return: `all` for "
            "all company limits (global, department, and employee), "
            "`department` for department rules, and `employee` for individual "
            "employee rules."
        ),
        manual_parameters=[
            openapi.Parameter(
                "applies_to", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                enum=["all", "department", "employee"], required=True,
                description="Which type of limit rules to return.",
            ),
        ],
        responses={200: TravelPolicyRuleSerializer(many=True)},
    )
    def get(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        applies_to = request.query_params.get("applies_to")
        if applies_to not in ("all", "department", "employee"):
            return Response(
                {"detail": "applies_to must be 'all', 'department' or 'employee'."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        rules = list_policy_rules(company_id) if applies_to == "all" else list_policy_rules_by_type(company_id, applies_to)
        return Response(TravelPolicyRuleSerializer(
            [_hydrate_rule_with_target(company_id, r) for r in rules], many=True
        ).data)

    @swagger_auto_schema(
        operation_summary="Add a new limit rule",
        operation_description=(
            "`applies_to`: `all` for a company-wide global limit (only one "
            "per company; do not send `target_id`); `department` or "
            "`employee` with the matching `target_id` (`department_id` or "
            "`employee_id`)."
        ),
        request_body=TravelPolicyRuleCreateSerializer,
        responses={
            201: TravelPolicyRuleSerializer(),
            400: openapi.Response(description="Validation error / missing target_id / global limit already exists"),
            404: openapi.Response(description="Target department/employee not found"),
        },
    )
    def post(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        serializer = TravelPolicyRuleCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        data = serializer.validated_data
        applies_to = data["applies_to"]
        target_id = data.get("target_id") if applies_to != "all" else None

        bad = _validate_limit_target(company_id, applies_to, target_id)
        if bad is not None:
            return bad

        if applies_to == "all" and list_policy_rules_by_type(company_id, "all"):
            return Response(
                {"detail": "Global limit already exists. Use PATCH to update it."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        rule = create_policy_rule(
            company_id=company_id,
            applies_to=applies_to,
            target_id=target_id,
            budget_limit=data.get("budget_limit"),
        )
        if not rule:
            return Response({"detail": "Failed to create limit rule."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(
            TravelPolicyRuleSerializer(_hydrate_rule_with_target(company_id, rule)).data,
            status=status.HTTP_201_CREATED,
        )


class TravelLimitDetailView(APIView):
    """PATCH / DELETE /api/b2b/travel-policy/limits/<rule_id>/ — owner-only."""
    permission_classes = [IsAuthenticated, IsB2BOwner]

    @swagger_auto_schema(
        operation_summary="Update a limit rule",
        request_body=TravelPolicyRuleUpdateSerializer,
        responses={
            200: TravelPolicyRuleSerializer(),
            404: openapi.Response(description="Limit rule not found"),
        },
    )
    def patch(self, request, rule_id: int):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        rule = get_policy_rule(rule_id, company_id)
        if not rule:
            return Response({"detail": "Limit rule not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = TravelPolicyRuleUpdateSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        data = serializer.validated_data

        if "applies_to" in data or "target_id" in data:
            applies_to = data.get("applies_to", rule["applies_to"])
            target_id = data.get("target_id") if applies_to != "all" else None
            data["target_id"] = target_id

            bad = _validate_limit_target(company_id, applies_to, target_id)
            if bad is not None:
                return bad

            if (
                applies_to == "all"
                and rule["applies_to"] != "all"
                and list_policy_rules_by_type(company_id, "all")
            ):
                return Response(
                    {"detail": "Global limit already exists. Use PATCH to update it."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        updated = update_policy_rule(rule_id, **data)
        if not updated:
            return Response({"detail": "Failed to update limit rule."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(
            TravelPolicyRuleSerializer(_hydrate_rule_with_target(company_id, updated)).data
        )

    @swagger_auto_schema(
        operation_summary="Delete a limit rule",
        responses={
            204: openapi.Response(description="Deleted"),
            404: openapi.Response(description="Limit rule not found"),
        },
    )
    def delete(self, request, rule_id: int):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        rule = get_policy_rule(rule_id, company_id)
        if not rule:
            return Response({"detail": "Limit rule not found."}, status=status.HTTP_404_NOT_FOUND)
        delete_policy_rule(rule_id)
        return Response(status=status.HTTP_204_NO_CONTENT)
