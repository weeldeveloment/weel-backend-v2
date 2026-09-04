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
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.b2b import repository as b2b_repo
from apps.b2b.integrations import ai, crypto
from apps.b2b.integrations import ai_repository as ai_repo
from apps.b2b.integrations import repository as int_repo
from apps.b2b.integrations.ai_views import _message_payload
from apps.b2b.integrations.serializers import AiSendSerializer
from apps.b2b.models import IntegrationProvider, IntegrationStatus
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
    provider, integration, key = connected_vendor(user.company_id)
    if provider is None or key is None:
        return None, Response(
            {"detail": _("Ask an owner to connect Claude or ChatGPT in Integrations first.")},
            status=status.HTTP_409_CONFLICT,
        )
    model = integration.get("ai_model")
    try:
        text = ai.complete(
            provider, key, model, _turns(conversation["id"]),
            system=system or _system_prompt(user),
        )
    except ai.AiError as exc:
        if exc.is_key_problem:
            int_repo.set_integration_status(
                integration["id"], IntegrationStatus.ERROR, error=str(exc)
            )
        code = status.HTTP_400_BAD_REQUEST if exc.is_key_problem else status.HTTP_502_BAD_GATEWAY
        return None, Response({"detail": str(exc)}, status=code)
    if integration.get("status") == IntegrationStatus.ERROR:
        int_repo.set_integration_status(integration["id"], IntegrationStatus.CONNECTED)
    if text:
        ai_repo.append_message(conversation["id"], "assistant", text)
    return text, None


# ─── Payloads ────────────────────────────────────────────────────────────────

def status_payload(user) -> dict:
    provider, integration, key = connected_vendor(user.company_id)
    conversation = conversation_for(user.company_id, user.id)
    last = None
    if conversation and conversation.get("message_count"):
        recent = ai_repo.recent_messages(conversation["id"], 1)
        if recent:
            last = _message_payload(recent[-1])
    return {
        "name": NAMES[_language()],
        "connected": key is not None,
        "provider": provider,
        "provider_name": IntegrationProvider.LABELS.get(provider) if provider else None,
        "model": (integration or {}).get("ai_model") if integration else None,
        # Whether this caller could go and connect one — the empty state's
        # button is drawn for them and a sentence for everybody else.
        "can_connect": bool(user.capabilities.get("can_manage_integrations")),
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

        provider, integration, key = connected_vendor(request.user.company_id)
        if key is None:
            return Response(
                {"detail": _("Ask an owner to connect Claude or ChatGPT in Integrations first.")},
                status=status.HTTP_409_CONFLICT,
            )
        conversation = ensure_conversation(
            request.user.company_id, request.user.id, model=integration.get("ai_model")
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
