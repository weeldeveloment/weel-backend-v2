"""Talking to Claude and ChatGPT with a workspace's own API key.

Two vendors, one shape. Everything the views need is here: whether a key is
real, which models it may use, and an answer to a conversation. The vendors'
wire formats differ and this is the only module that knows how.

**Why a key and not a login.** Neither Anthropic nor OpenAI lets a third party
sign a person into their *consumer* account (claude.ai, chatgpt.com) and read
the chats there — there is no OAuth for it and no endpoint that lists them.
What both offer is the developer API, unlocked by a key the person makes in
their own console. So "connecting the account" here is that key, and the old
chats arrive through the vendor's data export instead — see `ai_import`.

The key is stored exactly like a Meta token — Fernet under `crypto`, never
returned by any endpoint — and every call out of here is made on the
workspace's behalf with it. Calls use `requests` like `meta.py` does rather
than a vendor SDK: this module makes three requests per vendor and the
project's outbound HTTP already goes through one library.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable

import requests
from django.conf import settings

from apps.b2b.models import IntegrationProvider

logger = logging.getLogger(__name__)

ANTHROPIC_BASE = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"
OPENAI_BASE = "https://api.openai.com/v1"

#: Which model a fresh connection starts on, when the key can see it. The
#: person can switch afterwards; this only decides the first answer.
PREFERRED_MODELS = {
    IntegrationProvider.CLAUDE: ("claude-opus-5", "claude-sonnet-5"),
    IntegrationProvider.CHATGPT: ("gpt-5", "gpt-4o"),
}

#: Which of a vendor's models are worth offering as a chat model at all. A
#: key lists embeddings, image, audio and moderation models too; none of them
#: answers a message.
_CHAT_MODEL = {
    IntegrationProvider.CLAUDE: re.compile(r"^claude-"),
    IntegrationProvider.CHATGPT: re.compile(r"^(gpt-|o\d|chatgpt-)"),
}
_NOT_CHAT_MODEL = re.compile(
    r"(embedding|tts|whisper|audio|realtime|transcribe|moderation|image|dall-e|"
    r"search|instruct|computer-use|codex)",
)


class AiError(Exception):
    """The vendor said no, in a sentence worth showing.

    `status` is the vendor's HTTP status, so the view can tell "the key is
    wrong" (401) from "the key is fine and the vendor is down" (5xx) — one
    is the user's to fix and the other is not.
    """

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status

    @property
    def is_key_problem(self) -> bool:
        return self.status in (401, 403)


@dataclass
class KeyCheck:
    """What a valid key told us about itself."""

    models: list[str] = field(default_factory=list)
    default_model: str | None = None
    #: How the screen names the connection — the vendor does not tell us who
    #: owns a key, so it is the key's own last characters, which is how both
    #: consoles list them too.
    account_name: str = ""


def _timeout() -> int:
    return int(getattr(settings, "B2B_AI_REQUEST_TIMEOUT", 120))


def _max_tokens() -> int:
    return int(getattr(settings, "B2B_AI_MAX_OUTPUT_TOKENS", 4096))


def key_hint(key: str) -> str:
    """The tail of a key, for the screen. Never enough of it to use."""
    tail = key.strip()[-4:]
    return f"…{tail}" if tail else ""


def looks_like_key(provider: str, key: str) -> bool:
    """Cheap sanity before a network call.

    Not a validation — the vendor decides that — but a key pasted from the
    wrong console is the most common mistake and this names it in the same
    breath instead of after a round trip that fails obscurely.
    """
    key = key.strip()
    if provider == IntegrationProvider.CLAUDE:
        return key.startswith("sk-ant-")
    if provider == IntegrationProvider.CHATGPT:
        return key.startswith("sk-") and not key.startswith("sk-ant-")
    return False


def _headers(provider: str, key: str) -> dict[str, str]:
    if provider == IntegrationProvider.CLAUDE:
        return {
            "x-api-key": key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
    return {"Authorization": f"Bearer {key}", "content-type": "application/json"}


def _base(provider: str) -> str:
    return ANTHROPIC_BASE if provider == IntegrationProvider.CLAUDE else OPENAI_BASE


def _vendor_error(provider: str, response: requests.Response) -> AiError:
    """The vendor's own sentence, where it wrote one."""
    message = ""
    try:
        body = response.json()
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict):
            message = error.get("message") or ""
        elif isinstance(error, str):
            message = error
    except ValueError:
        pass
    if response.status_code in (401, 403):
        message = message or "The API key was not accepted."
    elif response.status_code == 429:
        message = message or "The vendor is rate-limiting this key."
    elif not message:
        message = f"{IntegrationProvider.LABELS[provider]} answered {response.status_code}."
    return AiError(message, response.status_code)


def _request(provider: str, key: str, method: str, path: str, **kwargs) -> Any:
    try:
        response = requests.request(
            method,
            f"{_base(provider)}{path}",
            headers=_headers(provider, key),
            timeout=_timeout(),
            **kwargs,
        )
    except requests.RequestException as exc:
        raise AiError(
            f"Could not reach {IntegrationProvider.LABELS[provider]}: {exc}"
        ) from exc
    if response.status_code >= 400:
        raise _vendor_error(provider, response)
    try:
        return response.json()
    except ValueError as exc:
        raise AiError("The vendor answered with something that is not JSON.") from exc


# ─── The key ─────────────────────────────────────────────────────────────────

def _chat_models(provider: str, ids: list[str]) -> list[str]:
    pattern = _CHAT_MODEL[provider]
    seen: list[str] = []
    for model_id in ids:
        if not isinstance(model_id, str) or not pattern.match(model_id):
            continue
        if _NOT_CHAT_MODEL.search(model_id):
            continue
        if model_id not in seen:
            seen.append(model_id)
    return seen


def pick_default_model(provider: str, models: list[str]) -> str | None:
    for preferred in PREFERRED_MODELS.get(provider, ()):
        if preferred in models:
            return preferred
    return models[0] if models else None


def verify_key(provider: str, key: str) -> KeyCheck:
    """Asks the vendor whether the key is real, and what it can use.

    `GET /models` is the one request every key may make, whatever its scope
    or billing state, so it is the check — a chat completion would also
    spend the person's money to find out.
    """
    key = key.strip()
    ids: list[str] = []
    if provider == IntegrationProvider.CLAUDE:
        # Anthropic paginates; one page of a hundred is every model there is.
        body = _request(provider, key, "GET", "/models", params={"limit": 100})
        ids = [item.get("id") for item in body.get("data") or []]
    else:
        body = _request(provider, key, "GET", "/models")
        ids = [item.get("id") for item in body.get("data") or []]
    models = _chat_models(provider, ids)
    if provider == IntegrationProvider.CHATGPT:
        # OpenAI lists models in no useful order; the newest family first
        # reads better in a picker than `gpt-3.5-turbo` at the top.
        models.sort(key=_openai_sort_key)
    return KeyCheck(
        models=models,
        default_model=pick_default_model(provider, models),
        account_name=f"{IntegrationProvider.LABELS[provider]} {key_hint(key)}",
    )


def _openai_sort_key(model_id: str) -> tuple:
    match = re.match(r"^(gpt|o|chatgpt)-?(\d+(?:\.\d+)?)", model_id)
    version = float(match.group(2)) if match else 0.0
    # Bare family names ("gpt-5") ahead of dated snapshots of the same.
    return (-version, len(model_id), model_id)


# ─── A turn ──────────────────────────────────────────────────────────────────

@dataclass
class Turn:
    role: str  # "user" | "assistant"
    text: str


def _alternating(turns: list[Turn]) -> list[dict[str, str]]:
    """Consecutive turns by the same side folded into one.

    Anthropic requires user and assistant to alternate and the first to be
    the user's. An imported chat does not promise either — a person who sent
    two messages before the answer, or an export that begins with the
    assistant's greeting — so the history is normalised here rather than
    refused there.
    """
    merged: list[dict[str, str]] = []
    for turn in turns:
        text = (turn.text or "").strip()
        if not text:
            continue
        role = "assistant" if turn.role == "assistant" else "user"
        if merged and merged[-1]["role"] == role:
            merged[-1]["content"] += "\n\n" + text
        else:
            merged.append({"role": role, "content": text})
    while merged and merged[0]["role"] != "user":
        merged.pop(0)
    return merged


def complete(
    provider: str,
    key: str,
    model: str,
    turns: list[Turn],
    *,
    system: str | None = None,
    max_tokens: int | None = None,
) -> str:
    """One answer to the conversation so far. Returns the assistant's text.

    `max_tokens` overrides the deployment-wide ceiling for the one caller
    that needs to — the analyst, whose answer is a report written twice, in
    both of the app's languages, and would be cut off mid-sentence at the
    chat default.
    """
    key = key.strip()
    messages = _alternating(turns)
    if not messages:
        raise AiError("There is nothing to send.")
    limit = max_tokens or _max_tokens()

    if provider == IntegrationProvider.CLAUDE:
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": limit,
            "messages": messages,
        }
        if system:
            payload["system"] = system
        body = _request(provider, key, "POST", "/messages", json=payload)
        if body.get("stop_reason") == "refusal":
            raise AiError("Claude declined to answer this message.", 200)
        parts = [
            block.get("text", "")
            for block in body.get("content") or []
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "".join(parts).strip()

    chat: list[dict[str, str]] = []
    if system:
        chat.append({"role": "system", "content": system})
    chat.extend(messages)
    body = _request(
        provider, key, "POST", "/chat/completions",
        json={"model": model, "messages": chat, "max_completion_tokens": limit},
    )
    choices = body.get("choices") or []
    if not choices:
        raise AiError("ChatGPT answered with no choices.")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    return (content or "").strip()


# ─── A turn with tools ───────────────────────────────────────────────────────

@dataclass
class Tool:
    """One function the model may call: a name, what it is for, and the
    JSON schema of its arguments. Vendor-neutral; `complete_with_tools`
    writes it in each vendor's dialect."""

    name: str
    description: str
    input_schema: dict[str, Any]


#: How the results of the model's calls are handed back: the tool's name
#: and its parsed arguments in, anything JSON-serialisable out. Raising
#: `ToolError` tells the model what went wrong instead of failing the turn.
ToolCall = Callable[[str, dict[str, Any]], Any]


class ToolError(Exception):
    """A tool that could not do what it was asked — shown to the model as
    the tool's result, so it can say so or try differently."""


def _tool_result_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _run_tool(call: ToolCall, name: str, args: Any) -> tuple[str, bool]:
    """Returns ``(text, is_error)``. Nothing a tool raises escapes into the
    request: the model is told, the conversation goes on."""
    if not isinstance(args, dict):
        args = {}
    try:
        return _tool_result_text(call(name, args)), False
    except ToolError as exc:
        return str(exc) or "The tool failed.", True
    except Exception as exc:  # noqa: BLE001 - one bad tool must not lose the answer
        logger.exception("AI tool %s failed", name)
        return f"The tool failed: {exc}", True


def _max_rounds() -> int:
    return int(getattr(settings, "B2B_AI_MAX_TOOL_ROUNDS", 8))


def complete_with_tools(
    provider: str,
    key: str,
    model: str,
    turns: list[Turn],
    *,
    tools: list[Tool],
    call: ToolCall,
    system: str | None = None,
    max_tokens: int | None = None,
) -> str:
    """One answer to the conversation, with the model free to call `tools`
    on the way. Returns the assistant's final text.

    The loop is the plain one: send, and while the model asks for tools,
    run them and send the results back. `B2B_AI_MAX_TOOL_ROUNDS` bounds
    it; when the bound is hit the last text the model wrote is returned,
    so a runaway never costs more than that many calls.

    No forced tool use (``tool_choice`` any/tool): Claude's newest models
    refuse it, and the tools' descriptions plus the system prompt are what
    tell the model when to look something up.
    """
    key = key.strip()
    messages: list[dict[str, Any]] = _alternating(turns)
    if not messages:
        raise AiError("There is nothing to send.")
    limit = max_tokens or _max_tokens()
    if not tools:
        return complete(provider, key, model, turns, system=system, max_tokens=limit)

    if provider == IntegrationProvider.CLAUDE:
        return _claude_with_tools(key, model, messages, tools, call, system, limit)
    return _openai_with_tools(key, model, messages, tools, call, system, limit)


def _claude_with_tools(key, model, messages, tools, call, system, limit) -> str:
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": limit,
        "messages": list(messages),
        "tools": [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in tools
        ],
    }
    if system:
        payload["system"] = system
    last_text = ""
    for _round in range(_max_rounds() + 1):
        body = _request(IntegrationProvider.CLAUDE, key, "POST", "/messages", json=payload)
        if body.get("stop_reason") == "refusal":
            raise AiError("Claude declined to answer this message.", 200)
        content = [b for b in (body.get("content") or []) if isinstance(b, dict)]
        text = "".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
        last_text = text or last_text
        uses = [b for b in content if b.get("type") == "tool_use"]
        if body.get("stop_reason") != "tool_use" or not uses:
            return text
        # The assistant turn goes back whole — thinking blocks included, if
        # any — and every result in one user turn, in the order asked.
        payload["messages"].append({"role": "assistant", "content": content})
        results = []
        for use in uses:
            result, failed = _run_tool(call, use.get("name") or "", use.get("input"))
            block: dict[str, Any] = {
                "type": "tool_result", "tool_use_id": use.get("id"), "content": result,
            }
            if failed:
                block["is_error"] = True
            results.append(block)
        payload["messages"].append({"role": "user", "content": results})
    return last_text


def _openai_with_tools(key, model, messages, tools, call, system, limit) -> str:
    chat: list[dict[str, Any]] = []
    if system:
        chat.append({"role": "system", "content": system})
    chat.extend(messages)
    payload: dict[str, Any] = {
        "model": model,
        "messages": chat,
        "max_completion_tokens": limit,
        "tools": [
            {"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": t.input_schema,
            }}
            for t in tools
        ],
    }
    last_text = ""
    for _round in range(_max_rounds() + 1):
        body = _request(IntegrationProvider.CHATGPT, key, "POST", "/chat/completions", json=payload)
        choices = body.get("choices") or []
        if not choices:
            raise AiError("ChatGPT answered with no choices.")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        text = (content or "").strip()
        last_text = text or last_text
        calls = message.get("tool_calls") or []
        if not calls:
            return text
        payload["messages"].append({
            "role": "assistant", "content": content or None, "tool_calls": calls,
        })
        for tool_call in calls:
            function = tool_call.get("function") or {}
            try:
                args = json.loads(function.get("arguments") or "{}")
            except ValueError:
                args = {}
            result, _failed = _run_tool(call, function.get("name") or "", args)
            payload["messages"].append({
                "role": "tool", "tool_call_id": tool_call.get("id"), "content": result,
            })
    return last_text
