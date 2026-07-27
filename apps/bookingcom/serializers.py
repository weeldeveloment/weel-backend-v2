from __future__ import annotations

from rest_framework import serializers

from apps.pms.serializers import JSONStringField


class BookingComConnectionSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    property_id = serializers.IntegerField(read_only=True)
    enabled = serializers.BooleanField(default=True)
    bookingcom_property_id = serializers.CharField(max_length=255)
    api_url = serializers.CharField(max_length=500)
    api_token = serializers.CharField(required=False, allow_blank=True, allow_null=True, write_only=True)
    username = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    password = serializers.CharField(required=False, allow_blank=True, allow_null=True, write_only=True)
    has_api_token = serializers.SerializerMethodField()
    has_password = serializers.SerializerMethodField()
    last_successful_sync_at = serializers.DateTimeField(read_only=True, allow_null=True)
    last_synced_at = serializers.DateTimeField(read_only=True, allow_null=True)
    last_sync_status = serializers.CharField(read_only=True, allow_null=True)
    last_error = serializers.CharField(read_only=True, allow_null=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    def get_has_api_token(self, obj):
        return bool(obj.get("api_token"))

    def get_has_password(self, obj):
        return bool(obj.get("password"))


class BookingComRoomMappingSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    property_id = serializers.IntegerField(read_only=True)
    external_room_id = serializers.CharField(max_length=255)
    room_id = serializers.IntegerField(required=False, allow_null=True)
    room_type_id = serializers.IntegerField(required=False, allow_null=True)
    is_active = serializers.BooleanField(default=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    def validate(self, data):
        if not data.get("room_id") and not data.get("room_type_id"):
            raise serializers.ValidationError(
                "Either room_id or room_type_id must be provided."
            )
        return data


class BookingComManualSyncSerializer(serializers.Serializer):
    full_resync = serializers.BooleanField(required=False, default=False)


class BookingComSyncRunSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    property_id = serializers.IntegerField(read_only=True)
    connection_id = serializers.IntegerField(read_only=True, allow_null=True)
    triggered_by = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    stats = JSONStringField(read_only=True)
    error_message = serializers.CharField(read_only=True, allow_null=True)
    sync_cursor_from = serializers.DateTimeField(read_only=True, allow_null=True)
    sync_cursor_to = serializers.DateTimeField(read_only=True, allow_null=True)
    started_at = serializers.DateTimeField(read_only=True, allow_null=True)
    finished_at = serializers.DateTimeField(read_only=True, allow_null=True)


class BookingComSyncErrorSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    sync_run_id = serializers.IntegerField(read_only=True)
    property_id = serializers.IntegerField(read_only=True)
    external_reservation_id = serializers.CharField(read_only=True, allow_null=True)
    external_room_id = serializers.CharField(read_only=True, allow_null=True)
    code = serializers.CharField(read_only=True)
    message = serializers.CharField(read_only=True)
    payload = JSONStringField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


class BookingComStatusSerializer(serializers.Serializer):
    connection = BookingComConnectionSerializer(allow_null=True)
    latest_run = BookingComSyncRunSerializer(allow_null=True)
    recent_errors = BookingComSyncErrorSerializer(many=True)
