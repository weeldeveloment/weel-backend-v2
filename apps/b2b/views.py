from __future__ import annotations

import base64
import logging
import random
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from typing import Any
from urllib.parse import quote

import qrcode

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
    get_hotel_booking_request,
    get_hotel_monthly_summary,
    get_trip_status_summary,
    get_or_create_travel_policy,
    get_policy_rule,
    get_rule_used_amount,
    get_monthly_spending_chart,
    get_spending_chart,
    get_weekly_spending_chart,
    get_spending_overview,
    get_top_employees_by_trip_count,
    get_top_hotels_by_booking_count,
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
    list_hotel_booking_requests,
    list_hotel_booking_rooms,
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
from apps.b2b.hotel_booking_service import (
    HotelBookingError,
    booking_detail,
    cancel_booking_request,
    create_booking_request,
    reconcile_booking_request,
)
from apps.b2b.permissions import IsB2BOwner, IsB2BOwnerOrPerformer
from apps.b2b.tasks import _send_b2b_lead_telegram_notification
from apps.property.hotel_repository import _run_in_schema, _safe_schema_name, get_hotel_for_public, resolve_hotel_guid
from apps.hotels.repository import count_hotels, get_available_rooms, get_hotel_calendar, get_hotel_card_by_guid, search_hotels
from shared.raw.db import fetch_all
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
    B2BDepartmentUpdateSerializer,
    B2BDepartmentMoveEmployeesSerializer,
    B2BEmployeeCreateSerializer,
    B2BEmployeeLimitSerializer,
    B2BEmployeePassportPreviewSerializer,
    B2BEmployeeSerializer,
    B2BHotelCalendarSerializer,
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
    HotelBookingRequestCreateSerializer,
    HotelBookingRequestDetailSerializer,
    HotelBookingRequestSerializer,
    B2BLeadRequestSerializer,
    RecentTripEmployeeSerializer,
    ReviewBudgetRequestSerializer,
    TopEmployeeByTripsSerializer,
    TopHotelByBookingsSerializer,
    HotelMonthlySummarySerializer,
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


class B2BHotelCardView(APIView):
    """GET /b2b/hotels/<hotel_guid>/card/

    Fetch one hotel's search-result card by GUID — used to reopen the
    booking flow for an already-known hotel (e.g. clicking a "popular
    hotel" in analytics) without a fuzzy city/name search.
    """
    permission_classes = [IsAuthenticated, IsB2BOwnerOrPerformer]

    @swagger_auto_schema(
        operation_summary="Fetch a single hotel card by GUID",
        responses={200: HotelCardSerializer(), 404: openapi.Response(description="Hotel not found.")},
        tags=["B2B / Executer"],
    )
    def get(self, request, hotel_guid):
        hotel = get_hotel_card_by_guid(hotel_guid)
        if not hotel:
            return Response({"detail": "Hotel not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(HotelCardSerializer(hotel).data)


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
        except Exception:
            logger.exception("Passport OCR failed unexpectedly")
            return Response(
                {"detail": "Не удалось распознать паспортные данные. Попробуйте другое фото."},
                status=status.HTTP_400_BAD_REQUEST,
            )

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
        if employee["role"] == EmployeeRole.PERFORMER:
            sync_performer_b2b_login(company_id, employee)
        return Response(B2BEmployeeSerializer(employee).data, status=status.HTTP_201_CREATED)


class B2BEmployeePassportPreviewView(APIView):
    """Passport old/orqa skanidan OCR orqali ma'lumotlarni oldindan
    ko'rsatish uchun. Hech narsa saqlanmaydi (na fayl, na xodim) — bu shunchaki
    ``POST /b2b/employees/`` chaqirilishidan oldin foydalanuvchiga natijani
    ko'rsatish uchun. Yakuniy saqlashda ``B2BEmployeeListCreateView.post`` OCR'ni
    yana o'zi ishga tushiradi."""

    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Preview passport OCR extraction",
        operation_description=(
            "Accepts the front and back scans of an ID document and returns "
            "the fields extracted by OCR (full_name, date_of_birth, "
            "passport_series, passport_pinfl). Nothing is saved — files are "
            "processed in memory only."
        ),
        consumes=["multipart/form-data"],
        manual_parameters=[
            openapi.Parameter("passport_upload_front", openapi.IN_FORM, type=openapi.TYPE_FILE, required=True, description="Front side of the ID document"),
            openapi.Parameter("passport_upload_back", openapi.IN_FORM, type=openapi.TYPE_FILE, required=True, description="Back side of the ID document with MRZ code"),
        ],
        responses={
            200: openapi.Response(description="OCR extraction result"),
            400: openapi.Response(description="Validation error or passport image does not match the template."),
        },
    )
    def post(self, request):
        serializer = B2BEmployeePassportPreviewSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        front_file = serializer.validated_data["passport_upload_front"]
        back_file = serializer.validated_data["passport_upload_back"]
        try:
            passport_data = extract_passport_data(front_file, back_file)
        except PassportOCRError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            logger.exception("Passport OCR preview failed unexpectedly")
            return Response(
                {"detail": "Не удалось распознать паспортные данные. Попробуйте другое фото."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(passport_data, status=status.HTTP_200_OK)


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
                if e["role"] == EmployeeRole.PERFORMER and e["id"] != employee_id:
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
            requested_by=_get_user_id(request),
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
    `category` is derived: `hotel` if the underlying trip has a hotel
    booking, otherwise `trip`. `status` is the raw budget-request status
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


class HotelMonthlySummaryView(APIView):
    """GET /api/b2b/hotels/monthly-summary/

    Returns this calendar month's confirmed hotel spend, plus the top 5
    hotels booked this month ordered by booking count descending.
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="This month's hotel spend + top booked hotels",
        operation_description=(
            "`month_spend` is the sum of confirmed hotel bookings' room "
            "prices for the current calendar month. `top_hotels` lists up "
            "to 5 hotels booked this month, ordered by `booking_count` "
            "descending."
        ),
        responses={200: HotelMonthlySummarySerializer()},
        tags=["B2B / Statistics"],
    )
    def get(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)

        now = timezone.now()
        summary = get_hotel_monthly_summary(company_id, now.year, now.month, limit=5)
        return Response({
            "year": now.year,
            "month": now.month,
            "month_spend": summary["month_spend"],
            "top_hotels": summary["top_hotels"],
        })


class B2BHotelRecommendationsView(APIView):
    """GET /api/b2b/hotels/recommendations/?limit=4

    Hotel picks for the dashboard's "Рекомендация" widget. Each hotel's
    `limit_status` reflects whether its nightly price fits the company's
    travel-policy limit (the company-wide "all" tier rule). If the company
    has no such limit configured, every hotel comes back `within_limit`.
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Hotel recommendations for the dashboard",
        operation_description=(
            "Return up to `limit` recommended hotels (default 4, max 12). "
            "`limit_status` is `limit_exceeded` when the hotel's nightly "
            "price is above the company's travel-policy limit, otherwise "
            "`within_limit`. Without a configured company-wide limit, all "
            "hotels are returned as `within_limit`."
        ),
        manual_parameters=[
            openapi.Parameter(
                "limit",
                openapi.IN_QUERY,
                description="Number of hotels to return (1-12). Default 4.",
                type=openapi.TYPE_INTEGER,
                default=4,
            ),
        ],
        tags=["B2B / Statistics"],
    )
    def get(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            limit = int(request.query_params.get("limit", 4))
        except (TypeError, ValueError):
            limit = 4
        if limit <= 0 or limit > 12:
            limit = 4

        price_limit = None
        company_rules = list_policy_rules_by_type(company_id, "all")
        if company_rules and company_rules[0].get("budget_limit") is not None:
            price_limit = Decimal(str(company_rules[0]["budget_limit"]))

        hotels = search_hotels(sort_by="weel_recommended", limit=limit)

        results = []
        for hotel in hotels:
            raw_price = hotel.get("min_price")
            price = Decimal(str(raw_price)) if raw_price is not None else None
            is_exceeded = price_limit is not None and price is not None and price > price_limit
            photos = hotel.get("photos") or []
            rating = hotel.get("rating")
            results.append({
                "id": hotel["id"],
                "guid": hotel.get("guid"),
                "name": hotel.get("name") or "",
                "city": hotel.get("city") or "",
                "country": hotel.get("country") or "",
                "rating": float(rating) if rating is not None else 0.0,
                "reviews_count": hotel.get("review_count") or 0,
                "price_per_night": float(price) if price is not None else 0.0,
                "photo_url": photos[0] if photos else "",
                "limit_status": "limit_exceeded" if is_exceeded else "within_limit",
            })

        return Response(results)


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


def _enrich_with_pms_vouchers(rows: list[dict[str, Any]]) -> None:
    by_schema: dict[str, set[int]] = {}
    for row in rows:
        schema = row.get("tenant_schema")
        pms_id = row.get("pms_booking_id")
        if schema and pms_id and _safe_schema_name(schema):
            by_schema.setdefault(schema, set()).add(pms_id)

    if not by_schema:
        return

    parts: list[str] = []
    params: list[Any] = []
    for schema, pms_ids in by_schema.items():
        placeholders = ", ".join(["%s"] * len(pms_ids))
        parts.append(
            f"SELECT %s AS _schema, id, voucher_number "
            f"FROM {schema}.pms_booking "
            f"WHERE id IN ({placeholders}) AND voucher_number IS NOT NULL"
        )
        params.append(schema)
        params.extend(pms_ids)

    union_sql = " UNION ALL ".join(parts)

    try:
        result = fetch_all(union_sql, params)
        voucher_map: dict[tuple[str, int], str] = {
            (row["_schema"], row["id"]): row["voucher_number"] for row in result
        }
    except Exception:
        voucher_map = {}

    for row in rows:
        schema = row.get("tenant_schema")
        pms_id = row.get("pms_booking_id")
        row["voucher_number"] = voucher_map.get((schema, pms_id)) if (schema and pms_id) else None


def _build_maps_url(address: str | None, latitude: Any, longitude: Any) -> str | None:
    if latitude is not None and longitude is not None:
        try:
            return f"https://www.google.com/maps?q={float(latitude)},{float(longitude)}"
        except (TypeError, ValueError):
            pass
    if address:
        return f"https://www.google.com/maps/search/?api=1&query={quote(address)}"
    return None


def _generate_qr_data_uri(data: str) -> str | None:
    try:
        img = qrcode.make(data)
        buf = BytesIO()
        img.save(buf, format="PNG")
        encoded = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/png;base64,{encoded}"
    except Exception:
        logger.exception("Failed to generate hotel location QR code")
        return None


def _format_hhmm(value: str | None) -> str | None:
    """``"14:00:00"`` (or ``"14:00"``) -> ``"14:00"``."""
    if not value:
        return None
    return value[:5]


def _enrich_with_hotel_location(rows: list[dict[str, Any]]) -> None:
    """Attach ``hotel_address``, ``hotel_maps_url`` and ``hotel_qr`` (a data-URI
    PNG QR code encoding the maps link) to each row, sourced from the hotel's
    own tenant schema (``pms_property``)."""
    by_schema: dict[str, set[int]] = {}
    for row in rows:
        schema = row.get("tenant_schema")
        property_id = row.get("hotel_property_id")
        if schema and property_id and _safe_schema_name(schema):
            by_schema.setdefault(schema, set()).add(property_id)

    if not by_schema:
        return

    parts: list[str] = []
    params: list[Any] = []
    for schema, property_ids in by_schema.items():
        placeholders = ", ".join(["%s"] * len(property_ids))
        parts.append(
            f"SELECT %s AS _schema, id, address, full_address, "
            f"latitude::text AS latitude, longitude::text AS longitude, "
            f"check_in_time::text AS check_in_time, check_out_time::text AS check_out_time "
            f"FROM {schema}.pms_property "
            f"WHERE id IN ({placeholders})"
        )
        params.append(schema)
        params.extend(property_ids)

    union_sql = " UNION ALL ".join(parts)

    try:
        result = fetch_all(union_sql, params)
        location_map: dict[tuple[str, int], dict[str, Any]] = {
            (row["_schema"], row["id"]): row for row in result
        }
    except Exception:
        location_map = {}

    for row in rows:
        schema = row.get("tenant_schema")
        property_id = row.get("hotel_property_id")
        location = location_map.get((schema, property_id)) if (schema and property_id) else None
        if not location:
            row["hotel_address"] = None
            row["hotel_maps_url"] = None
            row["hotel_qr"] = None
            row["hotel_check_in_time"] = None
            row["hotel_check_out_time"] = None
            continue

        address = location.get("full_address") or location.get("address")
        maps_url = _build_maps_url(address, location.get("latitude"), location.get("longitude"))
        row["hotel_address"] = address
        row["hotel_maps_url"] = maps_url
        row["hotel_qr"] = _generate_qr_data_uri(maps_url) if maps_url else None
        row["hotel_check_in_time"] = _format_hhmm(location.get("check_in_time"))
        row["hotel_check_out_time"] = _format_hhmm(location.get("check_out_time"))


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
            _enrich_with_pms_vouchers(rows)
            _enrich_with_hotel_location(rows)
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
