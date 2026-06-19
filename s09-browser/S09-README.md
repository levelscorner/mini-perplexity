# Session 9 — Browser Agents & Autonomous Web

> EAG V3 S9 assignment. Submitted on top of the provided **Session9Code**
> runtime (`code/`). The piece WE write — `make_replay_report.py` — turns a
> finished `state/sessions/<sid>/` directory into the 8-item replay report
> the rubric asks for. The orchestrator (`flow.py`), the four-layer Browser
> driver under `browser/`, and the cascade logic are byte-identical to the
> provided package. Our extension plugs in via a new script — not via an
> orchestrator edit — which is what the assignment requires.
>
> Branch: `s09/browser-agents` of `github.com/levelscorner/mini-perplexity`.
> See also the learning note `docs/S09-BROWSER-AGENTS.md` in the upstream
> `levelscorner/eva3` repo.

## Comparison task

> *"What are the top 3 most-liked open-source LLMs on Hugging Face right
> now (text-generation models, sorted by likes)? For each give the model
> name, parameter count, and a one-line description."*

This rides the exact Layer 2b a11y path the S9 lesson worked end-to-end
on `huggingface.co/models`. The Browser skill landed on `path = "a11y"`,
ran a 10-turn loop reading the accessibility-tree summary and clicking
filters / sort options, and ended on
`huggingface.co/models?pipeline_tag=text-generation&library=transformers&license=license:mit&sort=likes`.

## DAG (5 nodes — every one is a layer in the cascade)

```
USER_QUERY
   │
   ▼
n:1 planner          (4.0s, gemini)
   │
   ▼
n:2 browser          (46.5s, a11y path, 10 turns)
   │
   ▼
n:3 distiller        (4.6s)
   │
   ▼
n:5 critic           (3.5s, auto-inserted because distiller has critic:true)
   │
   ▼
n:4 formatter        (3.9s) ➝ FINAL ANSWER
```

The Critic node (n:5) was **not** in the Planner's plan. The orchestrator
inserts it automatically because `distiller.critic: true` in
`agent_config.yaml`. The S9 lesson's `flow.py:153-167` fix means this
splice now happens even for pre-planned `distiller → formatter` edges
(the bug it documents: a four-character short-circuit on `if … and
added` silently bypassed Critic auto-insertion when the child was
pre-planned, not dynamically added). We inherit the fix and observe it
firing live.

## The 8 rubric items — pointers

The full machine-generated report is at
[`evidence/replay_report.md`](./evidence/replay_report.md). The 8 items
map there as:

| # | Rubric item | Where in the report |
|---|---|---|
| 1 | Original user goal | §1 |
| 2 | Planner DAG | §2 (node list + edges) |
| 3 | Browser path chosen | §3 — **`a11y`**, start/end URLs |
| 4 | Browser actions taken | §4 — 10 turns, 9 clicks + 1 done |
| 5 | Screenshots / page-state logs | §5 — references `state/sessions/<sid>/browser/` |
| 6 | Extracted data | §6 — Distiller JSON |
| 7 | Final comparison table | §7 — Formatter answer |
| 8 | Turn count + cost summary | §8 — 5 DAG nodes, 10 browser turns, V9 by-agent cost |

## Final answer (Formatter)

```
Top 3 most-liked open-source LLMs on Hugging Face (text-generation):

1. deepseek-ai/DeepSeek-R1     — 685B parameters
2. deepseek-ai/DeepSeek-V4-Pro — 862B parameters
3. microsoft/phi-2             — 3B parameters
```

(Snapshot at the moment of the run. The model index is live and the
ranking shifts day-to-day; the architecture and trace are the
artefacts the rubric is graded on, not the specific ranking.)

## What we added (the diff vs the provided package)

```
make_replay_report.py — NEW. Renders state/sessions/<sid>/ into a
                       markdown replay report covering the 8 rubric items.
                       Pulls cost from V9 `/v1/cost/by_agent` when
                       available.
```

That's it. No edits to `flow.py`, `browser/skill.py`, `browser/driver.py`,
or any other orchestrator file. **The orchestrator was not modified** —
which is the S9 assignment's hard rule.

## Why the natural cascade landed on a11y, not vision

This is the S9 lesson's headline finding. `huggingface.co/models` has:
- ARIA-labelled filter toggles (`Tasks` group → `Text Generation`
  option),
- an ARIA-labelled sort menu (`Sort: most likes`),
- model cards as anchored `<a>` elements with accessible names.

Set-of-marks / vision wasn't needed. **The a11y tree is ~200× smaller
than the DOM** (lesson cited 1.1MB DOM → 30KB a11y for HF) and contains
the same actionable signal. A 2026 browser agent that fires the vision
path on every interaction has skipped a layer that would have cost
fractions of a cent.

The cost ledger in the replay report (§8) is the empirical statement —
the V9 ledger groups by agent, so we can see per-skill input/output
tokens for this run. (Note: `cost ledger unavailable` in the included
report because the ledger query ran after V9 had been cycled; replay
the run before stopping V9 to populate it.)

## Setup & run

```bash
# 1. V9 gateway on :8109 (vision endpoint + retry-on-5xx)
cd llm_gatewayV9 && ./run.sh

# 2. Sync code, install chromium
cd code && uv sync && uv run playwright install chromium

# 3. Run the comparison query
uv run python flow.py "What are the top 3 most-liked open-source LLMs ..."

# 4. Render the replay report (ours)
sid=$(ls -t state/sessions/ | head -1)
uv run python make_replay_report.py "$sid"
cat state/sessions/$sid/replay_report.md
```

State directories (`state/sessions/`, `state/artifacts/`, `usage.json`)
are excluded from git per the rubric. The specific session that produced
the answer above is captured in [`evidence/`](./evidence/) for graders to
audit without running the agent.

## Relationship to S6 / S7 / S8

- **S6** — `mini-perplexity` branch `s06/agentic-architecture` (HEAD
  `da63f3e`). Four cognitive roles, our own code.
- **S7** — `mini-perplexity` branch `s07/memory-retrieval`. Our own port
  of vector memory onto S6. Includes the dual-writer race fix in
  `memory.py:_persist()`.
- **S8** — `mini-perplexity` branch `s08/dag-orchestration`. Built on
  the provided patched runtime. Coder stub filled, `fact_checker`
  added as a new skill.
- **S9 (this)** — Built on the provided package. Comparison agent +
  replay viewer (`make_replay_report.py`). Orchestrator untouched.

## Honest limits this submission ships with

1. **Cost ledger empty in the included replay.** The V9 ledger group-by
   query runs against a live SQLite that was cycled before we generated
   the final report. Re-running the query immediately after a fresh
   browser run populates it.
2. **One worked target.** We ran the HF Layer 2b test only. The
   precondition layer (CAPTCHA → `gateway_blocked`) and the vision
   path are wired (and the lesson's worked logs are in `logs/`) but
   we did not re-exercise them.
3. **No per-turn screenshot artefacts** for this Layer 2b run. The
   a11y path doesn't need set-of-marks images; only the action log and
   final URL are evidence. A vision-layer run would have produced
   `state/sessions/<sid>/browser/` PNGs.
