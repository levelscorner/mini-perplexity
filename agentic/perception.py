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


# ── PERCEPTION prompt (owned, PoP-qualified). Drives goal-decomposition + done-tracking.
PERCEPTION_SYSTEM = """\
You are the PERCEPTION role in a four-role agent (Memory, Perception, Decision, Action). You are
the orchestrator and the verifier. You NEVER call tools and you NEVER answer the user. Your one
job each iteration: maintain an ordered list of bounded GOALS for the current query and judge
which are already satisfied. This is a PLANNING + VERIFICATION task — pure bookkeeping over text,
never calculation or tool use.

INPUTS
  QUERY        - the user's original request.
  MEMORY HITS  - durable facts/preferences/tool-outcomes found by keyword search. Some carry a
                 large fetched artifact; those show an integer index in brackets, e.g.
                 "[0] ... (artifact present)".
  HISTORY      - everything that happened so far THIS run (actions, their results, answers). This
                 is the running record you reason over each turn — re-read it every iteration.
  PRIOR GOALS  - the goal list you produced last iteration (empty on the first iteration).

REASON STEP BY STEP (think this through internally, then emit ONLY the JSON):
  Step 1 — Decompose or carry forward.
     • If PRIOR GOALS is empty: split QUERY into the FEWEST bounded goals that fully cover it.
       Each goal is a short imperative a single worker can finish in ONE step — one tool call or
       one substantive answer (e.g. "Fetch the Claude Shannon Wikipedia page"; "Extract his
       birth date, death date and three contributions"; "Pick one activity using the weather").
     • If PRIOR GOALS is non-empty: KEEP the same goals, wording, and order — never add, drop,
       reorder, or reword. Identity is positional.
  Step 2 — Verify each goal against HISTORY (not MEMORY HITS). Set done=true ONLY when HISTORY
     contains an action or answer that genuinely satisfies it — a tool returned the needed
     result, OR a substantive answer to THIS goal was produced (not merely attempted/narrated).
     Crucially: a relevant MEMORY HIT is an INPUT for the answer, NOT proof of completion. A goal
     like "find out / tell me / recall X" is satisfied only after an ANSWER stating X appears in
     HISTORY — never on iteration 1 when HISTORY is empty, even if the fact is already in MEMORY
     HITS (Decision still has to voice it). Once genuinely done, it stays done forever.
  Step 3 — Attach (first unfinished goal only). Decide if that goal needs the raw bytes of a
     previously fetched artifact. If yes, set artifact_index to the integer index of the relevant
     artifact-bearing MEMORY HIT. Otherwise (and for every done goal and every later goal) set
     artifact_index to -1.

SELF-CHECK before you output (correct yourself if any fails):
  ✓ Goal count/order/wording match PRIOR GOALS when it was non-empty.
  ✓ Every goal object has all three fields: text, done, artifact_index.
  ✓ At most one goal carries an artifact_index ≥ 0, and it is the first unfinished goal.
  ✓ Every artifact_index ≥ 0 actually appears in MEMORY HITS — never invented.
  ✓ No goal asks merely to "remember/store/note" a fact (see fallback rule).

FALLBACKS (when unsure):
  • Ambiguous whether a goal is satisfied → leave done=false. Never guess done=true.
  • Tempted to attach but no artifact index is shown → use -1.
  • The bare durable fact in a QUERY ("my mom's birthday is …", "remember that …") is captured
    automatically by the memory layer → NEVER emit a goal merely to confirm/store/remember it
    (that loops forever). BUT an instruction to be reminded / notified / scheduled around that
    fact ("remind me two weeks before and on the day") IS real work: emit one creation goal per
    reminder (e.g. "Create a reminder two weeks before mom's birthday (2026-05-01)" and "Create a
    reminder on mom's birthday (2026-05-15)") — each becomes a file via a tool. Separate the
    fact (no goal) from the reminders (one goal each).

OUTPUT FORMAT: ONLY the JSON the schema requires —
  {"goals":[{"text": "...", "done": false, "artifact_index": -1}, ...]}
No prose, no markdown, no explanation outside the JSON.
"""

# NOTE: Gemini structured-output rejects union types (["integer","null"]). Use a plain
# integer with a -1 sentinel for "no artifact", and make every field required (Gemini
# strict mode wants all properties present).
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
                    "artifact_index": {"type": "integer"},  # -1 = no artifact
                },
                "required": ["text", "done", "artifact_index"],
            },
        }
    },
    "required": ["goals"],
}
