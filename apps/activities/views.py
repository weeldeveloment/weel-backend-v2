from __future__ import annotations

from datetime import datetime, timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.generics import ListCreateAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from admin_auth.authentication import AdminJWTAuthentication
from admin_auth.permissions import IsAdminUser
from shared.permissions import IsClient, IsPartner
from users.authentication import ClientJWTAuthentication, PartnerJWTAuthentication

from .availability import compute_available_slots
from .hold_service import acquire_hold, release_hold
from .models import ActivityBookingStatus
from .raw_repository import (
    add_activity_image,
    create_activity,
    create_resource,
    create_tariff,
    delete_activity_image,
    delete_resource,
    delete_tariff,
    fetch_active_activities,
    fetch_activities_for_partner,
    fetch_activity,
    fetch_activity_by_guid,
    fetch_activity_images,
    fetch_all_activities_admin,
    fetch_all_working_hours,
    fetch_available_resource_id,
    fetch_booking_by_guid,
    fetch_bookings_for_client,
    fetch_bookings_for_partner_activity,
    fetch_resource,
    fetch_resource_by_guid,
    fetch_resources_for_activity,
    fetch_tariff_by_guid,
    fetch_tariffs_for_activity,
    insert_pending_booking,
    mark_booking_status,
    update_activity,
    update_resource,
    update_tariff,
    upsert_working_hours,
)
from .raw_serializers import (
    ActivityBookingSerializer,
    ActivityImageSerializer,
    ActivitySerializer,
    AvailabilityQuerySerializer,
    CreateBookingSerializer,
    ResourceSerializer,
    TariffSerializer,
    TimeSlotSerializer,
    WorkingHoursUpdateSerializer,
)


def _get_partner_activity_or_404(guid: str, partner_user_id: int) -> dict:
    activity = fetch_activity_by_guid(guid)
    if activity is None:
        raise NotFound("Activity not found")
    if activity["partner_user_id"] != partner_user_id:
        raise PermissionDenied("You don't own this activity")
    return activity


def _get_active_activity_or_404(guid: str) -> dict:
    activity = fetch_activity_by_guid(guid)
    if activity is None or not activity["is_active"]:
        raise NotFound("Activity not found")
    return activity


# ---------------------------------------------------------------------------
# Partner: Activity CRUD
# ---------------------------------------------------------------------------

class PartnerActivityListCreateView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    def get(self, request):
        activities = fetch_activities_for_partner(partner_user_id=request.user.id)
        return Response(ActivitySerializer(activities, many=True).data)

    def post(self, request):
        serializer = ActivitySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        activity = create_activity(partner_user_id=request.user.id, **serializer.validated_data)
        return Response(ActivitySerializer(activity).data, status=201)


class PartnerActivityDetailView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    def get(self, request, guid):
        activity = _get_partner_activity_or_404(guid, request.user.id)
        return Response(ActivitySerializer(activity).data)

    def patch(self, request, guid):
        activity = _get_partner_activity_or_404(guid, request.user.id)
        serializer = ActivitySerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated = update_activity(activity_id=activity["id"], **serializer.validated_data)
        return Response(ActivitySerializer(updated).data)

    def delete(self, request, guid):
        activity = _get_partner_activity_or_404(guid, request.user.id)
        from .raw_repository import delete_activity
        delete_activity(activity_id=activity["id"])
        return Response(status=204)


class PartnerActivityImageListCreateView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    def get(self, request, guid):
        activity = _get_partner_activity_or_404(guid, request.user.id)
        images = fetch_activity_images(activity["id"])
        return Response(ActivityImageSerializer(images, many=True).data)

    def post(self, request, guid):
        activity = _get_partner_activity_or_404(guid, request.user.id)
        serializer = ActivityImageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        image = add_activity_image(activity_id=activity["id"], **serializer.validated_data)
        return Response(ActivityImageSerializer(image).data, status=201)


class PartnerActivityImageDetailView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    def delete(self, request, guid, image_id):
        activity = _get_partner_activity_or_404(guid, request.user.id)
        delete_activity_image(image_id=image_id, activity_id=activity["id"])
        return Response(status=204)


# ---------------------------------------------------------------------------
# Partner: Tariff CRUD
# ---------------------------------------------------------------------------

class PartnerTariffListCreateView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    def get(self, request, guid):
        activity = _get_partner_activity_or_404(guid, request.user.id)
        tariffs = fetch_tariffs_for_activity(activity["id"])
        return Response(TariffSerializer(tariffs, many=True).data)

    def post(self, request, guid):
        activity = _get_partner_activity_or_404(guid, request.user.id)
        serializer = TariffSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tariff = create_tariff(activity_id=activity["id"], **serializer.validated_data)
        return Response(TariffSerializer(tariff).data, status=201)


class PartnerTariffDetailView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    def _get_owned_tariff(self, tariff_guid, partner_user_id):
        tariff = fetch_tariff_by_guid(tariff_guid)
        if tariff is None:
            raise NotFound("Tariff not found")
        activity = fetch_activity(tariff["activity_id"])
        if activity["partner_user_id"] != partner_user_id:
            raise PermissionDenied("You don't own this tariff")
        return tariff

    def patch(self, request, tariff_guid):
        tariff = self._get_owned_tariff(tariff_guid, request.user.id)
        serializer = TariffSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated = update_tariff(tariff_id=tariff["id"], **serializer.validated_data)
        return Response(TariffSerializer(updated).data)

    def delete(self, request, tariff_guid):
        tariff = self._get_owned_tariff(tariff_guid, request.user.id)
        delete_tariff(tariff_id=tariff["id"])
        return Response(status=204)


# ---------------------------------------------------------------------------
# Partner: Resource CRUD
# ---------------------------------------------------------------------------

class PartnerResourceListCreateView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    def get(self, request, guid):
        activity = _get_partner_activity_or_404(guid, request.user.id)
        resources = fetch_resources_for_activity(activity["id"])
        return Response(ResourceSerializer(resources, many=True).data)

    def post(self, request, guid):
        activity = _get_partner_activity_or_404(guid, request.user.id)
        serializer = ResourceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        resource = create_resource(activity_id=activity["id"], **serializer.validated_data)
        return Response(ResourceSerializer(resource).data, status=201)


class PartnerResourceDetailView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    def _get_owned_resource(self, resource_guid, partner_user_id):
        resource = fetch_resource_by_guid(resource_guid)
        if resource is None:
            raise NotFound("Resource not found")
        activity = fetch_activity(resource["activity_id"])
        if activity["partner_user_id"] != partner_user_id:
            raise PermissionDenied("You don't own this resource")
        return resource

    def patch(self, request, resource_guid):
        resource = self._get_owned_resource(resource_guid, request.user.id)
        serializer = ResourceSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated = update_resource(resource_id=resource["id"], **serializer.validated_data)
        return Response(ResourceSerializer(updated).data)

    def delete(self, request, resource_guid):
        resource = self._get_owned_resource(resource_guid, request.user.id)
        delete_resource(resource_id=resource["id"])
        return Response(status=204)


# ---------------------------------------------------------------------------
# Partner: Working hours + calendar
# ---------------------------------------------------------------------------

class PartnerWorkingHoursView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    def get(self, request, guid):
        activity = _get_partner_activity_or_404(guid, request.user.id)
        return Response(fetch_all_working_hours(activity["id"]))

    def put(self, request, guid):
        activity = _get_partner_activity_or_404(guid, request.user.id)
        serializer = WorkingHoursUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        rows = upsert_working_hours(activity_id=activity["id"], days=serializer.validated_data["days"])
        return Response(rows)


class PartnerActivityCalendarView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    def get(self, request, guid):
        activity = _get_partner_activity_or_404(guid, request.user.id)
        from_date = request.query_params.get("from_date")
        to_date = request.query_params.get("to_date")
        if not from_date or not to_date:
            raise ValidationError("from_date and to_date are required")

        from_dt = datetime.fromisoformat(from_date)
        to_dt = datetime.fromisoformat(to_date)
        resources = fetch_resources_for_activity(activity["id"])
        bookings = fetch_bookings_for_partner_activity(activity["id"], from_dt=from_dt, to_dt=to_dt)

        by_resource: dict[int, list] = {r["id"]: [] for r in resources}
        for booking in bookings:
            by_resource.setdefault(booking["resource_id"], []).append(ActivityBookingSerializer(booking).data)

        return Response([
            {"resource": ResourceSerializer(r).data, "bookings": by_resource.get(r["id"], [])}
            for r in resources
        ])


class PartnerBookingCompleteView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    def post(self, request, booking_guid):
        booking = fetch_booking_by_guid(booking_guid)
        if booking is None:
            raise NotFound("Booking not found")
        activity = fetch_activity(booking["activity_id"])
        if activity["partner_user_id"] != request.user.id:
            raise PermissionDenied("You don't own this booking")
        updated = mark_booking_status(booking_id=booking["id"], status=ActivityBookingStatus.COMPLETED.value)
        return Response(ActivityBookingSerializer(updated).data)


# ---------------------------------------------------------------------------
# Client: browse + availability + booking
# ---------------------------------------------------------------------------

class ClientActivityListView(APIView):
    permission_classes = []
    authentication_classes = []

    def get(self, request):
        category = request.query_params.get("category")
        activities = fetch_active_activities(category=category)
        return Response(ActivitySerializer(activities, many=True).data)


class ClientActivityDetailView(APIView):
    permission_classes = []
    authentication_classes = []

    def get(self, request, guid):
        activity = _get_active_activity_or_404(guid)
        tariffs = fetch_tariffs_for_activity(activity["id"])
        payload = ActivitySerializer(activity).data
        payload["tariffs"] = TariffSerializer(tariffs, many=True).data
        return Response(payload)


class ClientActivityAvailabilityView(APIView):
    permission_classes = []
    authentication_classes = []

    def get(self, request, guid):
        activity = _get_active_activity_or_404(guid)
        query = AvailabilityQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)

        slots = compute_available_slots(
            activity_id=activity["id"],
            tariff_id=query.validated_data["tariff_id"],
            target_date=query.validated_data["date"],
        )
        return Response(TimeSlotSerializer([
            {"start": s.start, "end": s.end, "available_resource_count": len(s.available_resource_ids)}
            for s in slots
        ], many=True).data)


class ClientCreateBookingView(APIView):
    authentication_classes = [ClientJWTAuthentication]
    permission_classes = [IsClient]

    @transaction.atomic
    def post(self, request, guid):
        activity = _get_active_activity_or_404(guid)
        serializer = CreateBookingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tariff_id = serializer.validated_data["tariff_id"]
        starts_at = serializer.validated_data["starts_at"]

        from .raw_repository import fetch_tariff
        tariff = fetch_tariff(tariff_id)
        if tariff is None or tariff["activity_id"] != activity["id"] or not tariff["is_active"]:
            raise ValidationError({"tariff_id": "Invalid tariff for this activity"})

        ends_at = starts_at + timedelta(minutes=tariff["duration_minutes"])
        buffer_minutes = activity["buffer_minutes"]
        blocked_until = ends_at + timedelta(minutes=buffer_minutes)

        resource_id = fetch_available_resource_id(
            activity_id=activity["id"], starts_at=starts_at, blocked_until=blocked_until,
        )
        if resource_id is None:
            raise ValidationError({"starts_at": "No free resource for this time slot"})

        held = acquire_hold(
            resource_id=resource_id, starts_at=starts_at, blocked_until=blocked_until,
            booking_guid=str(request.user.id),
        )
        if not held:
            raise ValidationError({"starts_at": "This slot is currently being booked by someone else"})

        try:
            booking = insert_pending_booking(
                activity_id=activity["id"],
                tariff_id=tariff_id,
                resource_id=resource_id,
                client_user_id=request.user.id,
                starts_at=starts_at,
                ends_at=ends_at,
                buffer_minutes_snapshot=buffer_minutes,
                blocked_until=blocked_until,
                price_snapshot=tariff["price"],
            )
        except IntegrityError:
            release_hold(resource_id=resource_id, starts_at=starts_at, blocked_until=blocked_until)
            raise ValidationError({"starts_at": "This slot was just taken by another booking"})

        return Response(ActivityBookingSerializer(booking).data, status=201)


class ClientBookingListView(APIView):
    authentication_classes = [ClientJWTAuthentication]
    permission_classes = [IsClient]

    def get(self, request):
        bookings = fetch_bookings_for_client(request.user.id)
        return Response(ActivityBookingSerializer(bookings, many=True).data)


class ClientBookingCancelView(APIView):
    authentication_classes = [ClientJWTAuthentication]
    permission_classes = [IsClient]

    def post(self, request, booking_guid):
        booking = fetch_booking_by_guid(booking_guid)
        if booking is None:
            raise NotFound("Booking not found")
        if booking["client_user_id"] != request.user.id:
            raise PermissionDenied("Not your booking")
        updated = mark_booking_status(
            booking_id=booking["id"],
            status=ActivityBookingStatus.CANCELLED.value,
            cancellation_reason="user_cancelled",
        )
        release_hold(resource_id=booking["resource_id"], starts_at=booking["starts_at"], blocked_until=booking["blocked_until"])
        return Response(ActivityBookingSerializer(updated).data)


# ---------------------------------------------------------------------------
# Admin (weel-admin panel): read-only moderation view across all partners
# ---------------------------------------------------------------------------

class AdminActivityListView(APIView):
    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]

    def get(self, request):
        activities = fetch_all_activities_admin()
        return Response(ActivitySerializer(activities, many=True).data)


class AdminActivityDetailView(APIView):
    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]

    def get(self, request, guid):
        activity = fetch_activity_by_guid(guid)
        if activity is None:
            raise NotFound("Activity not found")
        payload = ActivitySerializer(activity).data
        payload["tariffs"] = TariffSerializer(fetch_tariffs_for_activity(activity["id"]), many=True).data
        payload["resources"] = ResourceSerializer(fetch_resources_for_activity(activity["id"]), many=True).data
        return Response(payload)

    def patch(self, request, guid):
        """Admin moderation actions — e.g. deactivating a listing."""
        activity = fetch_activity_by_guid(guid)
        if activity is None:
            raise NotFound("Activity not found")
        serializer = ActivitySerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        updated = update_activity(activity_id=activity["id"], **serializer.validated_data)
        return Response(ActivitySerializer(updated).data)


class AdminActivityCalendarView(APIView):
    authentication_classes = [AdminJWTAuthentication]
    permission_classes = [IsAdminUser]

    def get(self, request, guid):
        activity = fetch_activity_by_guid(guid)
        if activity is None:
            raise NotFound("Activity not found")

        from_date = request.query_params.get("from_date")
        to_date = request.query_params.get("to_date")
        if not from_date or not to_date:
            raise ValidationError("from_date and to_date are required")

        from_dt = datetime.fromisoformat(from_date)
        to_dt = datetime.fromisoformat(to_date)
        resources = fetch_resources_for_activity(activity["id"])
        bookings = fetch_bookings_for_partner_activity(activity["id"], from_dt=from_dt, to_dt=to_dt)

        by_resource: dict[int, list] = {r["id"]: [] for r in resources}
        for booking in bookings:
            by_resource.setdefault(booking["resource_id"], []).append(ActivityBookingSerializer(booking).data)

        return Response([
            {"resource": ResourceSerializer(r).data, "bookings": by_resource.get(r["id"], [])}
            for r in resources
        ])
