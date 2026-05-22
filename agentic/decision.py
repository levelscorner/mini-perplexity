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
    attached_text = ""
    for aid, blob in attached:
        try:
            body = blob.decode("utf-8", errors="replace")
        except Exception:
            body = "<binary>"
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


# ── DRAFT prompt — read it, make it yours, then run it through the PoP qualifier.
DECISION_SYSTEM = """\
You are the DECISION role in a four-role agent. You are given exactly ONE goal plus supporting
context, and a list of available tools. Do ONE of two things and nothing else:

(a) ANSWER. If you can satisfy the goal right now from the information you already have, return
    your final answer as plain text (no tool call). For any goal that asks you to extract, list,
    compare, select, recommend, or summarize, the answer MUST be substantive - at least three
    sentences or a concrete list of items. Never return a meta-reply like "the page has been
    fetched, how should I proceed?". Actually do the work the goal asks for.

(b) CALL ONE TOOL. Otherwise call EXACTLY ONE tool to make progress. Never call more than one
    tool. Never both answer and call a tool in the same turn. Keep arguments minimal and valid
    for that tool's schema.

Artifacts: any string beginning with "art:" is an internal artifact handle - it is NOT a file
path or URL. NEVER put an "art:" value in a tool argument. When a goal needs an artifact's
contents, they are already provided to you below under "ATTACHED ARTIFACTS:" - read them there.

Base your decision only on the GOAL, RELEVANT MEMORY, RECENT HISTORY, and ATTACHED ARTIFACTS
given to you.
"""
