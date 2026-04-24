# Mini Perplexity — Roadmap: From One-Shot Agent to Production-Grade Orchestration

> What to build *next*, in what order, and why.

This document is the curated learning path that picks up where [`WALKTHROUGH.md`](WALKTHROUGH.md) ends. The walkthrough explains how the agent works *today*. The roadmap explains what it should become and how to get there without throwing any of it away.

It is grounded in two inputs:

1. **The state of the agent in this repo** as of 2026-04-24 — single-agent loop, 3 hand-rolled tools, no memory across runs, no evals, no protocol layer.
2. **2026 industry consensus** on what to build *after* a hand-rolled agent loop — synthesized from a structured web search across MCP docs, multi-agent framework comparisons, eval-platform reviews, and post-ReAct roadmap pieces (sources at the bottom).

It also aligns with the [EAG V3](https://github.com/levelscorner/levelscorner-eva3) curriculum the agent was originally built for — every step here maps to a course session.

---

## TL;DR

**Add three things in one focused sprint, in this order:**

1. **MCP** — wrap the 3 tools as a Model Context Protocol server. Same code, new wire protocol. Becomes reusable from Claude Desktop, Cursor, any other agent.
2. **Memory** — add `recall(query)` as a 4th MCP tool. Past answers compound instead of being thrown away.
3. **Eval harness** — 10 golden questions + trajectory + citation correctness scoring. Phoenix for traces. Built *before* you scale, not after.

**Do NOT skip ahead** to multi-agent / planning / browser tools / framework adoption until this sprint is shipped and the eval baseline is set. The 2026 consensus is unanimous: premature complexity is the dominant failure mode of agentic AI builds.

---

## Section 1 — Where this agent sits today

```
┌─────────────────────────────────────────────────┐
│  mini_perplexity.py                             │
│  ┌───────────────────────────────────────────┐  │
│  │  for iter in 1..max_iterations:           │  │
│  │    prompt = flatten(system + messages)    │  │
│  │    raw    = LLM.generate(prompt)          │  │
│  │    parsed = parse_llm_response(raw)       │  │
│  │    if "answer" in parsed: return          │  │
│  │    if "tool_name" in parsed:              │  │
│  │      result = TOOLS[name](**args)         │  │
│  │      messages += [assistant, tool]        │  │
│  └───────────────────────────────────────────┘  │
│                       │                         │
│       ┌───────────────┼───────────────┐         │
│       ▼               ▼               ▼         │
│   web_search      fetch_page      save_answer   │
│  (DuckDuckGo)   (trafilatura)   (markdown disk) │
└─────────────────────────────────────────────────┘
```

**Strengths:** primitive is correct; full-history carry; resilient JSON parser; reasoning chain visible; portable (no cloud deps beyond Gemini).

**Structural gaps (ranked by leverage):**

| # | Gap | Symptom | Highest-impact fix |
|---|-----|---------|--------------------|
| 1 | Tools imprisoned in this Python process | Can't reuse from Claude Desktop, Cursor, other agents | **MCP** — standardize the wire |
| 2 | Zero memory across runs | Every `python mini_perplexity.py "..."` starts from scratch | Vector store + `recall()` tool |
| 3 | No trajectory evals | Can't tell when a change improves vs regresses behavior | Phoenix / Braintrust traces + golden Qs |
| 4 | Step-by-step ReAct only | Wastes iterations on multi-part questions | Plan-then-act pattern |
| 5 | One agent, sequential | Genuinely parallel sub-tasks bottleneck | Multi-agent (LATER) |
| 6 | Text-only fetching | Paywalled / JS-heavy pages return junk | Browser tool |
| 7 | No agent-to-agent contract | This agent can't be called by another | A2A protocol |
| 8 | Untyped boundaries | LLM string drift hard to catch | Pydantic on tool I/O |

**The roadmap below addresses gaps 1–3 in one sprint, then sequences the rest.**

---

## Section 2 — The Sprint (Step 1): MCP + Memory + Evals

**Estimated effort:** one focused weekend (~12 hours), broken into three days.

### Day 1 — MCP server (~4 hrs)

#### Goal

The three tools (`web_search`, `fetch_page`, `save_answer`) become an MCP server. The current loop becomes an MCP client.

#### Why MCP first

- It's the dominant agent-tool standard in 2026. Anthropic's spec, now adopted across Claude SDK, Cursor, Cline, OpenAI Agents SDK, Google ADK. ([source 1])
- **Direct continuation** — `tools.py` becomes `server.py`. No throwaway code.
- **Distribution unlock** — once it's an MCP server, Claude Desktop / Cursor / Cline / any MCP-aware agent can call your tools. The same code becomes a product surface.
- It's a wire protocol, not a framework. You learn the actual pattern, not someone's abstraction over it.
- EAG V3 Session 4 is exactly this. You're shipping S04 by completing Day 1.

#### File layout after Day 1

```
mini-perplexity/
├── mcp_server/
│   ├── __init__.py
│   ├── server.py         # FastMCP server exposing the 3 tools
│   └── tools/
│       ├── search.py     # ported from tools.py
│       ├── fetch.py      # ported from tools.py
│       └── save.py       # ported from tools.py
├── mini_perplexity.py    # now an MCP CLIENT (uses mcp.client)
├── llm.py                # unchanged
├── parser.py             # unchanged
├── ui.py                 # unchanged
└── tools.py              # DELETED (or kept for back-compat if you want)
```

#### Concrete code

`mcp_server/server.py`:

```python
from mcp.server.fastmcp import FastMCP

from .tools.search import web_search as _web_search
from .tools.fetch  import fetch_page as _fetch_page
from .tools.save   import save_answer as _save_answer

mcp = FastMCP("mini-perplexity-tools")


@mcp.tool()
def web_search(query: str, n: int = 5) -> dict:
    """DuckDuckGo search. Returns ranked {title, url, snippet} results."""
    return _web_search(query, n)


@mcp.tool()
def fetch_page(url: str, max_chars: int = 5000) -> dict:
    """Fetch URL, extract main article text, truncate."""
    return _fetch_page(url, max_chars)


@mcp.tool()
def save_answer(question: str, answer: str, sources: list[dict]) -> dict:
    """Persist a Perplexity-style markdown answer with numbered citations."""
    return _save_answer(question, answer, sources)


if __name__ == "__main__":
    mcp.run(transport="stdio")
```

> **Watch out:** in stdio mode, *never* `print()` to stdout — it corrupts the JSON-RPC frame. Use `sys.stderr` or Python `logging` configured to stderr. ([source 2])

#### Migrating `tools.py` cleanly

The existing tool functions return JSON **strings**. MCP tools should return **dicts** (FastMCP serializes them). Two options:

- **Option A (recommended):** change the tool functions to return dicts, update `_smoke_test.py`. Cleaner, more idiomatic.
- **Option B:** keep returning strings, parse in the wrapper. Lower risk, more code.

Pick A.

#### Wire the agent as MCP client

In `mini_perplexity.py`, replace `from tools import TOOLS` with:

```python
from mcp.client.session import ClientSession
from mcp.client.stdio   import stdio_client, StdioServerParameters

# inside run_agent:
server = StdioServerParameters(command="python", args=["-m", "mcp_server.server"])
async with stdio_client(server) as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()
        # tool dispatch becomes:
        result = await session.call_tool(parsed["tool_name"], parsed["tool_arguments"])
```

Yes, this means the loop goes async. Acceptable cost — `asyncio.run(run_agent(...))` at the entry point.

#### Acceptance criteria for Day 1

- [ ] `python -m mcp_server.server` starts and waits on stdin (it should).
- [ ] `_smoke_test.py` updated to spawn the server in-process and exercise it.
- [ ] `python mini_perplexity.py "test question"` produces the same final markdown as before extraction.
- [ ] Adding the server to `claude_desktop_config.json` makes the 3 tools usable from Claude Desktop directly. **This is the unlock — verify it.**

---

### Day 2 — Memory (~4 hrs)

#### Goal

Add `recall(query, k=3)` as a 4th MCP tool. Past answers become semantically searchable. Saving an answer also indexes it.

#### Why memory next (not multi-agent)

Industry consensus 2026:

> "If your agent can't remember what it learned, you're automating amnesia. The systems that compound value over time treat memory as a substrate everything else builds on." ([source 3])

> "Start simple, add memory immediately, master planning patterns, then scale to multi-agent only when needed." ([source 4])

Memory is the highest-leverage primitive after the basic loop. Without it, every run is a cold start. With it, the agent gets quietly more useful every week.

#### Stack

All local, free, no cloud:

- **`chromadb`** — embedded vector DB, file-backed, no server.
- **`sentence-transformers`** with `all-MiniLM-L6-v2` — small (~80MB), fast (CPU-OK), good-enough embeddings.

Alternative if you want zero new heavy deps: **`sqlite-vss`** (vector search inside SQLite). Smaller blast radius but more glue code.

Pick `chromadb` for round 1 — least effort to get green.

#### File layout additions

```
mini-perplexity/
├── mcp_server/
│   ├── memory/
│   │   ├── __init__.py
│   │   ├── store.py       # Chroma + sentence-transformers wrapper
│   │   └── schema.py      # AnswerRecord dataclass
│   └── tools/
│       └── recall.py      # 4th tool
├── data/
│   └── chroma/            # gitignored — local index
```

#### Memory schema

```python
from dataclasses import dataclass
from datetime import datetime

@dataclass
class AnswerRecord:
    id: str                     # uuid
    question: str
    answer_path: str            # path to answers/<slug>.md
    summary: str                # first paragraph of answer (~500 chars)
    sources: list[str]          # URLs cited
    created_at: str             # ISO timestamp
    embedding: list[float]      # set by store.add()
```

#### `recall` tool

```python
@mcp.tool()
def recall(query: str, k: int = 3) -> list[dict]:
    """Semantic search over past answers. Returns [{question, answer_path, summary, score}]."""
    hits = MEMORY.query(query, k=k)
    return [
        {
            "question":    h.question,
            "answer_path": h.answer_path,
            "summary":     h.summary,
            "score":       float(h.score),
        }
        for h in hits
    ]
```

#### Modify `save_answer` to also index

After writing the markdown file, append:

```python
record = AnswerRecord(
    id=str(uuid.uuid4()),
    question=question,
    answer_path=str(path),
    summary=answer[:500],
    sources=[s["url"] for s in normalized],
    created_at=datetime.utcnow().isoformat() + "Z",
    embedding=[],  # filled by store.add()
)
MEMORY.add(record)
```

#### System prompt update

Add to `system_prompt.md` after the existing tool list:

```
4. `recall(query: string, k?: integer)`
   Semantic search over your own past answers. Call this FIRST on any question
   before doing fresh searches. If a relevant past answer exists (score > 0.7),
   reference it instead of re-doing the work, OR build on it for the new question.
```

Workflow update:

```
1. recall — check if a past answer covers it.
2. If yes and high-confidence: cite past answer, save_answer (new entry that links to it).
3. If no: web_search → fetch_page → save_answer (which also indexes).
```

#### Acceptance criteria for Day 2

- [ ] `data/chroma/` materializes after first `save_answer`.
- [ ] Re-running the same question hits `recall` first and short-circuits to a faster answer.
- [ ] Asking a *related but different* question surfaces the prior answer as context (score 0.5–0.8 range).
- [ ] `chromadb` files don't get committed (in `.gitignore`).

---

### Day 3 — Eval harness (~4 hrs)

#### Goal

10 golden questions. Each run scores the agent on trajectory correctness, citation correctness, and hallucination rate. Traces visible in Phoenix UI. Baseline numbers recorded so future changes can be measured.

#### Why eval *before* multi-agent / planning / browser

> "Build your eval suite before you see failures." ([source 5])

> "Step-level traces — capturing reasoning, tool calls, tool results, and decisions at each point in the loop — are the only way to understand what your agent did when something goes wrong." ([source 5])

Without an eval baseline, every future change is vibes. With one, you can definitively say "planning improved citation accuracy from 0.71 → 0.86 but slowed mean iters from 5 → 7." That's how the rest of the roadmap stays honest.

#### Tool choice — Arize Phoenix

Why Phoenix over LangSmith / Braintrust for *this* project:

- **OTel-native** — instruments via standard OpenTelemetry, no vendor lock-in.
- **Self-hosted, free, local-first** — runs as a container or `phoenix.launch_app()` in-process.
- **Vendor-neutral** — works with Gemini / Claude / OpenAI / local models without re-instrumentation.

LangSmith is better if you adopt LangGraph later. Braintrust is better if you want CI-gated evals with AI-automated prompt optimization. Phoenix is the right starting point for a hand-rolled agent that wants to stay framework-free. ([source 6])

#### File layout additions

```
mini-perplexity/
├── evals/
│   ├── __init__.py
│   ├── golden_questions.yaml   # the 10 Qs + expected facts/sources
│   ├── runner.py               # run agent on each Q, capture trace
│   ├── scorers.py              # trajectory, citation, hallucination
│   └── results/                # gitignored — JSON per run
├── pyproject.toml              # add: arize-phoenix, openinference-instrumentation-google-genai
```

#### `golden_questions.yaml`

```yaml
- id: q01
  question: "What is the Model Context Protocol?"
  must_mention: ["JSON-RPC", "Anthropic", "tool"]
  must_cite_domain: ["modelcontextprotocol.io", "anthropic.com"]
  red_flags: ["GraphQL", "REST"]   # MCP is not these — should not be claimed
  max_iters: 8

- id: q02
  question: "How do I sandbox eval() in Python safely?"
  must_mention: ["__builtins__", "RestrictedPython", "ast.literal_eval"]
  must_cite_domain: ["docs.python.org", "stackoverflow.com", "realpython.com"]
  red_flags: []
  max_iters: 8

# ... 8 more
```

#### Scorers (deterministic + LLM-judge mix)

```python
def trajectory_score(events: list[dict]) -> float:
    """Deterministic: did the agent hit search → fetch ×n → save?"""
    tool_seq = [e["payload"]["name"] for e in events if e["kind"] == "tool_call"]
    expected_pattern = ["web_search", "fetch_page", "fetch_page", "save_answer"]
    # ...

def citation_score(answer_md: str, fetched_urls: set[str]) -> float:
    """Deterministic: every [n] in answer maps to a URL we actually fetched."""

def hallucination_score(answer_md: str, fetched_texts: list[str], judge_llm) -> float:
    """LLM-as-judge: any claim not supported by fetched content?"""
```

#### Phoenix integration

```python
from phoenix.otel import register
tracer_provider = register(project_name="mini-perplexity")

# decorate run_agent with @tracer.start_as_current_span("agent_run")
# spans appear automatically; tool calls become child spans
```

Open `http://localhost:6006` to inspect traces.

#### Acceptance criteria for Day 3

- [ ] `python -m evals.runner` runs all 10 Qs and produces `evals/results/<timestamp>.json`.
- [ ] Phoenix UI shows a span tree per question with tool calls + LLM calls + token counts.
- [ ] Aggregate scores recorded as the **baseline**:
  - trajectory: ?/1.0
  - citation: ?/1.0
  - hallucination: ?/1.0 (lower is better; this measures how much the agent makes up)
  - mean iterations: ?
  - mean cost (USD): ?
- [ ] `evals/results/baseline-2026-04-26.json` committed (the trace JSON, not Phoenix's local DB).

---

## Section 3 — The Path Beyond (Don't Sprint Until Step 1 Ships)

These are sequenced. Do them *in order*. Skipping creates the kind of complexity-without-leverage failure the 2026 consensus warns against. ([source 4][source 7])

### Step 2 — Plan-then-Act (~1 weekend, EAG V3 S06)

**The shift:** instead of step-by-step ReAct, the agent first emits a *plan* (sub-questions / parallel fetches / synthesis structure), then executes.

**Why second:** plan-then-act lets you parallelize the `fetch_page` calls. For a question like "compare Claude 4.7, GPT-5, and Gemini 3," ReAct does 3 sequential fetch loops (~30s). A plan can run them concurrently (~10s) and the synthesizer is sharper because it sees all three at once.

**What changes:**
- New first turn: agent emits `{"plan": [{"subquestion": "...", "tools": ["web_search", "fetch_page"]}, ...]}`
- Loop becomes: plan → execute leaves in parallel via `asyncio.gather` → final synthesis turn.
- System prompt grows a "plan format" section.

**Re-eval after:** rerun the 10 golden Qs. Confirm trajectory score for multi-part questions improves.

### Step 3 — Multi-Agent (Manager / Worker / Synthesizer) (~1 weekend, EAG V3 S08)

**The shift:** the planner becomes a Manager agent. Each leaf becomes a Worker agent. A Synthesizer agent merges. All three speak to the same MCP server.

**Why third:**

> "If steps must happen sequentially and share state, a single agent with a planning loop handles it cleanly. If independent subtasks can run concurrently, that's where multi-agent pays off." ([source 4])

After Step 2 you'll know empirically whether Step 3 buys you anything. If your golden Qs are mostly single-thread, you may stop at Step 2 forever.

**Framework choice deferred until here.** When you do reach for a framework, the 2026 landscape ([source 8][source 9]):

| Framework | Pick when |
|-----------|-----------|
| **LangGraph** | You need stateful, persistent, time-travel-debuggable orchestration. Highest production maturity. |
| **CrewAI** | You want fastest prototyping and role-based DSL. Lower checkpoint maturity. |
| **Claude Agent SDK** | You're committed to Claude + want MCP-native, safety-first defaults. |
| **OpenAI Agents SDK** | You're committed to OpenAI + want clean handoffs. |
| **Google ADK** | Multimodal + A2A + Vertex AI integration. Newest of the bunch. |

For mini-perplexity specifically: stay framework-free through Step 3 the first time. Build the manager-worker loop by hand. Adopt LangGraph in Step 4+ only if you start needing checkpointing across long-running multi-agent workflows.

### Step 4 — Browser Tool (~3 days, EAG V3 S09)

**The shift:** add a 5th MCP tool, `browse(url, instruction)`, backed by Playwright. Unlocks paywalled sites, JS-heavy SPAs, sites that block plain `requests.get`.

**Implementation:** separate MCP server (`mcp_browser_server/`) so it can be started/stopped independently of the main one. Keeps blast radius contained.

**Trade-off:** Playwright is heavy (Chromium download ~200MB). Use only when `fetch_page` returns junk. Add a fallback chain: `fetch_page` → if `bytes < 500` → `browse`.

### Step 5 — A2A Protocol (~1 weekend, EAG V3 S13)

**The shift:** wrap mini-perplexity behind an Agent Card (JSON capability advertisement) + JSON-RPC 2.0 endpoint. Now another agent can call this one as a sub-task ("hey research-agent, find me the cited primary sources for this claim").

**Why fifth:** A2A is the protocol that lets your agents become services that other agents (yours or strangers') can compose. Build after multi-agent so you have something *worth* federating.

### Step 6 — A2UI / Generative UI (~1 weekend, EAG V3 S14)

**The shift:** replace the `rich` terminal renderer with a browser-side reasoning chain that renders interactive components per event. The `ChainEvent` shape in `ui.py` is already clean; it just needs a different transport (SSE) and renderer (React).

---

## Section 4 — Anti-Patterns to Dodge

Each of these has burned real teams in 2026. ([source 4][source 5][source 7])

| Anti-pattern | Why it bites |
|--------------|--------------|
| Adopting LangGraph / CrewAI before MCP-ifying | Frameworks abstract over the wire protocol you're trying to learn. The skill doesn't transfer. |
| Adding multi-agent before memory | You'll have N amnesiac agents instead of 1 amnesiac agent. Strictly worse. |
| Skipping evals to "move faster" | Without baseline you can't tell complexity from improvement; everything feels productive. |
| Using same untyped dict shape across 4+ agents | LLM string drift compounds across agent boundaries. Pydantic at every boundary becomes non-optional. |
| Bolting on observability after a prod outage | Step traces have to exist before you need them. They're useless retroactive. |
| Picking a framework before knowing what you need | The "best" framework varies dramatically by use case; match it to evidence from your eval suite. |

---

## Section 5 — Course Map (EAG V3 alignment)

Every step in this roadmap maps to a session in the EAG V3 curriculum that originally produced this agent. By following the roadmap you're effectively shipping the rest of Act 1–3 of the course.

| Roadmap step | EAG V3 session | What you'll have demonstrated |
|--------------|----------------|-------------------------------|
| Step 1a — MCP server | **S04 — MCP** | Standard tool protocol, stdio transport |
| Step 1b — Memory | **S07 — Memory** | Persistent semantic store, hybrid retrieval primitive |
| Step 1c — Evals | **(cross-cutting, S20 capstone)** | Trajectory + citation eval harness |
| Step 2 — Plan-then-act | **S06 — Cognitive pipeline** | Perception → Decision → Action stages |
| Step 3 — Multi-agent | **S08 — DAG executor** | Manager-worker pattern, NetworkX or hand-rolled |
| Step 4 — Browser tool | **S09 — Browser agents** | Playwright as MCP server |
| Step 5 — A2A | **S13 — A2A protocol** | Agent Card + JSON-RPC endpoint |
| Step 6 — Generative UI | **S14 — A2UI / AG-UI** | Browser-rendered reasoning chain |

---

## Section 6 — What to Do This Week

Concrete checklist for Step 1:

- [ ] **Mon eve** — bootstrap: `mkdir mcp_server && touch mcp_server/__init__.py mcp_server/server.py`. Add `mcp` to `pyproject.toml`. `uv pip install -e .`.
- [ ] **Tue eve** — port `tools.py` → `mcp_server/tools/{search,fetch,save}.py`. Make them return dicts. Update `_smoke_test.py`.
- [ ] **Wed eve** — wire `mini_perplexity.py` as MCP client. Verify same answer on a known question. Add to `claude_desktop_config.json` and confirm Claude Desktop sees the tools.
- [ ] **Thu eve** — `chromadb` + `sentence-transformers`. Add `recall()` tool. Modify `save_answer` to index. Update `system_prompt.md`.
- [ ] **Fri eve** — `phoenix` instrumentation. Write `evals/golden_questions.yaml` (10 Qs).
- [ ] **Sat day** — `evals/runner.py` + `evals/scorers.py`. Run baseline. Commit `evals/results/baseline-<date>.json`.
- [ ] **Sat eve** — write Part 2 of `WALKTHROUGH.md` covering MCP + memory + evals.
- [ ] **Sun** — push, post on the EAG V3 discord, optionally publish Phoenix screenshots in `docs/`.

After this week, you've shipped EAG V3 S04 + S07 *plus* a real eval baseline that none of your classmates will have. You're ahead of the curriculum, not behind it.

---

## Section 7 — Reference Code Sketches

### MCP server smoke test (after Day 1)

```python
import asyncio
from mcp.client.session import ClientSession
from mcp.client.stdio   import stdio_client, StdioServerParameters

async def smoke():
    server = StdioServerParameters(command="python", args=["-m", "mcp_server.server"])
    async with stdio_client(server) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert {t.name for t in tools.tools} == {"web_search", "fetch_page", "save_answer"}
            result = await session.call_tool("web_search", {"query": "hello", "n": 2})
            assert result.content[0].text  # JSON string

asyncio.run(smoke())
```

### Memory store sketch (Day 2)

```python
import chromadb
from sentence_transformers import SentenceTransformer

class MemoryStore:
    def __init__(self, path: str = "data/chroma"):
        self.client = chromadb.PersistentClient(path=path)
        self.col = self.client.get_or_create_collection("answers")
        self.encoder = SentenceTransformer("all-MiniLM-L6-v2")

    def add(self, record: AnswerRecord) -> None:
        emb = self.encoder.encode(record.question + " " + record.summary).tolist()
        self.col.add(
            ids=[record.id],
            embeddings=[emb],
            metadatas=[{
                "question": record.question,
                "answer_path": record.answer_path,
                "summary": record.summary,
                "created_at": record.created_at,
            }],
        )

    def query(self, q: str, k: int = 3) -> list[Hit]:
        emb = self.encoder.encode(q).tolist()
        res = self.col.query(query_embeddings=[emb], n_results=k)
        return [
            Hit(
                question=m["question"],
                answer_path=m["answer_path"],
                summary=m["summary"],
                score=1.0 - d,  # cosine distance → similarity
            )
            for m, d in zip(res["metadatas"][0], res["distances"][0])
        ]
```

### Eval runner sketch (Day 3)

```python
import yaml, json
from pathlib import Path
from datetime import datetime

import mini_perplexity
from evals.scorers import trajectory_score, citation_score, hallucination_score

def main():
    qs = yaml.safe_load(Path("evals/golden_questions.yaml").read_text())
    out = []
    for q in qs:
        events, answer_md, fetched = mini_perplexity.run_agent_capture(q["question"], max_iterations=q["max_iters"])
        out.append({
            "id": q["id"],
            "trajectory":   trajectory_score(events),
            "citation":     citation_score(answer_md, fetched),
            "hallucination": hallucination_score(answer_md, fetched, judge=mini_perplexity.LLMClient()),
            "iters":        max(e["iteration"] for e in events),
        })
    Path("evals/results").mkdir(exist_ok=True)
    Path(f"evals/results/{datetime.utcnow().isoformat()}.json").write_text(
        json.dumps(out, indent=2)
    )
```

---

## Section 8 — Decisions Already Made (Lock These)

| Decision | Locked because |
|----------|----------------|
| MCP before any framework | Wire-protocol literacy; doesn't lock you to one vendor |
| Phoenix for traces (not LangSmith first) | Vendor-neutral; OTel-standard; self-hosted free |
| Chroma for memory (not Pinecone / Weaviate) | Local-first; no cloud cost; can be replaced by Postgres+pgvector at any scale point |
| Stay framework-free through Step 3 | Forces you to understand the orchestration pattern, not someone's abstraction over it |
| Eval before multi-agent | Without baseline, "multi-agent helped" is unfalsifiable |
| Each MCP tool returns dicts (not JSON strings) | Idiomatic FastMCP; less serialize/deserialize churn |
| Browser tool is a separate MCP server | Heavy dep (Chromium); contain the blast radius |

---

## Section 9 — Open Questions (To Resolve During Step 1)

- **Sync vs async loop.** MCP client is async. Do we keep `mini_perplexity.py` sync with `asyncio.run()` at the boundary, or commit to async throughout? *Lean: sync facade, async internals.*
- **Stdio vs SSE for MCP transport.** Stdio is simplest for local + Claude Desktop. SSE if you ever want to remote-host. *Lean: stdio for round 1, SSE only if Step 5 (A2A) demands it.*
- **Where do `recall` hits land in the prompt?** Inline as a synthetic "tool result," or as an injected system-prompt section? *Lean: synthetic tool result — keeps the contract uniform.*
- **Eval frequency.** Run on every commit (CI), nightly, or manual? *Lean: manual until Step 3, then nightly when changes get hard to reason about.*

---

## Section 10 — When to Revisit This Document

- After Step 1 ships — update Section 6 with actual hours + actual baseline numbers.
- After Step 3 (multi-agent) — fix Section 4 (anti-patterns) with anything you actually hit.
- If a new MCP-aware client (e.g., a future Cursor / Cline release) changes integration patterns — update Section 2 Day 1.
- When the agent's eval scores plateau — that's the signal to advance to the next step in Section 3.

---

## Sources (curated 2026-04-24)

1. [MCP Python SDK on GitHub (official)](https://github.com/modelcontextprotocol/python-sdk)
2. [Build an MCP Server — modelcontextprotocol.io](https://modelcontextprotocol.io/docs/develop/build-server)
3. [Agentic Workflows for 2026 — Supermemory](https://blog.supermemory.ai/agentic-workflows-vp-engineering-guide/)
4. [Best Practices for Building Agentic Systems — InfoWorld](https://www.infoworld.com/article/4154570/best-practices-for-building-agentic-systems.html)
5. [Best AI Agent Debugging Tools 2026 — Braintrust](https://www.braintrust.dev/articles/best-ai-agent-debugging-tools-2026)
6. [Top AI Agent Evaluation Tools 2026 — Goodeye Labs](https://www.goodeyelabs.com/articles/top-ai-agent-evaluation-tools-2026)
7. [Roadmap to Mastering Agentic AI Design Patterns — MachineLearningMastery](https://machinelearningmastery.com/the-roadmap-to-mastering-agentic-ai-design-patterns/)
8. [Best Multi-Agent Frameworks 2026 — gurusup](https://gurusup.com/blog/best-multi-agent-frameworks-2026)
9. [2026 AI Agent Framework Showdown — QubitTool](https://qubittool.com/blog/ai-agent-framework-comparison-2026)
10. [Real Python — Python MCP Server tutorial](https://realpython.com/python-mcp/)

---

> **Read order if you're picking this up cold:** [`README.md`](../README.md) → [`docs/WALKTHROUGH.md`](WALKTHROUGH.md) → this file → start Section 6 checklist.
