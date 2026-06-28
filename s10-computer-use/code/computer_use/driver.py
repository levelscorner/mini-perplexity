"""Thin Python wrapper around cua-driver — the substrate for the S10 skill.

We shell out to `~/.local/bin/cua-driver call <tool> <json>` via subprocess.
The daemon (cua-driver serve) holds the element-index cache; every call
goes through it, so element_index from a SCAN is still valid on the next
ACT call in the same turn.

Two guarantees we add over raw shelling:
1. `call()` raises on the documented silent-failure shape
   (`element_count: 0` + empty `tree_markdown`) so the cascade can escalate
   instead of trying to address a non-existent index.
2. `scan(...)` always activates the app before reading the AX tree, so
   the background-launch trap (LaunchServices doesn't steal focus →
   AX tree is the empty system menu bar) doesn't bite on first scan.

This wrapper has zero LLM calls. It's the perception+action substrate.
The cascade in skill.py owns the LLM decisions.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

CUA_BIN = Path.home() / ".local" / "bin" / "cua-driver"


class CuaError(RuntimeError):
    """cua-driver returned a JSON `error` field or a non-zero exit code."""


class PermissionsError(CuaError):
    """get_window_state returned element_count=0 → TCC grants missing."""


def call(tool: str, args: dict | None = None, *, timeout: float = 20.0) -> dict:
    """Invoke `cua-driver call <tool> <json>` and parse the JSON response.

    Raises CuaError on driver-level failure. Returns the parsed result on
    success. Caller decides what to do with `element_count: 0` (which is
    a soft failure — see scan() for the standard guard).
    """
    payload = json.dumps(args or {})
    proc = subprocess.run(
        [str(CUA_BIN), "call", tool, payload],
        capture_output=True, text=True, timeout=timeout,
    )
    if proc.returncode != 0:
        raise CuaError(
            f"cua-driver call {tool} exit={proc.returncode}\n"
            f"stderr: {proc.stderr.strip()[:400]}"
        )
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise CuaError(f"cua-driver returned non-JSON on {tool}: {proc.stdout[:200]}") from e
    if isinstance(out, dict) and out.get("error"):
        raise CuaError(f"cua-driver {tool} reported error: {out['error']}")
    return out


def ensure_daemon() -> None:
    """Start `cua-driver serve` if it's not already running. Idempotent."""
    try:
        proc = subprocess.run(
            [str(CUA_BIN), "status"], capture_output=True, text=True, timeout=5
        )
        if proc.returncode == 0:
            return
    except FileNotFoundError as e:
        raise CuaError(
            f"cua-driver not found at {CUA_BIN}. Install: "
            "/bin/bash -c \"$(curl -fsSL https://raw.githubusercontent.com/trycua/cua/main"
            "/libs/cua-driver/scripts/install.sh)\""
        ) from e
    # Spawn the daemon and give it a moment.
    subprocess.Popen(
        [str(CUA_BIN), "serve"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    for _ in range(10):
        time.sleep(0.3)
        if subprocess.run([str(CUA_BIN), "status"], capture_output=True).returncode == 0:
            return
    raise CuaError("cua-driver serve failed to come up within 3s")


def launch_and_activate(bundle_id: str, *, electron_port: int | None = None) -> tuple[int, int]:
    """Launch an app, optionally with Electron debugging, then activate it
    via AppleScript (LaunchServices on macOS doesn't steal focus, so the
    AX tree of a background-launched app is empty — § 6.1 of the guide).

    Returns (pid, window_id) for the first top-level window.
    """
    launch_args: dict[str, Any] = {"bundle_id": bundle_id}
    if electron_port is not None:
        launch_args["electron_debugging_port"] = electron_port
    info = call("launch_app", launch_args, timeout=15)
    pid = info["pid"]

    # macOS-only: activate via AppleScript so the window realises in AX.
    # Translate bundle_id → app name (heuristic — "com.apple.Calculator" → "Calculator").
    app_name = bundle_id.split(".")[-1].capitalize()
    try:
        subprocess.run(
            ["osascript", "-e", f'tell application "{app_name}" to activate'],
            check=True, timeout=5, capture_output=True,
        )
        time.sleep(0.8)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass  # not fatal — some apps don't expose AppleScript activation

    # Find a window for this pid.
    windows = call("list_windows", {})
    for w in windows.get("windows", []):
        if w.get("pid") == pid:
            return pid, w["window_id"]
    raise CuaError(f"no window found for pid={pid} after launch+activate")


def scan(pid: int, window_id: int, *, query: str | None = None,
         capture_mode: str = "ax") -> dict:
    """SCAN phase: get_window_state. Raises PermissionsError on empty tree.

    `query` filters the rendered markdown (case-insensitive substring) to
    keep the LLM's context small; element_index values are preserved.
    """
    args: dict[str, Any] = {"pid": pid, "window_id": window_id, "capture_mode": capture_mode}
    if query:
        args["query"] = query
    state = call("get_window_state", args, timeout=15)
    if state.get("element_count", 0) == 0:
        raise PermissionsError(
            "cua-driver returned an empty AX tree. Check: "
            "(1) Accessibility grant for com.trycua.driver "
            "(2) Screen Recording grant if capture_mode != 'ax' "
            "(3) app was actually activated (AppleScript tell ... to activate) "
            "(4) Electron apps need launch with electron_debugging_port + page tool"
        )
    return state


def click_index(pid: int, window_id: int, element_index: int) -> dict:
    """ACT phase: click an element by its turn-scoped index from the last scan."""
    return call("click", {"pid": pid, "window_id": window_id, "element_index": element_index})


def click_xy(pid: int, window_id: int, x: int, y: int) -> dict:
    """Layer-3 fallback: click by (x, y) window-local pixels."""
    return call("click", {"pid": pid, "window_id": window_id, "x": x, "y": y})


def press_key(key: str, *, modifiers: list[str] | None = None) -> dict:
    """Single key (with optional modifiers like ['cmd','shift'])."""
    args: dict[str, Any] = {"key": key}
    if modifiers:
        args["modifiers"] = modifiers
    return call("press_key", args)


def type_text(text: str) -> dict:
    """Type a string into the focused field (more reliable than press_key for long strings)."""
    return call("type_text", {"text": text})


def page_call(pid: int, action: str, **kwargs) -> dict:
    """Electron escape hatch: drive the embedded Chromium via CDP.

    Requires the app was launched with electron_debugging_port. action ∈
    {click, type, evaluate, navigate, wait_for}. kwargs go straight through.
    """
    args: dict[str, Any] = {"pid": pid, "action": action}
    args.update(kwargs)
    return call("page", args, timeout=20)


def screenshot(pid: int, window_id: int, output_path: str) -> dict:
    """Capture a window as PNG (needs Screen Recording grant)."""
    return call("get_window_state", {
        "pid": pid, "window_id": window_id, "capture_mode": "vision",
        "save_to": output_path,
    }, timeout=15)


def start_recording(output_dir: str) -> dict:
    """Record every subsequent tool call into a trajectory directory."""
    return call("start_recording", {"output_dir": output_dir})


def stop_recording() -> dict:
    return call("stop_recording", {})
