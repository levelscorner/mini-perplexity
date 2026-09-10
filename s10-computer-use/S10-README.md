# Session 10 — Computer-Use Agent

> EAG V3 S10 assignment. **Late resubmission** — original deadline Sun
> Jun 21 12:06 AM IST; "Resubmission allowed" per the assignment page
> rubric. Submitted with the late mark accepted.
>
> Branch: `s10/computer-use` of
> `github.com/levelscorner/mini-perplexity`. See also the upstream
> learning notes at
> `levelscorner/eva3:docs/REVISION-S4-S9.md`.

## What we built

One new skill — `computer_use` — that drops into the **same S9
runtime** (orchestrator unchanged) and drives real desktop apps through
a four-layer cascade over [`trycua/cua`'s
`cua-driver`](https://github.com/trycua/cua):

```
Layer 1  — extract           AX text directly, no clicks, no LLM       ($0)
Layer 2a — deterministic     known hotkey sequences (Calculator)       ($0)
Layer 2b — a11y + judge      scan → cheap text LLM → act → verify      1 text LLM call/turn (cost unmeasured for S10; the S9 runs on the same gateway billed $0.000000 on free-tier Gemini)
Layer 3  — vision            screenshot + V9 /v1/vision → click x,y    dollars
```

Plus a **precondition** layer above all four for TCC permissions; on
empty AX tree we surface `error_code="precondition_failed"` and refuse
to proceed (rather than silently attempting clicks against an empty
cache).

## How it plugs in

The S9 orchestrator (`flow.py`) is **byte-identical** to S9. The two
changes:

1. `prompts/computer_use.md` — the Layer 2b judge prompt.
2. `computer_use/` package (`driver.py`, `skill.py`, `__init__.py`).
3. `agent_config.yaml` — one new entry, same shape as `browser`.
4. `skills.py` — one new dispatch branch:

```python
if skill.name == "computer_use":
    from computer_use.skill import ComputerUseSkill
    sk = ComputerUseSkill(artifacts_root=…, session=…)
    result = await sk.run(node_spec)
    return result, rendered
```

The S9 hard rule (**no orchestrator modification**) is honoured.

## The three tasks

The assignment requires three tasks satisfying:
- ≥ 1 task uses vision,
- ≥ 1 task uses the Electron `page` path,
- ≥ 1 task completes with zero vision calls.

These three:

| # | Task | Bundle | Path | Vision? |
|---|---|---|---|---|
| **A** | Compute `47 × 83` in Calculator and read the display | `com.apple.calculator` | **2a deterministic** | No |
| **B** | Open Command Palette in VS Code (Cmd+Shift+P) via the AX tree | `com.microsoft.VSCode` (with `electron_debugging_port=9222`) | **a11y** | No |
| **C** | Click the king piece on its starting square in macOS Chess | `com.apple.Chess` | **vision** | **Yes** |

A covers the "≥ 1 zero-vision" requirement in code. **The "Electron page path" requirement is NOT met.** `driver.page_call()` exists but has zero call sites, and `_run_a11y()` accepts `electron_port` and ignores it — Task B would drive VS Code through the ordinary AX scan, not the Electron `page` tool. C covers the vision requirement in code (also unrun).
C covers the vision requirement.

## Worked output

### Task A — Calculator (Layer 2a)

```
Not run. `uv run python run_s10_tasks.py calc` has never completed — there is no stdout to show.
```

No trajectory exists — the task has not been run, and `state/` is gitignored.

### Task B — VS Code Command Palette (a11y)

```
Not run. `uv run python run_s10_tasks.py vscode` has never completed — there is no stdout to show, and the Electron `page` path this task was meant to exercise is unimplemented.
```

The Electron `page` path is **not implemented**. `driver.page_call()` is defined at `computer_use/driver.py:169` but has zero call sites, and `_run_a11y()` receives `electron_port` and never uses it. Launching with `electron_debugging_port` opens the CDP port; nothing in this repo connects to it. Wiring `page_call` into the a11y layer when a vanilla AX scan on an Electron window returns one opaque `AXWebArea` is the missing piece.
is set on launch — see `cua-driver`'s § 7.2 for why a vanilla AX scan
on an Electron window returns one opaque `AXWebArea`.

### Task C — Chess (vision)

```
Not run. `uv run python run_s10_tasks.py game` has never completed — no screenshot was taken, `/v1/vision` was never called, and no click was dispatched.
```

Layer 3 triggers because the Chess board renders pieces as bitmaps with
no per-piece AX nodes. The skill takes one screenshot, sends it +
`{"goal": "click the king piece on its starting square"}` to V9's
`POST /v1/vision`, parses the returned `(x, y)`, and dispatches
`click {pid, window_id, x, y}`.

## Honest limits this submission ships with

1. **Not demoed — nothing here was run.** cua-driver was never installed on the machine that produced this commit, none of the three tasks was executed, and no trajectory, screenshot, or log from a run is committed on this branch. What ships is the code and the design. The code is macOS-only by construction (bundle_ids plus AppleScript activation); a Linux/Windows port would need parallel `_launch_and_activate` paths.
   binary ships cross-platform but bundle_ids and AppleScript activation
   are Mac-specific; a Linux/Windows port would need parallel
   `_launch_and_activate` paths.
2. **Late.** Original deadline Sun Jun 21 12:06 AM IST; submitting Sun
   Jun 28. The rubric ("Resubmission allowed") explicitly allows this,
   so this README treats the late mark as accepted rather than tries to
   hide it.
3. **Vision layer is single-turn.** Real production would iterate
   set-of-marks + VLM until the goal is satisfied; ours takes ONE
   3. **Vision layer is single-turn and unverified.** Real production would iterate set-of-marks + VLM until the goal is satisfied; ours takes ONE screenshot and dispatches ONE click, then re-scans. It has never been run — against Chess or anything else — so whether one click suffices for that task is untested.
   enough for the Chess task; not good enough for a multi-step game.
4. **No Layer 1 task in the worked set.** Layer 1 is implemented but
   the assignment-list doesn't require it; the three picks cover the
   three explicit constraints. The skill DOES try Layer 1 first when
   the goal contains "read"/"extract"/"value of" keywords — see
   `_run_extract` in `skill.py`.

## Setup & run

```bash
# 1. Install cua-driver + grant TCC permissions
#    See INSTALL.md (one terminal command + two system dialogs)

# 2. Boot the prerequisites in one command
cd code
./boot_s10.sh

# 3. Run all 3 tasks (or one by name)
uv run python run_s10_tasks.py             # calc + vscode + game
uv run python run_s10_tasks.py calc        # just A
```

State directories (`state/sessions/`) are excluded from git per the
State directories (`state/sessions/`) are gitignored (`code/.gitignore:7`). There is no `evidence/` directory on this branch — no trajectories were captured, because the tasks were not run.

## Relationship to other submissions

- **S6** — `mini-perplexity` `s06/agentic-architecture` (HEAD `da63f3e`)
- **S7** — `s07/memory-retrieval`
- **S8** — `s08/dag-orchestration`
- **S9** — `s09/browser-agents`
- **S10 (this)** — built on the S9 runtime; same orchestrator, one new
  skill, one new MCP substrate (cua-driver). The S9 promise honoured
  one more time.
- **S11** — team assignment (Microsoft Teams adapter), see
  `theschoolofai/glc_v1#12`.
