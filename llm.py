"""Gemini client wrapper with free-tier throttling.

Matches the course reference convention (reference/08_llm_basic.py):
    - Uses the new `google-genai` SDK (`from google import genai`).
    - Sleeps before each call to respect 15 RPM / 500 RPD free-tier limits.
    - Reads GEMINI_API_KEY, GEMINI_MODEL, THROTTLE_SECONDS from env.
"""
from __future__ import annotations

import os
import time

from google import genai


class LLMClient:
    """Thin wrapper around Gemini. Backend swap = change this file only."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        throttle_seconds: float | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "GEMINI_API_KEY not set. Copy .env.example to .env and fill in."
            )
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
        self.throttle_seconds = (
            throttle_seconds
            if throttle_seconds is not None
            else float(os.getenv("THROTTLE_SECONDS", "4"))
        )
        self._client = genai.Client(api_key=self.api_key)

    def generate(self, prompt: str) -> str:
        """Send a prompt, return text response. Sleeps THROTTLE_SECONDS first."""
        if self.throttle_seconds > 0:
            time.sleep(self.throttle_seconds)
        response = self._client.models.generate_content(
            model=self.model,
            contents=prompt,
        )
        return response.text or ""
