"""Reading a Claude or ChatGPT data export.

This is how the old chats get in. Both vendors let a person download
everything from their account — claude.ai under *Settings → Privacy → Export
data*, chatgpt.com under *Settings → Data controls → Export data* — and mail
them a ZIP. The person picks that ZIP (or a JSON out of it) in the app and
this module turns it into projects, chats and turns for `ai_repository`.

Nothing here touches the database or the network: given bytes, it answers an
[ExportBundle]. That is what makes it testable against a hand-written export
and what keeps the two vendors' formats — which share nothing — in one file
that says exactly where each field came from.

**Claude.** `conversations.json` is a list of chats, each with
`chat_messages` in order and a `sender` of `human` or `assistant`.
`projects.json` is a list of projects with their instructions
(`prompt_template`). A chat may name its project as `project_uuid`.

**ChatGPT.** `conversations.json` is a list of chats, each a *tree* of nodes
in `mapping` — every regeneration is a branch — with `current_node` naming
the leaf the person last saw. The import follows that leaf back to the root
and keeps that one path, which is the chat as the person remembers it.
Projects are not a file of their own: a chat in a project carries a
`conversation_template_id` of the form `g-p-<id>-<slug>`, and the slug is
the closest thing the export has to the project's name.
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone as dt_timezone
from typing import Any, BinaryIO

from apps.b2b.models import IntegrationProvider

#: Message roles as the database stores them, whichever word the vendor used.
USER = "user"
ASSISTANT = "assistant"


class ExportError(Exception):
    """The file is not an export we can read. The message names why."""


@dataclass
class ImportedMessage:
    role: str
    text: str
    external_id: str | None = None
    sent_at: datetime | None = None


@dataclass
class ImportedConversation:
    external_id: str
    title: str
    messages: list[ImportedMessage] = field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    model: str | None = None
    #: The vendor's id of the project this chat sits in, if any.
    project_external_id: str | None = None


@dataclass
class ImportedProject:
    external_id: str
    name: str
    description: str | None = None
    instructions: str | None = None
    created_at: datetime | None = None


@dataclass
class ExportBundle:
    projects: list[ImportedProject] = field(default_factory=list)
    conversations: list[ImportedConversation] = field(default_factory=list)

    @property
    def message_count(self) -> int:
        return sum(len(c.messages) for c in self.conversations)


# ─── Entry point ─────────────────────────────────────────────────────────────

def read_export(provider: str, upload: BinaryIO, filename: str) -> ExportBundle:
    """The whole file, as projects and chats.

    Accepts the ZIP the vendor mailed, or one JSON file taken out of it. A
    ZIP may hold both `conversations.json` and `projects.json`; a single JSON
    is recognised by its shape.
    """
    if provider not in IntegrationProvider.AI:
        raise ExportError(f"Unknown provider {provider!r}.")

    data = upload.read()
    if zipfile.is_zipfile(io.BytesIO(data)):
        return _read_zip(provider, data)

    try:
        document = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ExportError(
            "This is not a data export. Pick the ZIP the vendor emailed you, "
            "or the conversations.json inside it."
        ) from exc
    return _read_document(provider, document, filename)


def _read_zip(provider: str, data: bytes) -> ExportBundle:
    bundle = ExportBundle()
    found = False
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for info in archive.infolist():
            base = info.filename.rsplit("/", 1)[-1].lower()
            if base not in ("conversations.json", "projects.json"):
                continue
            found = True
            with archive.open(info) as member:
                try:
                    document = json.load(io.TextIOWrapper(member, encoding="utf-8-sig"))
                except ValueError as exc:
                    raise ExportError(f"{info.filename} is not valid JSON.") from exc
            part = _read_document(provider, document, base)
            bundle.projects.extend(part.projects)
            bundle.conversations.extend(part.conversations)
    if not found:
        raise ExportError(
            "The ZIP has no conversations.json in it. Make sure this is the "
            "data export the vendor emailed you."
        )
    return bundle


def _read_document(provider: str, document: Any, filename: str) -> ExportBundle:
    if not isinstance(document, list):
        raise ExportError(f"{filename} should hold a list of conversations.")
    if not document:
        return ExportBundle()

    sample = next((item for item in document if isinstance(item, dict)), None)
    if sample is None:
        raise ExportError(f"{filename} holds nothing recognisable.")

    if provider == IntegrationProvider.CLAUDE:
        if "chat_messages" in sample:
            return ExportBundle(conversations=_claude_conversations(document))
        if "prompt_template" in sample or "docs" in sample:
            return ExportBundle(projects=_claude_projects(document))
        if "mapping" in sample:
            raise ExportError("This looks like a ChatGPT export, not Claude's.")
        raise ExportError("This is not a Claude export we recognise.")

    if "mapping" in sample:
        return _chatgpt_bundle(document)
    if "chat_messages" in sample:
        raise ExportError("This looks like a Claude export, not ChatGPT's.")
    raise ExportError("This is not a ChatGPT export we recognise.")


# ─── Claude ──────────────────────────────────────────────────────────────────

def _claude_conversations(items: list[Any]) -> list[ImportedConversation]:
    conversations = []
    for raw in items:
        if not isinstance(raw, dict) or not raw.get("uuid"):
            continue
        messages = []
        for turn in raw.get("chat_messages") or []:
            if not isinstance(turn, dict):
                continue
            text = _claude_text(turn)
            if not text:
                continue
            sender = (turn.get("sender") or "").lower()
            messages.append(ImportedMessage(
                role=ASSISTANT if sender == "assistant" else USER,
                text=text,
                external_id=_str(turn.get("uuid")),
                sent_at=_when(turn.get("created_at")),
            ))
        project = raw.get("project_uuid") or (raw.get("project") or {}).get("uuid")
        conversations.append(ImportedConversation(
            external_id=str(raw["uuid"]),
            title=_title(raw.get("name"), messages),
            messages=messages,
            created_at=_when(raw.get("created_at")),
            updated_at=_when(raw.get("updated_at")),
            model=_str(raw.get("model")),
            project_external_id=_str(project),
        ))
    return conversations


def _claude_text(turn: dict) -> str:
    """The words in one turn.

    Newer exports carry `content`, a list of blocks; older ones only `text`.
    Both are read, `content` first, because on a turn with both the blocks
    are the complete version and `text` may be a summary.
    """
    blocks = turn.get("content")
    if isinstance(blocks, list):
        parts = [
            block.get("text", "")
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        joined = "\n".join(part for part in parts if part).strip()
        if joined:
            return joined
    return (turn.get("text") or "").strip()


def _claude_projects(items: list[Any]) -> list[ImportedProject]:
    projects = []
    for raw in items:
        if not isinstance(raw, dict) or not raw.get("uuid"):
            continue
        projects.append(ImportedProject(
            external_id=str(raw["uuid"]),
            name=(raw.get("name") or "").strip() or "Project",
            description=_str(raw.get("description")),
            instructions=_str(raw.get("prompt_template")),
            created_at=_when(raw.get("created_at")),
        ))
    return projects


# ─── ChatGPT ─────────────────────────────────────────────────────────────────

_PROJECT_ID = re.compile(r"^g-p-([0-9a-f]+)(?:-(.+))?$", re.IGNORECASE)


def _chatgpt_bundle(items: list[Any]) -> ExportBundle:
    bundle = ExportBundle()
    projects: dict[str, ImportedProject] = {}
    for raw in items:
        if not isinstance(raw, dict):
            continue
        conversation = _chatgpt_conversation(raw)
        if conversation is None:
            continue
        project_id = conversation.project_external_id
        if project_id and project_id not in projects:
            projects[project_id] = ImportedProject(
                external_id=project_id,
                name=_chatgpt_project_name(project_id),
            )
        bundle.conversations.append(conversation)
    bundle.projects = list(projects.values())
    return bundle


def _chatgpt_conversation(raw: dict) -> ImportedConversation | None:
    external_id = raw.get("conversation_id") or raw.get("id")
    mapping = raw.get("mapping")
    if not external_id or not isinstance(mapping, dict):
        return None

    messages = [
        message
        for node in _chatgpt_path(mapping, raw.get("current_node"))
        if (message := _chatgpt_message(node.get("message"))) is not None
    ]

    template = raw.get("conversation_template_id") or raw.get("gizmo_id")
    project_id = template if isinstance(template, str) and _PROJECT_ID.match(template) else None

    return ImportedConversation(
        external_id=str(external_id),
        title=_title(raw.get("title"), messages),
        messages=messages,
        created_at=_when(raw.get("create_time")),
        updated_at=_when(raw.get("update_time")),
        model=_str(raw.get("default_model_slug")),
        project_external_id=project_id,
    )


def _chatgpt_path(mapping: dict, current: Any) -> list[dict]:
    """The nodes from the root to the leaf the person last looked at.

    Walks parents from `current_node`. When the export names no leaf (or a
    leaf that is not in the tree), the deepest node by creation time stands
    in, so the chat still comes through rather than empty.
    """
    if not isinstance(current, str) or current not in mapping:
        current = _latest_leaf(mapping)
        if current is None:
            return []
    path: list[dict] = []
    seen: set[str] = set()
    node_id: Any = current
    while isinstance(node_id, str) and node_id in mapping and node_id not in seen:
        seen.add(node_id)
        node = mapping[node_id]
        if not isinstance(node, dict):
            break
        path.append(node)
        node_id = node.get("parent")
    path.reverse()
    return path


def _latest_leaf(mapping: dict) -> str | None:
    leaves = [
        (node_id, node)
        for node_id, node in mapping.items()
        if isinstance(node, dict) and not node.get("children")
    ]
    if not leaves:
        return None

    def stamp(entry):
        message = entry[1].get("message") or {}
        return message.get("create_time") or 0

    return max(leaves, key=stamp)[0]


def _chatgpt_message(message: Any) -> ImportedMessage | None:
    """One node's words, or None for the nodes that are not a turn.

    The tree also holds the hidden system message, tool calls, the model's
    reasoning recap, browsing results — none of which the person wrote or
    read as a message. Only the two sides of the conversation come through.
    """
    if not isinstance(message, dict):
        return None
    author = message.get("author") or {}
    role = (author.get("role") or "").lower()
    if role not in (USER, ASSISTANT):
        return None
    content = message.get("content") or {}
    kind = content.get("content_type")
    if kind not in ("text", "multimodal_text"):
        return None
    parts = [
        part for part in (content.get("parts") or [])
        if isinstance(part, str) and part.strip()
    ]
    text = "\n".join(parts).strip()
    if not text:
        return None
    return ImportedMessage(
        role=role,
        text=text,
        external_id=_str(message.get("id")),
        sent_at=_when(message.get("create_time")),
    )


def _chatgpt_project_name(project_id: str) -> str:
    """`g-p-68a1…-weel-backend` → "Weel backend".

    The slug is what ChatGPT made of the name when the project was created;
    it is not the name, but it is what the export has.
    """
    match = _PROJECT_ID.match(project_id)
    slug = (match.group(2) if match else "") or ""
    words = [word for word in slug.split("-") if word]
    if not words:
        return "ChatGPT project"
    return " ".join(words).capitalize()


# ─── Shared ──────────────────────────────────────────────────────────────────

def _title(name: Any, messages: list[ImportedMessage]) -> str:
    """The chat's name, or its first line when the vendor left it blank."""
    text = (name or "").strip() if isinstance(name, str) else ""
    if text:
        return text[:300]
    for message in messages:
        if message.role == USER and message.text:
            first = message.text.strip().splitlines()[0]
            return first[:80]
    return "Untitled"


def _str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _when(value: Any) -> datetime | None:
    """A vendor timestamp — ISO text from Claude, epoch seconds from ChatGPT."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=dt_timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt_timezone.utc)
        return parsed
    return None
