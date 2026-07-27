from __future__ import annotations

from rest_framework import status
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_yasg.utils import swagger_auto_schema

from apps.bookingcom.repository import delete_connection, get_connection, replace_room_mappings
from apps.bookingcom.serializers import (
    BookingComConnectionSerializer,
    BookingComManualSyncSerializer,
    BookingComRoomMappingSerializer,
    BookingComStatusSerializer,
)
from apps.bookingcom.service import get_property_mappings, get_property_status, sync_property_reservations
from apps.platform.authentication import PmsJWTAuthentication
from apps.pms.repository import get_property
from apps.pms.views import _require_org
from apps.shared.permissions import HasOrganization


class BookingComBaseView(APIView):
    authentication_classes = [PmsJWTAuthentication]
    permission_classes = [IsAuthenticated, HasOrganization]
    parser_classes = [JSONParser]

    def _get_property(self, request, property_id):
        org_id = _require_org(request)
        if not org_id:
            return None, Response({"detail": "Organization context required."}, status=status.HTTP_400_BAD_REQUEST)
        prop = get_property(property_id, organization_id=int(org_id))
        if not prop:
            return None, Response({"detail": "Property not found."}, status=status.HTTP_404_NOT_FOUND)
        return prop, None


class BookingComConnectionView(BookingComBaseView):
    @swagger_auto_schema(responses={200: BookingComConnectionSerializer(), 404: "Not found"})
    def get(self, request, property_id):
        _, error = self._get_property(request, property_id)
        if error:
            return error
        connection = get_connection(property_id)
        if not connection:
            return Response({"detail": "Booking.com connection not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(BookingComConnectionSerializer(connection).data)

    @swagger_auto_schema(request_body=BookingComConnectionSerializer, responses={200: BookingComConnectionSerializer()})
    def put(self, request, property_id):
        from apps.bookingcom.repository import upsert_connection

        _, error = self._get_property(request, property_id)
        if error:
            return error
        serializer = BookingComConnectionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        row = upsert_connection(property_id, **serializer.validated_data)
        return Response(BookingComConnectionSerializer(row).data)

    @swagger_auto_schema(responses={204: "Disconnected"})
    def delete(self, request, property_id):
        _, error = self._get_property(request, property_id)
        if error:
            return error
        delete_connection(property_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class BookingComRoomMappingView(BookingComBaseView):
    @swagger_auto_schema(responses={200: BookingComRoomMappingSerializer(many=True)})
    def get(self, request, property_id):
        _, error = self._get_property(request, property_id)
        if error:
            return error
        return Response(BookingComRoomMappingSerializer(get_property_mappings(property_id), many=True).data)

    @swagger_auto_schema(request_body=BookingComRoomMappingSerializer(many=True), responses={200: BookingComRoomMappingSerializer(many=True)})
    def put(self, request, property_id):
        _, error = self._get_property(request, property_id)
        if error:
            return error
        if not isinstance(request.data, list):
            return Response({"detail": "Expected a list of mappings."}, status=status.HTTP_400_BAD_REQUEST)
        serializer = BookingComRoomMappingSerializer(data=request.data, many=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        rows = replace_room_mappings(property_id, serializer.validated_data)
        return Response(BookingComRoomMappingSerializer(rows, many=True).data)


class BookingComManualSyncView(BookingComBaseView):
    @swagger_auto_schema(request_body=BookingComManualSyncSerializer, responses={200: BookingComStatusSerializer()})
    def post(self, request, property_id):
        _, error = self._get_property(request, property_id)
        if error:
            return error
        serializer = BookingComManualSyncSerializer(data=request.data or {})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        result = sync_property_reservations(
            property_id,
            full_resync=serializer.validated_data["full_resync"],
            triggered_by="manual",
        )
        return Response(BookingComStatusSerializer(result).data)


class BookingComStatusView(BookingComBaseView):
    @swagger_auto_schema(responses={200: BookingComStatusSerializer()})
    def get(self, request, property_id):
        _, error = self._get_property(request, property_id)
        if error:
            return error
        return Response(BookingComStatusSerializer(get_property_status(property_id)).data)
