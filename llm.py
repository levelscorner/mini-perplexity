"""LLM client wrapper with pluggable backend (Gemini or local Ollama).

The agent loop never imports a vendor SDK directly. It only ever talks to
LLMClient. That means swapping backends is a single-file change — the
loop, tools, parser, and UI stay exactly the same.

Backends supported:

    - gemini  (cloud, free tier, 15 RPM / 500 RPD)
          Uses the new `google-genai` SDK. Throttles before each call.

    - ollama  (local, unlimited, offline-capable)
          Uses plain HTTP against `localhost:11434/api/generate`. No key,
          no throttle. Uses Ollama's `format: "json"` mode which forces
          the model to emit valid JSON at generation time — eliminates
          almost all parse-failure recovery logic.

Pick a backend via env:

    LLM_PROVIDER=auto     (DEFAULT — try local Ollama first; if unreachable,
                           fall back to Gemini. Prefers the free/offline path.)
    LLM_PROVIDER=ollama   (force local only; fail if not running)
    LLM_PROVIDER=gemini   (force cloud only; fail if key missing)

Each backend reads its own subset of env vars (see each _init_ method).

Shared stop_sequences: both backends are instructed to halt generation
at "\\nTool Result:", "\\nUser:", "\\nSystem:". Without this, the model
pattern-matches our flattened conversation format and hallucinates a
whole next turn, which breaks the parser.
"""
from __future__ import annotations

import json
import os
import time

import requests


class LLMClient:
    """Backend-agnostic LLM client. Dispatches to gemini or ollama internals.

    Construction order:
        1. Read LLM_PROVIDER from env (default "gemini").
        2. Call the matching _init_<backend>() to resolve model, URL/key, etc.
        3. On generate(), dispatch to _generate_<backend>().

    Keeping both paths in one class (instead of two subclasses) keeps the
    file short + one-screen readable. If a third backend gets added later
    (Claude, local vLLM, LM Studio), refactor to a Protocol then.
    """

    # Stop sequences shared across all backends. Apply at the boundary where
    # the backend speaks to the model — Gemini via config, Ollama via
    # options.stop. Same list either way.
    _STOP = ["\nTool Result:", "\nUser:", "\nSystem:"]

    def __init__(
        self,
        provider: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        throttle_seconds: float | None = None,
    ) -> None:
        """Build a client for whichever backend LLM_PROVIDER names.

        Args:
            provider:         "auto" | "ollama" | "gemini". If None, reads
                              LLM_PROVIDER (default: "auto" — tries Ollama
                              first, falls back to Gemini).
            model:            Model name override; each backend has its own
                              default if None.
            api_key:          Gemini only. If None, reads GEMINI_API_KEY.
            throttle_seconds: Gemini only. If None, reads THROTTLE_SECONDS
                              (default: 4). Ignored by Ollama.

        Raises:
            RuntimeError: If provider is unknown, or auto-detection can't
                          reach any backend (Ollama down AND no Gemini key).
        """
        # Normalize the provider string so case + whitespace doesn't surprise.
        requested = (provider or os.getenv("LLM_PROVIDER", "auto")).strip().lower()

        if requested == "auto":
            # Try Ollama first (free, offline, no quota). Fall back to Gemini
            # if Ollama isn't reachable. This is the user's preferred default:
            # "only if we don't have local model then use the API."
            if self._ollama_reachable():
                self.provider = "ollama"
                self._init_ollama(model)
            else:
                self.provider = "gemini"
                try:
                    self._init_gemini(model, api_key, throttle_seconds)
                except RuntimeError as exc:
                    # Reword the error so the user knows BOTH paths failed.
                    raise RuntimeError(
                        f"Auto-detect failed: Ollama unreachable at "
                        f"{os.getenv('OLLAMA_HOST', 'http://localhost:11434')} "
                        f"AND {exc}"
                    ) from exc
        elif requested == "gemini":
            self.provider = "gemini"
            self._init_gemini(model, api_key, throttle_seconds)
        elif requested == "ollama":
            self.provider = "ollama"
            self._init_ollama(model)
        else:
            raise RuntimeError(
                f"unknown LLM_PROVIDER '{requested}'; expected 'auto', 'gemini', or 'ollama'"
            )

    @staticmethod
    def _ollama_reachable() -> bool:
        """Probe the Ollama server with a short timeout.

        Uses /api/tags (lists installed models) as a health check — it's
        lightweight and tells us both that the server is up and that the
        HTTP API is responsive. A 1-second connect timeout keeps the
        fallback snappy when Ollama isn't running.
        """
        host = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
        try:
            resp = requests.get(f"{host}/api/tags", timeout=1.0)
            return resp.status_code == 200
        except requests.RequestException:
            return False

    # ─── Gemini backend ────────────────────────────────────────────────────

    def _init_gemini(
        self,
        model: str | None,
        api_key: str | None,
        throttle_seconds: float | None,
    ) -> None:
        """Gemini: needs API key + throttle because of the 15 RPM free tier."""
        # Import the SDK only when the Gemini backend is selected. Keeps the
        # Ollama path usable even if google-genai isn't installed.
        from google import genai

        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "GEMINI_API_KEY not set. Copy .env.example to .env and fill in, "
                "or switch to Ollama with LLM_PROVIDER=ollama."
            )
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
        # Throttle can legitimately be 0.0 for tests, so `is not None` — don't
        # use `or`, which would incorrectly replace 0 with the default.
        self.throttle_seconds = (
            throttle_seconds
            if throttle_seconds is not None
            else float(os.getenv("THROTTLE_SECONDS", "4"))
        )
        self._gemini_client = genai.Client(api_key=self.api_key)

    def _generate_gemini(self, prompt: str) -> str:
        """Send one prompt to Gemini. Sleeps first to respect rate limits."""
        if self.throttle_seconds > 0:
            time.sleep(self.throttle_seconds)
        response = self._gemini_client.models.generate_content(
            model=self.model,
            contents=prompt,
            config={"stop_sequences": self._STOP},
        )
        return response.text or ""

    # ─── Ollama backend ────────────────────────────────────────────────────

    def _init_ollama(self, model: str | None) -> None:
        """Ollama: local HTTP, no key, no throttle."""
        self.model = model or os.getenv("OLLAMA_MODEL", "gemma4:26b")
        self.ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
        self.throttle_seconds = 0.0  # local → no throttle needed

    def _generate_ollama(self, prompt: str) -> str:
        """Send one prompt to Ollama's /api/generate endpoint.

        Key Ollama options we use:
            format: "json"    → forces the model to emit valid JSON. Huge
                                win — our parse-retry branch is unlikely
                                to ever fire on this backend.
            temperature: 0.1  → near-deterministic; stability beats creativity
                                for tool-calling.
            stop: self._STOP  → same stop words as Gemini; defense in depth
                                even though format:json already helps.
            stream: False     → single blocking response; simpler to read.
        """
        url = f"{self.ollama_host}/api/generate"
        payload = {
            "model": self.model,
            "prompt": prompt,
            "format": "json",
            "stream": False,
            # keep_alive tells Ollama how long to keep the model loaded in
            # memory after this call. Default is 5 minutes, which unloads
            # between user sessions and forces a slow cold-start reload on
            # the next run. 30m keeps gemma4:26b (17 GB) resident through
            # an entire demo + submission write-up.
            "keep_alive": os.getenv("OLLAMA_KEEP_ALIVE", "30m"),
            "options": {
                "temperature": 0.1,
                "stop": self._STOP,
            },
        }
        try:
            # Long timeout — 26B params on CPU/GPU takes real time for long
            # prompts. Raise if the server isn't running; the agent loop
            # will show the error and exit cleanly.
            resp = requests.post(url, json=payload, timeout=300)
        except requests.RequestException as exc:
            raise RuntimeError(
                f"Ollama request failed: {type(exc).__name__}: {exc}. "
                f"Is `ollama serve` running at {self.ollama_host}?"
            ) from exc
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Ollama HTTP {resp.status_code}: {resp.text[:200]}"
            )
        data = resp.json()
        # Ollama returns {"response": "...", ...other metadata...}.
        # The "response" field contains the generated text.
        return data.get("response", "") or ""

    # ─── Public dispatch ───────────────────────────────────────────────────

    def generate(self, prompt: str) -> str:
        """Send a prompt, return the text response. Dispatches by provider."""
        if self.provider == "gemini":
            return self._generate_gemini(prompt)
        if self.provider == "ollama":
            return self._generate_ollama(prompt)
        # Unreachable — __init__ validates provider — but defensive.
        raise RuntimeError(f"unknown provider '{self.provider}'")
