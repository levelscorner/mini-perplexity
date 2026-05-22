"""Client to the provided LLM gateway.

Aligned to the **V2** API (Session 5) you actually have on disk:
  - port 8100, endpoint POST /v1/chat
  - `system` is its own field (string or [{text,cache}]); messages are user/assistant/tool
  - response is top-level: {text, tool_calls:[{id,name,arguments,provider_meta}], parsed, ...}
  - provider shortcuts: "g"=gemini, "gr"=groq, "n"=nvidia, "c"=cerebras, "o"=openrouter, ...
  - structured output via response_format -> response["parsed"]
  - native tool use via tools=[{name,description,input_schema}] + tool_choice
  - NO `auto_route` (that is a V3 addition). On V2 we route by explicit `provider`
    (e.g. provider="g" pins Perception/Memory to Gemini) + the gateway's own failover.

For the Session-6 V3 gateway: set GATEWAY_URL=http://localhost:8101 and
GATEWAY_AUTO_ROUTE=1 so the router pool is used. Everything else is the same shape.
"""
from __future__ import annotations

import os
import time
from typing import Any

import httpx

# Free-tier single-provider setups hit transient 429/503 (no other provider to fail over
# to). Retry with backoff absorbs those — agents need retry/fallback by design.
RETRY_STATUSES = {429, 502, 503}
MAX_RETRIES = 4

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:8101")  # V3 (S6); V2 = :8100
SEND_AUTO_ROUTE = os.getenv("GATEWAY_AUTO_ROUTE", "1") == "1"     # V3 router pool (opt-in; ignored by V2)
DEFAULT_TEMPERATURE = 1.0  # free-tier models degrade / loop at low temperature


class Gateway:
    def __init__(self, base_url: str = GATEWAY_URL, timeout: float = 600.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout)

    # ---- 1. plain chat -----------------------------------------------------
    def chat(self, *, system: str, user: str,
             auto_route: str | None = None, provider: str | None = None,
             temperature: float = DEFAULT_TEMPERATURE) -> str:
        body: dict[str, Any] = {"system": system, "prompt": user, "temperature": temperature}
        self._route(body, auto_route, provider)
        return self._post(body).get("text") or ""

    # ---- 2. native tool use (Decision) -------------------------------------
    def chat_with_tools(self, *, system: str, messages: list[dict],
                        tools: list[dict], auto_route: str | None = "decision",
                        provider: str | None = None,
                        temperature: float = DEFAULT_TEMPERATURE) -> dict:
        body: dict[str, Any] = {"system": system, "messages": messages, "tools": tools,
                                "tool_choice": "auto", "temperature": temperature}
        self._route(body, auto_route, provider)
        data = self._post(body)
        return {
            "text": data.get("text") or None,
            "tool_calls": [{"name": tc["name"], "arguments": tc.get("arguments") or {}}
                           for tc in (data.get("tool_calls") or [])],
            "raw": data,
        }

    # ---- 3. structured output (Perception, Memory classify) ----------------
    def structured(self, *, system: str, user: str, schema: dict,
                   schema_name: str = "Output",
                   auto_route: str | None = None, provider: str | None = "g",
                   temperature: float = DEFAULT_TEMPERATURE) -> dict:
        body: dict[str, Any] = {
            "system": system, "prompt": user, "temperature": temperature,
            "response_format": {"type": "json_schema", "schema": schema,
                                "name": schema_name, "strict": True},
        }
        self._route(body, auto_route, provider)
        data = self._post(body)
        parsed = data.get("parsed")
        if parsed is None:
            # V2 returns parsed=None (usually with 503) on schema-validation failure;
            # fall back to parsing the text if the provider returned raw JSON.
            import json
            parsed = json.loads(data.get("text") or "{}")
        return parsed

    # ---- routing + transport ----------------------------------------------
    def _route(self, body: dict, auto_route: str | None, provider: str | None) -> None:
        # AGENT_PROVIDER is the one knob to flip the whole agent onto a single
        # strong model (e.g. "openai", "anthropic", "g") — overrides per-call hints.
        forced = os.getenv("AGENT_PROVIDER")
        prov = forced or provider
        if prov:
            body["provider"] = prov
        # auto_route only matters when no explicit/forced provider is set (explicit
        # provider bypasses the router anyway); V2 ignores it.
        if auto_route and SEND_AUTO_ROUTE and not prov:
            body["auto_route"] = auto_route

    def _post(self, body: dict) -> dict:
        last: httpx.HTTPStatusError | None = None
        for attempt in range(MAX_RETRIES):
            r = self._client.post(self.base_url + "/v1/chat", json=body)
            if r.status_code in RETRY_STATUSES and attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)   # 1, 2, 4s backoff for transient rate limits
                continue
            r.raise_for_status()
            return r.json()
        r.raise_for_status()  # final attempt's status
        return r.json()
