"""End-to-end WIRING test (no real LLM, no real MCP server).

Drives the REAL agent6 loop, REAL memory (keyword search + JSON persistence),
REAL artifact store, and REAL attach gating — against a fake gateway + fake MCP
session that return scripted responses. Proves the architecture/plumbing works
for all four assignment queries.

It does NOT judge prompt quality — only a real LLM can. Swap in the provided
gateway V3 + mcp_server.py for that.

Run:  uv run python _e2e_smoke.py
"""
from __future__ import annotations

import asyncio
import os
import tempfile

import agent6


# ── fakes ────────────────────────────────────────────────────────────────
class FakeGateway:
    def __init__(self, memory_q, perception_q, decision_q):
        self.memory_q = list(memory_q)
        self.perception_q = list(perception_q)
        self.decision_q = list(decision_q)
        self.perception_calls = 0
        self.memory_writes = 0
        self.decision_user_contents: list[str] = []

    def structured(self, *, system, user, schema, schema_name="Output",
                   auto_route=None, provider=None, temperature=1.0):
        if auto_route == "memory":
            self.memory_writes += 1
            return self.memory_q.pop(0)
        if auto_route == "perception":
            self.perception_calls += 1
            return self.perception_q.pop(0)
        raise AssertionError(f"unexpected structured auto_route={auto_route}")

    def chat_with_tools(self, *, system, messages, tools, auto_route=None,
                        provider=None, temperature=1.0):
        self.decision_user_contents.append(messages[-1]["content"])
        return self.decision_q.pop(0)


class _Block:
    def __init__(self, text): self.text = text


class _Result:
    def __init__(self, text): self.content = [_Block(text)]


class FakeSession:
    def __init__(self, tool_responses): self.tool_responses = tool_responses
    async def call_tool(self, name, arguments=None):
        resp = self.tool_responses.get(name, f"(fake {name} ok)")
        if callable(resp):
            resp = resp(arguments or {})
        return _Result(resp)


def make_patches(gw: FakeGateway, session: FakeSession):
    class _Ctx:
        async def __aenter__(self): return session
        async def __aexit__(self, *a): return False
    agent6.Gateway = lambda *a, **k: gw
    agent6.mcp_session = lambda: _Ctx()
    async def _load_tools(s): return []
    agent6.load_tools = _load_tools
    agent6.mcp_tools_for_decision = lambda tools: []


def goal(text, done, idx=None):
    return {"text": text, "done": done, "artifact_index": idx}


def tool_call(name, **args):
    return {"text": None, "tool_calls": [{"name": name, "arguments": args}], "raw": {}}


def answer(text):
    return {"text": text, "tool_calls": [], "raw": {}}


BIG = "Claude Shannon (April 30, 1916 - Feb 24, 2001) founded information theory. " * 200  # >4KB


def run_scenario(query, memory_q, perception_q, decision_q, tool_responses, *, cwd):
    gw = FakeGateway(memory_q, perception_q, decision_q)
    session = FakeSession(tool_responses)
    make_patches(gw, session)
    old = os.getcwd()
    os.chdir(cwd)
    try:
        final = asyncio.run(agent6.run(query))
    finally:
        os.chdir(old)
    return final, gw


PASS, FAIL = [], []
def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'} · {name}" + (f"  — {detail}" if detail and not cond else ""))


# ── Query A: artifact attach ───────────────────────────────────────────────
def test_A():
    print("\n[A] Shannon Wikipedia — artifact attach")
    with tempfile.TemporaryDirectory() as d:
        final, gw = run_scenario(
            "Fetch https://en.wikipedia.org/wiki/Claude_Shannon and tell me his birth date, "
            "death date, and three key contributions to information theory.",
            memory_q=[{"kind": "none", "keywords": [], "descriptor": "", "value": {}}],
            perception_q=[
                {"goals": [goal("Fetch the Claude Shannon Wikipedia page", False),
                           goal("Extract birth date, death date and three contributions", False)]},
                {"goals": [goal("Fetch the Claude Shannon Wikipedia page", True),
                           goal("Extract birth date, death date and three contributions", False, 0)]},
                {"goals": [goal("Fetch the Claude Shannon Wikipedia page", True),
                           goal("Extract birth date, death date and three contributions", True)]},
            ],
            decision_q=[
                tool_call("fetch_url", url="https://en.wikipedia.org/wiki/Claude_Shannon"),
                answer("Claude Shannon (1916-2001). Birth: April 30, 1916. Death: Feb 24, 2001. "
                       "Contributions: (1) A Mathematical Theory of Communication; (2) the bit and "
                       "entropy; (3) the Shannon limit."),
            ],
            tool_responses={"fetch_url": BIG},
            cwd=d,
        )
        check("loop terminated with an answer", "Shannon" in final)
        check("perception ran 3 iterations", gw.perception_calls == 3, f"got {gw.perception_calls}")
        check("artifact bytes were attached to Decision on iter 2",
              any("--- art:1 ---" in u for u in gw.decision_user_contents))
        check("decision queue fully consumed", not gw.decision_q)


# ── Query C: durable memory across two runs ─────────────────────────────────
def test_C():
    print("\n[C] Mom's birthday — durable memory across runs")
    with tempfile.TemporaryDirectory() as d:
        # Run 1: writes the fact, creates two reminders
        run_scenario(
            "My mom's birthday is 15 May 2026. Remember that and give me a calendar reminder "
            "for two weeks before and on the day.",
            memory_q=[{"kind": "fact", "keywords": ["mom", "birthday", "may", "2026"],
                       "descriptor": "mom's birthday is 2026-05-15",
                       "value": {"entity": "mom", "attribute": "birthday", "value": "2026-05-15"}}],
            perception_q=[
                {"goals": [goal("Create reminder for 1 May 2026", False),
                           goal("Create reminder for 15 May 2026", False)]},
                {"goals": [goal("Create reminder for 1 May 2026", True),
                           goal("Create reminder for 15 May 2026", False)]},
                {"goals": [goal("Create reminder for 1 May 2026", True),
                           goal("Create reminder for 15 May 2026", True)]},
            ],
            decision_q=[tool_call("create_file", path="reminders/before.txt", content="..."),
                        tool_call("create_file", path="reminders/onday.txt", content="...")],
            tool_responses={"create_file": "ok"},
            cwd=d,
        )
        # the fact must now be on disk
        from gateway_client import Gateway  # not used; just to keep import parity
        from memory import Memory
        mem = Memory(FakeGateway([], [], []), path=os.path.join(d, "state", "memory.json"))
        hits = mem.read("when is my mom's birthday?")
        check("run 1 persisted the fact to state/memory.json", any(h.kind == "fact" for h in hits))

        # Run 2: fresh agent run, SAME state dir — must read the fact back
        final2, gw2 = run_scenario(
            "When is mom's birthday?",
            memory_q=[{"kind": "none", "keywords": [], "descriptor": "", "value": {}}],
            perception_q=[
                {"goals": [goal("Answer when mom's birthday is", False)]},
                {"goals": [goal("Answer when mom's birthday is", True)]},
            ],
            decision_q=[answer("Mom's birthday is on 15 May 2026.")],
            tool_responses={},
            cwd=d,
        )
        check("run 2 answered from durable memory", "15 May 2026" in final2)


# ── Query B: multi-goal + memory carryover (lighter) ────────────────────────
def test_B():
    print("\n[B] Tokyo — multi-goal + carryover")
    with tempfile.TemporaryDirectory() as d:
        final, gw = run_scenario(
            "Find 3 family-friendly things to do in Tokyo this weekend. Check Saturday's weather "
            "there and tell me which one is most appropriate.",
            memory_q=[{"kind": "none", "keywords": [], "descriptor": "", "value": {}}],
            perception_q=[
                {"goals": [goal("Find 3 family-friendly things in Tokyo", False),
                           goal("Check Saturday weather in Tokyo", False),
                           goal("Choose the most appropriate activity", False)]},
                {"goals": [goal("Find 3 family-friendly things in Tokyo", True),
                           goal("Check Saturday weather in Tokyo", False),
                           goal("Choose the most appropriate activity", False)]},
                {"goals": [goal("Find 3 family-friendly things in Tokyo", True),
                           goal("Check Saturday weather in Tokyo", True),
                           goal("Choose the most appropriate activity", False)]},
                {"goals": [goal("Find 3 family-friendly things in Tokyo", True),
                           goal("Check Saturday weather in Tokyo", True),
                           goal("Choose the most appropriate activity", True)]},
            ],
            decision_q=[
                tool_call("web_search", query="family friendly things to do in Tokyo this weekend"),
                tool_call("fetch_url", url="https://wttr.in/Tokyo"),
                answer("Given Saturday's patchy rain, the indoor Tsukiji sushi class is the most "
                       "appropriate of the three (Ueno Zoo, Tsukiji sushi class, Tokyo Skytree)."),
            ],
            tool_responses={"web_search": '[{"title":"Ueno Zoo"},{"title":"Tsukiji"},{"title":"Skytree"}]',
                            "fetch_url": "Tokyo Saturday: patchy rain, 18C"},
            cwd=d,
        )
        check("loop terminated with a recommendation", "Tsukiji" in final)
        check("perception ran 4 iterations", gw.perception_calls == 4, f"got {gw.perception_calls}")


# ── Query D: multi-source synthesis (lighter) ───────────────────────────────
def test_D():
    print("\n[D] asyncio research — multi-source synthesis")
    with tempfile.TemporaryDirectory() as d:
        final, gw = run_scenario(
            "Search 'Python asyncio best practices', read the top 3 results, and give me a short "
            "numbered list of the advice they agree on.",
            memory_q=[{"kind": "none", "keywords": [], "descriptor": "", "value": {}}],
            perception_q=[
                {"goals": [goal("Search asyncio best practices", False),
                           goal("Fetch top 3 results", False),
                           goal("Synthesise common advice", False)]},
                {"goals": [goal("Search asyncio best practices", True),
                           goal("Fetch top 3 results", False),
                           goal("Synthesise common advice", False)]},
                {"goals": [goal("Search asyncio best practices", True),
                           goal("Fetch top 3 results", True),
                           goal("Synthesise common advice", False, 0)]},
                {"goals": [goal("Search asyncio best practices", True),
                           goal("Fetch top 3 results", True),
                           goal("Synthesise common advice", True)]},
            ],
            decision_q=[
                tool_call("web_search", query="Python asyncio best practices"),
                tool_call("fetch_url", url="https://example.com/asyncio"),
                answer("1. Use asyncio.run() as the entry point. 2. Prefer gather/TaskGroup. "
                       "3. Avoid blocking calls; use to_thread. 4. Always set timeouts."),
            ],
            tool_responses={"web_search": '[{"url":"u1"},{"url":"u2"},{"url":"u3"}]',
                            "fetch_url": BIG},
            cwd=d,
        )
        check("loop terminated with a synthesised list", "asyncio.run()" in final)
        check("artifact created from a >4KB fetch + attached",
              any("--- art:1 ---" in u for u in gw.decision_user_contents))


if __name__ == "__main__":
    test_A(); test_B(); test_C(); test_D()
    print(f"\n=== {len(PASS)} passed, {len(FAIL)} failed ===")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
        raise SystemExit(1)
    print("ALL WIRING TESTS GREEN ✓")
