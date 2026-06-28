"""S10 assignment runner — drives 3 tasks through the Computer-Use skill.

Each task picks a different layer of the cascade and records its trajectory.
Run from the s10-computer-use/code directory:

    uv run python run_s10_tasks.py            # all 3 tasks
    uv run python run_s10_tasks.py calc        # just Calculator
    uv run python run_s10_tasks.py vscode      # just VS Code (Electron)
    uv run python run_s10_tasks.py game        # just the vision task

The assignment requires: ≥1 task uses vision, ≥1 task uses the Electron
page path, ≥1 task completes with zero vision calls. These three satisfy:

  calc   → Layer 2a deterministic   (zero vision, zero LLM)
  vscode → Electron page path       (zero vision)
  game   → Layer 3 vision           (vision)
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from computer_use.skill import ComputerUseSkill
from schemas import NodeSpec

ROOT = Path(__file__).resolve().parent
SESSION_BASE = ROOT / "state" / "sessions"


TASKS: dict[str, dict] = {
    "calc": {
        "label": "S10-A: Calculator arithmetic (Layer 2a deterministic)",
        "metadata": {
            "goal": "Compute 47 * 83 in the macOS Calculator and read the displayed result.",
            "bundle_id": "com.apple.calculator",
            "force_path": "deterministic",   # exercise the hotkey recipe
        },
    },
    "vscode": {
        "label": "S10-B: VS Code Electron page path (zero vision)",
        "metadata": {
            "goal": "Open the Command Palette in VS Code (Cmd+Shift+P).",
            "bundle_id": "com.microsoft.VSCode",
            "electron_port": 9222,
            "force_path": "a11y",   # a11y picks press_key for shortcuts
        },
    },
    "game": {
        "label": "S10-C: Canvas drawing app — forces Layer 3 vision",
        "metadata": {
            # Excalidraw / Photopea / a small browser game in Safari
            # would all qualify; for the demo we open the macOS
            # Chess app which renders pieces as canvas-equivalent
            # bitmaps without addressable AX nodes for individual pieces.
            "goal": "Open the Chess app and click the king piece on its starting square.",
            "bundle_id": "com.apple.Chess",
            "force_path": "vision",
        },
    },
}


async def run_task(task_id: str, task: dict) -> dict:
    session_id = f"s10-{task_id}-{int(time.time())}"
    session_dir = SESSION_BASE / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    # Persist the goal so we can render a replay report later.
    (session_dir / "query.txt").write_text(task["metadata"]["goal"])
    record_dir = str(session_dir / "trajectory")

    meta = dict(task["metadata"])
    meta["record_dir"] = record_dir

    skill = ComputerUseSkill(
        artifacts_root=str(session_dir / "computer_use"),
        session=session_id,
    )
    node = NodeSpec(skill="computer_use", inputs=[], metadata=meta)

    print(f"\n══════════════════════════════════════════════════════════════════════")
    print(f"  {task['label']}")
    print(f"  session: {session_id}")
    print(f"  goal: {task['metadata']['goal']}")
    print(f"══════════════════════════════════════════════════════════════════════")

    started = time.time()
    result = await skill.run(node)
    elapsed = time.time() - started

    out = result.output if hasattr(result, "output") else {}
    print(f"\n  status:  {getattr(result, 'status', '?')}")
    print(f"  path:    {out.get('path', '?')}")
    print(f"  turns:   {out.get('turns', 0)}")
    print(f"  elapsed: {elapsed:.1f}s")
    if out.get("result"):
        print(f"  result:  {json.dumps(out['result'])[:300]}")
    if getattr(result, "error_code", None):
        print(f"  ERROR:   {result.error_code} — {out.get('error', '')[:200]}")

    # persist the result for the replay viewer
    (session_dir / "result.json").write_text(json.dumps({
        "task_id": task_id,
        "label": task["label"],
        "metadata": task["metadata"],
        "status": getattr(result, "status", "?"),
        "error_code": getattr(result, "error_code", None),
        "output": out,
        "elapsed_s": elapsed,
    }, indent=2, default=str))
    return {"task": task_id, "session": session_id, "status": getattr(result, "status", "?"),
            "path": out.get("path", "?"), "elapsed": elapsed}


async def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    selected = {only: TASKS[only]} if only in TASKS else TASKS

    summary = []
    for task_id, task in selected.items():
        try:
            summary.append(await run_task(task_id, task))
        except Exception as e:
            print(f"\n  CRASHED on {task_id}: {type(e).__name__}: {str(e)[:200]}")
            summary.append({"task": task_id, "status": "crashed", "error": str(e)[:200]})

    print("\n══════════════════════════════════════════════════════════════════════")
    print("  SUMMARY")
    print("══════════════════════════════════════════════════════════════════════")
    for s in summary:
        print(f"  {s['task']:8s}  status={s.get('status','?'):10s}  path={s.get('path','?'):15s}  {s.get('elapsed', 0):.1f}s")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
