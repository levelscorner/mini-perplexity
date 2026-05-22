"""Typed contracts between the four cognitive roles.

Every arrow between roles carries one of these. No free-form dicts cross a
role boundary; no regex on LLM output. Pydantic validates at construction,
emits the JSON Schema the LLM sees (structured output), and round-trips for
persistence.

These shapes were shown publicly in class — they are the skeleton, not the
graded work. The graded work is the Perception/Decision *prompts* and the
role *logic*, which live (as TODOs) in perception.py / decision.py / memory.py.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

MemoryKind = Literal["fact", "preference", "tool_outcome", "scratchpad"]


class MemoryItem(BaseModel):
    """One durable (or run-scoped) thing the agent knows.

    Only `kind` + `value` (+ keywords/descriptor for retrieval) are truly
    persisted; the rest is provenance. `artifact_id` is a *handle* into the
    artifact store — the bytes never live inside the memory item.
    """
    id: str
    kind: MemoryKind
    keywords: list[str] = Field(default_factory=list)
    descriptor: str = ""            # one short human-readable line
    value: dict = Field(default_factory=dict)  # structured payload
    artifact_id: str | None = None  # handle into the artifact store, if any
    source: str = ""
    run_id: str = ""
    goal_id: str | None = None
    confidence: float = 1.0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Artifact(BaseModel):
    """Metadata for a blob in the artifact store. Bytes live on disk/RAM,
    addressed by `id` (a short auto-increment handle like 'art:1', NOT a long
    sha256 string — weak models hallucinate long strings)."""
    id: str
    content_type: str = "text/plain"
    size_bytes: int = 0
    source: str = ""
    descriptor: str = ""


class Goal(BaseModel):
    """One bounded sub-task. Identity is POSITIONAL in the Observation list —
    there is deliberately no string id the model can invent/drift."""
    id: str
    text: str                       # short imperative description
    done: bool = False
    attach_artifact_id: str | None = None


class Observation(BaseModel):
    """Perception's output: the current goal list with done flags."""
    goals: list[Goal] = Field(default_factory=list)

    @property
    def all_done(self) -> bool:
        return bool(self.goals) and all(g.done for g in self.goals)

    def next_unfinished(self) -> Goal | None:
        for g in self.goals:
            if not g.done:
                return g
        return None


class ToolCall(BaseModel):
    name: str
    arguments: dict = Field(default_factory=dict)


class DecisionOutput(BaseModel):
    """Decision's output: EXACTLY one of these two is populated."""
    answer: str | None = None       # plain-text final answer for the goal
    tool_call: ToolCall | None = None

    @property
    def is_answer(self) -> bool:
        return self.answer is not None and self.tool_call is None
