"""Claude / ChatGPT integrations, where they do not need a database.

What is worth pinning is the reading of the two vendors' exports — the whole
reason the old chats appear at all — and the shaping of a conversation for
the vendor's API:

  * **A Claude export** is a flat list of chats with `chat_messages`, and a
    separate `projects.json`. Both the ZIP and a bare JSON must read.
  * **A ChatGPT export** is a tree per chat. Only the branch the person last
    saw is a conversation; the system and tool nodes are not turns; the
    project is a slug inside `conversation_template_id`.
  * **The wrong vendor's file** is refused by name, not by a parse error.
  * **Anthropic wants alternation**, and an imported chat does not promise
    it, so consecutive turns from one side fold together.
"""
from __future__ import annotations

import io
import json
import zipfile

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from apps.b2b.integrations import ai
from apps.b2b.integrations.ai_import import ExportError, read_export
from apps.b2b.models import IntegrationProvider

CLAUDE = IntegrationProvider.CLAUDE
CHATGPT = IntegrationProvider.CHATGPT


def _upload(document) -> io.BytesIO:
    return io.BytesIO(json.dumps(document).encode())


def _zip(**members) -> io.BytesIO:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, document in members.items():
            archive.writestr(name, json.dumps(document))
    buffer.seek(0)
    return buffer


# ─── Claude ──────────────────────────────────────────────────────────────────

CLAUDE_CHATS = [
    {
        "uuid": "c-1",
        "name": "Invoice wording",
        "created_at": "2025-03-01T10:00:00Z",
        "updated_at": "2025-03-01T10:05:00Z",
        "project_uuid": "p-1",
        "chat_messages": [
            {
                "uuid": "m-1", "sender": "human", "text": "Draft an invoice note",
                "created_at": "2025-03-01T10:00:00Z",
            },
            {
                "uuid": "m-2", "sender": "assistant", "text": "",
                "content": [{"type": "text", "text": "Here is a draft:"},
                            {"type": "text", "text": "Dear customer…"}],
                "created_at": "2025-03-01T10:01:00Z",
            },
            {"uuid": "m-3", "sender": "human", "text": "   ", "created_at": None},
        ],
    },
    {"uuid": "c-2", "name": "", "chat_messages": [
        {"uuid": "m-4", "sender": "human", "text": "Salom, menga yordam bering\nikkinchi qator"},
    ]},
]

CLAUDE_PROJECTS = [
    {
        "uuid": "p-1", "name": "Weel sales", "description": "The funnel",
        "prompt_template": "Always answer in Uzbek.",
        "created_at": "2025-02-01T00:00:00Z",
        "docs": [{"uuid": "d-1", "filename": "notes.md", "content": "…"}],
    },
]


def test_claude_conversations_json_reads_turns_in_order():
    bundle = read_export(CLAUDE, _upload(CLAUDE_CHATS), "conversations.json")

    assert [c.external_id for c in bundle.conversations] == ["c-1", "c-2"]
    first = bundle.conversations[0]
    assert first.title == "Invoice wording"
    assert first.project_external_id == "p-1"
    assert first.created_at.year == 2025
    # The blank third turn is dropped; the block-based second turn is joined.
    assert [(m.role, m.text) for m in first.messages] == [
        ("user", "Draft an invoice note"),
        ("assistant", "Here is a draft:\nDear customer…"),
    ]


def test_a_nameless_claude_chat_is_titled_from_its_first_line():
    bundle = read_export(CLAUDE, _upload(CLAUDE_CHATS), "conversations.json")
    assert bundle.conversations[1].title == "Salom, menga yordam bering"


def test_claude_zip_reads_projects_and_chats_together():
    upload = _zip(**{
        "export/conversations.json": CLAUDE_CHATS,
        "export/projects.json": CLAUDE_PROJECTS,
    })
    bundle = read_export(CLAUDE, upload, "data-export.zip")

    assert len(bundle.conversations) == 2
    assert len(bundle.projects) == 1
    project = bundle.projects[0]
    assert project.external_id == "p-1"
    assert project.name == "Weel sales"
    assert project.instructions == "Always answer in Uzbek."
    assert bundle.message_count == 3


def test_a_chatgpt_file_given_to_claude_is_refused_by_name():
    with pytest.raises(ExportError, match="ChatGPT"):
        read_export(CLAUDE, _upload(CHATGPT_CHATS), "conversations.json")


def test_a_zip_without_conversations_is_refused():
    with pytest.raises(ExportError, match="conversations.json"):
        read_export(CLAUDE, _zip(**{"user.json": {}}), "x.zip")


def test_something_that_is_not_json_is_refused():
    with pytest.raises(ExportError):
        read_export(CLAUDE, io.BytesIO(b"<html>"), "chat.html")


# ─── ChatGPT ─────────────────────────────────────────────────────────────────

def _node(node_id, parent, children, message=None):
    return {"id": node_id, "parent": parent, "children": children, "message": message}


def _msg(node_id, role, parts, kind="text", when=1_700_000_000):
    return {
        "id": node_id,
        "author": {"role": role},
        "create_time": when,
        "content": {"content_type": kind, "parts": parts},
    }


CHATGPT_CHATS = [
    {
        "title": "Pricing table",
        "create_time": 1_700_000_000.5,
        "update_time": 1_700_000_900,
        "conversation_id": "g-1",
        "conversation_template_id": "g-p-68a1b2c3d4e5f6a7b8c9d0e1-weel-pricing",
        "default_model_slug": "gpt-5",
        "current_node": "a2",
        "mapping": {
            "root": _node("root", None, ["sys"]),
            "sys": _node("sys", "root", ["u1"], _msg("sys", "system", [""], when=None)),
            "u1": _node("u1", "sys", ["a1", "a2"], _msg("u1", "user", ["Make a table"])),
            # The first answer was regenerated; `a2` is what the person kept.
            "a1": _node("a1", "u1", [], _msg("a1", "assistant", ["Old answer"], when=1_700_000_100)),
            "a2": _node("a2", "u1", ["t1"], _msg("a2", "assistant", ["New answer"], when=1_700_000_200)),
            "t1": _node("t1", "a2", [], _msg("t1", "tool", ["{}"], kind="code")),
        },
    },
    {
        "title": None,
        "conversation_id": "g-2",
        "current_node": "missing",
        "mapping": {
            "u": _node("u", None, ["a"], _msg("u", "user", ["Hi there", {"asset_pointer": "x"}],
                                              kind="multimodal_text")),
            "a": _node("a", "u", [], _msg("a", "assistant", ["Hello!"], when=1_700_000_300)),
        },
    },
]


def test_chatgpt_keeps_the_branch_the_person_last_saw():
    bundle = read_export(CHATGPT, _upload(CHATGPT_CHATS), "conversations.json")

    chat = bundle.conversations[0]
    assert chat.external_id == "g-1"
    assert chat.title == "Pricing table"
    assert chat.model == "gpt-5"
    assert chat.created_at.year == 2023
    # System and tool nodes are not turns; the abandoned branch is not kept.
    assert [(m.role, m.text) for m in chat.messages] == [
        ("user", "Make a table"),
        ("assistant", "New answer"),
    ]


def test_chatgpt_project_comes_from_the_template_slug():
    bundle = read_export(CHATGPT, _upload(CHATGPT_CHATS), "conversations.json")

    assert bundle.conversations[0].project_external_id == (
        "g-p-68a1b2c3d4e5f6a7b8c9d0e1-weel-pricing"
    )
    assert bundle.conversations[1].project_external_id is None
    assert [p.name for p in bundle.projects] == ["Weel pricing"]


def test_chatgpt_without_a_current_node_falls_back_to_the_latest_leaf():
    bundle = read_export(CHATGPT, _upload(CHATGPT_CHATS), "conversations.json")

    chat = bundle.conversations[1]
    assert chat.title == "Hi there"
    assert [(m.role, m.text) for m in chat.messages] == [
        ("user", "Hi there"),
        ("assistant", "Hello!"),
    ]


def test_a_claude_file_given_to_chatgpt_is_refused_by_name():
    with pytest.raises(ExportError, match="Claude"):
        read_export(CHATGPT, _upload(CLAUDE_CHATS), "conversations.json")


# ─── Talking to the vendor ───────────────────────────────────────────────────

def test_consecutive_turns_from_one_side_are_folded_for_anthropic():
    turns = [
        ai.Turn("assistant", "Welcome!"),
        ai.Turn("user", "First"),
        ai.Turn("user", "Second"),
        ai.Turn("assistant", "Answer"),
        ai.Turn("user", ""),
        ai.Turn("user", "Third"),
    ]
    assert ai._alternating(turns) == [
        {"role": "user", "content": "First\n\nSecond"},
        {"role": "assistant", "content": "Answer"},
        {"role": "user", "content": "Third"},
    ]


def test_keys_from_the_wrong_console_are_told_apart():
    assert ai.looks_like_key(CLAUDE, "sk-ant-api03-abc")
    assert not ai.looks_like_key(CLAUDE, "sk-proj-abc")
    assert ai.looks_like_key(CHATGPT, "sk-proj-abc")
    assert not ai.looks_like_key(CHATGPT, "sk-ant-api03-abc")


def test_only_chat_models_are_offered():
    models = ai._chat_models(CHATGPT, [
        "gpt-4o", "text-embedding-3-small", "whisper-1", "gpt-5", "dall-e-3",
        "o3-mini", "gpt-4o-realtime-preview", "gpt-4o",
    ])
    assert models == ["gpt-4o", "gpt-5", "o3-mini"]
    assert ai.pick_default_model(CHATGPT, models) == "gpt-5"

    claude = ai._chat_models(CLAUDE, ["claude-sonnet-5", "claude-opus-5", "gpt-5"])
    assert claude == ["claude-sonnet-5", "claude-opus-5"]
    assert ai.pick_default_model(CLAUDE, claude) == "claude-opus-5"


def test_key_hint_never_shows_the_key():
    assert ai.key_hint("sk-ant-api03-abcdefgh") == "…efgh"
