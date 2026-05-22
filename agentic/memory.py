"""Memory — a typed service. Persists across runs in state/memory.json.

Reads are pure keyword search (NO LLM) — fully implemented here.
Writes:
  - record_outcome(...)  -> NO LLM, fully implemented.
  - remember(...)        -> ONE LLM classify call. The CLASSIFY PROMPT is yours
                            to write (see TODO). Everything around it is wired.

Reminder: Memory is NOT the conversation. It holds durable facts/preferences
(survive forever) + tool_outcomes + scratchpad. The per-run "history" the loop
carries is separate. Query C works only because run 1 writes a durable fact.
"""
from __future__ import annotations

import json
import re
import uuid
from pathlib import Path

from gateway_client import Gateway
from schemas import MemoryItem, MemoryKind, ToolCall

_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "of", "to", "in", "on", "for",
    "and", "or", "my", "me", "i", "you", "it", "this", "that", "what", "when",
    "where", "who", "how", "do", "does", "give", "tell", "with", "at", "by",
}


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if w not in _STOPWORDS and len(w) > 1}


class Memory:
    def __init__(self, gateway: Gateway, path: str = "state/memory.json"):
        self.gateway = gateway
        self.path = Path(path)
        self.items: list[MemoryItem] = self._load()

    # ---- persistence -------------------------------------------------------
    def _load(self) -> list[MemoryItem]:
        if self.path.exists():
            raw = json.loads(self.path.read_text())
            return [MemoryItem.model_validate(x) for x in raw]
        return []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps([json.loads(i.model_dump_json()) for i in self.items], indent=2)
        )

    # ---- reads (NO LLM) ----------------------------------------------------
    def read(self, query: str, history: list[dict] | None = None,
             kinds: list[MemoryKind] | None = None, top_k: int = 8) -> list[MemoryItem]:
        """Rank items by lowercase-token overlap of query vs (keywords + descriptor)."""
        q = _tokens(query)
        scored: list[tuple[int, MemoryItem]] = []
        for it in self.items:
            if kinds and it.kind not in kinds:
                continue
            hay = set(t.lower() for t in it.keywords) | _tokens(it.descriptor)
            score = len(q & hay)
            if score:
                scored.append((score, it))
        scored.sort(key=lambda s: s[0], reverse=True)
        return [it for _, it in scored[:top_k]]

    def filter(self, kinds: list[MemoryKind] | None = None,
               goal_id: str | None = None, recent: int | None = None) -> list[MemoryItem]:
        out = self.items
        if kinds:
            out = [i for i in out if i.kind in kinds]
        if goal_id:
            out = [i for i in out if i.goal_id == goal_id]
        if recent:
            out = out[-recent:]
        return out

    # ---- writes ------------------------------------------------------------
    def record_outcome(self, tool_call: ToolCall, result_text: str,
                       artifact_id: str | None, run_id: str,
                       goal_id: str | None = None) -> MemoryItem:
        """NO LLM. kind='tool_outcome'; keywords from tool name + arg tokens."""
        kw = list(_tokens(tool_call.name) | _tokens(json.dumps(tool_call.arguments)))
        item = MemoryItem(
            id=uuid.uuid4().hex[:8], kind="tool_outcome", keywords=kw,
            descriptor=f"{tool_call.name}({json.dumps(tool_call.arguments)[:80]}) -> "
                       f"{result_text[:80]}",
            value={"tool": tool_call.name, "arguments": tool_call.arguments,
                   "result": result_text[:500]},
            artifact_id=artifact_id, source=tool_call.name, run_id=run_id, goal_id=goal_id,
        )
        self.items.append(item)
        self._save()
        return item

    def remember(self, raw_text: str, source: str, run_id: str,
                 goal_id: str | None = None) -> MemoryItem | None:
        """ONE LLM classify call. Extract a structured fact/preference if the
        text carries one durable enough to persist.

        ▓▓▓ TODO (your graded role logic) ▓▓▓
        Write CLASSIFY_SYSTEM so the model returns JSON matching _CLASSIFY_SCHEMA:
          {kind: fact|preference|tool_outcome|scratchpad|none,
           keywords: [...], descriptor: "...",
           value: {entity, attribute, value}}
        Goal: "My mom's birthday is 15 May 2026" ->
          kind=fact, value={entity:"mom", attribute:"birthday", value:"2026-05-15"},
          keywords=["mom","birthday","may","2026"].
        Return None when there's nothing durable (kind == "none").
        Tip: pin to Gemini (provider="g") — small routed models misclassify.
        """
        parsed = self.gateway.structured(
            system=CLASSIFY_SYSTEM, user=raw_text,
            schema=_CLASSIFY_SCHEMA, schema_name="MemoryClassify",
            provider="g", auto_route="memory",
        )
        if parsed.get("kind") in (None, "none"):
            return None
        item = MemoryItem(
            id=uuid.uuid4().hex[:8], kind=parsed["kind"],
            keywords=parsed.get("keywords", []),
            descriptor=parsed.get("descriptor", ""),
            value=parsed.get("value", {}),
            source=source, run_id=run_id, goal_id=goal_id,
        )
        self.items.append(item)
        self._save()
        return item


# ── DRAFT prompt — read it, make it yours, then run it through the PoP qualifier.
CLASSIFY_SYSTEM = """\
You are the MEMORY-WRITER for an agent. You are given one piece of raw text (usually the user's
message). Decide whether it contains something worth storing durably; if so, extract it cleanly.
Return ONLY JSON matching the schema.

kind:
  "fact"        - a durable, objective truth about the user or world
                  (e.g. "My mom's birthday is 15 May 2026"; "John's office is in HSR Layout").
  "preference"  - something the user likes/wants/dislikes that could change later
                  (e.g. "I prefer morning meetings"; "use uv, not pip").
  "tool_outcome"- the record of a tool result (usually written by code, rarely here).
  "scratchpad"  - a short run-scoped working note.
  "none"        - nothing worth persisting (a question, small talk, a command with no durable
                  fact). When unsure between a transient request and a durable fact, choose
                  "none" unless a concrete fact/preference is clearly stated.

value: a small object capturing the essence. For facts/preferences prefer
  {"entity","attribute","value"} and normalize dates to YYYY-MM-DD.
  "My mom's birthday is 15 May 2026" -> {"entity":"mom","attribute":"birthday","value":"2026-05-15"}.

keywords: lowercase tokens someone would later search by (names, the attribute, date parts).
  The example above -> ["mom","birthday","may","2026"].

descriptor: one short human-readable line, e.g. "mom's birthday is 2026-05-15".

Return {"kind","keywords","descriptor","value"}. If kind is "none": keywords=[], descriptor="",
value={}.
"""

_CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string",
                 "enum": ["fact", "preference", "tool_outcome", "scratchpad", "none"]},
        "keywords": {"type": "array", "items": {"type": "string"}},
        "descriptor": {"type": "string"},
        "value": {"type": "object"},
    },
    "required": ["kind", "keywords", "descriptor", "value"],
}
