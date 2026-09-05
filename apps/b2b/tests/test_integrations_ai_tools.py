"""`ai.complete_with_tools` — the loop that lets the model call back into
the app while it answers, in both vendors' dialects, without a network.

Pinned: a tool the model asks for runs with its parsed arguments and its
result goes back in the shape the vendor expects; a tool that fails tells
the model instead of failing the turn; the loop is bounded.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from django.conf import settings

if not settings.configured:
    settings.configure(USE_TZ=True, TIME_ZONE="UTC", REST_FRAMEWORK={})

from apps.b2b.integrations import ai
from apps.b2b.models import IntegrationProvider

CLAUDE = IntegrationProvider.CLAUDE
CHATGPT = IntegrationProvider.CHATGPT

WEATHER = ai.Tool(
    name="weather",
    description="Today's weather in a city.",
    input_schema={"type": "object", "properties": {"city": {"type": "string"}}},
)


def _claude_tool_use(name, args, use_id="toolu_1"):
    return {
        "stop_reason": "tool_use",
        "content": [
            {"type": "text", "text": "Let me check."},
            {"type": "tool_use", "id": use_id, "name": name, "input": args},
        ],
    }


def _claude_text(text):
    return {"stop_reason": "end_turn", "content": [{"type": "text", "text": text}]}


def _openai_tool_call(name, args, call_id="call_1"):
    return {"choices": [{"message": {
        "content": None,
        "tool_calls": [{"id": call_id, "type": "function",
                        "function": {"name": name, "arguments": json.dumps(args)}}],
    }}]}


def _openai_text(text):
    return {"choices": [{"message": {"content": text}}]}


def test_claude_runs_the_tool_and_sends_the_result_back():
    sent = []
    answers = [_claude_tool_use("weather", {"city": "Toshkent"}), _claude_text("Sunny, 31°.")]

    def request(provider, key, method, path, **kwargs):
        sent.append(kwargs["json"])
        return answers.pop(0)

    calls = []

    def call(name, args):
        calls.append((name, args))
        return {"temp": 31, "sky": "sunny"}

    with patch.object(ai, "_request", side_effect=request):
        text = ai.complete_with_tools(
            CLAUDE, "sk-ant-x", "claude-opus-5", [ai.Turn("user", "Weather in Toshkent?")],
            tools=[WEATHER], call=call, system="Be brief.",
        )

    assert text == "Sunny, 31°."
    assert calls == [("weather", {"city": "Toshkent"})]
    first, second = sent
    assert first["tools"][0]["name"] == "weather"
    assert first["tools"][0]["input_schema"] == WEATHER.input_schema
    assert "tool_choice" not in first
    # The assistant's whole turn goes back, then the result in one user turn.
    assert second["messages"][-2]["role"] == "assistant"
    assert second["messages"][-2]["content"][1]["type"] == "tool_use"
    result = second["messages"][-1]
    assert result["role"] == "user"
    assert result["content"][0]["type"] == "tool_result"
    assert result["content"][0]["tool_use_id"] == "toolu_1"
    assert json.loads(result["content"][0]["content"]) == {"temp": 31, "sky": "sunny"}
    assert "is_error" not in result["content"][0]


def test_a_failing_tool_is_reported_to_the_model_not_raised():
    sent = []
    answers = [_claude_tool_use("weather", {"city": "Atlantis"}), _claude_text("No such city.")]

    def request(provider, key, method, path, **kwargs):
        sent.append(kwargs["json"])
        return answers.pop(0)

    def call(name, args):
        raise ai.ToolError("Unknown city.")

    with patch.object(ai, "_request", side_effect=request):
        text = ai.complete_with_tools(
            CLAUDE, "sk-ant-x", "claude-opus-5", [ai.Turn("user", "Atlantis?")],
            tools=[WEATHER], call=call,
        )
    assert text == "No such city."
    block = sent[1]["messages"][-1]["content"][0]
    assert block["is_error"] is True
    assert block["content"] == "Unknown city."


def test_the_loop_is_bounded(settings):
    settings.B2B_AI_MAX_TOOL_ROUNDS = 2
    count = {"n": 0}

    def request(provider, key, method, path, **kwargs):
        count["n"] += 1
        return _claude_tool_use("weather", {"city": "Toshkent"}, use_id=f"t{count['n']}")

    with patch.object(ai, "_request", side_effect=request):
        text = ai.complete_with_tools(
            CLAUDE, "sk-ant-x", "claude-opus-5", [ai.Turn("user", "Loop?")],
            tools=[WEATHER], call=lambda name, args: "ok",
        )
    assert count["n"] == 3  # the bound plus the first request
    assert text == "Let me check."


def test_chatgpt_runs_the_tool_in_its_own_dialect():
    sent = []
    answers = [_openai_tool_call("weather", {"city": "Samarqand"}), _openai_text("Clear.")]

    def request(provider, key, method, path, **kwargs):
        sent.append((path, kwargs["json"]))
        return answers.pop(0)

    with patch.object(ai, "_request", side_effect=request):
        text = ai.complete_with_tools(
            CHATGPT, "sk-x", "gpt-5", [ai.Turn("user", "Samarqand?")],
            tools=[WEATHER], call=lambda name, args: {"sky": "clear", **args}, system="Brief.",
        )

    assert text == "Clear."
    path, first = sent[0]
    assert path == "/chat/completions"
    assert first["tools"][0]["function"]["name"] == "weather"
    assert first["messages"][0] == {"role": "system", "content": "Brief."}
    _, second = sent[1]
    assert second["messages"][-2]["role"] == "assistant"
    assert second["messages"][-2]["tool_calls"][0]["id"] == "call_1"
    assert second["messages"][-1]["role"] == "tool"
    assert second["messages"][-1]["tool_call_id"] == "call_1"
    assert json.loads(second["messages"][-1]["content"]) == {"sky": "clear", "city": "Samarqand"}


def test_without_tools_it_is_a_plain_completion():
    with patch.object(ai, "complete", return_value="plain") as complete:
        assert ai.complete_with_tools(
            CLAUDE, "k", "m", [ai.Turn("user", "hi")], tools=[], call=lambda n, a: None,
        ) == "plain"
    complete.assert_called_once()


def test_nothing_to_send_is_refused():
    with pytest.raises(ai.AiError):
        ai.complete_with_tools(CLAUDE, "k", "m", [], tools=[WEATHER], call=lambda n, a: None)
