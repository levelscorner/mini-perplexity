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
from typing import Protocol

# Stop sequences inherited from the original Gemini-only design — they
# stop the model from pattern-matching past its own JSON turn into
# fake "Tool Result:" / "User:" sections that the prompt format trains.
_STOP = ["\nTool Result:", "\nUser:", "\nSystem:"]


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
