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

**Real run (2026-05-22, V3 gateway :8101 + provided 9-tool `mcp_server.py`, `AGENT_PROVIDER=openai`
→ GPT-4o). All four queries pass from a clean `state/` within the iteration budget:**

| # | Query | Result | iters (budget 2×) |
|---|-------|--------|-------------------|
| A | Shannon Wikipedia → birth/death + 3 contributions | born Apr 30 1916, died Feb 24 2001; Mathematical Theory of Communication, Shannon entropy, Sampling Theorem | **3** (≤6) |
| B | Tokyo family activities + Saturday weather → pick one | 3 activities, fetched `wttr.in/Tokyo` (16 °C cloudy), recommended the **indoor** Samurai/Ninja museum | **6** (≤12) |
| C1 | "mom's birthday 15 May 2026, remind me…" | fact persisted to `state/memory.json`; **two** reminder files created in `sandbox/` | **3** (≤8) |
| C2 | "When is my mom's birthday?" (fresh process, same state) | recalls **"May 15, 2026"** from durable memory, no re-ask | **2** (≤4) |
| D | asyncio best practices → read top 3 → agreed advice | 7 agreed practices synthesised across the search results | **4** (≤14) |

Full trimmed traces + final answers are in **[Actual run output](#actual-run-output-clean-state)** below.

- **Strong model via the gateway:** added native `openai` + `anthropic` providers to the
  gateway's `providers.py` (+ `LIMITS`/`SHORTCUTS` in `router.py`); set `AGENT_PROVIDER=openai`
  to route every agent call to **GPT-4o**. (Claude via the OpenAI-compat endpoint didn't work —
  it needs the native Messages API; GPT-4o is the working path.) Web tools: **Tavily**
  (`web_search`) + **crawl4ai** (`fetch_url`, chromium via `playwright install chromium`).
- **Prompts are owned + PoP-qualified.** All three role prompts were reworked from the drafts
  (explicit step-by-step reasoning, self-checks, fallbacks, reasoning-type framing) and run
  through the Session-5 PoP qualifier → `pop/{perception,decision,memory_classify}.json`
  (Perception 8/8, Decision 8/8, Memory 7–8/8 — the two N/A criteria are tool-separation /
  multi-turn for a single-shot classifier). Re-run with `pop/run_pop.py`.
- **Fixes that made the four converge** (the graded thinking — wrong architecture can't pass all
  four): (1) Query B — Decision now **fetches `wttr.in/<City>` directly** instead of
  `web_search`-ing for it (search returned a wrong-city wttr page and looped). (2) Query C run 1
  — Perception splits "remind me before/on" into **one creation goal per reminder** and Decision
  writes each to its **own flat-named file** (`reminders/…` subpaths fail — the provided server
  doesn't auto-create dirs). (3) Query C run 2 — Perception marks a recall goal done **only when
  an answer is in HISTORY**, not when the fact merely sits in MEMORY (that was breaking out with
  no answer). (4) attached artifact bytes **capped to 16 KB** in `decision.py` (raw 254 KB blew
  past the gateway HUGE-tier ceiling → 503). (5) `artifact_index` plain `integer`+`-1` sentinel
  (Gemini rejects union types); 429/502/503 retry-backoff in `gateway_client`; agent6 wraps each
  iteration in try/except so a transient gateway error ends the run gracefully.

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

### Web UI (optional) — the same loop, in the browser

`webapp/` is a thin presentation layer over the **same** `agent6.run` loop (same gateway,
same MCP stdio — no logic duplicated). `agent6.run` takes an optional `on_event` callback;
the FastAPI server streams those events over SSE and the single-page UI renders each
iteration as Memory → Perception → Decision → Action cards (goal checklists, tool calls,
results, final answer). CLI behaviour is unchanged when `on_event` is omitted.

```bash
uv add fastapi uvicorn        # one-time
# gateway must be up on :8101, then from agentic/:
env -u ANTHROPIC_API_KEY -u ANTHROPIC_BASE_URL MCP_SERVER_CMD=".venv/bin/python mcp_server.py" \
    GATEWAY_URL="http://localhost:8101" AGENT_PROVIDER="openai" \
    .venv/bin/python -m uvicorn webapp.server:app --port 8000
# open http://127.0.0.1:8000
```

## The four target queries (the assignment)

| # | Query | Tests | ~iters |
|---|-------|-------|--------|
| A | Fetch the Claude Shannon Wikipedia page → birth/death date + 3 contributions | artifact attach (≈250 KB page) | ~3 |
| B | 3 family-friendly things in Tokyo this weekend + Saturday weather (wttr.in) → pick one | multi-goal + memory carryover | ~5–6 |
| C | Run 1: "My mom's birthday is 15 May 2026, remind me 2 weeks before + on the day." Run 2: "When is mom's birthday?" | durable memory across runs | ~4 / ~2 |
| D | Search 'Python asyncio best practices', read top 3, list the advice they agree on | multi-source synthesis | ~5–7 |

Pass bar: correct answers within **2× the expected iterations**. Designed so a wrong
architecture can't pass all four ("fix 1 → 2 breaks…").

## Actual run output (clean state)

Captured 2026-05-22, GPT-4o via the V3 gateway, each from a fresh `rm -rf state sandbox`
(C2 reuses C1's `state/` — that's the durable-memory test). Tool/answer lines trimmed for width.

### Query A — Shannon (3 iters)
```
─── iter 1 ───
  [open] Fetch the Claude Shannon Wikipedia page (https://en.wikipedia.org/wiki/Claude_Shannon)
  [open] Extract his birth date, death date, and three key contributions to information theory
  [tool] fetch_url({'url': 'https://en.wikipedia.org/wiki/Claude_Shannon'}) -> [artifact art:1, 261107 bytes] …
─── iter 2 ───
  [done] Fetch the Claude Shannon Wikipedia page
  [open] Extract his birth date, death date, and three key contributions  attach=art:1
  [answer] Claude Shannon was an influential figure in the field of information theory. …
─── iter 3 ───
  [done] Fetch the Claude Shannon Wikipedia page
  [done] Extract his birth date, death date, and three key contributions
=== FINAL ===
Birthdate: April 30, 1916.  Death: February 24, 2001.
Key contributions: (1) "A Mathematical Theory of Communication" (1948) — founded information
theory, introduced the bit. (2) Shannon entropy — quantifies uncertainty / information content.
(3) The Sampling Theorem — continuous signals fully represented digitally if sampled fast enough.
```

### Query B — Tokyo + weather → pick one (6 iters)
```
─── iter 1 ───
  [open] Suggest three family-friendly things to do in Tokyo this weekend.
  [open] Check Saturday's weather in Tokyo via wttr.in.
  [open] Recommend one activity based on the weather forecast.
  [tool] web_search({'query': 'family-friendly activities in Tokyo this weekend', 'max_results': 3}) -> [artifact art:1] …
─── iter 2 ───  [answer] three activities: Samurai/Ninja museum, Yoyogi Park, Inokashira Park Zoo …
─── iter 4 ───  [tool] fetch_url({'url': 'https://wttr.in/Tokyo?format=3&1'}) -> …      # fetched directly, not searched
─── iter 5 ───  [answer] forecast 16 °C / cloudy → recommend the INDOOR Samurai/Ninja museum …
─── iter 6 ───
  [done] Suggest three family-friendly things …  [done] Check Saturday's weather …  [done] Recommend one activity …
=== FINAL ===
Recommended (weather-grounded): the Samurai & Ninja museum in Asakusa — it's indoor, ideal for
the cloudy 16 °C Saturday, and engaging for kids.
```

### Query C run 1 — remember + create reminders (3 iters)
```
─── iter 1 ───
  [open] Create a reminder two weeks before mom's birthday (2026-05-01)
  [open] Create a reminder on mom's birthday (2026-05-15)
  [tool] create_file({'path': 'moms_birthday_reminder_2weeks.txt', …}) -> ok
─── iter 2 ───  [tool] create_file({'path': 'moms_birthday_reminder_onday.txt', …}) -> ok
─── iter 3 ───  [done] … two weeks before  [done] … on the day
=== FINAL ===
Done — completed 2 action(s): create_file(moms_birthday_reminder_2weeks.txt); create_file(moms_birthday_reminder_onday.txt).
# state/memory.json → fact | mom's birthday is 2026-05-15
# sandbox/ → moms_birthday_reminder_2weeks.txt, moms_birthday_reminder_onday.txt
```
(A create-only query produces no "answer" events, so the loop summarises the actions it
completed instead of reporting a misleading "no answer".)

### Query C run 2 — recall across a fresh process, same state (2 iters)
```
─── iter 1 ───
  [open] Find out when mom's birthday is.
  [answer] Mom's birthday is on May 15, 2026.
─── iter 2 ───
  [done] Find out when mom's birthday is.
=== FINAL ===
Mom's birthday is on May 15, 2026.
```

### Query D — asyncio multi-source synthesis (4 iters)
```
─── iter 1 ───
  [open] Search for 'Python asyncio best practices'
  [open] Read the top three results
  [open] List the advice they agree on
  [tool] web_search({'query': 'Python asyncio best practices', 'max_results': 5}) -> [artifact art:1] …
─── iter 2 ───  [answer] top three results: Shane's blog, Python.org Async-SIG, OneUptime …
─── iter 3 ───  [answer] agreed advice list …
─── iter 4 ───  [done] search  [done] read top 3  [done] list agreed advice
=== FINAL ===
Agreed-on advice: use asyncio.run() as the entry point; use async context managers (async with)
for resources; always await coroutines; don't block the event loop; offload blocking code; create
tasks for independent concurrent work; handle cancellations gracefully.
```

## Deliverables (1000 pts)
- ✅ GitHub repo (code + README documenting how to run each query; **`state/` excluded by `.gitignore`** per the rubric) [200]
- ✅ This README with the **actual terminal output** of all 4 queries from a clean state [200] — see [Actual run output](#actual-run-output-clean-state)
- ⬜ YouTube demo of all 4 end-to-end [400] — *record against this same setup*
- ✅ Perception prompt (`perception.py` → `PERCEPTION_SYSTEM`) + PoP JSON (`pop/perception.json`, 8/8) [100]
- ✅ Decision prompt (`decision.py` → `DECISION_SYSTEM`) + PoP JSON (`pop/decision.json`, 8/8) [100]

PoP qualifier prompt: `pop/qualifier.md`. Re-run all three through it with
`GATEWAY_URL=http://localhost:8101 AGENT_PROVIDER=openai uv run python pop/run_pop.py`.

## Constraints
Pydantic v2 on every boundary · `uv` · MCP **stdio** · gateway V3 for **every** LLM call ·
**no** LangChain/LangGraph/CrewAI · `state/` cleanable.

---
_Interactive walkthrough of every piece (what / why / how it connects): the **S6 Studio**
in `~/ws/projects/EVA3/learn-app/s6-studio.html`._
