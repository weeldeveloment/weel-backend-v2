"""The AI assistant every employee has in their chat list.

Second row of the list, right under "Saqlangan xabarlar": one private chat
per person with whichever assistant the workspace connected — Claude or
ChatGPT, through the key an owner pasted on the integrations screen. The
integrations screen itself is the manager's, and its chats are the
workspace's imported history; *this* is the everyday one, open to anybody
who can see the chat tab, and it is theirs alone.

Storage is the same three tables the integrations screen uses
(`b2b_ai_conversation` / `b2b_ai_message`), under a provider of its own,
``ASSISTANT_PROVIDER``. That is what keeps it out of the imported-history
lists, and what keeps the history in place when the workspace swaps Claude
for ChatGPT: the row does not belong to a vendor, only the answers do.

The assistant is told it works alongside Weel AI — the built-in analyst in
`analyst.py` — and a report from there can be dropped into this chat with a
"how do I fix this?" attached (see `AnalystDiscussView`). The report arrives
as a turn with role ``report``, which the app draws as a card rather than as
something the person said, and which the vendor is shown as the person's
own words: the analyst found this, now explain what to do about it.
"""
from __future__ import annotations

import logging

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.translation import get_language
from django.utils.translation import gettext_lazy as _
from drf_yasg.utils import swagger_auto_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.b2b import repository as b2b_repo
from apps.b2b.integrations import ai, crypto
from apps.b2b.integrations import ai_repository as ai_repo
from apps.b2b.integrations import repository as int_repo
from apps.b2b.integrations.ai_views import CONSOLE_URLS, _message_payload, _wrong_console
from apps.b2b.integrations.serializers import AiSendSerializer
from apps.b2b.models import IntegrationProvider, IntegrationStatus
from apps.b2b.workspace import assistant_keys
from apps.b2b.workspace.access import Module
from apps.b2b.workspace.permissions import IsWorkspaceUser
from apps.b2b.workspace.views import WORKSPACE_TAG, WorkspaceAPIView

logger = logging.getLogger(__name__)

#: The `provider` column the assistant's rows carry. Not a vendor: the row
#: outlives the workspace's choice of one.
ASSISTANT_PROVIDER = "assistant"

#: A turn that is neither side of the conversation — a Weel AI report the
#: person asked the assistant about. Stored as its own role so the app can
#: draw it as a card; sent to the vendor as the person's turn.
ROLE_REPORT = "report"

#: Names the app shows, per language. The vendor is not named: the row says
#: "AI yordamchi" whether it is Claude or ChatGPT behind it, and switching
#: the key does not rename a chat.
NAMES = {"uz": "AI yordamchi", "ru": "AI-помощник", "en": "AI assistant"}

LANGUAGE_NAMES = {"uz": "Uzbek (Latin script)", "ru": "Russian", "en": "English"}

SYSTEM_PROMPT = """You are the AI assistant inside the Weel workspace app, talking to one employee of a company.

Language: answer in the language the person writes in. The app's languages are Uzbek (Latin script) and Russian; if the person's message is ambiguous or very short, answer in {language}. Never mix languages in one answer, and never switch to English unless the person writes in English.

You work alongside Weel AI, the workspace's built-in analyst. Weel AI reads the company's tasks, sales funnel, calendar and attendance and writes daily, weekly, monthly and yearly reports about which departments and which people are doing well or falling behind. When such a report appears in this conversation (marked "Weel AI report"), your job is to explain concretely how to fix what it found: which conversations to have, what to change in the process, what to measure next week. Be specific to the numbers in the report; do not restate them.

Keep answers practical and reasonably short. Use plain paragraphs and simple lists; no headings.

The person you are talking to: {name}, {position}, {role} at {company}."""


# ─── Which key ───────────────────────────────────────────────────────────────

def connected_vendor(company_id: int) -> tuple[str | None, dict | None, str | None]:
    """The vendor the workspace connected, its row, and its decrypted key.

    Claude first when both are connected — it is what the product defaults
    to elsewhere — and ``(None, None, None)`` when neither is, which the
    screens read as "ask an owner to connect one".
    """
    for provider in IntegrationProvider.AI:
        integration = int_repo.get_integration(company_id, provider)
        if not integration or not integration.get("access_token_enc"):
            continue
        if not integration.get("ai_model"):
            continue
        try:
            key = crypto.decrypt(integration["access_token_enc"])
        except (ValueError, ImproperlyConfigured):
            continue
        return provider, integration, key
    return None, None, None


class Resolved:
    """Whose key the assistant answers this person on.

    The person's own connection first (`assistant_keys`), the workspace's
    as the fallback. ``own`` says which, so the status can tell the app
    whether the settings screen is about this person's key or an owner's.
    """

    def __init__(
        self,
        *,
        provider: str | None,
        key: str | None,
        model: str | None,
        own: bool,
        integration: dict | None = None,
        connection: dict | None = None,
    ) -> None:
        self.provider = provider
        self.key = key
        self.model = model
        self.own = own
        self.integration = integration
        self.connection = connection

    @property
    def connected(self) -> bool:
        return self.key is not None and bool(self.model)


def resolve_vendor(user) -> Resolved:
    own = assistant_keys.get(user.id)
    if own and own.get("key_enc"):
        try:
            key = crypto.decrypt(own["key_enc"])
        except (ValueError, ImproperlyConfigured):
            key = None
        if key:
            return Resolved(
                provider=own["provider"], key=key, model=own.get("model"),
                own=True, connection=own,
            )
    provider, integration, key = connected_vendor(user.company_id)
    return Resolved(
        provider=provider, key=key,
        model=(integration or {}).get("ai_model") if integration else None,
        own=False, integration=integration, connection=own,
    )


def _connection_payload(row: dict | None) -> dict | None:
    """The person's own key as the app may see it — never the key itself."""
    if not row:
        return None
    return {
        "provider": row["provider"],
        "provider_name": IntegrationProvider.LABELS.get(row["provider"]),
        "key_hint": row.get("key_hint"),
        "model": row.get("model"),
        "models": list(row.get("models") or []),
        "status": row.get("status") or assistant_keys.STATUS_CONNECTED,
        "error": row.get("error"),
        "console_url": CONSOLE_URLS.get(row["provider"]),
    }


# ─── The conversation ────────────────────────────────────────────────────────

def conversation_for(company_id: int, employee_id: int) -> dict | None:
    """This person's assistant chat, or None until they have said something."""
    return ai_repo.find_owned_conversation(company_id, ASSISTANT_PROVIDER, employee_id)


def ensure_conversation(company_id: int, employee_id: int, *, model: str | None) -> dict:
    existing = conversation_for(company_id, employee_id)
    if existing:
        return existing
    row = ai_repo.create_conversation(
        company_id=company_id,
        provider=ASSISTANT_PROVIDER,
        title=NAMES["uz"],
        model=model,
        project_id=None,
        created_by_id=employee_id,
    )
    return ai_repo.get_conversation(row["id"], company_id, ASSISTANT_PROVIDER) or row


def _language() -> str:
    code = (get_language() or "uz").split("-")[0].lower()
    return code if code in NAMES else "uz"


def _system_prompt(user) -> str:
    company = b2b_repo.get_company(user.company_id) or {}
    employee = user._data if hasattr(user, "_data") else {}
    return SYSTEM_PROMPT.format(
        language=LANGUAGE_NAMES[_language()],
        name=user.full_name or "",
        position=employee.get("position") or "",
        role=user.role,
        company=company.get("name") or "",
    )


def _turns(conversation_id: int) -> list[ai.Turn]:
    history = int(getattr(settings, "B2B_AI_HISTORY_TURNS", 40))
    turns = []
    for message in ai_repo.recent_messages(conversation_id, history):
        role = message["role"]
        text = message["text"]
        if role == ROLE_REPORT:
            role, text = "user", f"Weel AI report:\n\n{text}"
        turns.append(ai.Turn(role=role, text=text))
    return turns


def answer(user, conversation: dict, *, system: str | None = None) -> tuple[str | None, Response | None]:
    """The vendor's reply to the conversation as it stands.

    Returns ``(text, None)`` on success and ``(None, response)`` with the
    refusal to send back when there is no key or the vendor said no. The
    person's own turn has to be stored *before* this is called: a vendor that
    times out must not lose what they typed.
    """
    resolved = resolve_vendor(user)
    if not resolved.connected:
        return None, Response({"detail": NOT_CONNECTED_MESSAGE}, status=status.HTTP_409_CONFLICT)
    try:
        text = ai.complete(
            resolved.provider, resolved.key, resolved.model, _turns(conversation["id"]),
            system=system or _system_prompt(user),
        )
    except ai.AiError as exc:
        if exc.is_key_problem:
            if resolved.own:
                assistant_keys.set_status(user.id, assistant_keys.STATUS_ERROR, error=str(exc))
            elif resolved.integration:
                int_repo.set_integration_status(
                    resolved.integration["id"], IntegrationStatus.ERROR, error=str(exc)
                )
        code = status.HTTP_400_BAD_REQUEST if exc.is_key_problem else status.HTTP_502_BAD_GATEWAY
        return None, Response({"detail": str(exc)}, status=code)
    if resolved.own:
        if (resolved.connection or {}).get("status") == assistant_keys.STATUS_ERROR:
            assistant_keys.set_status(user.id, assistant_keys.STATUS_CONNECTED)
    elif resolved.integration and resolved.integration.get("status") == IntegrationStatus.ERROR:
        int_repo.set_integration_status(resolved.integration["id"], IntegrationStatus.CONNECTED)
    if text:
        ai_repo.append_message(conversation["id"], "assistant", text)
    return text, None


#: What the app is told when nobody has connected anything for this person.
#: Their own key is the fix, whatever their role — so the sentence no longer
#: sends them to an owner.
NOT_CONNECTED_MESSAGE = _("Connect your Claude or ChatGPT key in the assistant's settings first.")


# ─── Payloads ────────────────────────────────────────────────────────────────

def status_payload(user) -> dict:
    resolved = resolve_vendor(user)
    conversation = conversation_for(user.company_id, user.id)
    last = None
    if conversation and conversation.get("message_count"):
        recent = ai_repo.recent_messages(conversation["id"], 1)
        if recent:
            last = _message_payload(recent[-1])
    return {
        "name": NAMES[_language()],
        "connected": resolved.connected,
        "provider": resolved.provider,
        "provider_name": IntegrationProvider.LABELS.get(resolved.provider) if resolved.provider else None,
        "model": resolved.model,
        # Whose key the answers come on. False when it is the workspace's
        # fallback (or nothing at all), so the app can offer "connect your own".
        "own": resolved.own,
        # Everybody may connect their own key, whatever their role — that is
        # the whole point of a personal assistant. Kept for older builds that
        # read it as "may this person see the connect button".
        "can_connect": True,
        # This person's own connection, connected or in error; null when they
        # never pasted a key.
        "connection": _connection_payload(resolved.connection),
        # Whether an owner connected a workspace key this person falls back to.
        "workspace_connected": connected_vendor(user.company_id)[2] is not None,
        "console_urls": dict(CONSOLE_URLS),
        "message_count": int((conversation or {}).get("message_count") or 0),
        "last_message": last,
    }


def conversation_payload(user, conversation: dict | None) -> dict:
    messages = ai_repo.list_messages(conversation["id"]) if conversation else []
    return {
        **status_payload(user),
        "id": conversation["id"] if conversation else None,
        "messages": [_message_payload(m) for m in messages],
    }


# ─── Views ───────────────────────────────────────────────────────────────────

class AssistantAPIView(WorkspaceAPIView):
    required_module = Module.CHAT
    permission_classes = [IsAuthenticated, IsWorkspaceUser]


class AssistantView(AssistantAPIView):
    """GET /api/b2b/workspace/assistant/ — the row on the chat list: whether
    an assistant is connected, and the last thing said in the chat."""

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="The AI assistant's row")
    def get(self, request):
        return Response(status_payload(request.user))


class AssistantMessagesView(AssistantAPIView):
    """GET    /assistant/messages/ — the whole chat.
    POST   /assistant/messages/ — say something and get the answer.
    DELETE /assistant/messages/ — start over."""

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="The assistant chat")
    def get(self, request):
        return Response(conversation_payload(
            request.user, conversation_for(request.user.company_id, request.user.id)
        ))

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Ask the assistant",
                         request_body=AiSendSerializer())
    def post(self, request):
        serializer = AiSendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        text = serializer.validated_data["text"].strip()

        resolved = resolve_vendor(request.user)
        if not resolved.connected:
            return Response({"detail": NOT_CONNECTED_MESSAGE}, status=status.HTTP_409_CONFLICT)
        conversation = ensure_conversation(
            request.user.company_id, request.user.id, model=resolved.model
        )
        ai_repo.append_message(conversation["id"], "user", text)
        _answer, refusal = answer(request.user, conversation)
        if refusal is not None:
            return refusal
        return Response(conversation_payload(
            request.user, conversation_for(request.user.company_id, request.user.id)
        ))

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Clear the assistant chat")
    def delete(self, request):
        conversation = conversation_for(request.user.company_id, request.user.id)
        if conversation:
            ai_repo.delete_conversation(
                conversation["id"], request.user.company_id, ASSISTANT_PROVIDER
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─── The person's own key ────────────────────────────────────────────────────

class OwnConnectSerializer(serializers.Serializer):
    provider = serializers.ChoiceField(choices=list(IntegrationProvider.AI))
    api_key = serializers.CharField(max_length=400, write_only=True, trim_whitespace=True)


class OwnModelSerializer(serializers.Serializer):
    model = serializers.CharField(max_length=120)


class AssistantConnectionView(AssistantAPIView):
    """GET    /assistant/connection/ — this person's own key, as a hint.
    POST   /assistant/connection/ {provider, api_key} — connect (or replace).
    PATCH  /assistant/connection/ {model} — pick the model.
    DELETE /assistant/connection/ — forget the key; the workspace's, if any,
    takes over again.

    Open to every role. The key is checked against the vendor before it is
    kept, the same way the workspace's is on the integrations screen.
    """

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="My own AI connection")
    def get(self, request):
        return Response(status_payload(request.user))

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Connect my own Claude or ChatGPT key",
                         request_body=OwnConnectSerializer())
    def post(self, request):
        serializer = OwnConnectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        provider = serializer.validated_data["provider"]
        key = serializer.validated_data["api_key"]

        if not crypto.is_configured():
            return Response(
                {"detail": _("The server has no key to store credentials with. "
                             "Ask an administrator to set B2B_INTEGRATIONS_SECRET_KEY.")},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        if not ai.looks_like_key(provider, key):
            return Response({"api_key": [_wrong_console(provider)]}, status=status.HTTP_400_BAD_REQUEST)
        try:
            check = ai.verify_key(provider, key)
        except ai.AiError as exc:
            code = status.HTTP_400_BAD_REQUEST if exc.is_key_problem else status.HTTP_502_BAD_GATEWAY
            return Response({"api_key": [str(exc)]}, status=code)

        previous = assistant_keys.get(request.user.id) or {}
        model = previous.get("model") if previous.get("model") in check.models else check.default_model
        assistant_keys.save(
            request.user.id,
            provider=provider,
            key_enc=crypto.encrypt(key),
            key_hint=ai.key_hint(key),
            model=model,
            models=check.models,
        )
        return Response(status_payload(request.user))

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Pick the model my key answers on",
                         request_body=OwnModelSerializer())
    def patch(self, request):
        serializer = OwnModelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        own = assistant_keys.get(request.user.id)
        if not own:
            return Response({"detail": _("Connect a key first.")}, status=status.HTTP_409_CONFLICT)
        model = serializer.validated_data["model"]
        if own.get("models") and model not in own["models"]:
            return Response({"model": [_("This key cannot use that model.")]}, status=status.HTTP_400_BAD_REQUEST)
        assistant_keys.set_model(request.user.id, model)
        return Response(status_payload(request.user))

    @swagger_auto_schema(tags=WORKSPACE_TAG, operation_summary="Forget my own key")
    def delete(self, request):
        assistant_keys.delete(request.user.id)
        return Response(status_payload(request.user))
