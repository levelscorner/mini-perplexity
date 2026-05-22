"""Thin client to the PROVIDED LLM gateway V3 (runs at http://localhost:8101).

The gateway V3 zip from class ships its own `client.py`. The cleanest thing is
to import and use THAT. This module is a small adapter exposing the three call
shapes the four roles need, so the rest of the agent doesn't care about the
wire format.

⚠️ ALIGN ME: confirm endpoint paths + request/response field names against the
gateway V3 README before relying on this. The method *shapes* are what matter;
the JSON keys below are best-effort and may need a one-line tweak each.

Key V3 features used:
  - auto_route="perception"|"memory"|"decision"  -> router pool picks a tier
  - provider="g"                                  -> override router, force Gemini
  - tools=[...] + tool_choice                     -> native tool use
  - response_format={"type":"json_schema",...}    -> structured output
  - temperature=1                                 -> free models misbehave otherwise
"""
from __future__ import annotations

import os
from typing import Any

import httpx

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:8101")
DEFAULT_TEMPERATURE = 1.0  # free-tier models degrade / loop at temperature 0


class Gateway:
    def __init__(self, base_url: str = GATEWAY_URL, timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout)

    # ---- 1. plain chat (Memory classify, simple Decision answers) ----------
    def chat(self, *, system: str, user: str,
             auto_route: str | None = None, provider: str | None = None,
             temperature: float = DEFAULT_TEMPERATURE) -> str:
        body: dict[str, Any] = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
        }
        if auto_route:
            body["auto_route"] = auto_route
        if provider:
            body["provider"] = provider
        data = self._post("/v1/chat/completions", body)
        return _first_text(data)

    # ---- 2. native tool use (Decision) -------------------------------------
    def chat_with_tools(self, *, system: str, messages: list[dict],
                        tools: list[dict], auto_route: str | None = "decision",
                        provider: str | None = None,
                        temperature: float = DEFAULT_TEMPERATURE) -> dict:
        """Return {"text": str|None, "tool_calls": [{"name","arguments"}], "raw": data}."""
        body: dict[str, Any] = {
            "messages": [{"role": "system", "content": system}] + messages,
            "tools": tools,
            "tool_choice": "auto",
            "temperature": temperature,
        }
        if auto_route:
            body["auto_route"] = auto_route
        if provider:
            body["provider"] = provider
        data = self._post("/v1/chat/completions", body)
        return {
            "text": _first_text(data),
            "tool_calls": _tool_calls(data),
            "raw": data,
        }

    # ---- 3. structured output (Perception, Memory classify) ----------------
    def structured(self, *, system: str, user: str, schema: dict,
                   schema_name: str = "Output",
                   auto_route: str | None = None, provider: str | None = "g",
                   temperature: float = DEFAULT_TEMPERATURE) -> dict:
        """Return the parsed dict validated by the gateway against `schema`."""
        body: dict[str, Any] = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema", "name": schema_name,
                "schema": schema, "strict": True,
            },
            "temperature": temperature,
        }
        if auto_route:
            body["auto_route"] = auto_route
        if provider:
            body["provider"] = provider
        data = self._post("/v1/chat/completions", body)
        # gateway returns a validated parsed dict; fall back to parsing the text
        if isinstance(data, dict) and "parsed" in data:
            return data["parsed"]
        import json
        return json.loads(_first_text(data))

    # ---- transport ---------------------------------------------------------
    def _post(self, path: str, body: dict) -> dict:
        r = self._client.post(self.base_url + path, json=body)
        r.raise_for_status()
        return r.json()


# --- response shape helpers (OpenAI-style; tweak if the gateway differs) ----
def _first_text(data: dict) -> str | None:
    try:
        msg = data["choices"][0]["message"]
        return msg.get("content")
    except (KeyError, IndexError, TypeError):
        return data.get("content") if isinstance(data, dict) else None


def _tool_calls(data: dict) -> list[dict]:
    out: list[dict] = []
    try:
        for tc in data["choices"][0]["message"].get("tool_calls") or []:
            fn = tc.get("function", tc)
            args = fn.get("arguments", {})
            if isinstance(args, str):
                import json
                args = json.loads(args or "{}")
            out.append({"name": fn.get("name"), "arguments": args})
    except (KeyError, IndexError, TypeError):
        pass
    return out
