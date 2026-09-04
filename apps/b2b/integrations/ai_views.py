"""Claude and ChatGPT, as the integrations screen sees them.

One set of views for both; `<provider>` in the path says which. Every
endpoint is the owner's, administrator's or manager's, like Meta's — see
`permissions.CanManageIntegrations`.

Three things happen here and nothing else:

* **Connecting** is pasting an API key. The key is checked against the
  vendor (`ai.verify_key`), stored encrypted in the same column Meta's token
  uses, and never returned.
* **Importing** is uploading the vendor's data export. `ai_import` reads it,
  `ai_repository.store_bundle` keeps it, and the chats and projects appear.
* **Chatting** is a message on a chat — imported or started here — answered
  by the vendor with the recent turns as context, both sides stored.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import Http404
from django.utils.translation import gettext_lazy as _
from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.b2b.integrations import ai, crypto
from apps.b2b.integrations import ai_repository as ai_repo
from apps.b2b.integrations import repository as int_repo
from apps.b2b.integrations.ai_import import ExportError, read_export
from apps.b2b.integrations.permissions import CanManageIntegrations
from apps.b2b.integrations.serializers import (
    AiConnectSerializer,
    AiConversationDetailSerializer,
    AiConversationListSerializer,
    AiConversationSerializer,
    AiImportResultSerializer,
    AiModelSerializer,
    AiNewConversationSerializer,
    AiProjectListSerializer,
    AiSendSerializer,
    IntegrationSerializer,
)
from apps.b2b.models import IntegrationProvider, IntegrationStatus
from apps.b2b import repository as b2b_repo
from apps.b2b.workspace.permissions import IsWorkspaceUser
from apps.b2b.workspace.views import WorkspaceAPIView

logger = logging.getLogger(__name__)

AI_TAG = ["B2B / Workspace (mobile)"]

#: Where a person goes to make a key and to download their data. Sent to the
#: app with the connection rather than baked into it, so a moved page is a
#: server change and not a release.
CONSOLE_URLS = {
    IntegrationProvider.CLAUDE: "https://console.anthropic.com/settings/keys",
    IntegrationProvider.CHATGPT: "https://platform.openai.com/api-keys",
}
EXPORT_URLS = {
    IntegrationProvider.CLAUDE: "https://claude.ai/settings/data-privacy-controls",
    IntegrationProvider.CHATGPT: "https://chatgpt.com/#settings/DataControls",
}

#: What the assistant is told before any chat from the app. Short on purpose:
#: the person connected their own account and expects their own assistant,
#: not one that has been re-briefed.
SYSTEM_PROMPT = (
    "You are assisting an employee of a company through the Weel workspace app. "
    "Answer in the language the person writes in."
)


# ─── Payloads ────────────────────────────────────────────────────────────────

def _provider_or_404(provider: str) -> str:
    if provider not in IntegrationProvider.AI:
        raise Http404(
            f"No such integration. Expected one of: {', '.join(IntegrationProvider.AI)}."
        )
    return provider


def ai_payload(company_id: int, provider: str) -> dict:
    """One assistant's row on the integrations screen, connected or not.

    Same shape as `views.meta_payload` so the list is one list; the fields
    Meta has and this does not (`setup`, `pages`) are empty, and `ai` carries
    what only this has.
    """
    integration = int_repo.get_integration(company_id, provider)

    connected_by = None
    if integration and integration.get("connected_by_id"):
        employee = b2b_repo.get_employee(integration["connected_by_id"], company_id)
        connected_by = (employee or {}).get("full_name")

    status_value = (integration or {}).get("status") or IntegrationStatus.DISCONNECTED
    if integration and not integration.get("access_token_enc"):
        status_value = IntegrationStatus.DISCONNECTED

    return {
        "provider": provider,
        "name": IntegrationProvider.LABELS[provider],
        "status": status_value,
        "connected": status_value == IntegrationStatus.CONNECTED,
        # Always: nothing has to be configured on the server for a workspace
        # to paste its own key. `crypto` needs a Fernet key to store it, and
        # a deployment without one is told so at connect time, not here.
        "available": crypto.is_configured(),
        "account_name": (integration or {}).get("account_name"),
        "connected_at": (integration or {}).get("connected_at"),
        "connected_by": connected_by,
        "last_sync_at": (integration or {}).get("last_sync_at"),
        "last_error": (integration or {}).get("last_error"),
        "lead_count": 0,
        "token_expires_at": None,
        "setup": None,
        "pages": [],
        "ai": {
            "model": (integration or {}).get("ai_model"),
            "models": ai_repo.models_of(integration),
            **ai_repo.counts(company_id, provider),
            "last_import_at": (integration or {}).get("last_import_at"),
            "console_url": CONSOLE_URLS[provider],
            "export_url": EXPORT_URLS[provider],
        },
    }


def _project_payload(project: dict) -> dict:
    return {
        "id": project["id"],
        "name": project.get("name") or "",
        "description": project.get("description"),
        "instructions": project.get("instructions"),
        "chat_count": int(project.get("chat_count") or 0),
        "created_at": project.get("external_created_at") or project.get("created_at"),
    }


def _conversation_payload(conversation: dict) -> dict:
    return {
        "id": conversation["id"],
        "title": conversation.get("title") or "",
        "project_id": conversation.get("project_id"),
        "project_name": conversation.get("project_name"),
        "model": conversation.get("model"),
        "source": conversation.get("source") or ai_repo.SOURCE_IMPORT,
        "message_count": int(conversation.get("message_count") or 0),
        "last_message_at": conversation.get("last_message_at"),
        "created_at": conversation.get("external_created_at") or conversation.get("created_at"),
    }


def _message_payload(message: dict) -> dict:
    return {
        "id": message["id"],
        "role": message.get("role") or "user",
        "text": message.get("text") or "",
        "sent_at": message.get("sent_at") or message.get("created_at"),
    }


def _key_for(company_id: int, provider: str) -> tuple[dict | None, str | None]:
    """The connection and its decrypted key, or (row, None) when there is no
    usable key — disconnected, or encrypted under a key since rotated."""
    integration = int_repo.get_integration(company_id, provider)
    if not integration or not integration.get("access_token_enc"):
        return integration, None
    try:
        return integration, crypto.decrypt(integration["access_token_enc"])
    except (ValueError, ImproperlyConfigured):
        return integration, None


# ─── Views ───────────────────────────────────────────────────────────────────

class AiAPIView(WorkspaceAPIView):
    permission_classes = [IsAuthenticated, IsWorkspaceUser, CanManageIntegrations]


class AiConnectionView(AiAPIView):
    """GET    /integrations/<provider>/ — where the connection stands.
    POST   /integrations/<provider>/ — connect with an API key.
    PATCH  /integrations/<provider>/ — pick the model.
    DELETE /integrations/<provider>/ — forget the key."""

    @swagger_auto_schema(tags=AI_TAG, operation_summary="The AI connection",
                         responses={200: IntegrationSerializer()})
    def get(self, request, provider: str):
        provider = _provider_or_404(provider)
        return Response(ai_payload(request.user.company_id, provider))

    @swagger_auto_schema(
        tags=AI_TAG,
        operation_summary="Connect Claude or ChatGPT with an API key",
        request_body=AiConnectSerializer(),
        responses={
            200: IntegrationSerializer(),
            400: openapi.Response(description="The vendor did not accept the key"),
            503: openapi.Response(description="The server cannot store a key"),
        },
    )
    def post(self, request, provider: str):
        provider = _provider_or_404(provider)
        serializer = AiConnectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        key = serializer.validated_data["api_key"]

        if not crypto.is_configured():
            return Response(
                {"detail": _("The server has no key to store credentials with. "
                             "Ask an administrator to set B2B_INTEGRATIONS_SECRET_KEY.")},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if not ai.looks_like_key(provider, key):
            return Response(
                {"api_key": [_wrong_console(provider)]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            check = ai.verify_key(provider, key)
        except ai.AiError as exc:
            code = status.HTTP_400_BAD_REQUEST if exc.is_key_problem else status.HTTP_502_BAD_GATEWAY
            return Response({"api_key": [str(exc)]}, status=code)

        company_id = request.user.company_id
        row = int_repo.upsert_integration(
            company_id=company_id,
            provider=provider,
            account_id=None,
            account_name=check.account_name,
            access_token_enc=crypto.encrypt(key),
            token_expires_at=None,
            scopes="",
            connected_by_id=request.user.id,
        )
        if row:
            # Keep the model the person had picked, when it is still offered
            # — a reconnect after a key rotation should not reset a choice.
            previous = row.get("ai_model")
            model = previous if previous in check.models else check.default_model
            ai_repo.set_models(row["id"], model, check.models)
        return Response(ai_payload(company_id, provider))

    @swagger_auto_schema(tags=AI_TAG, operation_summary="Pick the model",
                         request_body=AiModelSerializer(),
                         responses={200: IntegrationSerializer()})
    def patch(self, request, provider: str):
        provider = _provider_or_404(provider)
        serializer = AiModelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        company_id = request.user.company_id
        integration = int_repo.get_integration(company_id, provider)
        model = serializer.validated_data["model"]
        if not integration or model not in ai_repo.models_of(integration):
            return Response(
                {"model": [_("This key cannot use that model.")]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        ai_repo.set_model(company_id, provider, model)
        return Response(ai_payload(company_id, provider))

    @swagger_auto_schema(tags=AI_TAG, operation_summary="Disconnect",
                         responses={200: IntegrationSerializer()})
    def delete(self, request, provider: str):
        provider = _provider_or_404(provider)
        company_id = request.user.company_id
        # The key goes; the imported chats stay. They are the person's own
        # history, brought in on purpose, and reconnecting with a new key
        # should find them where they were.
        int_repo.disconnect(company_id, provider)
        return Response(ai_payload(company_id, provider))


def _wrong_console(provider: str) -> str:
    if provider == IntegrationProvider.CLAUDE:
        return _("A Claude API key starts with sk-ant-. Make one at console.anthropic.com.")
    return _("An OpenAI API key starts with sk-. Make one at platform.openai.com.")


class AiImportView(AiAPIView):
    """POST /integrations/<provider>/import/ — the vendor's data export."""

    parser_classes = [MultiPartParser, FormParser]

    @swagger_auto_schema(
        tags=AI_TAG,
        operation_summary="Import a Claude / ChatGPT data export",
        manual_parameters=[
            openapi.Parameter("file", openapi.IN_FORM, type=openapi.TYPE_FILE, required=True),
        ],
        responses={200: AiImportResultSerializer()},
    )
    def post(self, request, provider: str):
        provider = _provider_or_404(provider)
        upload = request.FILES.get("file")
        if upload is None:
            return Response({"file": [_("No file was sent.")]},
                            status=status.HTTP_400_BAD_REQUEST)
        max_mb = int(getattr(settings, "B2B_AI_MAX_IMPORT_MB", 200))
        if upload.size > max_mb * 1024 * 1024:
            return Response(
                {"file": [_("Files must be smaller than %(n)d MB.") % {"n": max_mb}]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            bundle = read_export(provider, upload, upload.name or "")
        except ExportError as exc:
            return Response({"file": [str(exc)]}, status=status.HTTP_400_BAD_REQUEST)

        company_id = request.user.company_id
        # A workspace may import before pasting a key — the history is
        # useful to read on its own — so make sure there is a row for the
        # counters to hang off.
        integration = ai_repo.ensure_row(company_id, provider)

        result = ai_repo.store_bundle(
            company_id=company_id, provider=provider, bundle=bundle,
            employee_id=request.user.id,
        )
        if integration:
            ai_repo.mark_imported(integration["id"])
        return Response({**result, "integration": ai_payload(company_id, provider)})


class AiProjectListView(AiAPIView):
    """GET /integrations/<provider>/projects/"""

    @swagger_auto_schema(tags=AI_TAG, operation_summary="The assistant's projects",
                         responses={200: AiProjectListSerializer()})
    def get(self, request, provider: str):
        provider = _provider_or_404(provider)
        projects = ai_repo.list_projects(request.user.company_id, provider)
        return Response({"results": [_project_payload(p) for p in projects]})


class AiConversationListView(AiAPIView):
    """GET  /integrations/<provider>/conversations/?project=&q=&limit=&offset=
    POST /integrations/<provider>/conversations/ — start a chat here."""

    @swagger_auto_schema(
        tags=AI_TAG, operation_summary="The chats",
        manual_parameters=[
            openapi.Parameter("project", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter("q", openapi.IN_QUERY, type=openapi.TYPE_STRING),
            openapi.Parameter("limit", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
            openapi.Parameter("offset", openapi.IN_QUERY, type=openapi.TYPE_INTEGER),
        ],
        responses={200: AiConversationListSerializer()},
    )
    def get(self, request, provider: str):
        provider = _provider_or_404(provider)
        company_id = request.user.company_id
        project_id = _int(request.query_params.get("project"))
        query = (request.query_params.get("q") or "").strip() or None
        limit = min(max(_int(request.query_params.get("limit")) or 50, 1), 200)
        offset = max(_int(request.query_params.get("offset")) or 0, 0)
        rows = ai_repo.list_conversations(
            company_id, provider, project_id=project_id, query=query,
            limit=limit, offset=offset,
        )
        return Response({
            "results": [_conversation_payload(row) for row in rows],
            "count": ai_repo.count_conversations(
                company_id, provider, project_id=project_id, query=query,
            ),
        })

    @swagger_auto_schema(tags=AI_TAG, operation_summary="Start a chat",
                         request_body=AiNewConversationSerializer(),
                         responses={201: AiConversationDetailSerializer()})
    def post(self, request, provider: str):
        provider = _provider_or_404(provider)
        serializer = AiNewConversationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        company_id = request.user.company_id
        project_id = serializer.validated_data.get("project_id")
        if project_id is not None and not ai_repo.get_project(project_id, company_id, provider):
            return Response({"project_id": [_("No such project.")]},
                            status=status.HTTP_400_BAD_REQUEST)
        integration = int_repo.get_integration(company_id, provider)
        row = ai_repo.create_conversation(
            company_id=company_id, provider=provider,
            title=(serializer.validated_data.get("title") or "").strip() or _("New chat"),
            model=(integration or {}).get("ai_model"),
            project_id=project_id, created_by_id=request.user.id,
        )
        conversation = ai_repo.get_conversation(row["id"], company_id, provider)
        return Response(
            {**_conversation_payload(conversation), "messages": []},
            status=status.HTTP_201_CREATED,
        )


class AiConversationView(AiAPIView):
    """GET    /integrations/<provider>/conversations/<id>/ — with its turns.
    DELETE /integrations/<provider>/conversations/<id>/"""

    @swagger_auto_schema(tags=AI_TAG, operation_summary="One chat, with its messages",
                         responses={200: AiConversationDetailSerializer()})
    def get(self, request, provider: str, conversation_id: int):
        provider = _provider_or_404(provider)
        conversation = ai_repo.get_conversation(
            conversation_id, request.user.company_id, provider
        )
        if conversation is None:
            raise Http404
        messages = ai_repo.list_messages(conversation_id)
        return Response({
            **_conversation_payload(conversation),
            "messages": [_message_payload(m) for m in messages],
        })

    @swagger_auto_schema(tags=AI_TAG, operation_summary="Delete a chat",
                         responses={204: openapi.Response(description="Deleted")})
    def delete(self, request, provider: str, conversation_id: int):
        provider = _provider_or_404(provider)
        if not ai_repo.delete_conversation(
            conversation_id, request.user.company_id, provider
        ):
            raise Http404
        return Response(status=status.HTTP_204_NO_CONTENT)


class AiMessageView(AiAPIView):
    """POST /integrations/<provider>/conversations/<id>/messages/ — say
    something and get the assistant's answer.

    Both turns are stored before the answer is returned, the person's first:
    a vendor that times out must not lose what they typed, and the app can
    re-read the chat and see the question waiting.
    """

    @swagger_auto_schema(tags=AI_TAG, operation_summary="Send a message",
                         request_body=AiSendSerializer(),
                         responses={200: AiConversationDetailSerializer()})
    def post(self, request, provider: str, conversation_id: int):
        provider = _provider_or_404(provider)
        serializer = AiSendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        text = serializer.validated_data["text"].strip()

        company_id = request.user.company_id
        conversation = ai_repo.get_conversation(conversation_id, company_id, provider)
        if conversation is None:
            raise Http404

        integration, key = _key_for(company_id, provider)
        if key is None:
            return Response(
                {"detail": _("Connect %(name)s with an API key first.")
                 % {"name": IntegrationProvider.LABELS[provider]}},
                status=status.HTTP_409_CONFLICT,
            )
        model = (integration or {}).get("ai_model")
        if not model:
            return Response({"detail": _("Pick a model first.")},
                            status=status.HTTP_409_CONFLICT)

        # A chat from the app takes its title from the first thing said in it.
        if not conversation.get("message_count") and conversation.get("source") == ai_repo.SOURCE_APP:
            ai_repo.set_title(conversation_id, text.splitlines()[0][:80])
        ai_repo.append_message(conversation_id, "user", text)

        history_turns = int(getattr(settings, "B2B_AI_HISTORY_TURNS", 40))
        turns = [
            ai.Turn(role=m["role"], text=m["text"])
            for m in ai_repo.recent_messages(conversation_id, history_turns)
        ]
        system = SYSTEM_PROMPT
        instructions = (conversation.get("project_instructions") or "").strip()
        if instructions:
            system = f"{system}\n\n{instructions}"

        try:
            answer = ai.complete(provider, key, model, turns, system=system)
        except ai.AiError as exc:
            if integration and exc.is_key_problem:
                # The key stopped working — mark it so the screen says so,
                # rather than every send failing with the same sentence.
                int_repo.set_integration_status(
                    integration["id"], IntegrationStatus.ERROR, error=str(exc)
                )
            code = status.HTTP_400_BAD_REQUEST if exc.is_key_problem else status.HTTP_502_BAD_GATEWAY
            return Response({"detail": str(exc)}, status=code)

        if answer:
            ai_repo.append_message(conversation_id, "assistant", answer)
        if integration and integration.get("status") == IntegrationStatus.ERROR:
            int_repo.set_integration_status(integration["id"], IntegrationStatus.CONNECTED)

        conversation = ai_repo.get_conversation(conversation_id, company_id, provider)
        return Response({
            **_conversation_payload(conversation),
            "messages": [_message_payload(m) for m in ai_repo.list_messages(conversation_id)],
        })


def _int(value) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
