"""Perception — the orchestrator. The HARD role; the prompt is graded.

Runs every iteration. Inputs: the query, memory hits, the run history, and the
prior goal list. Output: a fresh Observation (goal list with done flags +
optional artifact attachment on the next unfinished goal).

It also subsumes the old (S5) Verifier — there is no separate verify call. By
re-reading history each iteration it decides whether a goal is now satisfied.

Pinned to Gemini (provider="g"): small routed models drop goals / hallucinate.

The four obligations the prompt must enforce (shown in class):
  1. If prior goals is empty -> decompose the query into 1+ bounded goals,
     each a short imperative statement.
  2. For each prior goal, read history; mark done=true the moment history
     contains an action that satisfies it. Once done, stays done.
  3. For the FIRST unfinished goal, decide if it needs raw bytes from a
     previously-fetched artifact. If yes, set its attachment to one of the
     artifact handles present in the memory hits.
  4. Preserve goal ORDER — do not reorder, insert in the middle, or drop a goal.

Anti-hallucination shapes (use these — they were the fix in class):
  - Goals carry NO string id in the model output; identity is positional. The
    outer loop maps positions -> stable ids.
  - Artifacts are referenced by an INTEGER index into the listed hits
    (artifact_index), never by a raw 'art:...' string the model could invent.
"""
from __future__ import annotations

import json

from gateway_client import Gateway
from schemas import Goal, MemoryItem, Observation


def observe(gateway: Gateway, query: str, hits: list[MemoryItem],
            history: list[dict], prior_goals: list[Goal], run_id: str) -> Observation:
    # Index the artifact-bearing hits so the model can point at an integer.
    artifact_hits = [h for h in hits if h.artifact_id]
    hit_lines = []
    for i, h in enumerate(artifact_hits):
        hit_lines.append(f"[{i}] {h.descriptor} (artifact present)")
    for h in (h for h in hits if not h.artifact_id):
        hit_lines.append(f"[-] {h.kind}: {h.descriptor}")

    # Assemble the context the prompt reasons over.
    context = (
        f"QUERY:\n{query}\n\n"
        f"MEMORY HITS:\n" + ("\n".join(hit_lines) or "(none)") + "\n\n"
        f"HISTORY (this run):\n{json.dumps(history, indent=2) if history else '(none)'}\n\n"
        f"PRIOR GOALS:\n" +
        (json.dumps([{"text": g.text, "done": g.done} for g in prior_goals], indent=2)
         if prior_goals else "(none — decompose the query)")
    )

    parsed = gateway.structured(
        system=PERCEPTION_SYSTEM, user=context,
        schema=_OBSERVE_SCHEMA, schema_name="Observation",
        provider="g", auto_route="perception",
    )
    goals: list[Goal] = []
    for pos, g in enumerate(parsed.get("goals", [])):
        # stable id by position; reuse the prior id if this slot existed
        gid = prior_goals[pos].id if pos < len(prior_goals) else f"g{pos + 1}"
        attach = None
        idx = g.get("artifact_index")
        if isinstance(idx, int) and 0 <= idx < len(artifact_hits):
            attach = artifact_hits[idx].artifact_id
        goals.append(Goal(id=gid, text=g["text"], done=bool(g.get("done")),
                          attach_artifact_id=attach))
    return Observation(goals=goals)


# ── DRAFT prompt — read it, make it yours, then run it through the PoP qualifier.
PERCEPTION_SYSTEM = """\
You are the PERCEPTION role in a four-role agent (Memory, Perception, Decision, Action).
You are the orchestrator. You do NOT call tools and you do NOT answer the user. Your only job
is to maintain a list of bounded GOALS for the current query and judge which are done.

You are given:
  QUERY        - the user's original request.
  MEMORY HITS  - durable facts/preferences/tool-outcomes found by keyword search. Some carry an
                 artifact (large fetched content); those are listed with an integer index in
                 brackets, e.g. "[0] ... (artifact present)".
  HISTORY      - what has happened so far THIS run (actions taken, their results, answers given).
  PRIOR GOALS  - the goal list you produced last iteration (empty on the first iteration).

Follow these obligations exactly:

1. DECOMPOSE. If PRIOR GOALS is empty, split the QUERY into one or more bounded goals. Each goal
   is a short imperative sentence a single worker could finish in one step given the right
   information (e.g. "Fetch the Claude Shannon Wikipedia page"; "Extract his birth date, death
   date and three contributions"). Use the fewest goals that fully cover the request. If PRIOR
   GOALS is non-empty, KEEP the same goals, wording, and order - never add, drop, reorder, or
   reword them.

2. MARK DONE. For each goal, read HISTORY and set done=true the moment HISTORY contains an action
   or answer that genuinely satisfies it (a tool returned the needed result, or a substantive
   answer was produced - not merely attempted). A goal that is done stays done forever.

3. ATTACH (only for the FIRST goal whose done is false). Decide whether that goal needs the raw
   bytes of a previously fetched artifact. If yes, set its "artifact_index" to the integer index
   of the relevant artifact-bearing MEMORY HIT (e.g. 0); otherwise null. Never invent an index
   that is not shown. Only the first unfinished goal may carry an artifact_index; all others null.

4. ORDER. Emit goals in the same order every iteration - identity is positional.

Output ONLY the JSON required by the schema: {"goals":[{"text","done","artifact_index"}]}.
No prose.
"""

_OBSERVE_SCHEMA = {
    "type": "object",
    "properties": {
        "goals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "done": {"type": "boolean"},
                    "artifact_index": {"type": ["integer", "null"]},
                },
                "required": ["text", "done"],
            },
        }
    },
    "required": ["goals"],
}
