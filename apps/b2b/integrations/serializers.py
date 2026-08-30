"""What the integrations endpoints answer with.

No serializer here carries a token, and none accepts one. The credential
arrives from Meta at the callback and leaves only through the Graph API — see
`crypto`.
"""
from __future__ import annotations

from rest_framework import serializers


class IntegrationPageSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    page_id = serializers.CharField()
    page_name = serializers.CharField()
    is_active = serializers.BooleanField()
    subscribed = serializers.BooleanField()
    lead_count = serializers.IntegerField()
    last_lead_at = serializers.DateTimeField(allow_null=True)
    last_error = serializers.CharField(allow_null=True)


class MetaAppSerializer(serializers.Serializer):
    """The workspace's own Facebook app — what it takes to connect through one.

    The secret is write-only and never comes back: it is a credential we hold
    on their behalf, exactly like the access token.
    """

    app_id = serializers.CharField(max_length=64)
    app_secret = serializers.CharField(max_length=200, write_only=True)
    #: Optional. Generated when it is left out, which is what should normally
    #: happen — see `credentials.new_verify_token`.
    verify_token = serializers.CharField(
        max_length=120, required=False, allow_blank=True
    )

    def validate_app_id(self, value: str) -> str:
        value = value.strip()
        # Meta app ids are numeric. Catching it here turns "why does nothing
        # happen when I press Ulash" into a message beside the field.
        if not value.isdigit():
            raise serializers.ValidationError(
                "Meta App ID faqat raqamlardan iborat bo‘ladi."
            )
        return value

    def validate_app_secret(self, value: str) -> str:
        value = value.strip()
        if len(value) < 16:
            raise serializers.ValidationError("Meta App Secret juda qisqa.")
        return value


class MetaSetupSerializer(serializers.Serializer):
    """Everything the owner has to paste into their Facebook app.

    Answered by the app endpoints so the screen can show it with a copy button
    rather than sending somebody to a document — the three values below are
    the entire difference between an integration that works and one that
    silently receives nothing.
    """

    uses_own_app = serializers.BooleanField()
    app_id = serializers.CharField(allow_null=True)
    redirect_uri = serializers.CharField()
    webhook_url = serializers.CharField()
    verify_token = serializers.CharField(allow_null=True)


class IntegrationSerializer(serializers.Serializer):
    """One provider, whether or not the workspace has connected it.

    Deliberately answered for a provider with no row at all: the screen lists
    what *could* be connected, so "Meta — ulanmagan" is a real answer and not
    an empty list.
    """

    provider = serializers.CharField()
    name = serializers.CharField()
    status = serializers.CharField()
    connected = serializers.BooleanField()
    available = serializers.BooleanField(
        help_text="Whether the server is configured for this provider at all."
    )
    account_name = serializers.CharField(allow_null=True)
    connected_at = serializers.DateTimeField(allow_null=True)
    connected_by = serializers.CharField(allow_null=True)
    last_sync_at = serializers.DateTimeField(allow_null=True)
    last_error = serializers.CharField(allow_null=True)
    lead_count = serializers.IntegerField()
    token_expires_at = serializers.DateTimeField(allow_null=True)
    #: Whether this workspace connects through its own Facebook app rather
    #: than the deployment's. The screen has to say which, because it decides
    #: whose app settings the redirect URI belongs in.
    setup = MetaSetupSerializer()
    pages = IntegrationPageSerializer(many=True)


class IntegrationListSerializer(serializers.Serializer):
    results = IntegrationSerializer(many=True)
    can_manage = serializers.BooleanField()


class MetaConnectSerializer(serializers.Serializer):
    authorize_url = serializers.CharField()
    state = serializers.CharField()
    expires_in = serializers.IntegerField()


class PageToggleSerializer(serializers.Serializer):
    is_active = serializers.BooleanField()
