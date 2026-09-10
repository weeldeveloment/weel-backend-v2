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


class MetaSetupSerializer(serializers.Serializer):
    """Retired. The values a workspace once pasted into its own Facebook app.

    Every company now connects through Weel's one app by signing in, so
    `setup` is always null. The shape stays in the schema so the builds
    already on people's phones, and the API contract check, read it unchanged.
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
    #: Always null — see [MetaSetupSerializer].
    setup = MetaSetupSerializer(allow_null=True)
    pages = IntegrationPageSerializer(many=True)
    #: Meta only: whether the viewer may unplug it or pause a page — the
    #: owner, the administrator, the manager, or whoever connected it.
    can_disconnect = serializers.BooleanField(required=False)
    #: Claude / ChatGPT only; null for Meta.
    ai = serializers.SerializerMethodField()

    def get_ai(self, obj):
        value = obj.get("ai") if isinstance(obj, dict) else None
        return AiSummarySerializer(value).data if value else None


class AiSummarySerializer(serializers.Serializer):
    """What an AI connection has, beyond whether it is connected."""

    model = serializers.CharField(allow_null=True)
    models = serializers.ListField(child=serializers.CharField())
    chat_count = serializers.IntegerField()
    project_count = serializers.IntegerField()
    message_count = serializers.IntegerField()
    last_import_at = serializers.DateTimeField(allow_null=True)
    #: Where the person makes a key, and where they download their data.
    #: Sent by the server so the app never hardcodes a vendor URL that moves.
    console_url = serializers.CharField()
    export_url = serializers.CharField()


class AiConnectSerializer(serializers.Serializer):
    """The one field that connects an assistant. Write-only, like the Meta
    app secret: it is a credential and never comes back."""

    api_key = serializers.CharField(max_length=400, write_only=True, trim_whitespace=True)

    def validate_api_key(self, value: str) -> str:
        value = value.strip()
        if len(value) < 20:
            raise serializers.ValidationError("API kalit juda qisqa.")
        return value


class AiModelSerializer(serializers.Serializer):
    model = serializers.CharField(max_length=120)


class AiProjectSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()
    description = serializers.CharField(allow_null=True)
    instructions = serializers.CharField(allow_null=True)
    chat_count = serializers.IntegerField()
    created_at = serializers.DateTimeField(allow_null=True)


class AiConversationSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    project_id = serializers.IntegerField(allow_null=True)
    project_name = serializers.CharField(allow_null=True)
    model = serializers.CharField(allow_null=True)
    source = serializers.CharField()
    message_count = serializers.IntegerField()
    last_message_at = serializers.DateTimeField(allow_null=True)
    created_at = serializers.DateTimeField(allow_null=True)


class AiMessageSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    role = serializers.CharField()
    text = serializers.CharField()
    sent_at = serializers.DateTimeField(allow_null=True)


class AiConversationDetailSerializer(AiConversationSerializer):
    messages = AiMessageSerializer(many=True)


class AiConversationListSerializer(serializers.Serializer):
    results = AiConversationSerializer(many=True)
    count = serializers.IntegerField()


class AiProjectListSerializer(serializers.Serializer):
    results = AiProjectSerializer(many=True)


class AiNewConversationSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=300, required=False, allow_blank=True)
    project_id = serializers.IntegerField(required=False, allow_null=True)


class AiSendSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=100_000, trim_whitespace=True)


class AiImportResultSerializer(serializers.Serializer):
    projects = serializers.IntegerField()
    chats_created = serializers.IntegerField()
    chats_updated = serializers.IntegerField()
    messages = serializers.IntegerField()
    integration = IntegrationSerializer()


class IntegrationListSerializer(serializers.Serializer):
    results = IntegrationSerializer(many=True)
    can_manage = serializers.BooleanField()


class MetaConnectSerializer(serializers.Serializer):
    authorize_url = serializers.CharField()
    state = serializers.CharField()
    expires_in = serializers.IntegerField()


class PageToggleSerializer(serializers.Serializer):
    is_active = serializers.BooleanField()
