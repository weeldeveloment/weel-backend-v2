from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from django.core.files.storage import default_storage
from django.db import IntegrityError
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

from apps.platform.authentication import PmsJWTAuthentication

from apps.pms.repository import (
    accept_booking,
    add_property_image,
    add_room_image,
    block_dates,
    cancel_booking,
    change_meal_plan,
    check_in_booking,
    check_out_booking,
    complain_review,
    create_booking,
    create_guest,
    create_property,
    create_rate,
    create_review,
    create_room,
    create_room_type,
    delete_property,
    delete_property_image,
    delete_rate,
    delete_room,
    delete_room_type,
    expire_holds,
    find_or_create_guest,
    get_booking,
    get_booking_history,
    get_effective_rate,
    get_guest,
    get_or_create_calendar_slot,
    get_property,
    get_property_images,
    get_room,
    get_room_availability,
    get_room_images,
    get_room_type,
    hold_dates,
    list_bookings,
    list_guests,
    list_properties,
    list_rates,
    list_reviews,
    list_room_types,
    list_rooms,
    mass_update_rooms,
    move_booking,
    respond_to_review,
    unblock_dates,
    unhold_dates,
    update_booking,
    update_guest,
    update_property,
    update_rate,
    update_room,
    update_room_type,
)
from apps.pms.serializers import (
    BookingHistorySerializer,
    BookingSerializer,
    CalendarSlotSerializer,
    DateRangeSerializer,
    GuestSerializer,
    MealPlanChangeSerializer,
    MoveBookingSerializer,
    PropertyImageSerializer,
    PropertySerializer,
    RateSerializer,
    ResizeBookingSerializer,
    ReviewComplainSerializer,
    ReviewRespondSerializer,
    ReviewSerializer,
    RoomIdsSerializer,
    RoomImageSerializer,
    RoomMassUpdateItemSerializer,
    RoomSerializer,
    RoomTypeSerializer,
)

logger = logging.getLogger(__name__)


class PMSBaseView(APIView):
    authentication_classes = [PmsJWTAuthentication]
    permission_classes = [IsAuthenticated]


def _get_user_id(request) -> int | None:
    user = getattr(request, "user", None)
    if isinstance(user, dict):
        return user.get("id")
    return getattr(user, "id", None)


def _require_org(request):
    org = getattr(request, "organization", None)
    if org:
        if isinstance(org, dict):
            return org.get("id")
        return org

    user = getattr(request, "user", None)
    if isinstance(user, dict):
        org_id = user.get("organization_id")
        if org_id:
            return org_id

    org_id = getattr(user, "organization_id", None)
    if org_id:
        return org_id

    return getattr(request, "META", {}).get("HTTP_X_ORGANIZATION_ID")


class PropertyListCreateView(PMSBaseView):
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @swagger_auto_schema(responses={200: PropertySerializer(many=True)})
    def get(self, request):
        org_id = _require_org(request)
        if not org_id:
            return Response({"detail": "Organization context required."}, status=status.HTTP_400_BAD_REQUEST)
        props = list_properties(organization_id=int(org_id))
        return Response(PropertySerializer(props, many=True).data)

    @swagger_auto_schema(
        responses={201: PropertySerializer()},
        operation_description="Create a new property",
        request_body=PropertySerializer,
    )
    def post(self, request):
        org_id = _require_org(request)
        if not org_id:
            return Response({"detail": "Organization context required."}, status=status.HTTP_400_BAD_REQUEST)

        serializer = PropertySerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            prop = create_property(organization_id=int(org_id), **serializer.validated_data)
        except IntegrityError as e:
            return Response({"detail": f"Database error: {e}"}, status=status.HTTP_400_BAD_REQUEST)

        if not prop:
            return Response({"detail": "Failed to create property."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response(PropertySerializer(prop).data, status=status.HTTP_201_CREATED)


class PropertyRetrieveUpdateDestroyView(PMSBaseView):
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @swagger_auto_schema(responses={200: PropertySerializer()})
    def get(self, request, property_id):
        org_id = _require_org(request)
        if not org_id:
            return Response({"detail": "Organization context required."}, status=status.HTTP_400_BAD_REQUEST)
        prop = get_property(property_id, organization_id=int(org_id))
        if not prop:
            return Response({"detail": "Property not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(PropertySerializer(prop).data)

    @swagger_auto_schema(responses={200: PropertySerializer()})
    def patch(self, request, property_id):
        org_id = _require_org(request)
        if not org_id:
            return Response({"detail": "Organization context required."}, status=status.HTTP_400_BAD_REQUEST)

        serializer = PropertySerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        prop = update_property(property_id, organization_id=int(org_id), **serializer.validated_data)
        if not prop:
            return Response({"detail": "Property not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(PropertySerializer(prop).data)

    @swagger_auto_schema(responses={204: "Deleted"})
    def delete(self, request, property_id):
        org_id = _require_org(request)
        if not org_id:
            return Response({"detail": "Organization context required."}, status=status.HTTP_400_BAD_REQUEST)
        if not delete_property(property_id, organization_id=int(org_id)):
            return Response({"detail": "Property not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


class PropertyImageCreateView(PMSBaseView):
    parser_classes = [MultiPartParser, FormParser]

    @swagger_auto_schema(responses={201: PropertyImageSerializer()})
    def post(self, request, property_id):
        org_id = _require_org(request)
        if not org_id:
            return Response({"detail": "Organization context required."}, status=status.HTTP_400_BAD_REQUEST)
        prop = get_property(property_id, organization_id=int(org_id))
        if not prop:
            return Response({"detail": "Property not found."}, status=status.HTTP_404_NOT_FOUND)

        image_file = request.FILES.get("image")
        if not image_file:
            return Response({"detail": "No image file provided."}, status=status.HTTP_400_BAD_REQUEST)

        path = default_storage.save(f"pms/properties/{property_id}/{image_file.name}", image_file)
        image_url = default_storage.url(path)

        order = request.data.get("order", 0)
        img = add_property_image(property_id, image_url, int(order))
        return Response(PropertyImageSerializer(img).data, status=status.HTTP_201_CREATED)


class PropertyImageDeleteView(PMSBaseView):
    @swagger_auto_schema(responses={204: "Deleted"})
    def delete(self, request, property_id, image_id):
        if not delete_property_image(image_id):
            return Response({"detail": "Image not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


class RoomTypeListCreateView(PMSBaseView):
    @swagger_auto_schema(responses={200: RoomTypeSerializer(many=True)})
    def get(self, request, property_id):
        org_id = _require_org(request)
        if not org_id:
            return Response({"detail": "Organization context required."}, status=status.HTTP_400_BAD_REQUEST)
        prop = get_property(property_id, organization_id=int(org_id))
        if not prop:
            return Response({"detail": "Property not found."}, status=status.HTTP_404_NOT_FOUND)
        types = list_room_types(property_id)
        return Response(RoomTypeSerializer(types, many=True).data)

    @swagger_auto_schema(responses={201: RoomTypeSerializer()})
    def post(self, request, property_id):
        org_id = _require_org(request)
        if not org_id:
            return Response({"detail": "Organization context required."}, status=status.HTTP_400_BAD_REQUEST)
        prop = get_property(property_id, organization_id=int(org_id))
        if not prop:
            return Response({"detail": "Property not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = RoomTypeSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            rt = create_room_type(property_id=property_id, **serializer.validated_data)
        except IntegrityError as e:
            if "duplicate key" in str(e) and "name" in str(e):
                return Response({"name": "A room type with this name already exists for this property."}, status=status.HTTP_400_BAD_REQUEST)
            return Response({"detail": f"Database error: {e}"}, status=status.HTTP_400_BAD_REQUEST)

        if not rt:
            return Response({"detail": "Failed to create room type."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(RoomTypeSerializer(rt).data, status=status.HTTP_201_CREATED)


class RoomTypeRetrieveUpdateDestroyView(PMSBaseView):
    @swagger_auto_schema(responses={200: RoomTypeSerializer()})
    def get(self, request, property_id, room_type_id):
        rt = get_room_type(room_type_id, property_id)
        if not rt:
            return Response({"detail": "Room type not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(RoomTypeSerializer(rt).data)

    @swagger_auto_schema(responses={200: RoomTypeSerializer()})
    def patch(self, request, property_id, room_type_id):
        serializer = RoomTypeSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        rt = update_room_type(room_type_id, **serializer.validated_data)
        if not rt:
            return Response({"detail": "Room type not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(RoomTypeSerializer(rt).data)

    @swagger_auto_schema(responses={204: "Deleted"})
    def delete(self, request, property_id, room_type_id):
        if not delete_room_type(room_type_id):
            return Response({"detail": "Room type not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


class RoomListCreateView(PMSBaseView):
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    @swagger_auto_schema(responses={200: RoomSerializer(many=True)})
    def get(self, request, property_id):
        org_id = _require_org(request)
        if not org_id:
            return Response({"detail": "Organization context required."}, status=status.HTTP_400_BAD_REQUEST)
        prop = get_property(property_id, organization_id=int(org_id))
        if not prop:
            return Response({"detail": "Property not found."}, status=status.HTTP_404_NOT_FOUND)

        room_type_id = request.query_params.get("room_type_id")
        rooms = list_rooms(property_id, room_type_id=int(room_type_id) if room_type_id else None)
        return Response(RoomSerializer(rooms, many=True).data)

    @swagger_auto_schema(responses={201: RoomSerializer()})
    def post(self, request, property_id):
        org_id = _require_org(request)
        if not org_id:
            return Response({"detail": "Organization context required."}, status=status.HTTP_400_BAD_REQUEST)
        prop = get_property(property_id, organization_id=int(org_id))
        if not prop:
            return Response({"detail": "Property not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = RoomSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            room = create_room(property_id=property_id, **serializer.validated_data)
        except IntegrityError as e:
            if "duplicate key" in str(e) and "room_number" in str(e):
                return Response({"room_number": "A room with this number already exists for this property."}, status=status.HTTP_400_BAD_REQUEST)
            return Response({"detail": f"Database error: {e}"}, status=status.HTTP_400_BAD_REQUEST)

        if not room:
            return Response({"detail": "Failed to create room."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(RoomSerializer(room).data, status=status.HTTP_201_CREATED)


class RoomRetrieveUpdateDestroyView(PMSBaseView):
    @swagger_auto_schema(responses={200: RoomSerializer()})
    def get(self, request, property_id, room_id):
        room = get_room(room_id, property_id)
        if not room:
            return Response({"detail": "Room not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(RoomSerializer(room).data)

    @swagger_auto_schema(responses={200: RoomSerializer()})
    def patch(self, request, property_id, room_id):
        serializer = RoomSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        room = update_room(room_id, **serializer.validated_data)
        if not room:
            return Response({"detail": "Room not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(RoomSerializer(room).data)

    @swagger_auto_schema(responses={204: "Deleted"})
    def delete(self, request, property_id, room_id):
        if not delete_room(room_id):
            return Response({"detail": "Room not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


class RoomMassUpdateView(PMSBaseView):
    @swagger_auto_schema(responses={200: RoomSerializer(many=True)})
    def post(self, request, property_id):
        if not isinstance(request.data, list):
            return Response({"detail": "Expected a list of room updates."}, status=status.HTTP_400_BAD_REQUEST)

        results = []
        for item in request.data:
            item_serializer = RoomMassUpdateItemSerializer(data=item)
            if not item_serializer.is_valid():
                return Response(item_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            room = update_room(item_serializer.validated_data["id"], **{
                k: v for k, v in item_serializer.validated_data.items() if k != "id"
            })
            if room:
                results.append(RoomSerializer(room).data)

        return Response(results, status=status.HTTP_200_OK)


class RoomImageCreateView(PMSBaseView):
    parser_classes = [MultiPartParser, FormParser]

    @swagger_auto_schema(responses={201: RoomImageSerializer()})
    def post(self, request, property_id, room_id):
        room = get_room(room_id, property_id)
        if not room:
            return Response({"detail": "Room not found."}, status=status.HTTP_404_NOT_FOUND)

        image_file = request.FILES.get("image")
        if not image_file:
            return Response({"detail": "No image file provided."}, status=status.HTTP_400_BAD_REQUEST)

        path = default_storage.save(f"pms/rooms/{room_id}/{image_file.name}", image_file)
        image_url = default_storage.url(path)

        order = request.data.get("order", 0)
        img = add_room_image(room_id, image_url, int(order))
        return Response(RoomImageSerializer(img).data, status=status.HTTP_201_CREATED)


class CalendarView(PMSBaseView):
    @swagger_auto_schema(responses={200: CalendarSlotSerializer(many=True)})
    def get(self, request, property_id):
        serializer = DateRangeSerializer(data=request.query_params)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        from_date = serializer.validated_data["from_date"]
        to_date = serializer.validated_data["to_date"]
        room_id = request.query_params.get("room_id")

        slots = get_room_availability(property_id=property_id, from_date=from_date, to_date=to_date)
        return Response(slots)


class CalendarBlockView(PMSBaseView):
    @swagger_auto_schema(responses={201: CalendarSlotSerializer(many=True)})
    def post(self, request, property_id):
        serializer = RoomIdsSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        slots = block_dates(
            room_ids=serializer.validated_data["room_ids"],
            from_date=serializer.validated_data["from_date"],
            to_date=serializer.validated_data["to_date"],
        )
        return Response(CalendarSlotSerializer(slots, many=True).data, status=status.HTTP_201_CREATED)


class CalendarUnblockView(PMSBaseView):
    @swagger_auto_schema(responses={200: openapi.Response("Unblocked count", openapi.Schema(type=openapi.TYPE_OBJECT, properties={"unblocked": openapi.Schema(type=openapi.TYPE_INTEGER)}))})
    def post(self, request, property_id):
        serializer = RoomIdsSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        count = unblock_dates(
            room_ids=serializer.validated_data["room_ids"],
            from_date=serializer.validated_data["from_date"],
            to_date=serializer.validated_data["to_date"],
        )
        return Response({"unblocked": count})


class CalendarHoldView(PMSBaseView):
    @swagger_auto_schema(responses={201: CalendarSlotSerializer(many=True)})
    def post(self, request, property_id):
        serializer = RoomIdsSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        slots = hold_dates(
            room_ids=serializer.validated_data["room_ids"],
            from_date=serializer.validated_data["from_date"],
            to_date=serializer.validated_data["to_date"],
            hold_duration_minutes=serializer.validated_data.get("hold_duration_minutes", 30),
        )
        return Response(CalendarSlotSerializer(slots, many=True).data, status=status.HTTP_201_CREATED)


class CalendarUnholdView(PMSBaseView):
    @swagger_auto_schema(responses={200: openapi.Response("Unheld count", openapi.Schema(type=openapi.TYPE_OBJECT, properties={"unheld": openapi.Schema(type=openapi.TYPE_INTEGER)}))})
    def post(self, request, property_id):
        serializer = RoomIdsSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        count = unhold_dates(
            room_ids=serializer.validated_data["room_ids"],
            from_date=serializer.validated_data["from_date"],
            to_date=serializer.validated_data["to_date"],
        )
        return Response({"unheld": count})


class GuestListCreateView(PMSBaseView):
    @swagger_auto_schema(responses={200: GuestSerializer(many=True)})
    def get(self, request):
        search = request.query_params.get("search")
        guests = list_guests(search=search)
        return Response(GuestSerializer(guests, many=True).data)

    @swagger_auto_schema(responses={201: GuestSerializer()})
    def post(self, request):
        serializer = GuestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            guest = create_guest(**serializer.validated_data)
        except IntegrityError as e:
            return Response({"detail": f"Database error: {e}"}, status=status.HTTP_400_BAD_REQUEST)

        if not guest:
            return Response({"detail": "Failed to create guest."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(GuestSerializer(guest).data, status=status.HTTP_201_CREATED)


class GuestRetrieveUpdateView(PMSBaseView):
    @swagger_auto_schema(responses={200: GuestSerializer()})
    def get(self, request, guest_id):
        guest = get_guest(guest_id)
        if not guest:
            return Response({"detail": "Guest not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(GuestSerializer(guest).data)

    @swagger_auto_schema(responses={200: GuestSerializer()})
    def patch(self, request, guest_id):
        serializer = GuestSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        guest = update_guest(guest_id, **serializer.validated_data)
        if not guest:
            return Response({"detail": "Guest not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(GuestSerializer(guest).data)


class BookingListCreateView(PMSBaseView):
    @swagger_auto_schema(responses={200: BookingSerializer(many=True)})
    def get(self, request, property_id):
        status_filter = request.query_params.get("status")
        from_date = request.query_params.get("from_date")
        to_date = request.query_params.get("to_date")
        room_id = request.query_params.get("room_id")

        bookings = list_bookings(
            property_id=property_id,
            status=status_filter,
            from_date=from_date,
            to_date=to_date,
            room_id=int(room_id) if room_id else None,
        )
        return Response(BookingSerializer(bookings, many=True).data)

    @swagger_auto_schema(responses={201: BookingSerializer()})
    def post(self, request, property_id):
        serializer = BookingSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        validated = serializer.validated_data
        guest_id = validated.pop("guest_id", None)

        if not guest_id:
            guest_data = request.data.get("guest", {})
            if guest_data:
                guest = find_or_create_guest(
                    first_name=guest_data.get("first_name", "Guest"),
                    last_name=guest_data.get("last_name"),
                    email=guest_data.get("email"),
                    phone=guest_data.get("phone"),
                )
                guest_id = guest.get("id")

        try:
            booking = create_booking(
                property_id=property_id,
                check_in=validated["check_in"],
                check_out=validated["check_out"],
                room_id=validated["room_id"],
                guest_id=guest_id,
                created_by=_get_user_id(request),
                **{k: v for k, v in validated.items() if k not in ("check_in", "check_out", "room_id")},
            )
        except IntegrityError as e:
            return Response({"detail": f"Database error: {e}"}, status=status.HTTP_400_BAD_REQUEST)

        if not booking:
            return Response({"detail": "Failed to create booking."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(BookingSerializer(booking).data, status=status.HTTP_201_CREATED)


class BookingRetrieveView(PMSBaseView):
    @swagger_auto_schema(responses={200: BookingSerializer()})
    def get(self, request, property_id, booking_id):
        booking = get_booking(booking_id, property_id)
        if not booking:
            return Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(BookingSerializer(booking).data)

    @swagger_auto_schema(
        request_body=ResizeBookingSerializer,
        responses={200: BookingSerializer()},
    )
    def patch(self, request, property_id, booking_id):
        booking = get_booking(booking_id, property_id)
        if not booking:
            return Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = ResizeBookingSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        updated = update_booking(
            booking_id,
            check_in=serializer.validated_data["check_in"],
            check_out=serializer.validated_data["check_out"],
        )
        if not updated:
            return Response({"detail": "Failed to update booking."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response(BookingSerializer(updated).data)


class BookingAcceptView(PMSBaseView):
    @swagger_auto_schema(responses={200: BookingSerializer()})
    def post(self, request, property_id, booking_id):
        booking = accept_booking(booking_id, _get_user_id(request))
        if not booking:
            return Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(BookingSerializer(booking).data)


class BookingCancelView(PMSBaseView):
    @swagger_auto_schema(responses={200: BookingSerializer()})
    def post(self, request, property_id, booking_id):
        try:
            booking = cancel_booking(booking_id, _get_user_id(request))
        except IntegrityError as e:
            return Response({"detail": f"Database error: {e}"}, status=status.HTTP_400_BAD_REQUEST)
        if not booking:
            return Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(BookingSerializer(booking).data)


class BookingCheckInView(PMSBaseView):
    @swagger_auto_schema(responses={200: BookingSerializer()})
    def post(self, request, property_id, booking_id):
        booking = check_in_booking(booking_id, _get_user_id(request))
        if not booking:
            return Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(BookingSerializer(booking).data)


class BookingCheckOutView(PMSBaseView):
    @swagger_auto_schema(responses={200: BookingSerializer()})
    def post(self, request, property_id, booking_id):
        booking = check_out_booking(booking_id, _get_user_id(request))
        if not booking:
            return Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(BookingSerializer(booking).data)


class BookingMoveView(PMSBaseView):
    @swagger_auto_schema(responses={200: BookingSerializer()})
    def post(self, request, property_id, booking_id):
        serializer = MoveBookingSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        booking = move_booking(
            booking_id,
            new_room_id=serializer.validated_data["new_room_id"],
            new_check_in=serializer.validated_data.get("new_check_in"),
            new_check_out=serializer.validated_data.get("new_check_out"),
            user_id=_get_user_id(request),
        )
        if not booking:
            return Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(BookingSerializer(booking).data)


class BookingMealPlanView(PMSBaseView):
    @swagger_auto_schema(responses={200: BookingSerializer()})
    def post(self, request, property_id, booking_id):
        serializer = MealPlanChangeSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        booking = change_meal_plan(
            booking_id,
            serializer.validated_data["meal_plan"],
            _get_user_id(request),
        )
        if not booking:
            return Response({"detail": "Booking not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(BookingSerializer(booking).data)


class BookingHistoryView(PMSBaseView):
    @swagger_auto_schema(responses={200: BookingHistorySerializer(many=True)})
    def get(self, request, property_id, booking_id):
        history = get_booking_history(booking_id)
        return Response(BookingHistorySerializer(history, many=True).data)


class RateListCreateView(PMSBaseView):
    @swagger_auto_schema(responses={200: RateSerializer(many=True)})
    def get(self, request, property_id):
        room_type_id = request.query_params.get("room_type_id")
        rates = list_rates(property_id, room_type_id=int(room_type_id) if room_type_id else None)
        return Response(RateSerializer(rates, many=True).data)

    @swagger_auto_schema(responses={201: RateSerializer()})
    def post(self, request, property_id):
        serializer = RateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            rate = create_rate(property_id=property_id, **serializer.validated_data)
        except IntegrityError as e:
            return Response({"detail": f"Database error: {e}"}, status=status.HTTP_400_BAD_REQUEST)

        if not rate:
            return Response({"detail": "Failed to create rate."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(RateSerializer(rate).data, status=status.HTTP_201_CREATED)


class RateRetrieveUpdateDestroyView(PMSBaseView):
    @swagger_auto_schema(responses={200: RateSerializer()})
    def get(self, request, property_id, rate_id):
        rates = list_rates(property_id)
        rate = next((r for r in rates if r["id"] == rate_id), None)
        if not rate:
            return Response({"detail": "Rate not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(RateSerializer(rate).data)

    @swagger_auto_schema(responses={200: RateSerializer()})
    def patch(self, request, property_id, rate_id):
        serializer = RateSerializer(data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        rate = update_rate(rate_id, **serializer.validated_data)
        if not rate:
            return Response({"detail": "Rate not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(RateSerializer(rate).data)

    @swagger_auto_schema(responses={204: "Deleted"})
    def delete(self, request, property_id, rate_id):
        if not delete_rate(rate_id):
            return Response({"detail": "Rate not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ReviewListCreateView(PMSBaseView):
    @swagger_auto_schema(responses={200: ReviewSerializer(many=True)})
    def get(self, request, property_id):
        rating = request.query_params.get("rating")
        is_complained = request.query_params.get("is_complained")
        reviews = list_reviews(
            property_id,
            rating=int(rating) if rating else None,
            is_complained=bool(is_complained) if is_complained else None,
        )
        return Response(ReviewSerializer(reviews, many=True).data)

    @swagger_auto_schema(responses={201: ReviewSerializer()})
    def post(self, request, property_id):
        serializer = ReviewSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            review = create_review(property_id=property_id, **serializer.validated_data)
        except IntegrityError as e:
            return Response({"detail": f"Database error: {e}"}, status=status.HTTP_400_BAD_REQUEST)

        if not review:
            return Response({"detail": "Failed to create review."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(ReviewSerializer(review).data, status=status.HTTP_201_CREATED)


class ReviewRespondView(PMSBaseView):
    @swagger_auto_schema(responses={200: ReviewSerializer()})
    def post(self, request, property_id, review_id):
        serializer = ReviewRespondSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        review = respond_to_review(review_id, serializer.validated_data["response"])
        if not review:
            return Response({"detail": "Review not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(ReviewSerializer(review).data)


class ReviewComplainView(PMSBaseView):
    @swagger_auto_schema(responses={200: ReviewSerializer()})
    def post(self, request, property_id, review_id):
        serializer = ReviewComplainSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        review = complain_review(review_id, serializer.validated_data["reason"])
        if not review:
            return Response({"detail": "Review not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(ReviewSerializer(review).data)
