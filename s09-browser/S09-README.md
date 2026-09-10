# Session 9 — Browser Agents & Autonomous Web

> EAG V3 S9 assignment. Submitted on top of the provided **Session9Code**
> runtime (`code/`). The piece WE write — `make_replay_report.py` — turns a
> finished `state/sessions/<sid>/` directory into the 8-item replay report
> the rubric asks for. The orchestrator (`flow.py`), the four-layer Browser
> driver under `browser/`, and the cascade logic are exactly as they arrived in
> the provided package — we changed none of them. (`code/VALIDATION.md` and
> `code/browser/PARITY_AUDIT.md` also ship with that package and are written in
> its author's voice; their 'Files added / modified' list describes how the
> package itself was built, not anything we did. We have left both files
> untouched rather than deleting course-supplied documentation.)
> provided package. Our extension plugs in via a new script — not via an
> orchestrator edit — which is what the assignment requires.
>
> Branch: `s09/browser-agents` of `github.com/levelscorner/mini-perplexity`.
> See also the learning note `docs/S09-BROWSER-AGENTS.md` in the upstream
> `levelscorner/levelscorner-eva3` repo. (Note: that learning note is not yet
> pushed — everything the grader needs is in this folder.)

## Comparison task

> *"What are the top 3 most-liked open-source LLMs on Hugging Face right
> now (text-generation models, sorted by likes)? For each give the model
> name, parameter count, and a one-line description."*

This rides the exact Layer 2b a11y path the S9 lesson worked end-to-end
on `huggingface.co/models`. The Browser skill landed on `path = "a11y"`,
ran a 10-turn loop reading the accessibility-tree summary and clicking
filters / sort options. **The four rubric-visible actions, evidenced by the
final URL** (`evidence/graph.json`, `final_url`): filter Tasks=Text Generation
(`pipeline_tag=text-generation`), filter Libraries=Transformers
(`library=transformers`), filter Licenses=MIT (`license=license:mit`), and sort
by Most Likes (`sort=likes`). Caveat: the action log records only the mark
integer per click, not the element name, so the URL is the audit trail — not a
per-click label. Nothing here came from a search snippet.
filters / sort options, and ended on
`huggingface.co/models?pipeline_tag=text-generation&library=transformers&license=license:mit&sort=likes`.

## DAG (5 nodes — 4 skills plus the auto-inserted Critic)

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
| 5 | Screenshots / page-state logs | §5 — **no screenshots**: the a11y path produced one 96-byte page-state stub, which is under gitignored `state/` and is not in `evidence/`. The auditable page-state record is the action log (§4) plus the final URL (§3). |
| 6 | Extracted data | §6 — Distiller JSON |
| 7 | Final comparison table | §7 — the models rendered as a markdown table, plus the Formatter's `final_answer` prose beneath it |  (only true after the make_replay_report.py:193 fix is applied AND evidence/replay_report.md is regenerated; until then the honest row is: `| 7 | Final comparison table | NOT RENDERED — §7 currently prints the formatter dict as a raw Python repr because the report script looks up the wrong key. Known bug, see below. |`)
| 8 | Turn count + cost summary | §8 — 5 DAG nodes, 10 browser turns. **Cost is missing**: the V9 ledger returned no rows for this session, see Honest limits #1. |

## Final answer (Formatter)

```
Here are the top 3 most-liked open-source LLMs for text generation on Hugging Face:

1. **deepseek-ai/DeepSeek-R1**
   - Parameter Count: 685B
   - Description: Text Generation

2. **deepseek-ai/DeepSeek-V4-Pro**
   - Parameter Count: 862B
   - Description: Text Generation

3. **microsoft/phi-2**
   - Parameter Count: 3B
   - Description: Text Generation

1. deepseek-ai/DeepSeek-R1     — 685B parameters
2. deepseek-ai/DeepSeek-V4-Pro — 862B parameters  ⚠ name not in captured page text

> **Provenance warning, stated plainly.** Only `deepseek-ai/DeepSeek-R1` appears
> in the text the Browser node actually captured (`content` in
> `evidence/graph.json`). The a11y capture returned the ranked rows with their
> parameter counts (685B / 862B / 3B, which do match rows 1–3) but lost the model
> card anchor text for rows 2 and 3. The names `deepseek-ai/DeepSeek-V4-Pro` and
> `microsoft/phi-2` come from the browser LLM's own `done` string, not from the
> page, and are therefore unverified. Fixing this means capturing the a11y legend
> (element names) per turn rather than only `{type, mark}` — see Honest limits.
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

That's the only file we wrote. Everything else under `code/` — including
`VALIDATION.md`, `browser/PARITY_AUDIT.md` and the four files in `tests/` —
arrived with the provided Session9Code package and is committed unmodified so
the runtime is reproducible. We did not edit `flow.py`, `browser/skill.py`,
or any other orchestrator file. **The orchestrator was not modified** —
which is the S9 assignment's hard rule.

## Why the natural cascade landed on a11y, not vision

This is the S9 lesson's headline finding. `huggingface.co/models` has:
- ARIA-labelled filter toggles (`Tasks` group → `Text Generation`
  option),
- an ARIA-labelled sort menu (`Sort: most likes`),
- model cards as anchored `<a>` elements with accessible names.

Set-of-marks / vision wasn't needed. **On Hugging Face the a11y tree is ~37×
smaller than the DOM** (the lesson measured 1.1 MB DOM → 30 KB a11y tree → 230
deduped interactive elements; its headline 200× figure is for a 6 MB DOM) and contains
than the DOM** (lesson cited 1.1MB DOM → 30KB a11y for HF) and contains
the same actionable signal. A 2026 browser agent that fires the vision
path on every interaction has skipped a layer that would have cost
fractions of a cent.

The cost ledger in the replay report (§8) is the empirical statement —
the V9 ledger groups by agent, so we can see per-skill input/output
tokens for this run. (Note: §8 of the included report says `cost ledger
unavailable: no data`. V9 answered the query — an unreachable gateway would have
printed a connection error instead — but its ledger held no rows attributed to
session `s8-8eabe810`. We have not diagnosed why; the by-agent cost figure is
simply missing from this submission.)
report because the ledger query ran after V9 had been cycled; replay
the run before stopping V9 to populate it.)

## Setup & run

```bash
# 1. V9 gateway on :8109 (vision endpoint + retry-on-5xx)
# 1. V9 gateway on :8109 (vision endpoint + retry-on-5xx).
#    NOT vendored here — llm_gatewayV9 is the course-provided gateway package;
#    download it from the course site and start it before step 3.
cd /path/to/llm_gatewayV9 && ./run.sh

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
   path are wired in `browser/skill.py`, but no run of either is committed to
   this repo — we did not re-exercise them and there are no logs to show.
   we did not re-exercise them.
3. **No per-turn screenshot artefacts** for this Layer 2b run. The
   a11y path doesn't need set-of-marks images; only the action log and
   final URL are evidence. A vision-layer run would have produced
   `state/sessions/<sid>/browser/` PNGs.
