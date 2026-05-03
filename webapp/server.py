"""FastAPI server that exposes Mini Perplexity over HTTP + SSE.

Routes:
    GET  /                  Chat page (static index.html)
    GET  /static/...        CSS/JS
    GET  /healthz           Liveness probe
    POST /api/run           Agent stream (SSE) — accepts {question, image_mode}
    GET  /api/stats         Runtime stats (totals + per-tool)
    GET  /api/recent-activity  Last 100 tool calls (newest first)
    POST /api/tool/<name>   Proxy for higgsfield_auth_status / start_higgsfield_auth
    GET  /api/cards/<slug>.png  Serve a saved render
    GET  /dashboard         Prefab single-page dashboard

Bridging sync agent ↔ async server: run_agent is sync (blocking on LLM
+ network). We run it in a thread; the on_event callback pushes events
into an asyncio.Queue via loop.call_soon_threadsafe. End-of-stream is
signalled by a None sentinel.

Stats + activity are recorded by wrapping every tool in TOOLS at import
time. The wrapper times each call, records to stats.py, and pushes an
event into the rolling _RECENT_ACTIVITY deque for the dashboard.
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
import uuid
from collections import deque
from pathlib import Path
from threading import Thread
from typing import Any

from fastapi import FastAPI
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# The agent core lives at the repo root. Webapp is a child package, so
# we add the parent to sys.path so `import mini_perplexity` finds it.
HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import stats as stats_mod  # noqa: E402
from mini_perplexity import run_agent  # noqa: E402
from tools import TOOLS as _RAW_TOOLS  # noqa: E402

STATIC_DIR = HERE / "static"
CARDS_DIR = REPO_ROOT / "images"
CARDS_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Tool wrapping — record stats + activity for every tool call
# ---------------------------------------------------------------------------
_RECENT_ACTIVITY: deque[dict[str, Any]] = deque(maxlen=100)


def _summarize_args(args: dict[str, Any], limit: int = 80) -> str:
    """Compact one-line summary of tool input for the activity row."""
    if not args:
        return ""
    parts: list[str] = []
    for k, v in args.items():
        s = json.dumps(v) if not isinstance(v, str) else v
        if len(s) > 30:
            s = s[:27] + "…"
        parts.append(f"{k}={s}")
    out = ", ".join(parts)
    return (out[:limit - 1] + "…") if len(out) > limit else out


def _record_activity(name: str, kwargs: dict[str, Any], duration_ms: float,
                     ok: bool, error: str | None) -> None:
    _RECENT_ACTIVITY.append({
        "name": name,
        "input": _summarize_args(kwargs),
        "status": "ok" if ok else "fail",
        "duration_ms": round(duration_ms, 1),
        "ts": time.strftime("%H:%M:%S", time.localtime()),
        "error": (error or "")[:200] if error else None,
    })


def _wrap_tool(name: str, fn):
    def wrapped(*args, **kwargs):
        start = time.monotonic()
        ok, err = True, None
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 — propagate; recorder still fires
            ok = False
            err = f"{type(e).__name__}: {e}"
            raise
        finally:
            dt = (time.monotonic() - start) * 1000
            try:
                stats_mod.record_tool_call(name, dt, ok=ok, error=err)
            except Exception:  # noqa: BLE001
                pass
            try:
                _record_activity(name, kwargs, dt, ok, err)
            except Exception:  # noqa: BLE001
                pass
    return wrapped


# Replace each entry in the agent's TOOLS dict with a wrapped version.
# Mutates _RAW_TOOLS in place — that's the same dict the agent loop reads.
for _name, _fn in list(_RAW_TOOLS.items()):
    _RAW_TOOLS[_name] = _wrap_tool(_name, _fn)


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(
    title="MINION",
    description=(
        "Research + image-gen + dashboard agent. Five tools, native "
        "Anthropic tool use, Prefab dashboard, Higgsfield image rendering."
    ),
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    """Serve the chat page."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# /api/run — SSE chat
# ---------------------------------------------------------------------------
class RunRequest(BaseModel):
    question: str
    max_iterations: int = 8
    image_mode: bool = False
    conversation_id: str | None = None
    """Opaque ID — when present, the agent is seeded with prior turns
    from this conversation. None = start a fresh conversation."""


_IMAGE_MODE_HINT = (
    "\n\n[Mode hint: the user has enabled image mode. Call the "
    "`render_image` tool with a refined, vivid prompt. Pick `panels=3` "
    "or `panels=4` if a comic strip suits the request, else `panels=1`.]"
)

# Server-side conversation store. Keyed by client-supplied conversation_id.
# Each value is the literal Anthropic Messages API list — user/assistant
# turns including tool_use + tool_result blocks. In-memory only; survives
# requests but not server restart, which is fine for this app.
_CONVERSATIONS: dict[str, list[dict]] = {}


def _maybe_emit_image(event: dict[str, Any]) -> dict[str, Any] | None:
    """If a tool_result is a render_image payload, return a synthetic image event."""
    if event.get("kind") != "tool_result":
        return None
    payload = event.get("payload") or {}
    if not isinstance(payload, dict) or payload.get("name") != "render_image":
        return None
    result = payload.get("result")
    if not isinstance(result, dict):
        return None
    t = result.get("type")
    if t == "image":
        return {
            "kind": "image",
            "iteration": event.get("iteration"),
            "payload": {
                "kind": "image",
                "slug": result.get("slug"),
                "url": result.get("url"),
                "alt": result.get("alt", ""),
            },
            "ts": event.get("ts", ""),
        }
    if t == "comic_strip":
        return {
            "kind": "image",
            "iteration": event.get("iteration"),
            "payload": {
                "kind": "comic_strip",
                "panels": result.get("panels", []),
            },
            "ts": event.get("ts", ""),
        }
    return None


@app.post("/api/run")
async def run(body: RunRequest) -> StreamingResponse:
    """Run the agent on a question, stream events as they happen.

    Threads conversation history through `conversation_id`: when the
    client supplies one, the agent is seeded with prior turns so the
    user can say "save it to my dashboard" and the agent knows what
    "it" refers to. The first turn returns a fresh server-allocated
    conversation_id via the SSE `system` event before `final`.
    """
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    question = body.question
    if body.image_mode:
        question = body.question + _IMAGE_MODE_HINT

    # Resolve conversation: client-supplied ID wins; else mint a new one.
    conv_id = body.conversation_id or uuid.uuid4().hex
    seed_messages = _CONVERSATIONS.get(conv_id, [])

    def on_event(event_dict: dict[str, Any]) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, event_dict)
        synthetic = _maybe_emit_image(event_dict)
        if synthetic is not None:
            loop.call_soon_threadsafe(queue.put_nowait, synthetic)

    def agent_thread() -> None:
        try:
            # Tell the frontend which conversation this turn lives in
            # — client should echo it back on subsequent /api/run calls.
            loop.call_soon_threadsafe(queue.put_nowait, {
                "kind": "conversation",
                "iteration": 0,
                "payload": {"conversation_id": conv_id},
                "ts": "",
            })
            result = run_agent(
                question,
                max_iterations=body.max_iterations,
                on_event=on_event,
                render_terminal=False,
                seed_messages=seed_messages,
                return_messages=True,
            )
            # When return_messages=True, run_agent returns (rc, messages).
            if isinstance(result, tuple):
                _rc, final_messages = result
                _CONVERSATIONS[conv_id] = final_messages
        except Exception as exc:  # noqa: BLE001
            loop.call_soon_threadsafe(
                queue.put_nowait,
                {
                    "kind": "error",
                    "iteration": 0,
                    "payload": f"agent thread crashed: {type(exc).__name__}: {exc}",
                    "ts": "",
                },
            )
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    Thread(target=agent_thread, daemon=True).start()

    async def stream():
        while True:
            event = await queue.get()
            if event is None:
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if event.get("kind") == "final":
                break

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# Dashboard endpoints
# ---------------------------------------------------------------------------
@app.get("/api/stats")
def api_stats() -> JSONResponse:
    payload = stats_mod.get_stats()
    payload["tools_array"] = [
        {"name": k, **v} for k, v in payload.get("tools", {}).items()
    ]
    return JSONResponse(payload)


@app.get("/api/recent-activity")
def api_recent_activity() -> JSONResponse:
    return JSONResponse({"events": list(reversed(_RECENT_ACTIVITY))})


@app.get("/api/feed")
def api_feed() -> JSONResponse:
    """Pinned cards (newest first) for the dashboard Feed tab."""
    from tools_dashboard import list_pinned
    return JSONResponse({"items": list_pinned(limit=50)})


@app.get("/api/cards/{slug}.png")
def api_card_image(slug: str):
    path = CARDS_DIR / f"{slug}.png"
    if not path.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path, media_type="image/png")


_AUTH_PROXIES = {
    # name → callable, looked up by import-time-deferred lambda so we
    # don't pay the higgsfield import cost on module load.
    "higgsfield_auth_status": lambda: __import__("higgsfield").auth_status(),
    "start_higgsfield_auth":  lambda: __import__("higgsfield").bootstrap_oauth(),
}


@app.post("/api/tool/{name}")
def api_tool(name: str) -> JSONResponse:
    """Proxy for the dashboard Auth tab. Records stats + activity like
    every other tool call so the dashboard sees them in the breakdown."""
    fn = _AUTH_PROXIES.get(name)
    if fn is None:
        return JSONResponse({"error": "unknown tool"}, status_code=404)

    start = time.monotonic()
    ok, err, payload = True, None, None
    try:
        payload = fn()
        return JSONResponse(payload)
    except Exception as e:  # noqa: BLE001
        ok = False
        err = f"{type(e).__name__}: {e}"
        return JSONResponse({"error": err}, status_code=500)
    finally:
        dt = (time.monotonic() - start) * 1000
        try:
            stats_mod.record_tool_call(name, dt, ok=ok, error=err)
        except Exception:  # noqa: BLE001
            pass
        try:
            _record_activity(name, {}, dt, ok, err)
        except Exception:  # noqa: BLE001
            pass


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    from webapp.dashboard import build_dashboard
    return HTMLResponse(build_dashboard().html())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    import uvicorn
    uvicorn.run(
        "webapp.server:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
