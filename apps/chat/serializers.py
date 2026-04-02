from __future__ import annotations

from rest_framework import serializers

from shared.raw.entities import RawUser


class ActorSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    role = serializers.ChoiceField(choices=['admin', 'partner'])
    full_name = serializers.CharField()
    email = serializers.CharField(allow_blank=True, required=False)
    username = serializers.CharField(allow_blank=True, required=False)
    phone_number = serializers.CharField(allow_blank=True, required=False)

    @staticmethod
    def from_admin(user: RawUser):
        first_name = (getattr(user, 'first_name', '') or '').strip()
        last_name = (getattr(user, 'last_name', '') or '').strip()
        full_name = (
            f"{first_name} {last_name}".strip()
            or getattr(user, 'email', '')
            or getattr(user, 'username', '')
            or str(user.id)
        )
        return {
            'id': user.id,
            'role': 'admin',
            'full_name': full_name,
            'email': getattr(user, 'email', '') or '',
            'username': getattr(user, 'username', '') or '',
            'phone_number': getattr(user, 'phone_number', '') or '',
        }

    @staticmethod
    def from_partner(partner: RawUser):
        full_name = f"{(partner.first_name or '').strip()} {(partner.last_name or '').strip()}".strip() or partner.username
        return {
            'id': partner.id,
            'role': 'partner',
            'full_name': full_name,
            'email': partner.email or '',
            'username': partner.username or '',
            'phone_number': partner.phone_number or '',
        }


class ChatMessageSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    conversation_id = serializers.IntegerField()
    sender_id = serializers.SerializerMethodField()
    receiver_id = serializers.SerializerMethodField()
    sender_type = serializers.SerializerMethodField()
    receiver_type = serializers.SerializerMethodField()
    content = serializers.CharField()
    is_read = serializers.BooleanField(required=False, allow_null=True)
    created_at = serializers.DateTimeField()
    updated_at = serializers.DateTimeField()

    @staticmethod
    def _value(obj, key: str):
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)

    def get_sender_id(self, obj):
        return self._value(obj, 'sender_user_id') or self._value(obj, 'sender_id')

    def get_receiver_id(self, obj):
        return self._value(obj, 'receiver_user_id') or self._value(obj, 'receiver_id')

    def get_sender_type(self, obj):
        return self._value(obj, 'sender_role') or self._value(obj, 'sender_type')

    def get_receiver_type(self, obj):
        return self._value(obj, 'receiver_role') or self._value(obj, 'receiver_type')


class ConversationSerializer(serializers.Serializer):
    counterpart = ActorSerializer()
    conversation_id = serializers.IntegerField()
    last_message = ChatMessageSerializer(required=False, allow_null=True)
    unread_count = serializers.IntegerField()
