from __future__ import annotations

import logging
import random
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema


from apps.b2b.repository import (
    add_hotel_booking_room,
    add_hotel_booking_room_employee,
    add_trip_employee,
    create_budget_request,
    create_company,
    create_b2b_user,
    create_department,
    create_employee,
    create_hotel_booking_request,
    create_policy_rule,
    create_trip,
    create_voucher,
    delete_policy_rule,
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
    update_hotel_booking_request_status,
    update_policy_rule,
    update_travel_policy,
    update_trip,
)
from apps.b2b.models import DepartmentBudgetStatus, HotelBookingRequestStatus
from apps.b2b.permissions import IsB2BOwner, IsB2BPerformer
from apps.property.hotel_repository import _run_in_schema, get_hotel_for_public, resolve_hotel_guid
from apps.hotels.repository import (
    count_hotels,
    create_hotel_booking,
    get_available_rooms,
    get_bookings_status,
    get_hotel_calendar,
    search_hotels,
)
from apps.hotels.serializers import (
    HotelCardSerializer,
    HotelSearchParamsSerializer,
    RoomAvailabilitySerializer,
    RoomSelectParamsSerializer,
)
from apps.b2b.serializers import (
    ActiveTripEmployeeSerializer,
    B2BCompanySerializer,
    B2BDepartmentSerializer,
    B2BDepartmentSummarySerializer,
    B2BEmployeeCreateSerializer,
    B2BEmployeeSerializer,
    B2BHotelCalendarSerializer,
    B2BUserSerializer,
    BudgetRequestSerializer,
    BusinessTripSerializer,
    DashboardSummarySerializer,
    DepartmentMonthlySpendingSerializer,
    HotelBookingRequestCreateSerializer,
    HotelBookingRequestDetailSerializer,
    HotelBookingRequestSerializer,
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

    Executer (performer) uchun mehmonxona qidiruv/filterlash — komandirovkaga
    xodim jo'natish uchun mos hotel tanlashda ishlatiladi. Faqat B2B
    "executer" (performer) rolidagi foydalanuvchilar uchun ochiq — owner bu
    endpointdan foydalana olmaydi.
    """
    permission_classes = [IsAuthenticated, IsB2BPerformer]

    @swagger_auto_schema(
        operation_summary="Mehmonxonalarni qidirish/filterlash (executer uchun)",
        operation_description=(
            "Komandirovka uchun mehmonxona tanlash. `sort_by` orqali: "
            "`popular` (mashhur), `weel_recommended` (weel-tavsiya), "
            "`cheap` (eng arzon), `expensive` (eng qimmat). Xarita bo'yicha "
            "tanlov uchun `lat`/`lon`/`radius_km` (km). Kalendar: "
            "`check_in`/`check_out` + `guests` (necha kishi) berilsa, faqat "
            "shu sanalar oralig'ida va shu odam soniga mos BO'SH xona bor "
            "mehmonxonalar qaytariladi. Har bir natija `guid` bilan "
            "keladi — mehmonxona bir nechta tashkilotlar (sxemalar) bo'ylab "
            "qidirilgani uchun bu identifikator raqamli `id`dan ko'ra "
            "ishonchliroq."
        ),
        query_serializer=HotelSearchParamsSerializer,
        responses={200: openapi.Response(
            "Paginated hotel list",
            openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    "count": openapi.Schema(type=openapi.TYPE_INTEGER),
                    "page": openapi.Schema(type=openapi.TYPE_INTEGER),
                    "page_size": openapi.Schema(type=openapi.TYPE_INTEGER),
                    "results": openapi.Schema(
                        type=openapi.TYPE_ARRAY,
                        items=openapi.Schema(type=openapi.TYPE_OBJECT),
                    ),
                },
            ),
        )},
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

    Bosqich 2 (1-qadam): bosqich-1'da tanlangan mehmonxona + sanalarga mos
    bo'sh xonalar ro'yxati — har bir xonaning sig'imi (`capacity`) qancha
    xodim joylashtirish mumkinligini ko'rsatadi.
    """
    permission_classes = [IsAuthenticated, IsB2BPerformer]

    @swagger_auto_schema(
        operation_summary="Mehmonxona xonalarini olish (executer, bosqich 2)",
        operation_description=(
            "Bosqich-1'da tanlangan `hotel_guid` uchun, xuddi shu "
            "`check_in`/`check_out`/`guests` bilan bo'sh xonalarni qaytaradi. "
            "Har bir xonaning `capacity`si — unga nechta xodim biriktirish "
            "mumkinligini bildiradi (odatda 1 yoki 2)."
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
                ),
            )
        except Exception:
            return Response({"detail": "Hotel not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(RoomAvailabilitySerializer(rooms, many=True).data)


def _refresh_booking_request_status(booking_request: dict) -> dict:
    """Pull-based status sync: while a request is still `pending`, ask the
    hotel's own tenant schema whether its sibling `pms_booking` rows have
    been accepted/rejected by the partner, and persist the derived group
    status. Called on every read (list + detail) so "qabul qilindi" /
    "bekor qilindi" reflects reality without needing a push from the PMS
    side.
    """
    if booking_request.get("status") != HotelBookingRequestStatus.PENDING:
        return booking_request
    rooms = list_hotel_booking_rooms(booking_request["id"])
    booking_ids = [r["pms_booking_id"] for r in rooms if r.get("pms_booking_id")]
    if not booking_ids:
        return booking_request
    try:
        statuses = _run_in_schema(
            booking_request["tenant_schema"],
            lambda: get_bookings_status(booking_ids),
        )
    except Exception:
        return booking_request
    status_values = {s["status"] for s in statuses}
    if not status_values:
        return booking_request
    if "cancelled" in status_values:
        new_status = HotelBookingRequestStatus.REJECTED
    elif status_values <= {"confirmed", "checked_in", "checked_out"}:
        new_status = HotelBookingRequestStatus.CONFIRMED
    else:
        return booking_request
    updated = update_hotel_booking_request_status(
        booking_request["id"], new_status, reviewed_at=timezone.now(),
    )
    return updated or booking_request


class B2BHotelBookingListCreateView(APIView):
    """GET/POST /b2b/hotels/bookings/

    Bosqich 2 (2-qadam) yakuni: tanlangan xonalar + har biriga biriktirilgan
    xodimlar bilan BITTA bron so'rovi yuboriladi. Bitta so'rov ichida bir
    nechta xona (va shu orqali bir nechta `pms_booking` yozuvi) bo'lishi
    mumkin, lekin bularning barchasi bronlash tarixida BITTA yozuv sifatida
    ko'rinadi.
    """
    permission_classes = [IsAuthenticated, IsB2BPerformer]

    @swagger_auto_schema(
        operation_summary="Bron so'rovlari tarixini olish (executer)",
        operation_description=(
            "Har bir bron so'rovi (bir nechta xona/xodimni o'z ichiga olgan "
            "guruh) shu yerda BITTA qator sifatida ko'rinadi — "
            "`room_count`/`employee_count` bilan qisqacha. To'liq tafsilot "
            "uchun `GET /b2b/hotels/bookings/<id>/` ga o'ting."
        ),
        manual_parameters=[
            openapi.Parameter("trip_id", openapi.IN_QUERY, type=openapi.TYPE_INTEGER, description="Komandirovka bo'yicha filtr"),
            openapi.Parameter(
                "status", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                enum=["pending", "confirmed", "rejected"],
                description="Holat bo'yicha filtr",
            ),
        ],
        responses={200: HotelBookingRequestSerializer(many=True)},
        tags=["B2B / Executer"],
    )
    def get(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        trip_id = request.query_params.get("trip_id")
        req_status = request.query_params.get("status")
        rows = list_hotel_booking_requests(
            company_id,
            trip_id=int(trip_id) if trip_id else None,
            status=req_status,
        )
        rows = [_refresh_booking_request_status(r) for r in rows]
        return Response(HotelBookingRequestSerializer(rows, many=True).data)

    @swagger_auto_schema(
        operation_summary="Bron so'rovini yuborish (xonalar + xodimlar, executer)",
        operation_description=(
            "Bosqich-2 yakuni: `hotel_guid`, sanalar va har bir xonaga "
            "biriktirilgan xodimlar (`employee_ids`, xonaga qarab 1 yoki 2 "
            "kishi) yuboriladi. Server: (1) har bir xonani qayta "
            "mavjudligini tekshiradi, (2) har bir xona uchun mehmonxonaning "
            "o'z sxemasida `pms_booking` yaratadi, (3) xodimlarni shu "
            "tripning `TripEmployee` yozuvlariga biriktiradi. Butun jarayon "
            "bitta tranzaksiya — birorta xona band bo'lib chiqsa, hech narsa "
            "yaratilmaydi. Natijada mehmonxona holati `pending` bo'ladi — "
            "mehmonxona qabul qilsa `confirmed` (\"qabul qilindi\"), rad "
            "etsa `rejected` (\"bekor bo'ldi\") ga o'zgaradi."
        ),
        request_body=HotelBookingRequestCreateSerializer,
        responses={
            201: HotelBookingRequestDetailSerializer(),
            400: openapi.Response(description="Validation error / room not available / capacity exceeded."),
            404: openapi.Response(description="Trip, hotel or employee not found."),
        },
        tags=["B2B / Executer"],
    )
    def post(self, request):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)

        serializer = HotelBookingRequestCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        data = serializer.validated_data

        trip = get_trip(data["trip_id"], company_id)
        if not trip:
            return Response({"detail": "Trip not found."}, status=status.HTTP_404_NOT_FOUND)

        resolved = resolve_hotel_guid(data["hotel_guid"])
        if not resolved:
            return Response({"detail": "Invalid hotel_guid."}, status=status.HTTP_400_BAD_REQUEST)
        schema_name, hotel_id = resolved

        hotel = get_hotel_for_public(data["hotel_guid"])
        if not hotel:
            return Response({"detail": "Hotel not found."}, status=status.HTTP_404_NOT_FOUND)

        check_in = data["check_in"]
        check_out = data["check_out"]

        # Re-check availability server-side (race-safe) before touching anything.
        try:
            available_rooms = _run_in_schema(
                schema_name,
                lambda: get_available_rooms(hotel_id, check_in=check_in, check_out=check_out, guests=1),
            )
        except Exception:
            return Response({"detail": "Hotel not found."}, status=status.HTTP_404_NOT_FOUND)
        available_by_id = {r["id"]: r for r in available_rooms}

        seen_employee_ids: set[int] = set()
        for room in data["rooms"]:
            avail_room = available_by_id.get(room["room_id"])
            if not avail_room:
                return Response(
                    {"detail": f"Room {room['room_id']} is not available for these dates."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            capacity = avail_room.get("capacity")
            if capacity is not None and len(room["employee_ids"]) > capacity:
                return Response(
                    {"detail": f"Room {room['room_id']} capacity is {capacity}."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            for employee_id in room["employee_ids"]:
                if not get_employee(employee_id, company_id):
                    return Response(
                        {"detail": f"Employee {employee_id} not found."},
                        status=status.HTTP_404_NOT_FOUND,
                    )
                seen_employee_ids.add(employee_id)

        requested_by = _get_user_id(request)

        with transaction.atomic():
            booking_request = create_hotel_booking_request(
                company_id=company_id,
                trip_id=data["trip_id"],
                tenant_schema=schema_name,
                hotel_property_id=hotel_id,
                hotel_name=hotel.get("title") or hotel.get("name"),
                check_in=check_in,
                check_out=check_out,
                requested_by=requested_by,
            )
            if not booking_request:
                transaction.set_rollback(True)
                return Response(
                    {"detail": "Failed to create booking request."},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

            for room in data["rooms"]:
                avail_room = available_by_id[room["room_id"]]
                room_id = room["room_id"]

                def _create_room_booking(rid=room_id, employees=room["employee_ids"]):
                    return create_hotel_booking(
                        property_id=hotel_id,
                        room_id=rid,
                        client_user_id=requested_by,
                        check_in=check_in,
                        check_out=check_out,
                        adults=len(employees),
                        b2b_company_id=company_id,
                    )

                pms_booking = _run_in_schema(schema_name, _create_room_booking)
                if not pms_booking:
                    transaction.set_rollback(True)
                    return Response(
                        {"detail": f"Room {room_id} could not be booked (may have just been taken)."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                booking_room = add_hotel_booking_room(
                    booking_request_id=booking_request["id"],
                    room_id=room_id,
                    room_name=avail_room.get("display_name") or avail_room.get("room_type_name"),
                    pms_booking_id=pms_booking["id"],
                    price_per_night=avail_room.get("price_per_night"),
                    total_price=pms_booking.get("total_cost"),
                )
                for employee_id in room["employee_ids"]:
                    add_hotel_booking_room_employee(
                        booking_room_id=booking_room["id"],
                        employee_id=employee_id,
                    )
                    add_trip_employee(
                        trip_id=data["trip_id"],
                        employee_id=employee_id,
                        property_id=hotel_id,
                        room_id=room_id,
                        check_in=check_in,
                        check_out=check_out,
                        pms_booking_id=pms_booking["id"],
                        status="invited",
                    )

        rooms = list_hotel_booking_rooms(booking_request["id"])
        booking_request["rooms"] = rooms
        booking_request["room_count"] = len(rooms)
        booking_request["employee_count"] = sum(len(r["employees"]) for r in rooms)
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
    permission_classes = [IsAuthenticated, IsB2BPerformer]

    @swagger_auto_schema(
        operation_summary="Bron so'rovi tafsilotini olish (executer)",
        responses={
            200: HotelBookingRequestDetailSerializer(),
            404: openapi.Response(description="Booking not found."),
        },
        tags=["B2B / Executer"],
    )
    def get(self, request, booking_id):
        company_id = _get_company_id(request)
        if not company_id:
            return Response({"detail": "Company context required."}, status=status.HTTP_400_BAD_REQUEST)
        booking_request = get_hotel_booking_request(booking_id, company_id)
        if not booking_request:
            return Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        booking_request = _refresh_booking_request_status(booking_request)
        rooms = list_hotel_booking_rooms(booking_id)
        booking_request["rooms"] = rooms
        booking_request["room_count"] = len(rooms)
        booking_request["employee_count"] = sum(len(r["employees"]) for r in rooms)
        return Response(HotelBookingRequestDetailSerializer(booking_request).data)


class B2BHotelCalendarView(APIView):
    """GET /b2b/hotels/<hotel_guid>/calendar/

    Mehmonxonaning butun taqvimidagi band/bo'sh holatini ko'rsatadi — har bir
    xona uchun har bir sana ``booked`` yoki ``available`` sifatida qaytariladi.
    Executer komandirovka uchun bo'sh sanalarni shu yerdan ko'rib tanlaydi.
    """
    permission_classes = [IsAuthenticated, IsB2BPerformer]

    @swagger_auto_schema(
        operation_summary="Mehmonxona bandlik taqvimi",
        operation_description=(
            "Tanlangan mehmonxona (``hotel_guid``) uchun ``from_date`` – "
            "``to_date`` oralig'idagi har bir faol xonaning kunlik bandlik "
            "holatini qaytaradi.\n\n"
            "Har bir qator: ``room_id``, ``room_name``, ``date`` va "
            "``status`` (``booked`` yoki ``available``). Bu yerda nafaqat "
            "B2B orqali qilingan bronlar, balki mehmonxonaning o'z sayti "
            "orqali qilingan bandliklar ham aks etadi — shuning uchun "
            "executer haqiqiy bandlik holatini ko'rib, xodimlarni "
            "joylashtirish uchun bo'sh sanalarni aniq tanlay oladi."
        ),
        manual_parameters=[
            openapi.Parameter(
                "hotel_guid", openapi.IN_PATH, type=openapi.TYPE_STRING,
                required=True, description="Mehmonxona GUID identifikatori.",
            ),
            openapi.Parameter(
                "from_date", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                format=openapi.FORMAT_DATE, required=True,
                description="Taqvim oralig'i boshlanish sanasi (YYYY-MM-DD).",
            ),
            openapi.Parameter(
                "to_date", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                format=openapi.FORMAT_DATE, required=True,
                description="Taqvim oralig'i tugash sanasi (YYYY-MM-DD).",
            ),
        ],
        responses={
            200: B2BHotelCalendarSerializer(many=True),
            400: openapi.Response(description="Noto'g'ri hotel_guid yoki sana parametrlari."),
            404: openapi.Response(description="Mehmonxona topilmadi."),
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

        try:
            calendar = _run_in_schema(
                schema_name,
                lambda: get_hotel_calendar(hotel_id, from_date=from_date, to_date=to_date),
            )
        except Exception:
            return Response({"detail": "Hotel not found."}, status=status.HTTP_404_NOT_FOUND)
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
        operation_summary="Departmentlar ro'yxati",
        operation_description=(
            "Har bir department uchun owner tomonidan berilgan limit "
            "(`budget_limit`), ishlatilgan summa (`used_amount`), qolgan summa "
            "(`remaining_amount`), holati (`status`) va unga biriktirilgan "
            "xodimlar (`employees`) qaytariladi. `status` limitning qolgan "
            "qismiga qarab hisoblanadi: `high` — qolgan summa limitning 25%"
            "idan ko'p, `low` — 25% yoki undan kam (lekin 0 emas), "
            "`empty` — qolmagan (yoki limitdan oshib ketilgan), "
            "`no_limit` — department uchun limit belgilanmagan."
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
        department_id = validated.get("department_id")
        if department_id and not any(d["id"] == department_id for d in list_departments(company_id)):
            return Response({"detail": "Department not found."}, status=status.HTTP_404_NOT_FOUND)
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
        department_id = serializer.validated_data.get("department_id")
        if department_id and not any(d["id"] == department_id for d in list_departments(company_id)):
            return Response({"detail": "Department not found."}, status=status.HTTP_404_NOT_FOUND)
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
        operation_summary="Byudjet so'rovlarini olish (owner uchun)",
        operation_description=(
            "Kompaniyaning barcha byudjet so'rovlarini qaytaradi. "
            "`status=pending` bilan filtrlab, owner tasdig'ini kutayotgan — "
            "executer tomonidan (xodim yoki bo'lim uchun) yuborilgan "
            "so'rovlarni ko'rish mumkin."
        ),
        manual_parameters=[
            openapi.Parameter(
                "status", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                enum=["pending", "approved", "rejected"],
                description="Holat bo'yicha filtr. Owner uchun odatda `pending`.",
            ),
        ],
        responses={
            200: openapi.Response(
                description="Byudjet so'rovlari + umumiy soni",
                schema=openapi.Schema(
                    type=openapi.TYPE_OBJECT,
                    properties={
                        "count": openapi.Schema(type=openapi.TYPE_INTEGER),
                        "results": openapi.Schema(
                            type=openapi.TYPE_ARRAY,
                            items=openapi.Schema(type=openapi.TYPE_OBJECT),
                        ),
                    },
                ),
            ),
        },
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
        operation_summary="Byudjet so'rovi yuborish (xodim yoki bo'lim uchun)",
        operation_description=(
            "Executer bitta xodim (`employee_id`) yoki butun bo'lim "
            "(`department_id`) uchun qo'shimcha summa so'raydi — aynan "
            "bittasi yuborilishi shart. `trip_id` ixtiyoriy — mavjud bo'lsa, "
            "so'rov shu komandirovka bilan bog'lanadi. Har bir so'rov "
            "`pending` holatda saqlanadi va owner uni "
            "`GET ?status=pending` orqali ko'rib, "
            "`POST /budget-requests/<id>/review/` bilan tasdiqlaydi/rad etadi."
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
        operation_summary="Byudjet so'rovini tasdiqlash/rad etish (faqat owner)",
        operation_description=(
            "Owner byudjet so'rovini `approved` yoki `rejected` qiladi. "
            "`description` — qarorning sababi, ixtiyoriy."
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


# ─── Top employees by trip count ───────────────────────────────────────────

class TopEmployeesByTripsView(APIView):
    """GET /api/b2b/employees/top-by-trips/?limit=5

    Returns the employees with the most business-trip (komandirovka)
    assignments for the company, ordered by trip count descending.
    """
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Eng ko'p komandirovkaga borgan xodimlar (top N)",
        operation_description=(
            "Kompaniya bo'yicha eng ko'p komandirovkaga (business trip) "
            "biriktirilgan xodimlarni, `trip_count` bo'yicha kamayish "
            "tartibida qaytaradi. Default `limit=5`, maksimum 100."
        ),
        manual_parameters=[
            openapi.Parameter(
                "limit",
                openapi.IN_QUERY,
                description="Qaytariladigan xodimlar soni (1-100). Default 5.",
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
        operation_summary="Kompaniya eng ko'p bron qilgan hotellar (top N)",
        operation_description=(
            "Shu kompaniya tomonidan eng ko'p bron qilingan mehmonxonalarni, "
            "`booking_count` bo'yicha kamayish tartibida qaytaradi. Default "
            "`limit=3`, maksimum 100."
        ),
        manual_parameters=[
            openapi.Parameter(
                "limit",
                openapi.IN_QUERY,
                description="Qaytariladigan hotellar soni (1-100). Default 3.",
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
        operation_summary="Limit qoidalarini olish",
        operation_description=(
            "`applies_to` orqali qaysi turdagi limitlar qaytarilishini tanlang: "
            "`all` — kompaniyaning barcha limitlari (global, department va employee), "
            "`department` — departmentlar uchun limitlar, "
            "`employee` — xodimlar uchun individual limitlar."
        ),
        manual_parameters=[
            openapi.Parameter(
                "applies_to", openapi.IN_QUERY, type=openapi.TYPE_STRING,
                enum=["all", "department", "employee"], required=True,
                description="Qaysi turdagi limitlarni olish kerak.",
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
        operation_summary="Yangi limit qoidasi qo'shish",
        operation_description=(
            "`applies_to`: `all` — kompaniya darajasidagi global limit (bitta "
            "kompaniyada faqat bitta bo'lishi mumkin, `target_id` yubormang); "
            "`department`/`employee` — mos `target_id` (department_id / "
            "employee_id) bilan."
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
        operation_summary="Limit qoidasini tahrirlash",
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
        operation_summary="Limit qoidasini o'chirish",
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
