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
from vector_index import VectorIndex

_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "of", "to", "in", "on", "for",
    "and", "or", "my", "me", "i", "you", "it", "this", "that", "what", "when",
    "where", "who", "how", "do", "does", "give", "tell", "with", "at", "by",
}

# S7: kinds for which an embedding is computed at write time. Scratchpad is
# run-scoped — skipping the vector path keeps it cheap.
_EMBEDDABLE_KINDS = {"fact", "preference", "tool_outcome"}


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())
            if w not in _STOPWORDS and len(w) > 1}


class Memory:
    def __init__(self, gateway: Gateway, path: str = "state/memory.json"):
        self.gateway = gateway
        self.path = Path(path)
        self.items: list[MemoryItem] = self._load()
        # S7 vector index lives alongside memory.json
        self._index = VectorIndex(self.path.parent)
        # Rebuild from existing items on cold start (no FAISS files yet but
        # memory.json had embeddings from a prior run).
        if self._index.size == 0:
            for item in self.items:
                if item.embedding is not None:
                    self._index.add(item.id, item.embedding)
            if self._index.size > 0:
                self._index.persist()

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

    # ---- S7 embed helper ---------------------------------------------------
    def _try_embed(self, text: str, *, task_type: str) -> list[float] | None:
        """Compute an embedding via the gateway. Returns None if the gateway
        is unavailable — the caller decides whether to persist anyway. The
        memory write path always persists; vectorless items stay reachable
        through the keyword fallback."""
        try:
            return self.gateway.embed(text, task_type=task_type)
        except Exception as exc:
            print(f"[memory] embed skipped: {type(exc).__name__}: {str(exc)[:80]}")
            return None

    # ---- reads (NO LLM in keyword path; one embed call on vector path) -----
    def _vector_search(self, query: str,
                       kinds: list[MemoryKind] | None,
                       top_k: int) -> list[MemoryItem]:
        qvec = self._try_embed(query, task_type="retrieval_query")
        if qvec is None or self._index.size == 0:
            return []
        # over-fetch when filtering by kind so the post-filter still has top_k
        hits = self._index.search(qvec, k=top_k * 2 if kinds else top_k)
        if not hits:
            return []
        by_id = {it.id: it for it in self.items}
        out: list[MemoryItem] = []
        for item_id, _score in hits:
            it = by_id.get(item_id)
            if it is None:
                continue
            if kinds and it.kind not in kinds:
                continue
            out.append(it)
            if len(out) >= top_k:
                break
        return out

    def _keyword_search(self, query: str,
                        kinds: list[MemoryKind] | None,
                        top_k: int) -> list[MemoryItem]:
        """S6 keyword overlap — used as fallback when the vector path is empty."""
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

    def read(self, query: str, history: list[dict] | None = None,
             kinds: list[MemoryKind] | None = None, top_k: int = 8) -> list[MemoryItem]:
        """S7: vector-first; fall back to S6 keyword overlap when vector is empty.

        Vector path catches semantic recall ("credit assignment" → docs about
        "blame propagation"). Keyword path catches exact-term queries and runs
        when there's no embedding (empty corpus, gateway down)."""
        vec_hits = self._vector_search(query, kinds, top_k)
        if vec_hits:
            return vec_hits
        return self._keyword_search(query, kinds, top_k)

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
    def _persist(self, item: MemoryItem) -> MemoryItem:
        """Append to JSON store + (if embedded) the FAISS index.

        Reload from disk BEFORE appending — agent and MCP subprocess both
        hold Memory instances pointing at the same files. Without the
        reload, whichever process writes last clobbers the other's items.
        At S7 scale this is fast (small JSON) and race-tolerant."""
        self.items = self._load()
        self.items.append(item)
        self._save()
        if item.embedding is not None and item.kind in _EMBEDDABLE_KINDS:
            # FAISS index also gets re-read so the agent process sees facts
            # the MCP subprocess just wrote.
            from vector_index import VectorIndex
            self._index = VectorIndex(self.path.parent)
            self._index.add(item.id, item.embedding)
            self._index.persist()
        return item

    def record_outcome(self, tool_call: ToolCall, result_text: str,
                       artifact_id: str | None, run_id: str,
                       goal_id: str | None = None) -> MemoryItem:
        """NO LLM classify. kind='tool_outcome'; keywords from tool name +
        arg tokens. S7: also embeds the descriptor for vector recall."""
        kw = list(_tokens(tool_call.name) | _tokens(json.dumps(tool_call.arguments)))
        descriptor = (f"{tool_call.name}({json.dumps(tool_call.arguments)[:80]}) -> "
                      f"{result_text[:80]}")
        embedding = self._try_embed(descriptor, task_type="retrieval_document")
        item = MemoryItem(
            id=uuid.uuid4().hex[:8], kind="tool_outcome", keywords=kw,
            descriptor=descriptor,
            value={"tool": tool_call.name, "arguments": tool_call.arguments,
                   "result": result_text[:500]},
            artifact_id=artifact_id, embedding=embedding,
            source=tool_call.name, run_id=run_id, goal_id=goal_id,
        )
        return self._persist(item)

    def add_fact(self, *, descriptor: str, value: dict | None = None,
                 keywords: list[str] | None = None, source: str,
                 run_id: str, goal_id: str | None = None) -> MemoryItem:
        """S7: direct fact write used by index_document. Skips the LLM
        classifier (kind is known) but still embeds the descriptor."""
        kw = keywords if keywords is not None else list(_tokens(descriptor))[:10]
        embedding = self._try_embed(descriptor, task_type="retrieval_document")
        item = MemoryItem(
            id=uuid.uuid4().hex[:8], kind="fact",
            keywords=[k.lower() for k in kw],
            descriptor=descriptor,
            value=value or {},
            embedding=embedding,
            source=source, run_id=run_id, goal_id=goal_id,
        )
        return self._persist(item)

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
        kind = parsed["kind"]
        descriptor = parsed.get("descriptor", "")
        # S7: embed the descriptor for vector recall (only for embeddable kinds).
        embedding = (self._try_embed(descriptor, task_type="retrieval_document")
                     if kind in _EMBEDDABLE_KINDS else None)
        item = MemoryItem(
            id=uuid.uuid4().hex[:8], kind=kind,
            keywords=parsed.get("keywords", []),
            descriptor=descriptor,
            value=parsed.get("value", {}),
            embedding=embedding,
            source=source, run_id=run_id, goal_id=goal_id,
        )
        return self._persist(item)


# ── MEMORY-WRITER prompt (owned, PoP-qualified). One classify call per user message.
CLASSIFY_SYSTEM = """\
You are the MEMORY-WRITER for a four-role agent. You receive ONE piece of raw text (usually the
user's message) and decide whether it carries something worth storing DURABLY — something a
future, separate run should still know. This is a CLASSIFICATION + EXTRACTION task over text.
Return ONLY JSON matching the schema.

ROLE BOUNDARY (separation of reasoning from tools): you NEVER call tools and you NEVER act in the
world — extraction is your only job. Other roles (Decision/Action) handle any tool use implied by
the text; you only persist the durable fact.

LOOP CONTEXT (multi-turn support): your JSON is written to a persistent store and retrieved by
keyword on later turns AND in entirely separate future runs. Extract so that a future turn,
holding only your descriptor + keywords, can recover the fact — that cross-turn carryover is the
whole point of this role.

REASON STEP BY STEP (internally), then output only the JSON:
  Step 1 — Classify the kind:
     "fact"        - a durable, objective truth about the user or world
                     (e.g. "My mom's birthday is 15 May 2026"; "John's office is in HSR Layout").
     "preference"  - something the user likes/wants/dislikes that could change later
                     (e.g. "I prefer morning meetings"; "use uv, not pip").
     "tool_outcome"- the record of a tool result (normally written by code, rarely here).
     "scratchpad"  - a short run-scoped working note.
     "none"        - nothing worth persisting: a question, small talk, or a pure command that
                     states no durable fact (e.g. "fetch this page and summarize it").
  Step 2 — If fact/preference, extract the essence into value. Prefer
     {"entity","attribute","value"} and NORMALIZE dates to YYYY-MM-DD.
     "My mom's birthday is 15 May 2026" → {"entity":"mom","attribute":"birthday","value":"2026-05-15"}.
  Step 3 — Build keywords: lowercase tokens someone would later search by (names, the attribute,
     date parts). Example above → ["mom","birthday","may","2026"].
  Step 4 — Write descriptor: one short human-readable line, e.g. "mom's birthday is 2026-05-15".

WORKED EXAMPLE
  Input:  "My mom's birthday is 15 May 2026, remind me two weeks before and on the day."
  Output: {"kind":"fact","keywords":["mom","birthday","may","2026"],
           "descriptor":"mom's birthday is 2026-05-15",
           "value":{"entity":"mom","attribute":"birthday","value":"2026-05-15"}}
  (The "remind me" part is an action for other roles — you only persist the durable fact.)

SELF-CHECK before output: ✓ a concrete, reusable fact/preference → NOT "none"; ✓ a question or a
pure do-this command with no embedded fact → "none"; ✓ dates normalized to YYYY-MM-DD;
✓ keywords are lowercase and searchable.

FALLBACK: when genuinely torn between a transient request and a durable fact, choose "none" unless
a concrete fact/preference is clearly stated — a false store is worse than a missed one.

OUTPUT: {"kind","keywords","descriptor","value"}. If kind is "none": keywords=[], descriptor="",
value={}. No prose outside the JSON.
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
