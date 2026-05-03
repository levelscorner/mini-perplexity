"""Pluggable LLM client. Anthropic preferred, Gemini fallback.

The agent loop only ever calls LLMClient(...).generate(prompt). Swap
backends by setting LLM_BACKEND=anthropic|gemini, or just provide the
matching API key and the right backend is auto-selected.

Env:
    LLM_BACKEND       — explicit override ("anthropic" | "gemini")
    ANTHROPIC_API_KEY — required for anthropic
    ANTHROPIC_MODEL   — defaults to claude-sonnet-4-6
    GEMINI_API_KEY    — required for gemini
    GEMINI_MODEL      — defaults to gemini-2.5-flash-lite
    THROTTLE_SECONDS  — gemini-only throttle (default 4)
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

# Stop sequences inherited from the original Gemini-only design — they
# stop the model from pattern-matching past its own JSON turn into
# fake "Tool Result:" / "User:" sections that the prompt format trains.
_STOP = ["\nTool Result:", "\nUser:", "\nSystem:"]


@dataclass
class ToolCall:
    """One tool_use block from a native-tool-use response."""

    id: str          # tool_use_id — used on the matching tool_result block
    name: str        # tool name (must match a key in tools.TOOLS)
    input: dict[str, Any]


@dataclass
class LLMTurn:
    """Normalized native-tool-use response. Cross-backend shape."""

    stop_reason: str           # "end_turn" | "tool_use" | "max_tokens" | …
    text: str = ""             # combined text from all text blocks
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage_in: int = 0
    usage_out: int = 0
    raw_content: list[Any] = field(default_factory=list)
    """Raw response.content — needed verbatim when echoing the assistant
    turn back into the next messages.create call (Anthropic requires
    the tool_use blocks to round-trip)."""


class _BackendImpl(Protocol):
    def generate(self, prompt: str) -> str: ...


def _detect_backend() -> str:
    """Return 'anthropic' or 'gemini'. Explicit LLM_BACKEND wins."""
    explicit = os.getenv("LLM_BACKEND", "").strip().lower()
    if explicit in {"anthropic", "gemini"}:
        return explicit
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.getenv("GEMINI_API_KEY"):
        return "gemini"
    raise RuntimeError(
        "no LLM key set — export ANTHROPIC_API_KEY or GEMINI_API_KEY"
    )


class _AnthropicImpl:
    """Anthropic Claude — single message, stop-sequence parity with Gemini path."""

    def __init__(self, api_key: str | None = None,
                 model: str | None = None,
                 throttle_seconds: float = 0.0):
        from anthropic import Anthropic
        self.api_key = api_key or os.environ["ANTHROPIC_API_KEY"]
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
        self.throttle_seconds = throttle_seconds
        self._client = Anthropic(api_key=self.api_key)

    def generate(self, prompt: str) -> str:
        if self.throttle_seconds > 0:
            time.sleep(self.throttle_seconds)
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=4096,
            stop_sequences=_STOP,
            messages=[{"role": "user", "content": prompt}],
        )
        for block in resp.content:
            if block.type == "text":
                return block.text or ""
        return ""

    def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
    ) -> LLMTurn:
        """Native Anthropic tool use. One round-trip; caller loops."""
        if self.throttle_seconds > 0:
            time.sleep(self.throttle_seconds)
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system,
            tools=tools,
            messages=messages,
        )

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text or "")
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(id=block.id, name=block.name, input=dict(block.input))
                )

        return LLMTurn(
            stop_reason=resp.stop_reason or "",
            text="\n".join(p for p in text_parts if p),
            tool_calls=tool_calls,
            usage_in=getattr(resp.usage, "input_tokens", 0),
            usage_out=getattr(resp.usage, "output_tokens", 0),
            raw_content=list(resp.content),
        )


class _GeminiImpl:
    """Gemini — preserved verbatim from the previous Gemini-only client."""

    def __init__(self, api_key: str | None = None,
                 model: str | None = None,
                 throttle_seconds: float | None = None):
        from google import genai
        self.api_key = api_key or os.environ["GEMINI_API_KEY"]
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
        self.throttle_seconds = (
            throttle_seconds if throttle_seconds is not None
            else float(os.getenv("THROTTLE_SECONDS", "4"))
        )
        self._client = genai.Client(api_key=self.api_key)

    def generate(self, prompt: str) -> str:
        if self.throttle_seconds > 0:
            time.sleep(self.throttle_seconds)
        response = self._client.models.generate_content(
            model=self.model,
            contents=prompt,
            config={"stop_sequences": _STOP},
        )
        return response.text or ""


class LLMClient:
    """Public interface used by the agent loop. Single-method facade."""

    def __init__(self, *, api_key: str | None = None,
                 model: str | None = None,
                 throttle_seconds: float | None = None):
        backend = _detect_backend()
        if backend == "anthropic":
            self._impl = _AnthropicImpl(
                api_key=api_key, model=model,
                throttle_seconds=throttle_seconds or 0.0,
            )
        else:
            self._impl = _GeminiImpl(
                api_key=api_key, model=model,
                throttle_seconds=throttle_seconds,
            )
        self.backend = backend

    def generate(self, prompt: str) -> str:
        return self._impl.generate(prompt)

    def supports_native_tools(self) -> bool:
        """True when this backend handles tool schemas natively."""
        return self.backend == "anthropic"

    def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        system: str,
        tools: list[dict[str, Any]],
    ) -> LLMTurn:
        """Native tool-use round-trip. Anthropic only — caller must check
        supports_native_tools() first and fall back to the prompt-engineered
        path otherwise."""
        if not self.supports_native_tools():
            raise RuntimeError(
                f"{self.backend} backend does not support native tool use"
            )
        return self._impl.chat_with_tools(messages, system, tools)
