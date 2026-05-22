"""agent6 — the orchestration loop. THE AGENT IS THIS LOOP (not any one LLM).

Wires the four roles. The loop shape was shown in class, so it ships wired;
it depends on the role internals you write in perception.py / decision.py /
memory.py.

Order per iteration:
  memory.read -> perception.observe -> (attach artifact bytes) ->
  decision.next_step -> action.execute -> memory.record_outcome -> repeat,
until Perception marks every goal done.

Run:  uv run python agent6.py "Fetch https://en.wikipedia.org/wiki/Claude_Shannon and ..."
"""
from __future__ import annotations

import asyncio
import sys
import uuid

import action
import perception
from artifacts import ArtifactStore
from gateway_client import Gateway
from mcp_client import load_tools, mcp_session, mcp_tools_for_decision
from memory import Memory

MAX_ITERATIONS = 12


async def run(query: str) -> str:
    gateway = Gateway()
    artifacts = ArtifactStore(state_dir="state", persist=True)
    memory = Memory(gateway, path="state/memory.json")

    run_id = uuid.uuid4().hex[:8]
    history: list[dict] = []
    prior_goals: list = []

    # Durable-memory contract: classify the query so any fact in it survives
    # into future runs (this is what makes Query C's run 2 work).
    try:
        memory.remember(query, source="user_query", run_id=run_id)
    except Exception as exc:  # a durable-write hiccup must not kill the run
        print(f"[memory.remember skipped: {type(exc).__name__}: {exc}]")

    async with mcp_session() as session:
        tools = mcp_tools_for_decision(await load_tools(session))

        for it in range(1, MAX_ITERATIONS + 1):
            hits = memory.read(query, history)
            obs = perception.observe(gateway, query, hits, history, prior_goals, run_id)
            prior_goals = obs.goals

            print(f"\n─── iter {it} ───")
            for g in obs.goals:
                mark = "done" if g.done else "open"
                print(f"  [{mark}] {g.text}"
                      + (f"  attach={g.attach_artifact_id}" if g.attach_artifact_id else ""))

            if obs.all_done:
                break

            goal = obs.next_unfinished()
            attached: list[tuple[str, bytes]] = []
            if goal.attach_artifact_id and artifacts.exists(goal.attach_artifact_id):
                attached.append((goal.attach_artifact_id,
                                 artifacts.get_bytes(goal.attach_artifact_id)))

            from decision import next_step
            out = next_step(gateway, goal, hits, attached, history, tools)

            if out.is_answer:
                print(f"  [answer] {out.answer[:160]}")
                history.append({"iter": it, "kind": "answer",
                                "goal_id": goal.id, "text": out.answer})
                continue

            result_text, art_id = await action.execute(session, out.tool_call, artifacts)
            print(f"  [tool] {out.tool_call.name}({out.tool_call.arguments}) -> "
                  f"{result_text[:120]}")
            memory.record_outcome(out.tool_call, result_text, art_id, run_id, goal.id)
            history.append({
                "iter": it, "kind": "action", "goal_id": goal.id,
                "tool": out.tool_call.name, "arguments": out.tool_call.arguments,
                "result_descriptor": result_text[:300], "artifact_id": art_id,
            })

        # OPTIONAL 3rd LLM call: end-of-run consolidation into durable memory.
        # (left to you — see memory.remember / a dedicated memory-writer prompt)

    answers = [h["text"] for h in history if h.get("kind") == "answer"]
    final = "\n".join(answers) if answers else "(no answer produced within iteration budget)"
    print("\n=== FINAL ===\n" + final)
    return final


def main() -> int:
    if len(sys.argv) < 2:
        print('usage: uv run python agent6.py "<query>"', file=sys.stderr)
        return 1
    asyncio.run(run(" ".join(sys.argv[1:])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
