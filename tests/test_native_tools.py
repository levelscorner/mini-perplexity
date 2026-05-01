"""Tests for native Anthropic tool use.

Covers:
  - LLMClient.supports_native_tools() flag
  - LLMClient.chat_with_tools() returns the normalized LLMTurn shape
  - mini_perplexity._run_native_tool_loop() round-trips tool_use/tool_result
  - mini_perplexity._dispatch_tool() error envelopes

The Anthropic API itself is mocked at the SDK boundary so these run
without network or credentials.
"""
from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def reload_llm():
    sys.modules.pop("llm", None)
    sys.modules.pop("mini_perplexity", None)
    yield
    sys.modules.pop("llm", None)
    sys.modules.pop("mini_perplexity", None)


def _make_anthropic_message(stop_reason: str, content_blocks: list,
                            input_tokens: int = 100,
                            output_tokens: int = 50):
    """Build an object that mimics anthropic.types.Message duck-style."""
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=content_blocks,
        usage=SimpleNamespace(
            input_tokens=input_tokens, output_tokens=output_tokens,
        ),
    )


def _text_block(text: str):
    return SimpleNamespace(type="text", text=text)


def _tool_use_block(id_: str, name: str, input_: dict):
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


def test_supports_native_tools_anthropic(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("LLM_BACKEND", raising=False)
    from llm import LLMClient
    client = LLMClient()
    assert client.supports_native_tools() is True


def test_supports_native_tools_gemini_false(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    monkeypatch.delenv("LLM_BACKEND", raising=False)
    from llm import LLMClient
    client = LLMClient()
    assert client.supports_native_tools() is False


def test_chat_with_tools_normalizes_text_response(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("LLM_BACKEND", raising=False)

    from llm import LLMClient

    client = LLMClient()
    fake_resp = _make_anthropic_message(
        stop_reason="end_turn",
        content_blocks=[_text_block("Hello, world.")],
    )
    client._impl._client.messages = MagicMock()
    client._impl._client.messages.create = MagicMock(return_value=fake_resp)

    turn = client.chat_with_tools(
        messages=[{"role": "user", "content": "hi"}],
        system="be brief",
        tools=[],
    )
    assert turn.stop_reason == "end_turn"
    assert turn.text == "Hello, world."
    assert turn.tool_calls == []
    assert turn.usage_in == 100
    assert turn.usage_out == 50


def test_chat_with_tools_normalizes_tool_use(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("LLM_BACKEND", raising=False)

    from llm import LLMClient

    client = LLMClient()
    fake_resp = _make_anthropic_message(
        stop_reason="tool_use",
        content_blocks=[
            _text_block("I'll search."),
            _tool_use_block("toolu_1", "web_search", {"query": "claude 4.7"}),
        ],
    )
    client._impl._client.messages = MagicMock()
    client._impl._client.messages.create = MagicMock(return_value=fake_resp)

    turn = client.chat_with_tools(
        messages=[{"role": "user", "content": "what's new?"}],
        system="research",
        tools=[{"name": "web_search", "description": "x",
                "input_schema": {"type": "object"}}],
    )
    assert turn.stop_reason == "tool_use"
    assert turn.text == "I'll search."
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].name == "web_search"
    assert turn.tool_calls[0].id == "toolu_1"
    assert turn.tool_calls[0].input == {"query": "claude 4.7"}


def test_chat_with_tools_raises_on_gemini(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    monkeypatch.delenv("LLM_BACKEND", raising=False)

    from llm import LLMClient

    client = LLMClient()
    with pytest.raises(RuntimeError, match="does not support native tool use"):
        client.chat_with_tools(messages=[], system="", tools=[])


def test_dispatch_tool_unknown_tool(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    from mini_perplexity import _dispatch_tool
    from ui import ReasoningChainUI

    ui = ReasoningChainUI(render_terminal=False)
    out = _dispatch_tool("nonexistent", {}, 1, ui)
    payload = json.loads(out)
    assert payload["error"].startswith("unknown tool")
    assert "web_search" in payload["available"]


def test_dispatch_tool_runs_real_tool(monkeypatch):
    """Confirms the dispatcher hands kwargs through to TOOLS[name]."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    import tools
    captured: dict = {}

    def fake_tool(**kwargs):
        captured.update(kwargs)
        return json.dumps({"ok": True, "echo": kwargs})

    # Inject a fake tool into the dispatch table for this test only.
    monkeypatch.setitem(tools.TOOLS, "fake_tool", fake_tool)

    from mini_perplexity import _dispatch_tool
    from ui import ReasoningChainUI

    ui = ReasoningChainUI(render_terminal=False)
    out = _dispatch_tool("fake_tool", {"query": "hi", "n": 2}, 1, ui)
    assert captured == {"query": "hi", "n": 2}
    assert json.loads(out) == {"ok": True, "echo": {"query": "hi", "n": 2}}


def test_native_tool_loop_full_round_trip(monkeypatch):
    """End-to-end: 1st turn fires render_image, 2nd turn returns end_turn."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.delenv("LLM_BACKEND", raising=False)

    # Stub render_image so we don't need Higgsfield.
    import tools
    fake_result = json.dumps({"type": "image", "slug": "stub-1",
                              "url": "/api/cards/stub-1.png", "alt": "x"})
    monkeypatch.setitem(tools.TOOLS, "render_image",
                        lambda **kw: fake_result)

    # Two-turn fake conversation.
    turn_1 = _make_anthropic_message(
        stop_reason="tool_use",
        content_blocks=[
            _text_block("rendering"),
            _tool_use_block("toolu_a", "render_image",
                            {"prompt": "a cat", "panels": 1}),
        ],
    )
    turn_2 = _make_anthropic_message(
        stop_reason="end_turn",
        content_blocks=[_text_block("Here's your image.")],
    )

    from llm import LLMClient
    client = LLMClient()
    client._impl._client.messages = MagicMock()
    client._impl._client.messages.create = MagicMock(
        side_effect=[turn_1, turn_2]
    )

    from mini_perplexity import _run_native_tool_loop
    from ui import ReasoningChainUI

    ui = ReasoningChainUI(render_terminal=False)
    rc = _run_native_tool_loop(
        llm=client, ui=ui, system="be helpful",
        user_query="render a cat", max_iterations=5,
    )
    assert rc == 0
    # Two messages.create calls: one to fire the tool, one to wrap up.
    assert client._impl._client.messages.create.call_count == 2

    # MagicMock stores references, not snapshots — `messages` was mutated
    # in place across both calls. Inspect the final state instead.
    final_msgs = client._impl._client.messages.create.call_args_list[1].kwargs["messages"]
    # Expected order:
    #   [0] user "render a cat"
    #   [1] assistant (turn 1: text + tool_use)
    #   [2] user [tool_result]
    #   [3] assistant (turn 2: end_turn text)
    assert final_msgs[0]["role"] == "user"
    assert final_msgs[2]["role"] == "user"
    tool_results = final_msgs[2]["content"]
    assert isinstance(tool_results, list)
    assert tool_results[0]["type"] == "tool_result"
    assert tool_results[0]["tool_use_id"] == "toolu_a"


def test_tool_schemas_match_tools_dict():
    """Every TOOL_SCHEMAS entry must have a matching TOOLS callable."""
    from tools import TOOL_SCHEMAS, TOOLS

    schema_names = {s["name"] for s in TOOL_SCHEMAS}
    tool_names = set(TOOLS.keys())

    # Every schema must have a callable.
    assert schema_names <= tool_names, (
        f"schema(s) without dispatcher: {schema_names - tool_names}"
    )
