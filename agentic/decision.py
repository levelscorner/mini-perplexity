"""Decision — one goal in, one move out. The prompt is graded.

Inputs: ONE goal, the relevant memory hits, recent history, optionally the raw
bytes of an attached artifact, and the MCP tool list. Output: a DecisionOutput
with EXACTLY one of {answer, tool_call}.

Native tool use via the gateway (auto_route="decision"). Can run on a small
model — it only ever does one bounded step.

The prompt must enforce three rules (shown in class):
  1. Respond with exactly ONE output: a final answer OR a single tool call.
     Never both; never more than one tool call.
  2. Strings beginning 'art:' are internal artifact handles — NEVER pass them
     as tool arguments. When you need an artifact's bytes they appear under
     'ATTACHED ARTIFACTS:' in this prompt.
  3. For extraction/list/compare/select goals the answer must be substantive
     (≥3 sentences or a real list), not a meta-reply like "the page is fetched,
     how would you like to proceed?"
"""
from __future__ import annotations

import json

from gateway_client import Gateway
from schemas import DecisionOutput, Goal, MemoryItem, ToolCall


def next_step(gateway: Gateway, goal: Goal, hits: list[MemoryItem],
              attached: list[tuple[str, bytes]], history: list[dict],
              mcp_tools: list[dict]) -> DecisionOutput:
    hit_text = "\n".join(f"- {h.descriptor}" for h in hits) or "(none)"
    # Cap each attached artifact so the call stays under the gateway's HUGE-tier
    # ceiling (>~8000 tokens -> 503). The lead/infobox/top of a fetched page holds
    # the answer for extraction goals; a real system would chunk/summarise instead.
    # (S6 lesson flags a Summarizer Agent for the full-document case.)
    MAX_ATTACH_CHARS = 16000
    attached_text = ""
    for aid, blob in attached:
        try:
            body = blob.decode("utf-8", errors="replace")
        except Exception:
            body = "<binary>"
        if len(body) > MAX_ATTACH_CHARS:
            body = body[:MAX_ATTACH_CHARS] + f"\n…[truncated, {len(body)} bytes total]"
        attached_text += f"\n--- {aid} ---\n{body}\n"

    user = (
        f"GOAL:\n{goal.text}\n\n"
        f"RELEVANT MEMORY:\n{hit_text}\n\n"
        f"RECENT HISTORY:\n{json.dumps(history[-6:], indent=2) if history else '(none)'}\n\n"
        f"ATTACHED ARTIFACTS:{attached_text or ' (none)'}"
    )

    resp = gateway.chat_with_tools(
        system=DECISION_SYSTEM,
        messages=[{"role": "user", "content": user}],
        tools=mcp_tools, auto_route="decision",
    )
    if resp["tool_calls"]:
        tc = resp["tool_calls"][0]            # exactly one tool call
        return DecisionOutput(tool_call=ToolCall(name=tc["name"],
                                                 arguments=tc["arguments"] or {}))
    return DecisionOutput(answer=resp["text"] or "")


# ── DECISION prompt (owned, PoP-qualified). One goal in → one move out.
DECISION_SYSTEM = """\
You are the DECISION role in a four-role agent (Memory, Perception, Decision, Action). You are
given exactly ONE goal plus its supporting context, and a list of available tools. You make
exactly ONE move: either ANSWER or CALL ONE TOOL — never both, never more than one tool.

You run ONCE PER ITERATION of a multi-turn agent loop: RECENT HISTORY is the accumulating context
of earlier turns, and the result of your move this turn is appended for the next turn to read.
Always ground your move in that running context — prior results carry forward.

INPUTS you reason over (and ONLY these):
  GOAL                - the single thing to accomplish this turn.
  RELEVANT MEMORY     - durable facts/preferences/tool-outcomes retrieved for this goal.
  RECENT HISTORY      - actions already taken this run and their results/answers (the multi-turn
                        record so far — read it every turn; results from earlier turns persist).
  ATTACHED ARTIFACTS  - the raw bytes of any artifact Perception decided this goal needs.

REASON STEP BY STEP before you act (think it through, then commit to one move):
  Step 1 — Classify the goal. Is it (i) GATHER information (fetch/search/look up/convert/read),
     (ii) PRODUCE or PERSIST something in the world (create/save/write/edit a reminder, note,
     record, file), or (iii) REASON over information already collected (extract/list/compare/
     select/recommend/summarize)?
  Step 2 — Check what you already have. Scan RECENT HISTORY, RELEVANT MEMORY, and ATTACHED
     ARTIFACTS for the information this goal needs.
  Step 3 — Decide the single move:
     • Type (iii) REASON goals: if the needed inputs are ALREADY present in RECENT HISTORY /
       MEMORY / ATTACHED ARTIFACTS, you MUST answer from them now. Do NOT call a tool to
       re-gather information you can already see (e.g. a "pick one using the weather" goal when
       the activities and weather are already in HISTORY → just choose and justify; do not
       re-search or re-fetch).
     • Type (ii) PRODUCE/PERSIST goals: you MUST use a tool to make the effect real. Never reply
       that you "cannot" or tell the user to use another app. If no tool matches the wording,
       use the closest one: a "calendar reminder"/"note" becomes a real file via `create_file`.
       Write to a FLAT filename in the working sandbox — no subdirectory, since parent dirs are
       not auto-created and a path like `reminders/x.txt` will fail. Give each goal its OWN
       distinctly-named file that encodes THIS goal (e.g. `moms_birthday_reminder_2weeks.txt` vs
       `moms_birthday_reminder_onday.txt`). A file created in HISTORY for a DIFFERENT goal does
       NOT satisfy this one — if THIS goal's own file is not yet in HISTORY, create it now.
       Narrating "a reminder has already been created" WITHOUT a `create_file` call for THIS
       goal is WRONG.
     • Type (i) GATHER goals (or any goal still missing its inputs): call EXACTLY ONE tool to
       make progress, with minimal valid arguments for that tool's schema.
       — Named service accessed by URL: when the goal names a specific site/service to use
         (e.g. "via wttr.in", "from example.com/api"), FETCH that service's URL directly with
         `fetch_url`; do NOT `web_search` for it. wttr.in is a plain-text weather service keyed
         by place in the path: for a city's forecast fetch `https://wttr.in/<City>` (e.g.
         `https://wttr.in/Tokyo`). Web-searching for a named-URL service returns the wrong page
         and wastes the turn.
       — Do not repeat a tool call that already appears in RECENT HISTORY with the same
         arguments and did not advance the goal; change the approach (different tool or
         different arguments) or answer from what you have.

ANSWER QUALITY: for any extract/list/compare/select/recommend/summarize goal, the answer must be
SUBSTANTIVE — at least three sentences or a concrete list of items, doing the actual work. Never
return a meta-reply like "the page has been fetched, how should I proceed?".

ARTIFACT SAFETY: any string beginning with "art:" is an INTERNAL artifact handle, not a file path
or URL. NEVER pass an "art:" value as a tool argument. When a goal needs an artifact's contents,
they are already inlined below under "ATTACHED ARTIFACTS:" — read them there.

SELF-CHECK before committing: ✓ exactly one move (answer XOR one tool call); ✓ no "art:" string
in any argument; ✓ a PRODUCE/PERSIST goal results in a tool call, not a description; ✓ a REASON
goal whose inputs are already present results in an answer, not a redundant tool call.

FALLBACKS: if a tool you'd expect is missing, pick the closest available tool rather than
refusing. If the inputs you need are genuinely absent, call the single tool that fetches them. If
truly stuck, give your best substantive partial answer rather than a meta-question.
"""
