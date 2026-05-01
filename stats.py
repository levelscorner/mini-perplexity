"""Stats tracking — aggregate tool-call counts/duration/success-fail for the dashboard.

Persistence: a single JSON file at <ROOT>/stats.json. Atomic write via
tempfile + os.replace so concurrent web/agent requests don't race.

Tracked per tool:
    count_total, count_ok, count_fail
    duration_ms_total, duration_ms_max, last_called_at, last_error

Plus run-level aggregates:
    total_higgsfield_credits_used  (best-effort, when render_news_card or
                                     research_persona report it via card.model_used)
    chat_turns, chat_cost_in_tokens, chat_cost_out_tokens
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from threading import Lock
from typing import Any


ROOT = Path(__file__).resolve().parent
STATS_PATH = ROOT / "stats.json"
_LOCK = Lock()


@dataclass
class ToolStat:
    count_total: int = 0
    count_ok: int = 0
    count_fail: int = 0
    duration_ms_total: float = 0.0
    duration_ms_max: float = 0.0
    last_called_at: str | None = None
    last_error: str | None = None


@dataclass
class Stats:
    tools: dict[str, dict] = field(default_factory=dict)   # tool_name → ToolStat dict
    chat_turns: int = 0
    chat_in_tokens: int = 0
    chat_out_tokens: int = 0
    cards_rendered_ok: int = 0
    cards_rendered_fail: int = 0
    started_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))


def _load() -> Stats:
    if not STATS_PATH.exists():
        return Stats()
    try:
        data = json.loads(STATS_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return Stats()
    s = Stats()
    s.tools = data.get("tools", {})
    s.chat_turns = data.get("chat_turns", 0)
    s.chat_in_tokens = data.get("chat_in_tokens", 0)
    s.chat_out_tokens = data.get("chat_out_tokens", 0)
    s.cards_rendered_ok = data.get("cards_rendered_ok", 0)
    s.cards_rendered_fail = data.get("cards_rendered_fail", 0)
    s.started_at = data.get("started_at", s.started_at)
    return s


def _save(s: Stats) -> None:
    """Atomic write via tempfile.replace."""
    payload = asdict(s)
    fd, tmp = tempfile.mkstemp(prefix="stats.", suffix=".tmp", dir=str(ROOT))
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(payload, f, indent=2, default=str)
        os.replace(tmp, STATS_PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def record_tool_call(name: str, duration_ms: float, *, ok: bool,
                     error: str | None = None) -> None:
    """Append a tool-call observation to stats.json."""
    with _LOCK:
        s = _load()
        cur = ToolStat(**s.tools.get(name, {})) if name in s.tools else ToolStat()
        cur.count_total += 1
        if ok:
            cur.count_ok += 1
        else:
            cur.count_fail += 1
            cur.last_error = (error or "")[:280]
        cur.duration_ms_total += duration_ms
        cur.duration_ms_max = max(cur.duration_ms_max, duration_ms)
        cur.last_called_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        s.tools[name] = asdict(cur)
        if name == "render_news_card":
            if ok:
                s.cards_rendered_ok += 1
            else:
                s.cards_rendered_fail += 1
        _save(s)


def record_chat_turn(in_tokens: int, out_tokens: int) -> None:
    with _LOCK:
        s = _load()
        s.chat_turns += 1
        s.chat_in_tokens += in_tokens
        s.chat_out_tokens += out_tokens
        _save(s)


def get_stats() -> dict[str, Any]:
    """Return all current stats as a dict ready for JSON."""
    s = _load()
    payload = asdict(s)
    # Compute derived: avg_duration_ms per tool
    for name, ts in payload["tools"].items():
        cnt = max(1, ts.get("count_total", 0))
        ts["duration_ms_avg"] = round(ts.get("duration_ms_total", 0) / cnt, 1)
    payload["totals"] = {
        "tool_calls": sum(t.get("count_total", 0) for t in payload["tools"].values()),
        "tool_calls_ok": sum(t.get("count_ok", 0) for t in payload["tools"].values()),
        "tool_calls_fail": sum(t.get("count_fail", 0) for t in payload["tools"].values()),
        "tool_seconds_total": round(
            sum(t.get("duration_ms_total", 0) for t in payload["tools"].values()) / 1000,
            1,
        ),
    }
    return payload


def reset() -> None:
    with _LOCK:
        if STATS_PATH.exists():
            STATS_PATH.unlink()
