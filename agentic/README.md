# Session 6 — Agentic Architecture

An agent built from **four cognitive roles** — Memory · Perception · Decision · Action —
wired by typed Pydantic contracts, running on the provided **MCP server** (tools) and
**LLM gateway V3** (every LLM call). It must pass the four target queries below.

> **The agent is the loop** (`agent6.py`), not any single LLM. Roles never talk to each
> other — everything goes through the loop.

## Architecture

```
                    ┌──────────────── agent6.py (the loop) ────────────────┐
   user query ─────▶│ memory.read → perception.observe → [attach artifact] │
                    │   → decision.next_step → action.execute → memory.rec  │──▶ final answer
                    └──────────────────────────────────────────────────────┘
   Memory  (state/memory.json)   persistent facts/prefs/outcomes/scratchpad — survives runs
   Perception (Gemini)           decompose into goals + mark done (also the verifier)
   Decision (router)             one goal → one answer OR one tool call
   Action  (no LLM)              run the MCP tool; >4 KB → artifact store
```

| File | Status |
|------|--------|
| `schemas.py` | ✅ complete — the six typed contracts |
| `artifacts.py` | ✅ complete — content-addressable store, 4 KB threshold, `art:N` handles |
| `action.py` | ✅ complete — pure dispatch + `art:` guard + artifact offload |
| `mcp_client.py` | ✅ complete — stdio session to the provided server (align `MCP_SERVER_CMD`) |
| `gateway_client.py` | ⚙️ complete adapter — **align field names** to the provided gateway V3 README |
| `memory.py` | ✅ wired — reads/`record_outcome` complete; `remember()` + **`CLASSIFY_SYSTEM` draft prompt** in place |
| `perception.py` | ✅ wired — **`PERCEPTION_SYSTEM` draft prompt** in place (review + PoP) |
| `decision.py` | ✅ wired — **`DECISION_SYSTEM` draft prompt** in place (review + PoP) |
| `agent6.py` | ✅ complete — the loop |

All three role prompts are **drafted and wired** (no stubs; everything compiles + smoke-tests).
They are first drafts — read them, make them yours, tune against the four queries.

### Tested — wiring (fakes) AND a real run

**Fakes:** `_e2e_smoke.py` drives the real loop/memory/artifacts/attach against scripted
gateway+MCP — all four queries, **10/10 green** (`uv run python _e2e_smoke.py`).

**Real run (2026-05-22, V3 gateway :8101 + provided 9-tool `mcp_server.py`):**
- gateway_client (V3 wire-compatible), mcp_client (stdio), the loop, structured output, and
  `memory.remember` classify all work against real infra.
- **Strong model via the gateway:** added native `openai` + `anthropic` providers to the
  gateway's `providers.py` (+ `LIMITS`/`SHORTCUTS` in `router.py`); set `AGENT_PROVIDER=openai`
  to route every agent call to **GPT-4o**. (Claude via the OpenAI-compat endpoint didn't work —
  it needs the native Messages API; GPT-4o is the working path.)
- **Query C passes fully end-to-end on GPT-4o:** Run 1 — Perception decomposes into 2 reminder
  goals, **Decision actually calls `create_file`** (writes `sandbox/moms_birthday_reminder_*.txt`),
  fact persisted to `state/memory.json`. Run 2 (fresh process, same state) — **reads the fact
  back and answers "May 15, 2026" in 2 iterations, no re-ask.** No crashes.
- **Query A passes fully end-to-end on GPT-4o** (3 iters): `fetch_url` → 258 KB artifact →
  Perception attaches it → Decision extracts the correct answer (born Apr 30 1916, died Feb 24
  2001, + 3 contributions). Web tools wired: **Tavily** (`web_search`, key in `.env`) +
  **crawl4ai** (`fetch_url`, chromium installed via `playwright install chromium`).
  Fix: attached artifact bytes are **capped to 16 KB** in `decision.py` — the raw 254 KB blew
  past the gateway's HUGE-tier ceiling (>~8000 tokens → 503); the page top holds the answer.
- Fixes made during testing: Perception `artifact_index` `["integer","null"]`→plain `integer`+`-1`
  (Gemini rejects union types, was a 502); 429/502/503 retry-backoff in `gateway_client`;
  Perception no longer emits unsatisfiable "remember the fact" goals (was an infinite loop);
  agent6 wraps each iteration in try/except so a transient gateway error ends the run gracefully
  instead of crashing.
- Weak-model contrast (kept for the learning): on `gemini-2.5-flash` Decision *narrated*
  ("a reminder has been created") instead of acting — the strong model fixed that. This is the
  point of the assignment's weak-model design. Prompts are still first drafts: review + PoP.

**Setup used for the real run** (provided substrate lives in `~/Downloads/agentic/`):
`cp ~/Downloads/agentic/7c50da52-*.py mcp_server.py` · `uv add ddgs` · gateway:
`cd ~/Downloads/agentic/llm_gatewayV3 && ./run.sh` (reads `../.env` for keys) · run:
`MCP_SERVER_CMD=".venv/bin/python mcp_server.py" GATEWAY_URL=http://localhost:8101 .venv/bin/python agent6.py "<query>"`.
For Queries A/B/D also `uv add crawl4ai tavily` and set `TAVILY_API_KEY`.

## What's left for you (the graded thinking)

1. **Read & tune** `PERCEPTION_SYSTEM`, `DECISION_SYSTEM`, `CLASSIFY_SYSTEM` so you own and can defend them.
2. Drop in the provided `mcp_server.py` + `llm_gatewayV3/`; align `gateway_client.py` field names to the gateway README if needed.
3. Run the four queries; tune prompts/contracts until each converges within 2× the expected iterations.
4. Run each role prompt through the **PoP** (Prompt-of-Prompts) qualifier from Session 5 and keep the validation JSON — it's a graded deliverable.

> Reuse-from-your-own-code shortcut: `mini-perplexity` already has the patterns —
> `s04/native-tools` (native tool-use round-trip → Decision) and `s04/minion`
> (`minion_mcp/server.py`, the FastMCP shape → helps you read the provided server).
> Its `search → fetch → synthesize` domain is Queries A & D.

## Setup & run

```bash
# 1. drop the PROVIDED code in here:  mcp_server.py  +  the llm_gatewayV3/ folder
# 2. deps
uv sync
uv add ddgs crawl4ai tavily        # the provided MCP server's deps
# 3. secrets
cp .env.example .env && $EDITOR .env   # set TAVILY_API_KEY
# 4. start the gateway V3 on :8101 (per its README), then:
uv run python agent6.py "Fetch https://en.wikipedia.org/wiki/Claude_Shannon and tell me his birth date, death date, and three key contributions to information theory."
```

`state/` is auto-created and **cleanable between attempts** (`rm -rf state/`).

## The four target queries (the assignment)

| # | Query | Tests | ~iters |
|---|-------|-------|--------|
| A | Fetch the Claude Shannon Wikipedia page → birth/death date + 3 contributions | artifact attach (≈250 KB page) | ~3 |
| B | 3 family-friendly things in Tokyo this weekend + Saturday weather (wttr.in) → pick one | multi-goal + memory carryover | ~5–6 |
| C | Run 1: "My mom's birthday is 15 May 2026, remind me 2 weeks before + on the day." Run 2: "When is mom's birthday?" | durable memory across runs | ~4 / ~2 |
| D | Search 'Python asyncio best practices', read top 3, list the advice they agree on | multi-source synthesis | ~5–7 |

Pass bar: correct answers within **2× the expected iterations**. Designed so a wrong
architecture can't pass all four ("fix 1 → 2 breaks…").

## Deliverables (1000 pts)
- GitHub repo (this folder, `state/` visible) [200]
- This README with the **actual terminal output** of all 4 queries from a clean state [200]
- YouTube demo of all 4 end-to-end [400]
- Perception prompt + PoP JSON [100]
- Decision prompt + PoP JSON [100]

## Constraints
Pydantic v2 on every boundary · `uv` · MCP **stdio** · gateway V3 for **every** LLM call ·
**no** LangChain/LangGraph/CrewAI · `state/` cleanable.

---
_Interactive walkthrough of every piece (what / why / how it connects): the **S6 Studio**
in `~/ws/projects/EVA3/learn-app/s6-studio.html`._
