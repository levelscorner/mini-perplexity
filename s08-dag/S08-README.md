# Session 8 — Multi-Agent DAG Orchestration

> EAG V3 S8 assignment. Submitted on top of the provided
> **Session8StartingCodePatched** runtime (`code/`). The two pieces of
> code WE write — the Coder skill prompt and a new `fact_checker` skill —
> are documented in this README. All runtime modifications are
> additive; `flow.py`, `recovery.py`, `persistence.py`, `skills.py`
> are byte-identical to the provided package. The S6/S7 modules
> (`memory.py`, `perception.py`, `decision.py`, `action.py`,
> `vector_index.py`, `artifacts.py`, `mcp_server.py`) are byte-identical
> to S7 — same loop carried forward.
>
> Branch: `s08/dag-orchestration` of
> `github.com/levelscorner/mini-perplexity`. See also the per-session
> learning note `docs/S08-DAG-ORCHESTRATION.md` in the upstream
> `levelscorner/eva3` repo.

## What we changed

### 1. `prompts/coder.md` — filled the stub
The provided `coder.md` was a stub. We wrote a prompt that emits
`{"code": "<python>", "rationale": "..."}` with five enforceable rules:
self-contained stdlib only, print one line per answer dimension, parse
numbers in code rather than eyeball them, no env / argv reads, also print
the comparison conclusion so the Formatter doesn't re-do reasoning. The
prompt has a worked example (populations → closest-pair via
`itertools.combinations`) and a self-check section.

### 2. `prompts/fact_checker.md` + `agent_config.yaml` entry — the new skill
**`fact_checker`** is not in the provided catalogue. It verifies factual
claims against live web evidence, emitting one verdict per claim with a
sourced quote:

```yaml
fact_checker:
  prompt: prompts/fact_checker.md
  tools_allowed: [web_search, fetch_url]
  temperature: 0.2
  max_tokens: 1500
  description: Verifies factual claims via live web evidence;
               emits per-claim verdicts with sources.
```

`planner.md` is taught about it with a "when to use fact_checker" rule:
**only** when the user explicitly asks to *verify* a factual statement;
open-ended research still routes to `researcher`. This keeps Planner
honest about which lane each query belongs in.

The output schema is a list of verdicts, each one of three values —
`supported` / `refuted` / `inconclusive`. The third is the honest middle:
"searched in good faith, evidence ambiguous." A real LLM judge that
forces a binary call against ambiguous evidence is the mechanism
through which "verdict quality" failure modes (e.g. the lesson's 4-6-4
syllable rubber stamp) leak in. The third value is the fix.

## Worked queries — terminal output

### Query 1 — `hello` (smallest DAG)
```
session s8-517cc46e  ─  query: hello
[n:1] planner            complete (2.2s)
[n:2] formatter          complete (3.3s)
FINAL: Hello! How can I help you today?
```
Two nodes. Planner correctly recognises no research is needed and routes
straight to Formatter.

### Query 2 — Populations of London/Paris/Berlin (**parallel fan-out**)
```
session s8-ea252337
[n:1] planner            complete (2.7s)
[n:2] researcher         complete (28.3s)
[n:3] researcher         complete (45.4s)
[n:4] researcher         complete (37.3s)
[n:5] formatter          complete (3.2s)

FINAL: London ≈ 9.1M, Berlin ≈ 3.7M, Paris ≈ 2.06M.
Paris and Berlin are the closest in population size.
```
**The three Researchers ran concurrently.** Sum-of-elapsed = 111s;
wall-clock at the parallel layer = max(28.3, 45.4, 37.3) = 45.4s.
**Parallel speedup ≈ 2.4× over a serial run.** This satisfies S8
assignment item #2 — "≥3 independent sub-tasks the Planner emits as
concurrent nodes; the parallel layer's wall-clock is the maximum of the
branches, not the sum."

### Query 3 — Coder + SandboxExecutor (**precise computation**)
Query: *"If I invest 47215 USD at 6.4% annual compound interest
(compounded monthly), what is the exact balance after 7 years 5 months?
Use coder + sandbox to compute the exact figure."*
```
session s8-18df3dbc
[n:1] planner            complete (3.7s)
[n:2] coder              complete (4.9s)
[n:3] sandbox_executor   complete (0.0s)
[n:4] formatter          complete (1.6s)
[n:5] sandbox_executor   complete (0.0s)

FINAL: The exact balance after 7 years and 5 months, with an investment
of 47,215 USD at 6.4% annual compound interest (compounded monthly),
is 75,801.42 USD.
```
The Coder emitted a Python program; the SandboxExecutor ran it as a
subprocess and printed the figure. The Formatter quoted the printed
number verbatim. **The Coder/SandboxExecutor diamond is the architecture
that grounds a precise arithmetic claim in real execution** — the
Formatter never has to "know" what `(1 + 0.064/12)^89 × 47215` evaluates
to. That's S8 assignment item #4 (Coder filled + demonstrated on a query
needing real computation).

### Query 4 — `fact_checker` (the new skill in action)
Query: *"Verify these two claims and tell me which are correct:
(1) The Eiffel Tower was completed in 1889. (2) Mount Everest is 9,848
metres tall."*
```
session s8-f76b4f34
[n:1] planner            complete (3.2s)
[n:2] fact_checker       failed   (0.0s)  err=exception in tool group
  ↪ recovery (upstream_failure): planner node n:5 queued for n:2
[n:3] fact_checker       complete (18.3s)
[n:5] planner            complete (10.6s)
```
What this demonstrates (S8 assignment item #5 + a free demonstration
of item #3):

- The Planner DID route to `fact_checker`. Our new skill is reachable
  from the catalogue and the Planner's prompt teaches it correctly.
- `fact_checker` called `web_search` and `fetch_url` against
  `en.wikipedia.org/w/api.php`, `grokipedia.com`, and Google to verify
  the Eiffel Tower and Everest claims (visible in the gateway log).
- A transient `TaskGroup` exception killed n:2 → the **recovery
  classifier** in `recovery.py` triaged it as `upstream_failure` →
  **the orchestrator spliced in a recovery Planner (n:5)** → which
  re-queued `fact_checker` (n:3) → which completed in 18.3s.
- This is exactly the splice mechanism the lesson describes for a
  Critic-fail recovery, exercised here via a transient upstream
  failure. The mechanism (recovery splice) is mechanism; the verdict
  (would the Critic say *pass* or *fail*) is policy.

## On the Critic-verdict requirement (assignment #3)

The provided `agent_config.yaml` declares `distiller: critic: true` so
the orchestrator auto-splices a Critic between any Distiller and its
successor (verified by the provided `tests/test_critic_autoinsert.py`,
all green). The S9 lesson documents the bug we'd otherwise have hit: a
pre-planned Distiller→Formatter edge would *not* have triggered the
auto-insert because the child wasn't dynamically `added`. The S9 patch
to `flow.py:153-167` reads outgoing edges instead and splices on
every non-Critic outgoing edge. We inherit the S9-patched flow.py.

Reproducible pass-and-fail with verdict change across two runs is the
piece we did not engineer this submission round — the queries above
exercise the splice mechanism via the recovery path (transient-failure
→ Planner re-plan) but not the Critic's binary verdict. The plumbing
is wired; the policy work is a separate exercise (the lesson's own
"verdict quality vs mechanism" point). For a clean Critic
pass-and-fail demo we'd give the Critic a tool (e.g. a
`count_syllables` MCP tool) and ask for a syllable-strict haiku — that's
the S9 forward pointer ("Critic with tools").

## Files we wrote (the diff vs the provided package)

```
prompts/coder.md            — filled the stub (was: "STUB — STUDENT ASSIGNMENT")
prompts/fact_checker.md     — NEW (the new skill's prompt)
agent_config.yaml           — +9 lines (fact_checker entry)
prompts/planner.md          — +6 lines (fact_checker rule)
```

Everything else in `code/` is byte-identical to
`Session8StartingCodePatched`. Run `git log code/` on the patched
package vs ours to verify.

## Setup & run

```bash
# 1. V8 gateway on :8108
cd llm_gatewayV8 && ./run.sh

# 2. Run a query
cd code && uv sync && uv run python flow.py "<query>"

# 3. Inspect a session afterwards
ls state/sessions/                     # find the session id
python3 -c "import json; print(json.dumps(json.load(open('state/sessions/<sid>/graph.json')), indent=2))"
```

State is excluded from git per the rubric (`state/sessions/`,
`state/artifacts/`, `usage.json`).

## Honest limits this submission ships with

1. **The Critic pass-and-fail demo is not provided.** See the Critic
   section above for what's wired vs what's exercised.
2. **`fact_checker`'s first run hit a TaskGroup exception** before the
   recovery succeeded. Real submission would track that exception (it's
   in the gateway log) and tighten the prompt to avoid it.
3. **Verdict quality vs mechanism is unsolved here too.** Our
   fact_checker prompt asks the model to ground every supported/refuted
   verdict in a real URL. A more rigorous version would constrain the
   model to *only* `supported`/`refuted`/`inconclusive` via a strict
   JSON schema and validate the URL is reachable before returning.

## Relationship to other submissions

- **S6:** `mini-perplexity` branch `s06/agentic-architecture` (HEAD
  `da63f3e`).
- **S7:** `mini-perplexity` branch `s07/memory-retrieval` (our own port
  of vector memory onto S6, including the dual-writer fix in
  `memory.py:_persist()`).
- **S8 (this):** runs on the provided patched runtime; our writeup is in
  this README and our diff is the four files listed above.
- **S9 (next):** Browser skill on top of this S8 runtime.
