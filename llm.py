"""Gemini client wrapper with free-tier throttling.

Matches the course reference convention (reference/08_llm_basic.py):
    - Uses the new `google-genai` SDK (`from google import genai`).
    - Sleeps before each call to respect 15 RPM / 500 RPD free-tier limits.
    - Reads GEMINI_API_KEY, GEMINI_MODEL, THROTTLE_SECONDS from env.

Why a wrapper class exists at all:
    The agent loop never imports Gemini directly. It only ever talks to
    LLMClient. That means swapping to Claude, OpenAI, or a local Ollama
    model is a single-file change — rewrite `generate()` to hit the new
    backend, leave the loop and every other module untouched.
"""
from __future__ import annotations

import os
import time

from google import genai


class LLMClient:
    """Thin wrapper around Gemini. Backend swap = change this file only.

    Instance attributes:
        api_key:          The GEMINI_API_KEY value (must be non-empty).
        model:            Model name, default "gemini-2.5-flash-lite".
        throttle_seconds: Seconds to sleep before every generate() call.
                          Enforces the free-tier rate limit without caller
                          cooperation.
        _client:          Underlying google.genai.Client instance. Leading
                          underscore = "private by convention, don't poke".
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        throttle_seconds: float | None = None,
    ) -> None:
        """Build a Gemini client.

        Every argument follows the same pattern:
            explicit value  →  environment variable  →  default (or error).

        This three-layer resolution is standard for Python CLIs:
            - Tests can pass values directly (highest priority).
            - Real runs pull from .env / shell env (middle priority).
            - Missing values fall through to a sane default, or raise.

        Args:
            api_key:          If None, read GEMINI_API_KEY from env. Required.
            model:            If None, read GEMINI_MODEL (default: flash-lite).
            throttle_seconds: If None, read THROTTLE_SECONDS (default: 4).

        Raises:
            RuntimeError: If no API key can be resolved.
        """
        # `x or y` returns y when x is falsy (None, "", 0, []). Here it means:
        #   "use `api_key` if caller passed one, otherwise fall back to env."
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            # Fail loudly at construction time rather than on the first
            # .generate() call — easier to debug, closer to the real cause.
            raise RuntimeError(
                "GEMINI_API_KEY not set. Copy .env.example to .env and fill in."
            )

        # Model has a default, so the `or` chain goes:
        #   caller-provided → env-provided → "gemini-2.5-flash-lite".
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")

        # Throttle can legitimately be 0 (tests), which is falsy. Using
        # `throttle_seconds or ...` would incorrectly replace 0 with the
        # env default. Use `is not None` to check "explicitly provided".
        self.throttle_seconds = (
            throttle_seconds
            if throttle_seconds is not None
            else float(os.getenv("THROTTLE_SECONDS", "4"))
        )

        # Build the underlying SDK client once; reuse for every call.
        self._client = genai.Client(api_key=self.api_key)

    def generate(self, prompt: str) -> str:
        """Send a prompt, return the text response.

        Sleeps `self.throttle_seconds` *before* the API call. Putting the
        sleep before (not after) means:
            - The first call of a session is slow, but subsequent calls
              don't stack up against a rate limit window that hasn't
              reset yet.
            - If the caller aborts before the sleep finishes, no API call
              ever happened, so no quota was burned.

        Args:
            prompt: The fully-rendered prompt string (system + history).

        Returns:
            The model's text response, or an empty string if the SDK
            returned None (rare; can happen if safety filters fire).
        """
        if self.throttle_seconds > 0:
            time.sleep(self.throttle_seconds)

        # One-shot generation. For streaming or chat, the SDK offers
        # .generate_content_stream() and .chats API — not used here
        # because the agent loop re-sends the full history every turn.
        #
        # stop_sequences: critical for our flatten-conversation prompt format.
        # Without these, the model pattern-matches past its own JSON and
        # starts hallucinating "Tool Result:" / "User:" sections (the
        # prompt trains it on those labels). Each of these strings makes
        # Gemini stop generation the instant it's about to emit them.
        response = self._client.models.generate_content(
            model=self.model,
            contents=prompt,
            config={
                "stop_sequences": [
                    "\nTool Result:",
                    "\nUser:",
                    "\nSystem:",
                ],
            },
        )
        # `response.text` can be None if the model refused to answer.
        # The `or ""` ensures callers always get a string, not a Nonetype
        # they'd have to special-case.
        return response.text or ""
