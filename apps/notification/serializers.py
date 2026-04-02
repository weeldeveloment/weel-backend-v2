from __future__ import annotations

from rest_framework import serializers


DEVICE_TYPE_CHOICES = (("ios", "iOS"), ("android", "Android"))


class ClientDeviceSerializer(serializers.Serializer):
    fcm_token = serializers.CharField(max_length=255)
    device_type = serializers.ChoiceField(choices=DEVICE_TYPE_CHOICES)


class PartnerDeviceSerializer(serializers.Serializer):
    fcm_token = serializers.CharField(max_length=255)
    device_type = serializers.ChoiceField(choices=DEVICE_TYPE_CHOICES)


class PartnerNotificationSerializer(serializers.Serializer):
    """Serializer for partner notifications in normalized notification table."""

    guid = serializers.CharField()
    title = serializers.CharField(allow_null=True)
    body = serializers.SerializerMethodField()
    notification_type = serializers.CharField()
    data = serializers.SerializerMethodField()
    is_read = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField()

    def get_body(self, obj):
        return obj.get("push_message")

    def get_data(self, obj):
        return {}

    def get_is_read(self, obj):
        return obj.get("status") == "read"


class PartnerNotificationListSerializer(serializers.Serializer):
    notifications = PartnerNotificationSerializer(many=True)
    total = serializers.IntegerField()
    unread_count = serializers.IntegerField()


class MarkAsReadSerializer(serializers.Serializer):
    notification_ids = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        allow_empty=True,
    )

