from __future__ import annotations

import logging
import random
from datetime import timedelta
from typing import Any

from django.core.files.storage import default_storage
from django.db import IntegrityError
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema


from apps.b2b.repository import (
    add_trip_employee,
    create_budget_request,
    create_company,
    create_b2b_user,
    create_department,
    create_employee,
    create_policy_rule,
    create_trip,
    create_voucher,
    delete_policy_rule,
    get_company,
    get_dashboard_summary,
    get_department_monthly_spending,
    get_department_spending,
    get_employee,
    get_employee_month_spend,
    get_or_create_travel_policy,
    get_policy_rule,
    get_spending_overview,
    get_trip,
    get_voucher,
    list_active_trip_employees,
    list_budget_requests,
    list_departments,
    list_employees,
    list_policy_rules_by_type,
    list_recent_trip_employees,
    list_trip_employees,
    list_trips,
    review_budget_request,
    update_company,
    update_employee,
    update_policy_rule,
    update_travel_policy,
    update_trip,
)
from apps.b2b.serializers import (
    ActiveTripEmployeeSerializer,
    B2BCompanySerializer,
    B2BDepartmentSerializer,
    B2BEmployeeCreateSerializer,
    B2BEmployeeSerializer,
    B2BUserSerializer,
    BudgetRequestSerializer,
    BusinessTripSerializer,
    DashboardSummarySerializer,
    DepartmentMonthlySpendingSerializer,
    GlobalTravelLimitSerializer,
    RecentTripEmployeeSerializer,
    ReviewBudgetRequestSerializer,
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


class B2BCompanyView(APIView):
    permission_classes = [IsAuthenticated]

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

    @swagger_auto_schema(responses={200: B2BDepartmentSerializer(many=True)})
    def get(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        depts = list_departments(company_id)
        return Response(B2BDepartmentSerializer(depts, many=True).data)

    @swagger_auto_schema(request_body=B2BDepartmentSerializer, responses={201: B2BDepartmentSerializer()})
    def post(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        serializer = B2BDepartmentSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        dept = create_department(company_id=company_id, name=serializer.validated_data["name"])
        if not dept:
            return Response({"detail": "Failed to create department."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(B2BDepartmentSerializer(dept).data, status=status.HTTP_201_CREATED)


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
        operation_summary="Yangi xodim qo'shish",
        operation_description=(
            "Kompaniyaga yangi xodim qo'shadi. `department_id`, `email`, `phone`, "
            "`pinfl` va `passport_upload` (passport rasmi/fayli) — barchasi majburiy."
        ),
        consumes=["multipart/form-data"],
        manual_parameters=[
            openapi.Parameter("full_name", openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="Xodimning to'liq ismi"),
            openapi.Parameter("department_id", openapi.IN_FORM, type=openapi.TYPE_INTEGER, required=True, description="Bo'lim ID (majburiy)"),
            openapi.Parameter("email", openapi.IN_FORM, type=openapi.TYPE_STRING, format=openapi.FORMAT_EMAIL, required=True, description="Email (majburiy)"),
            openapi.Parameter("phone", openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="Telefon raqami (majburiy)"),
            openapi.Parameter("pinfl", openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="PINFL (majburiy)"),
            openapi.Parameter("passport_upload", openapi.IN_FORM, type=openapi.TYPE_FILE, required=True, description="Passport fayli/rasmi (pdf, jpg, png; maksimum 5MB)"),
            openapi.Parameter("position", openapi.IN_FORM, type=openapi.TYPE_STRING, required=False, description="Lavozimi"),
            openapi.Parameter("date_of_birth", openapi.IN_FORM, type=openapi.TYPE_STRING, format=openapi.FORMAT_DATE, required=False, description="Tug'ilgan sana (YYYY-MM-DD)"),
            openapi.Parameter("passport_series", openapi.IN_FORM, type=openapi.TYPE_STRING, required=False, description="Passport seriyasi"),
            openapi.Parameter("passport_number", openapi.IN_FORM, type=openapi.TYPE_STRING, required=False, description="Passport raqami"),
            openapi.Parameter("individual_limit", openapi.IN_FORM, type=openapi.TYPE_NUMBER, required=False, description="Xodim uchun individual limit"),
            openapi.Parameter("status", openapi.IN_FORM, type=openapi.TYPE_STRING, enum=["available", "on_trip", "blocked"], required=False, description="Xodim holati (default: available)"),
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
        passport_file = validated.pop("passport_upload")
        saved_path = default_storage.save(f"b2b/employees/passports/{passport_file.name}", passport_file)
        employee = create_employee(
            company_id=company_id,
            full_name=validated.pop("full_name"),
            passport_upload=default_storage.url(saved_path),
            **{k: v for k, v in validated.items() if k not in ("company_id", "department_name")},
        )
        if not employee:
            return Response({"detail": "Failed to create employee."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(B2BEmployeeSerializer(employee).data, status=status.HTTP_201_CREATED)


class B2BEmployeeRetrieveUpdateView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(responses={200: B2BEmployeeSerializer()})
    def get(self, request, employee_id):
        company_id = _get_company_id(request)
        employee = get_employee(employee_id, company_id)
        if not employee:
            return Response({"detail": "Employee not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(B2BEmployeeSerializer(employee).data)

    @swagger_auto_schema(request_body=B2BEmployeeSerializer, responses={200: B2BEmployeeSerializer()})
    def patch(self, request, employee_id):
        company_id = _get_company_id(request)
        serializer = B2BEmployeeSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        employee = update_employee(employee_id, **{
            k: v for k, v in serializer.validated_data.items()
            if k not in ("company_id", "department_name")
        })
        if not employee:
            return Response({"detail": "Employee not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(B2BEmployeeSerializer(employee).data)


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
            created_by=_get_user_id(request),
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
    permission_classes = [IsAuthenticated]

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


class BudgetRequestListCreateView(APIView):
    """GET/POST /b2b/budget-requests/

    A budget request is how an executer (performer, e.g. the one who sends an
    employee on a business trip) asks the owner for extra budget when an
    employee's spend for the current calendar month would go over that
    employee's owner-set monthly cap (``B2BEmployee.individual_limit``).

    - The executer presses one button (``POST``) with the trip/employee/amount.
    - If this month's already-approved spend for that employee + the new
      ``amount`` still fits within ``individual_limit`` (or no limit is set),
      the request is auto-approved immediately — no owner action needed.
    - If it would exceed the monthly limit, the request is stored ``pending``,
      tagged with the executer as ``requested_by``, and shows up for the
      owner via ``GET ?status=pending`` to review (approve/reject) via
      ``POST /budget-requests/<id>/review/``.
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Byudjet so'rovlarini olish (owner uchun)",
        operation_description=(
            "Kompaniyaning barcha byudjet so'rovlarini qaytaradi. "
            "`status=pending` bilan filtrlab, owner tasdig'ini kutayotgan — "
            "ya'ni xodimning oylik individual_limit'idan oshib ketgan va "
            "executer tomonidan yuborilgan so'rovlarni ko'rish mumkin."
        ),
        manual_parameters=[
            openapi.Parameter(
                "status", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                enum=["pending", "approved", "rejected"],
                description="Holat bo'yicha filtr. Owner uchun odatda `pending`.",
            ),
        ],
        responses={200: BudgetRequestSerializer(many=True)},
    )
    def get(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        req_status = request.query_params.get("status")
        requests = list_budget_requests(company_id, status=req_status)
        return Response(BudgetRequestSerializer(requests, many=True).data)

    @swagger_auto_schema(
        operation_summary="Byudjet so'rovi yuborish (oylik limit yetmasa ownerga boradi)",
        operation_description=(
            "Executer (komandirovkaga xodim jo'natayotgan foydalanuvchi) "
            "xodim uchun qo'shimcha summa so'raydi — bitta tugma bosish "
            "yetarli. Tizim shu oy uchun xodimning allaqachon tasdiqlangan "
            "xarajatlari + yangi `amount` ni xodimning oylik "
            "`individual_limit`i bilan solishtiradi:\n\n"
            "- Agar limit yetsa — so'rov avtomatik `approved` bo'ladi, "
            "owner aralashuvi shart emas.\n"
            "- Agar limit yetmasa — so'rov `pending` holatda, so'rovchi "
            "(executer) `requested_by` sifatida saqlanadi va owner uni "
            "`GET ?status=pending` orqali ko'rib, tasdiqlaydi/rad etadi."
        ),
        request_body=BudgetRequestSerializer,
        responses={
            201: BudgetRequestSerializer(),
            400: openapi.Response(description="Validation error / Company context required."),
            404: openapi.Response(description="Employee or trip not found."),
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

        employee = get_employee(validated["employee_id"], company_id)
        if not employee:
            return Response({"detail": "Employee not found."}, status=status.HTTP_404_NOT_FOUND)
        trip = get_trip(validated["trip_id"], company_id)
        if not trip:
            return Response({"detail": "Trip not found."}, status=status.HTTP_404_NOT_FOUND)

        amount = validated["amount"]
        individual_limit = employee.get("individual_limit")
        if individual_limit is None:
            within_limit = True
        else:
            now = timezone.now()
            month_spend = get_employee_month_spend(employee["id"], now.year, now.month)
            within_limit = (month_spend + amount) <= individual_limit

        budget_req = create_budget_request(
            trip_id=validated["trip_id"],
            employee_id=validated["employee_id"],
            requested_by=_get_user_id(request),
            amount=amount,
            reason=validated["reason"],
            auto_approved=within_limit,
        )
        if not budget_req:
            return Response({"detail": "Failed to create budget request."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(BudgetRequestSerializer(budget_req).data, status=status.HTTP_201_CREATED)


class BudgetRequestReviewView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(request_body=ReviewBudgetRequestSerializer, responses={200: BudgetRequestSerializer()})
    def post(self, request, request_id):
        serializer = ReviewBudgetRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        result = review_budget_request(
            request_id=request_id,
            status=serializer.validated_data["status"],
            reviewed_by=_get_user_id(request),
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
      total_budget   – sum of trip budgets created in that window
      total_trips    – number of trips created in that window
      approved_spend – sum of approved budget-request amounts in that window
    """

    permission_classes = [IsAuthenticated]

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
        operation_summary="Dashboard uchun 4 ta asosiy statistika",
        operation_description=(
            "Umumiy oylik limit (`monthly_limit`), shu oy sarflangan summa "
            "(`spent_this_month`), komandirovkada yoki rejada turgan xodimlar "
            "soni (`active_employees`) va owner tomonidan ko'rib chiqilishi "
            "kutilayotgan limit oshirish so'rovlari sonini (`pending_limit_requests`) "
            "qaytaradi."
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
        operation_summary="Oxirgi komandirovkaga biriktirilgan employee lar",
        operation_description=(
            "Kompaniya bo'yicha eng so'nggi komandirovkalarga biriktirilgan "
            "employee larni qaytaradi. Default `limit=5`, maksimum 100."
        ),
        manual_parameters=[
            openapi.Parameter(
                "limit",
                openapi.IN_QUERY,
                description="Qaytariladigan employee lar soni (1-100). Default 5.",
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


# ─── Active / upcoming trip employees (yolda / borgan) ─────────────────────

_VALID_ACTIVE_TRIP_TYPES = {"yolda", "borgan", "all"}


class ActiveTripEmployeesView(APIView):
    """GET /api/b2b/trips/active-employees/?type=yolda|borgan|all

    Returns employees that are currently on a business trip or about to go
    on one. Scoped to the authenticated company.

    Query params:
        type: ``"yolda"``  – today is between the trip's ``start_date`` and
                              ``end_date`` (currently travelling).
               ``"borgan"`` – trip starts in the future (upcoming trip).
               ``"all"``    – both groups combined (default).

    Trip must be in ``active`` or ``pending`` status and the trip-employee
    assignment must not be ``cancelled`` or ``checked_out``.
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Komandirovkada yoki ketayotgan xodimlar",
        operation_description=(
            "Faol (``active`` yoki ``pending``) tripga biriktirilgan va "
            "``cancelled``/``checked_out`` bo'lmagan xodimlarni qaytaradi. "
            "`type=yolda` bugun ``start_date`` va ``end_date`` orasida "
            "bo'lganlarni, `type=borgan` esa ``start_date`` kelajakda "
            "bo'lganlarni qaytaradi. `type=all` (default) ikkalasini birlashtiradi."
        ),
        manual_parameters=[
            openapi.Parameter(
                "type",
                openapi.IN_QUERY,
                description="Filtr turi: yolda | borgan | all (default: all)",
                type=openapi.TYPE_STRING,
                enum=["yolda", "borgan", "all"],
                default="all",
            ),
        ],
        responses={
            200: ActiveTripEmployeeSerializer(many=True),
            400: openapi.Response(description="Company context required."),
        },
    )
    def get(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)

        type_ = request.query_params.get("type", "all")
        if type_ not in _VALID_ACTIVE_TRIP_TYPES:
            return Response(
                {"detail": f"Invalid type. Choose from: yolda, borgan, all."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        rows = list_active_trip_employees(company_id, type_=type_)
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
        operation_summary="Har bir department bo'yicha oylik xarajatlar",
        operation_description=(
            "Tanlangan oy uchun har bir department ning approved "
            "budget_request summalari va trip sonini qaytaradi. "
            "`month` ko'rsatilmasa joriy oy ishlatiladi."
        ),
        manual_parameters=[
            openapi.Parameter(
                "month",
                openapi.IN_QUERY,
                description="YYYY-MM formatidagi oy. Bo'sh qoldirilsa, joriy oy.",
                type=openapi.TYPE_STRING,
                format=openapi.FORMAT_DATE,
                example="2026-06",
            ),
        ],
        responses={
            200: openapi.Response(
                description="Har bir department uchun oylik xarajatlar",
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
        payload = [
            {
                "department_id": r["department_id"],
                "department_name": r["department_name"],
                "month_trips": r["month_trips"] or 0,
                "total_employees": r["total_employees"] or 0,
                "month_spend": str(r["month_spend"] or "0"),
            }
            for r in rows
        ]
        return Response({
            "year": year,
            "month": month,
            "departments": DepartmentMonthlySpendingSerializer(payload, many=True).data,
        })


# ─── Travel limit helpers ───────────────────────────────────────────────────

def _hydrate_rule_with_target(company_id: int, rule: dict[str, Any]) -> dict[str, Any]:
    """Decorate a TravelPolicyRule row with the human-readable name of the
    department / employee it targets (if any)."""
    out = dict(rule)
    out["target_name"] = None
    applies_to = rule.get("applies_to")
    target_id = rule.get("target_id")
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


class GlobalTravelLimitView(APIView):
    """GET / PATCH /api/b2b/travel-policy/limits/global/

    Company-wide limit that applies to *all* employees unless a more specific
    (per-department or per-employee) rule overrides it.

    Backed by the existing ``b2b_travel_policy`` row.
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Global (hamma uchun) komandirovka limitini olish",
        operation_description=(
            "Kompaniya darajasidagi `budget_per_trip` (bir komandirovka uchun "
            "maksimum) va `monthly_budget` (oylik limit) qiymatlarini qaytaradi. "
            "Agar policy mavjud bo'lmasa, avtomatik yaratiladi."
        ),
        responses={200: GlobalTravelLimitSerializer()},
    )
    def get(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        policy = get_or_create_travel_policy(company_id)
        return Response(GlobalTravelLimitSerializer(policy).data)

    @swagger_auto_schema(
        operation_summary="Global limitni o'zgartirish",
        request_body=GlobalTravelLimitSerializer,
        responses={200: GlobalTravelLimitSerializer()},
    )
    def patch(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        serializer = GlobalTravelLimitSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        policy = update_travel_policy(company_id, **serializer.validated_data)
        if not policy:
            return Response({"detail": "Failed to update global limit."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(GlobalTravelLimitSerializer(policy).data)


class _TravelLimitListCreateView(APIView):
    """Internal base for the per-department and per-employee limit endpoints.

    Concrete subclasses define ``applies_to`` ("department" or "employee") and
    override ``_validate_target`` to ensure the ``target_id`` refers to a real
    row in the right table.
    """
    applies_to: str = ""  # set in subclasses
    target_label: str = ""  # "department" or "employee" — used in error messages

    def _validate_target(self, company_id: int, target_id: int | None) -> Response | None:
        """Return ``None`` on success, otherwise a ready-to-return 4xx Response."""
        raise NotImplementedError

    @swagger_auto_schema(
        operation_summary="Limit qoidalarini olish",
        responses={200: TravelPolicyRuleSerializer(many=True)},
    )
    def get(self, request, **_):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        rules = list_policy_rules_by_type(company_id, self.applies_to)
        return Response(TravelPolicyRuleSerializer(
            [_hydrate_rule_with_target(company_id, r) for r in rules], many=True
        ).data)

    @swagger_auto_schema(
        operation_summary="Yangi limit qoidasi qo'shish",
        request_body=TravelPolicyRuleCreateSerializer,
        responses={
            201: TravelPolicyRuleSerializer(),
            400: openapi.Response(description="Validation error / wrong applies_to / missing target_id"),
            404: openapi.Response(description="Target department/employee not found"),
        },
    )
    def post(self, request, **_):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        serializer = TravelPolicyRuleCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        data = serializer.validated_data

        # Force the applies_to to match the concrete endpoint — callers cannot
        # create a department rule via the employee endpoint or vice versa.
        if data.get("applies_to") and data["applies_to"] != self.applies_to:
            return Response(
                {"detail": f"applies_to must be '{self.applies_to}' for this endpoint."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        target_id = data.get("target_id")
        bad = self._validate_target(company_id, target_id)
        if bad is not None:
            return bad

        rule = create_policy_rule(
            company_id=company_id,
            applies_to=self.applies_to,
            target_id=target_id,
            budget_limit=data.get("budget_limit"),
        )
        if not rule:
            return Response({"detail": "Failed to create limit rule."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(
            TravelPolicyRuleSerializer(_hydrate_rule_with_target(company_id, rule)).data,
            status=status.HTTP_201_CREATED,
        )


class _TravelLimitDetailView(APIView):
    """Internal base for PATCH/DELETE on a single limit rule."""
    applies_to: str = ""

    def _validate_target(self, company_id: int, target_id: int | None) -> Response | None:
        raise NotImplementedError

    def _get_scoped_rule(self, rule_id: int, company_id: int) -> dict | None:
        rule = get_policy_rule(rule_id, company_id)
        if not rule or rule.get("applies_to") != self.applies_to:
            return None
        return rule

    @swagger_auto_schema(
        operation_summary="Limit qoidasini tahrirlash",
        request_body=TravelPolicyRuleUpdateSerializer,
        responses={
            200: TravelPolicyRuleSerializer(),
            404: openapi.Response(description="Limit rule not found"),
        },
    )
    def patch(self, request, rule_id: int, **_):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        rule = self._get_scoped_rule(rule_id, company_id)
        if not rule:
            return Response({"detail": "Limit rule not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = TravelPolicyRuleUpdateSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        if "budget_limit" in serializer.validated_data:
            new_target_id = serializer.validated_data.get("target_id", rule.get("target_id"))
            bad = self._validate_target(company_id, new_target_id)
            if bad is not None:
                return bad
        updated = update_policy_rule(rule_id, **serializer.validated_data)
        if not updated:
            return Response({"detail": "Failed to update limit rule."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(
            TravelPolicyRuleSerializer(_hydrate_rule_with_target(company_id, updated)).data
        )

    @swagger_auto_schema(
        operation_summary="Limit qoidasini o'chirish",
        responses={
            204: openapi.Response(description="Deleted"),
            404: openapi.Response(description="Limit rule not found"),
        },
    )
    def delete(self, request, rule_id: int, **_):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        rule = self._get_scoped_rule(rule_id, company_id)
        if not rule:
            return Response({"detail": "Limit rule not found."}, status=status.HTTP_404_NOT_FOUND)
        delete_policy_rule(rule_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class DepartmentTravelLimitsView(_TravelLimitListCreateView):
    applies_to = "department"
    target_label = "department"

    @swagger_auto_schema(
        operation_summary="Departmentlar uchun limitlarni olish",
        operation_description="Kompaniya departmentlari uchun o'rnatilgan barcha limitlarni qaytaradi.",
    )
    def get(self, request, **_):
        return super().get(request, **_)

    @swagger_auto_schema(
        operation_summary="Department uchun yangi limit qo'shish",
        operation_description=(
            "Tanlangan department uchun alohida budget_limit belgilaydi. "
            "`target_id` — department id si, `budget_limit` — ruxsat berilgan "
            "maksimum summa."
        ),
    )
    def post(self, request, **_):
        return super().post(request, **_)

    def _validate_target(self, company_id: int, target_id: int | None) -> Response | None:
        if not target_id:
            return Response({"detail": "target_id (department_id) is required."}, status=status.HTTP_400_BAD_REQUEST)
        dept = next((d for d in list_departments(company_id) if d["id"] == target_id), None)
        if not dept:
            return Response({"detail": "Department not found."}, status=status.HTTP_404_NOT_FOUND)
        return None


class DepartmentTravelLimitDetailView(_TravelLimitDetailView):
    applies_to = "department"

    @swagger_auto_schema(
        operation_summary="Department limitini tahrirlash",
        operation_description="Mavjud department limitining `budget_limit` qiymatini yangilaydi.",
    )
    def patch(self, request, rule_id: int, **_):
        return super().patch(request, rule_id, **_)

    @swagger_auto_schema(
        operation_summary="Department limitini o'chirish",
        operation_description="Department limit qoidasini o'chirib tashlaydi.",
    )
    def delete(self, request, rule_id: int, **_):
        return super().delete(request, rule_id, **_)

    def _validate_target(self, company_id: int, target_id: int | None) -> Response | None:
        if not target_id:
            return Response({"detail": "target_id (department_id) is required."}, status=status.HTTP_400_BAD_REQUEST)
        dept = next((d for d in list_departments(company_id) if d["id"] == target_id), None)
        if not dept:
            return Response({"detail": "Department not found."}, status=status.HTTP_404_NOT_FOUND)
        return None


class EmployeeTravelLimitsView(_TravelLimitListCreateView):
    applies_to = "employee"
    target_label = "employee"

    @swagger_auto_schema(
        operation_summary="Employeelar uchun limitlarni olish",
        operation_description="Kompaniya employeelari uchun o'rnatilgan barcha individual limitlarni qaytaradi.",
    )
    def get(self, request, **_):
        return super().get(request, **_)

    @swagger_auto_schema(
        operation_summary="Employee uchun yangi individual limit qo'shish",
        operation_description=(
            "Tanlangan employee uchun alohida budget_limit belgilaydi. "
            "`target_id` — employee id si."
        ),
    )
    def post(self, request, **_):
        return super().post(request, **_)

    def _validate_target(self, company_id: int, target_id: int | None) -> Response | None:
        if not target_id:
            return Response({"detail": "target_id (employee_id) is required."}, status=status.HTTP_400_BAD_REQUEST)
        emp = get_employee(target_id, company_id)
        if not emp:
            return Response({"detail": "Employee not found."}, status=status.HTTP_404_NOT_FOUND)
        return None


class EmployeeTravelLimitDetailView(_TravelLimitDetailView):
    applies_to = "employee"

    @swagger_auto_schema(
        operation_summary="Employee limitini tahrirlash",
        operation_description="Mavjud employee limitining `budget_limit` qiymatini yangilaydi.",
    )
    def patch(self, request, rule_id: int, **_):
        return super().patch(request, rule_id, **_)

    @swagger_auto_schema(
        operation_summary="Employee limitini o'chirish",
        operation_description="Employee limit qoidasini o'chirib tashlaydi.",
    )
    def delete(self, request, rule_id: int, **_):
        return super().delete(request, rule_id, **_)

    def _validate_target(self, company_id: int, target_id: int | None) -> Response | None:
        if not target_id:
            return Response({"detail": "target_id (employee_id) is required."}, status=status.HTTP_400_BAD_REQUEST)
        emp = get_employee(target_id, company_id)
        if not emp:
            return Response({"detail": "Employee not found."}, status=status.HTTP_404_NOT_FOUND)
        return None
