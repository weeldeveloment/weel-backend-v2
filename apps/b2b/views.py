from __future__ import annotations

import logging
import random
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

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

from apps.b2b.passport_ocr import PassportOCRError, extract_passport_data
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
    delete_employee,
    delete_policy_rule,
    delete_trip,
    get_company,
    get_dashboard_summary,
    get_department_monthly_spending,
    get_department_spending,
    get_employee,
    get_hotel_booking_request,
    get_or_create_travel_policy,
    get_policy_rule,
    get_spending_overview,
    get_top_employees_by_trip_count,
    get_top_hotels_by_booking_count,
    get_trip,
    get_voucher,
    list_active_trip_employees,
    list_budget_requests,
    list_departments,
    list_departments_with_budget,
    list_employees,
    list_hotel_booking_requests,
    list_hotel_booking_rooms,
    list_policy_rules,
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
from apps.b2b.models import DepartmentBudgetStatus, EmployeeRole
from apps.b2b.hotel_booking_service import (
    HotelBookingError,
    booking_detail,
    cancel_booking_request,
    create_booking_request,
    reconcile_booking_request,
)
from apps.b2b.permissions import IsB2BOwner, IsB2BOwnerOrPerformer
from apps.b2b.tasks import _send_b2b_lead_telegram_notification
from apps.property.hotel_repository import _run_in_schema, get_hotel_for_public, resolve_hotel_guid
from apps.hotels.repository import count_hotels, get_available_rooms, get_hotel_calendar, search_hotels
from apps.hotels.serializers import (
    HotelCardSerializer,
    HotelSearchParamsSerializer,
    HotelSearchPageSerializer,
    RoomAvailabilitySerializer,
    RoomSelectParamsSerializer,
)
from apps.b2b.serializers import (
    ActiveTripEmployeeSerializer,
    ActiveTripEmployeesResponseSerializer,
    B2BCompanySerializer,
    B2BDepartmentSerializer,
    B2BDepartmentSummarySerializer,
    B2BEmployeeCreateSerializer,
    B2BEmployeeSerializer,
    B2BHotelCalendarSerializer,
    B2BUserSerializer,
    BudgetRequestListResponseSerializer,
    BudgetRequestSerializer,
    BusinessTripSerializer,
    DashboardSummarySerializer,
    DepartmentMonthlySpendingSerializer,
    StatisticsResponseSerializer,
    HotelBookingRequestCreateSerializer,
    HotelBookingRequestDetailSerializer,
    HotelBookingRequestSerializer,
    B2BLeadRequestSerializer,
    RecentTripEmployeeSerializer,
    ReviewBudgetRequestSerializer,
    TopEmployeeByTripsSerializer,
    TopHotelByBookingsSerializer,
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


class B2BHotelSearchView(APIView):
    """GET /b2b/hotels/search/

    Hotel search and filtering for B2B owners and performers. Used to choose
    a suitable hotel for sending employees on a business trip.
    """
    permission_classes = [IsAuthenticated, IsB2BOwnerOrPerformer]

    @swagger_auto_schema(
        operation_summary="Search and filter hotels (owner or performer)",
        operation_description=(
            "Choose a hotel for a business trip. Use `sort_by` for sorting: "
            "`popular`, `weel_recommended`, `cheap`, or `expensive`. For map-"
            "based selection, provide `lat`, `lon`, and `radius_km`. If "
            "`check_in`, `check_out`, and `guests` are provided, only hotels "
            "that can accommodate the stay are returned. If one room is not "
            "enough, the response includes the best matching room combination "
            "for that hotel as `matching_rooms` (for example, two rooms with "
            "capacities 3 and 4 for 7 guests). If `budget_max` is provided, "
            "the hotel and matching room selection must stay within the total "
            "estimated price for the selected dates. Each result includes "
            "`total_estimated_price`. `guid` is more reliable than the numeric "
            "`id` because the hotel can be searched across multiple tenant "
            "schemas."
        ),
        query_serializer=HotelSearchParamsSerializer,
        responses={200: HotelSearchPageSerializer()},
        tags=["B2B / Executer"],
    )
    def get(self, request):
        params = HotelSearchParamsSerializer(data=request.query_params)
        if not params.is_valid():
            return Response(params.errors, status=status.HTTP_400_BAD_REQUEST)

        d = params.validated_data
        page = d.pop("page")
        page_size = d.pop("page_size")
        offset = (page - 1) * page_size
        d.pop("adults", None)
        d.pop("children", None)
        d.pop("babies", None)

        hotels = search_hotels(**d, limit=page_size, offset=offset)
        count = count_hotels(**{k: v for k, v in d.items() if k != "sort_by"})
        return Response({
            "count": count,
            "page": page,
            "page_size": page_size,
            "results": HotelCardSerializer(hotels, many=True).data,
        })


class B2BHotelRoomsView(APIView):
    """GET /b2b/hotels/<hotel_guid>/rooms/

    Step 2, part 1: list the available rooms for the hotel and dates chosen
    in step 1. Each room's `capacity` shows how many employees can be placed
    there.
    """
    permission_classes = [IsAuthenticated, IsB2BOwnerOrPerformer]

    @swagger_auto_schema(
        operation_summary="List hotel rooms (owner or performer, step 2)",
        operation_description=(
            "For the selected `hotel_guid`, return the available rooms for the "
            "same `check_in`/`check_out`/`guests` values chosen in step 1. "
            "Each room's `capacity` indicates how many employees can be "
            "assigned to it, usually 1 or 2."
        ),
        query_serializer=RoomSelectParamsSerializer,
        responses={
            200: RoomAvailabilitySerializer(many=True),
            400: openapi.Response(description="Invalid hotel_guid / validation error."),
            404: openapi.Response(description="Hotel not found."),
        },
        tags=["B2B / Executer"],
    )
    def get(self, request, hotel_guid):
        resolved = resolve_hotel_guid(hotel_guid)
        if not resolved:
            return Response({"detail": "Invalid hotel_guid."}, status=status.HTTP_400_BAD_REQUEST)
        schema_name, hotel_id = resolved

        params = RoomSelectParamsSerializer(data=request.query_params)
        if not params.is_valid():
            return Response(params.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            rooms = _run_in_schema(
                schema_name,
                lambda: get_available_rooms(
                    hotel_id,
                    check_in=params.validated_data["check_in"],
                    check_out=params.validated_data["check_out"],
                    guests=params.validated_data["guests"],
                    room_types=params.validated_data.get("room_types"),
                    room_type_presets=params.validated_data.get("room_type_presets"),
                    rate_plans=params.validated_data.get("rate_plans"),
                    meal_plans=params.validated_data.get("meal_plans"),
                    min_capacity=params.validated_data.get("min_capacity"),
                    max_capacity=params.validated_data.get("max_capacity"),
                ),
            )
        except Exception:
            return Response({"detail": "Hotel not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(RoomAvailabilitySerializer(rooms, many=True).data)


class B2BHotelBookingListCreateView(APIView):
    """GET/POST /b2b/hotels/bookings/

    Step 2, part 2: submit one booking request containing the selected rooms
    and assigned employees. A single request may contain multiple rooms and
    therefore multiple `pms_booking` rows, but they are shown as one entry in
    booking history.
    """
    def get_permissions(self):
        return [IsAuthenticated(), IsB2BOwnerOrPerformer()]

    @swagger_auto_schema(
        operation_summary="List company booking requests",
        operation_description=(
            "Each booking request, including requests with multiple rooms and "
            "employees, appears here as a single row with `room_count` and "
            "`employee_count`. For the full details, use "
            "`GET /b2b/hotels/bookings/<id>/`."
        ),
        manual_parameters=[
            openapi.Parameter("trip_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER, description="Filter by business trip."),
            openapi.Parameter(
                "status", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                enum=["pending", "confirmed", "rejected", "cancelled"],
                description="Filter by status.",
            ),
        ],
        responses={200: HotelBookingRequestSerializer(many=True)},
        tags=["B2B / Hotels"],
    )
    def get(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        trip_id = request.query_params.get("trip_id")
        req_status = request.query_params.get("status")
        try:
            parsed_trip_id = int(trip_id) if trip_id else None
        except (TypeError, ValueError):
            return Response({"detail": "trip_id must be an integer."}, status=status.HTTP_400_BAD_REQUEST)
        rows = list_hotel_booking_requests(company_id, trip_id=parsed_trip_id)
        rows = [reconcile_booking_request(row) for row in rows]
        if req_status:
            rows = [row for row in rows if row.get("status") == req_status]
        return Response(HotelBookingRequestSerializer(rows, many=True).data)

    @swagger_auto_schema(
        operation_summary="Submit a booking request (rooms + employees, owner or performer)",
        operation_description=(
            "Final step 2 submission: send `hotel_guid`, dates, and the "
            "employees assigned to each room (`employee_ids`, 1 or 2 people "
            "per room depending on capacity). The server will: (1) re-check "
            "availability for each room, (2) create a `pms_booking` for each "
            "room in the hotel's tenant schema, and (3) attach employees to "
            "the trip's `TripEmployee` rows. The whole process runs in a "
            "single transaction, so if any room is unavailable nothing is "
            "created. The resulting hotel booking request starts in "
            "`pending`; if the hotel accepts it becomes `confirmed`, and if "
            "it is rejected it becomes `rejected`."
        ),
        request_body=HotelBookingRequestCreateSerializer,
        responses={
            201: HotelBookingRequestDetailSerializer(),
            400: openapi.Response(description="Validation error, date, capacity, or budget violation."),
            404: openapi.Response(description="Trip, hotel or employee not found."),
            409: openapi.Response(description="Room or employee is no longer available."),
        },
        tags=["B2B / Hotels"],
    )
    def post(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)

        serializer = HotelBookingRequestCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        try:
            booking_request = create_booking_request(
                company_id=company_id,
                requested_by=_get_user_id(request),
                data=serializer.validated_data,
            )
        except HotelBookingError as exc:
            return Response({"detail": exc.detail}, status=exc.status_code)
        return Response(
            HotelBookingRequestDetailSerializer(booking_request).data,
            status=status.HTTP_201_CREATED,
        )


class B2BHotelBookingDetailView(APIView):
    """GET /b2b/hotels/bookings/<booking_id>/

    Bitta bron so'rovining to'liq tafsiloti — bosishda "hammasi ko'rinadi":
    mehmonxona, sanalar, holat, va har bir xona + unga biriktirilgan
    xodimlar ro'yxati.
    """
    permission_classes = [IsAuthenticated, IsB2BOwnerOrPerformer]

    @swagger_auto_schema(
        operation_summary="Get company booking request details",
        responses={
            200: HotelBookingRequestDetailSerializer(),
            404: openapi.Response(description="Booking not found."),
        },
        tags=["B2B / Hotels"],
    )
    def get(self, request, booking_id):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        booking_request = get_hotel_booking_request(booking_id, company_id)
        if not booking_request:
            return Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        booking_request = reconcile_booking_request(booking_request)
        return Response(HotelBookingRequestDetailSerializer(booking_detail(booking_request)).data)


class B2BHotelBookingCancelView(APIView):
    permission_classes = [IsAuthenticated, IsB2BOwnerOrPerformer]

    @swagger_auto_schema(
        operation_summary="Cancel a grouped hotel booking",
        operation_description=(
            "Cancels every active room booking in the request. Only pending or "
            "confirmed bookings can be cancelled, and only before check-in."
        ),
        responses={
            200: HotelBookingRequestDetailSerializer(),
            404: openapi.Response(description="Booking not found."),
            409: openapi.Response(description="Booking can no longer be cancelled."),
        },
        tags=["B2B / Hotels"],
    )
    def post(self, request, booking_id):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            booking_request = cancel_booking_request(booking_id=booking_id, company_id=company_id)
        except HotelBookingError as exc:
            return Response({"detail": exc.detail}, status=exc.status_code)
        return Response(HotelBookingRequestDetailSerializer(booking_request).data)


class B2BHotelCalendarView(APIView):
    """GET /b2b/hotels/<hotel_guid>/calendar/

    Shows the full occupancy calendar for the hotel. Each date for each room
    is returned as ``booked`` or ``available``. Owners and performers use this
    view to inspect free dates before arranging a business trip.
    """
    permission_classes = [IsAuthenticated, IsB2BOwnerOrPerformer]

    @swagger_auto_schema(
        operation_summary="Hotel occupancy calendar",
        operation_description=(
            "Return the daily occupancy status for each active room in the "
            "selected hotel (`hotel_guid`) over the `from_date` to `to_date` "
            "range.\n\n"
            "Each row contains `room_id`, `room_name`, `date`, and `status` "
            "(`booked` or `available`). This includes both B2B bookings and "
            "bookings made through the hotel's own site, so owners and "
            "performers can see the real occupancy state and choose free dates "
            "accurately."
        ),
        manual_parameters=[
            openapi.Parameter(
                "hotel_guid", openapi.IN_PATH, type=openapi.TYPE_STRING,
                required=True, description="Hotel GUID identifier.",
            ),
            openapi.Parameter(
                "from_date", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                format=openapi.FORMAT_DATE, required=True,
                description="Start date for the calendar range (YYYY-MM-DD).",
            ),
            openapi.Parameter(
                "to_date", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                format=openapi.FORMAT_DATE, required=True,
                description="End date for the calendar range (YYYY-MM-DD).",
            ),
        ],
        responses={
            200: B2BHotelCalendarSerializer(many=True),
            400: openapi.Response(description="Invalid hotel_guid or date parameters."),
            404: openapi.Response(description="Hotel not found."),
        },
        tags=["B2B / Executer"],
    )
    def get(self, request, hotel_guid):
        resolved = resolve_hotel_guid(hotel_guid)
        if not resolved:
            return Response({"detail": "Invalid hotel_guid."}, status=status.HTTP_400_BAD_REQUEST)
        schema_name, hotel_id = resolved

        from_date_str = request.query_params.get("from_date")
        to_date_str = request.query_params.get("to_date")
        if not from_date_str or not to_date_str:
            return Response(
                {"detail": "from_date and to_date are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            from_date = date.fromisoformat(from_date_str)
            to_date = date.fromisoformat(to_date_str)
        except ValueError:
            return Response(
                {"detail": "Invalid date format. Use YYYY-MM-DD."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        room_types = [v.strip() for v in (request.query_params.get("room_types") or "").split(",") if v.strip()]
        room_type_presets = [v.strip() for v in (request.query_params.get("room_type_presets") or "").split(",") if v.strip()]
        include_summary = str(request.query_params.get("include_summary", "")).lower() in {"1", "true", "yes"}

        try:
            calendar = _run_in_schema(
                schema_name,
                lambda: get_hotel_calendar(
                    hotel_id,
                    from_date=from_date,
                    to_date=to_date,
                    room_types=room_types or None,
                    room_type_presets=room_type_presets or None,
                    include_summary=include_summary,
                ),
            )
        except Exception:
            return Response({"detail": "Hotel not found."}, status=status.HTTP_404_NOT_FOUND)
        if include_summary:
            return Response({
                "rows": B2BHotelCalendarSerializer(calendar["rows"], many=True).data,
                "summary": calendar["summary"],
            })
        return Response(B2BHotelCalendarSerializer(calendar, many=True).data)


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
        responses={200: B2BDepartmentSummarySerializer(many=True)},
    )
    def get(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)

        search = request.query_params.get("search")
        depts = list_departments_with_budget(company_id, search=search)
        employees_by_dept: dict[int, list[dict[str, Any]]] = {}
        for emp in list_employees(company_id):
            employees_by_dept.setdefault(emp["department_id"], []).append(emp)

        payload = []
        for d in depts:
            budget_limit = d["budget_limit"]
            used_amount = d["used_amount"]
            if budget_limit is None:
                remaining_amount = None
                dept_status = DepartmentBudgetStatus.NO_LIMIT
            else:
                remaining_amount = budget_limit - used_amount
                if remaining_amount <= 0:
                    dept_status = DepartmentBudgetStatus.EMPTY
                elif remaining_amount <= budget_limit * Decimal("0.25"):
                    dept_status = DepartmentBudgetStatus.LOW
                else:
                    dept_status = DepartmentBudgetStatus.HIGH
            payload.append({
                "id": d["department_id"],
                "company_id": d["company_id"],
                "name": d["department_name"],
                "budget_limit": budget_limit,
                "used_amount": used_amount,
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
        operation_summary="Add a new employee",
        operation_description=(
            "Adds a new employee to the company. `department_id`, `email`, "
            "`phone`, `passport_upload_front`, and `passport_upload_back` "
            "(front and back of the ID document) are required. `full_name`, "
            "`date_of_birth`, `passport_series`, and `passport_pinfl` are not "
            "accepted from the client; they are extracted automatically from "
            "the uploaded images using OCR."
        ),
        consumes=["multipart/form-data"],
        manual_parameters=[
            openapi.Parameter("department_id", openapi.IN_FORM, type=openapi.TYPE_INTEGER, required=True, description="Department ID (required)"),
            openapi.Parameter("email", openapi.IN_FORM, type=openapi.TYPE_STRING, format=openapi.FORMAT_EMAIL, required=True, description="Email address (required)"),
            openapi.Parameter("phone", openapi.IN_FORM, type=openapi.TYPE_STRING, required=True, description="Phone number (required)"),
            openapi.Parameter("passport_upload_front", openapi.IN_FORM, type=openapi.TYPE_FILE, required=True, description="Front side of the ID document (jpg, png; max 5MB)"),
            openapi.Parameter("passport_upload_back", openapi.IN_FORM, type=openapi.TYPE_FILE, required=True, description="Back side of the ID document with MRZ code (jpg, png; max 5MB)"),
            openapi.Parameter("photo", openapi.IN_FORM, type=openapi.TYPE_FILE, required=False, description="Employee profile photo (jpg, png; max 5MB, optional)"),
            openapi.Parameter("position", openapi.IN_FORM, type=openapi.TYPE_STRING, required=False, description="Job title"),
            openapi.Parameter("individual_limit", openapi.IN_FORM, type=openapi.TYPE_NUMBER, required=False, description="Individual limit for the employee"),
            openapi.Parameter("status", openapi.IN_FORM, type=openapi.TYPE_STRING, enum=["available", "on_trip", "blocked"], required=False, description="Employee status (default: available)"),
            openapi.Parameter("role", openapi.IN_FORM, type=openapi.TYPE_STRING, enum=["owner", "performer", "employee"], required=False, description="Employee role (default: employee)"),
        ],
        responses={
            201: B2BEmployeeSerializer(),
            400: openapi.Response(description="Validation error / Company context required / passport image does not match the template."),
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
                e["role"] == EmployeeRole.PERFORMER for e in list_employees(company_id)
            )
            if has_performer:
                return Response(
                    {"detail": "Company already has a performer employee."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        front_file = validated.pop("passport_upload_front")
        back_file = validated.pop("passport_upload_back")
        try:
            passport_data = extract_passport_data(front_file, back_file)
        except PassportOCRError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        front_path = default_storage.save(f"b2b/employees/passports/{front_file.name}", front_file)
        back_path = default_storage.save(f"b2b/employees/passports/{back_file.name}", back_file)

        photo_file = validated.pop("photo", None)
        photo_url = None
        if photo_file:
            photo_path = default_storage.save(f"b2b/employees/photos/{photo_file.name}", photo_file)
            photo_url = default_storage.url(photo_path)

        for field in ("full_name", "passport_pinfl", "date_of_birth", "passport_series"):
            validated.pop(field, None)
        employee = create_employee(
            company_id=company_id,
            full_name=passport_data["full_name"],
            date_of_birth=passport_data["date_of_birth"],
            passport_series=passport_data["passport_series"],
            passport_pinfl=passport_data["passport_pinfl"],
            passport_upload_front=default_storage.url(front_path),
            passport_upload_back=default_storage.url(back_path),
            photo=photo_url,
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

    @swagger_auto_schema(
        request_body=B2BEmployeeSerializer,
        operation_description=(
            """The 'owner' role is never assigned (resulting in a 400 error). If a user is designated as the 'performer', the company's current performer is automatically reassigned to the 'employee' role, and the new user becomes the performer."""
        ),
        responses={200: B2BEmployeeSerializer()},
    )
    def patch(self, request, employee_id):
        company_id = _get_company_id(request)
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
                if e["role"] == EmployeeRole.PERFORMER and e["id"] != employee_id:
                    update_employee(e["id"], role=EmployeeRole.EMPLOYEE)

        employee = update_employee(employee_id, **{
            k: v for k, v in serializer.validated_data.items()
            if k not in ("company_id", "department_name")
        })
        if not employee:
            return Response({"detail": "Employee not found."}, status=status.HTTP_404_NOT_FOUND)
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
    permission_classes = [IsAuthenticated]

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
        requests = list_budget_requests(company_id, status=req_status)
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
            requested_by=_get_user_id(request),
            amount=validated["amount"],
            description=validated.get("description"),
        )
        if not budget_req:
            return Response({"detail": "Failed to create budget request."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(BudgetRequestSerializer(budget_req).data, status=status.HTTP_201_CREATED)


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
            reviewed_by=_get_user_id(request),
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
      total_budget   – sum of trip budgets created in that window
      total_trips    – number of trips created in that window
      approved_spend – sum of approved budget-request amounts in that window
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


# ─── Top hotels by booking count ───────────────────────────────────────────

class TopHotelsByBookingsView(APIView):
    """GET /api/b2b/hotels/top-by-bookings/?limit=3

    Returns the hotels this company has booked the most, ordered by
    booking count descending.
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Top hotels by company booking count",
        operation_description=(
            "Return the hotels most frequently booked by the company, ordered "
            "by `booking_count` descending. For each hotel, `total_spend` is "
            "also returned as the total price of all bookings for that hotel. "
            "Default `limit=3`, maximum 100."
        ),
        manual_parameters=[
            openapi.Parameter(
                "limit",
                openapi.IN_QUERY,
                description="Number of hotels to return (1-100). Default 3.",
                type=openapi.TYPE_INTEGER,
                default=3,
            ),
        ],
        responses={
            200: TopHotelByBookingsSerializer(many=True),
            400: openapi.Response(description="Company context required."),
        },
    )
    def get(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        try:
            limit = int(request.query_params.get("limit", 3))
        except (TypeError, ValueError):
            limit = 3
        if limit <= 0 or limit > 100:
            limit = 3
        rows = get_top_hotels_by_booking_count(company_id, limit=limit)
        return Response(TopHotelByBookingsSerializer(rows, many=True).data)


# ─── Active / upcoming trip employees (yolda / borgan) ─────────────────────

_VALID_ACTIVE_TRIP_TYPES = {"yolda", "borgan", "all"}


def _enrich_with_pms_vouchers(rows: list[dict[str, Any]]) -> None:
    from shared.raw.db import fetch_all as _fetch_all

    by_schema: dict[str, set[int]] = {}
    for row in rows:
        schema = row.get("tenant_schema")
        pms_id = row.get("pms_booking_id")
        if schema and pms_id:
            by_schema.setdefault(schema, set()).add(pms_id)

    voucher_map: dict[tuple[str, int], str] = {}
    for schema, pms_ids in by_schema.items():
        placeholders = ", ".join(["%s"] * len(pms_ids))
        try:
            result = _run_in_schema(
                schema,
                lambda: _fetch_all(
                    f"SELECT id, voucher_number FROM pms_booking WHERE id IN ({placeholders}) AND voucher_number IS NOT NULL",
                    list(pms_ids),
                ),
            )
            for row in result or []:
                voucher_map[(schema, row["id"])] = row["voucher_number"]
        except Exception:
            pass

    for row in rows:
        schema = row.get("tenant_schema")
        pms_id = row.get("pms_booking_id")
        row["voucher_number"] = voucher_map.get((schema, pms_id)) if (schema and pms_id) else None


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
        operation_summary="Employees on a trip or about to depart",
        operation_description=(
            "Return employees attached to active (`active` or `pending`) trips "
            "whose assignments are not `cancelled` or `checked_out`. "
            "`type=yolda` returns employees whose trip dates include today, "
            "`type=borgan` returns employees whose trip starts in the future, "
            "and `type=all` (default) combines both groups."
        ),
        manual_parameters=[
            openapi.Parameter(
                "type",
                openapi.IN_QUERY,
                description="Filter type: yolda | borgan | all (default: all)",
                type=openapi.TYPE_STRING,
                enum=["yolda", "borgan", "all"],
                default="all",
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
                {"detail": f"Invalid type. Choose from: yolda, borgan, all."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        rows = list_active_trip_employees(company_id, type_=type_)
        _enrich_with_pms_vouchers(rows)
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
    """
    permission_classes = [IsAuthenticated]

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
    """PATCH / DELETE /api/b2b/travel-policy/limits/<rule_id>/"""
    permission_classes = [IsAuthenticated]

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
        updated = update_policy_rule(rule_id, **serializer.validated_data)
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
