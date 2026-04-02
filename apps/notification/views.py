import logging

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema

from .serializers import (
    ClientDeviceSerializer,
    PartnerDeviceSerializer,
    PartnerNotificationSerializer,
    MarkAsReadSerializer,
)
from shared.permissions import IsClient, IsPartner
from users.services import ClientDeviceService, PartnerDeviceService
from users.authentication import ClientJWTAuthentication, PartnerJWTAuthentication
from .raw_repository import (
    count_partner_notifications,
    list_partner_notifications,
    mark_partner_notifications_as_read,
)

logger = logging.getLogger(__name__)


class StandardResultsSetPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'limit'
    max_page_size = 100


class FCMTokenUpdateView(APIView):
    authentication_classes = [ClientJWTAuthentication]
    permission_classes = [IsClient]

    @swagger_auto_schema(
        tags=["Notification"],
        operation_summary="Update FCM token",
        operation_description="Update the client's Firebase Cloud Messaging(FCM) token\nfor push notification",
        request_body=ClientDeviceSerializer,
        responses={
            status.HTTP_200_OK: openapi.Response(
                description="FCM token updated successfully",
                examples={
                    "application/json": {"detail": "FCM token updated successfully"}
                },
            ),
            status.HTTP_400_BAD_REQUEST: "Validation error",
            status.HTTP_401_UNAUTHORIZED: "Unauthorized",
        },
    )
    def post(self, request):
        serializer = ClientDeviceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        fcm_token = serializer.validated_data["fcm_token"]
        device_type = serializer.validated_data["device_type"]

        logger.info(
            "Client FCM token update requested. client_id=%s device_type=%s token_preview=%s",
            getattr(request.user, "id", None),
            device_type,
            f"{fcm_token[:8]}...{fcm_token[-4:]}" if len(fcm_token) > 12 else fcm_token,
        )

        ClientDeviceService.register_device(
            client=request.user,
            fcm_token=fcm_token,
            device_type=device_type,
        )

        return Response(
            status=status.HTTP_200_OK,
            data={
                "detail": "FCM token updated successfully",
            },
        )


class PartnerFCMTokenUpdateView(APIView):
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    @swagger_auto_schema(
        tags=["Notification"],
        operation_summary="Update partner FCM token",
        operation_description="Update the partner's Firebase Cloud Messaging(FCM) token\nfor push notification",
        request_body=PartnerDeviceSerializer,
        responses={
            status.HTTP_200_OK: openapi.Response(
                description="Partner FCM token updated successfully",
                examples={
                    "application/json": {
                        "detail": "Partner FCM token updated successfully"
                    }
                },
            ),
            status.HTTP_400_BAD_REQUEST: "Validation error",
            status.HTTP_401_UNAUTHORIZED: "Unauthorized",
        },
    )
    def post(self, request):
        serializer = PartnerDeviceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        fcm_token = serializer.validated_data["fcm_token"]
        device_type = serializer.validated_data["device_type"]

        logger.info(
            "Partner FCM token update requested. partner_id=%s device_type=%s token_preview=%s",
            getattr(request.user, "id", None),
            device_type,
            f"{fcm_token[:8]}...{fcm_token[-4:]}" if len(fcm_token) > 12 else fcm_token,
        )

        PartnerDeviceService.register_device(
            partner=request.user,
            fcm_token=fcm_token,
            device_type=device_type,
        )

        return Response(
            status=status.HTTP_200_OK,
            data={
                "detail": "Partner FCM token updated successfully",
            },
        )


class PartnerNotificationListView(APIView):
    """Get partner notification history"""
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]
    pagination_class = StandardResultsSetPagination

    @swagger_auto_schema(
        tags=["Notification"],
        operation_summary="Get partner notifications",
        operation_description="Get paginated list of partner notifications with read status",
        manual_parameters=[
            openapi.Parameter(
                'page',
                openapi.IN_QUERY,
                description="Page number",
                type=openapi.TYPE_INTEGER,
                default=1
            ),
            openapi.Parameter(
                'limit',
                openapi.IN_QUERY,
                description="Items per page (max 100)",
                type=openapi.TYPE_INTEGER,
                default=20
            ),
        ],
        responses={
            status.HTTP_200_OK: openapi.Response(
                description="List of notifications",
                examples={
                    "application/json": {
                        "notifications": [
                            {
                                "guid": "uuid-string",
                                "title": "New Booking",
                                "body": "You have a new booking request",
                                "notification_type": "booking_new",
                                "data": {"booking_id": "123"},
                                "is_read": False,
                                "created_at": "2024-01-15T10:30:00Z"
                            }
                        ],
                        "total": 50,
                        "unread_count": 3
                    }
                },
            ),
            status.HTTP_401_UNAUTHORIZED: "Unauthorized",
        },
    )
    def get(self, request):
        partner = request.user

        page_number = int(request.query_params.get("page", 1) or 1)
        limit = int(request.query_params.get("limit", self.pagination_class.page_size) or self.pagination_class.page_size)
        limit = max(1, min(limit, self.pagination_class.max_page_size))
        page_number = max(1, page_number)
        offset = (page_number - 1) * limit

        notifications = list_partner_notifications(
            partner_user_id=partner.id,
            limit=limit,
            offset=offset,
        )
        serializer = PartnerNotificationSerializer(notifications, many=True)
        counts = count_partner_notifications(partner.id)

        return Response({
            "notifications": serializer.data,
            "total": counts["total"],
            "unread_count": counts["unread_count"]
        })


class PartnerNotificationMarkAsReadView(APIView):
    """Mark notifications as read"""
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    @swagger_auto_schema(
        tags=["Notification"],
        operation_summary="Mark notifications as read",
        operation_description="Mark specific notifications or all as read",
        request_body=MarkAsReadSerializer,
        responses={
            status.HTTP_200_OK: openapi.Response(
                description="Notifications marked as read",
                examples={
                    "application/json": {
                        "detail": "Notifications marked as read",
                        "marked_count": 5
                    }
                },
            ),
            status.HTTP_400_BAD_REQUEST: "Validation error",
            status.HTTP_401_UNAUTHORIZED: "Unauthorized",
        },
    )
    def post(self, request):
        partner = request.user
        serializer = MarkAsReadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        notification_ids = serializer.validated_data.get('notification_ids', [])

        count = mark_partner_notifications_as_read(
            partner_user_id=partner.id,
            notification_guids=notification_ids or None,
        )

        return Response({
            "detail": "Notifications marked as read",
            "marked_count": count
        })


class PartnerNotificationMarkAllAsReadView(APIView):
    """Mark all notifications as read"""
    authentication_classes = [PartnerJWTAuthentication]
    permission_classes = [IsPartner]

    @swagger_auto_schema(
        tags=["Notification"],
        operation_summary="Mark all notifications as read",
        operation_description="Mark all partner notifications as read",
        responses={
            status.HTTP_200_OK: openapi.Response(
                description="All notifications marked as read",
                examples={
                    "application/json": {
                        "detail": "All notifications marked as read",
                        "marked_count": 5
                    }
                },
            ),
            status.HTTP_401_UNAUTHORIZED: "Unauthorized",
        },
    )
    def post(self, request):
        partner = request.user

        count = mark_partner_notifications_as_read(
            partner_user_id=partner.id,
            notification_guids=None,
        )

        return Response({
            "detail": "All notifications marked as read",
            "marked_count": count
        })
