"""FastAPI server that exposes the Mini Perplexity agent over HTTP + SSE.

Architecture:

    POST /api/run  {question: str}
        Starts run_agent in a background thread. Responds with a
        Server-Sent Events stream; one `data: {...}` line per ChainEvent
        the UI emits (user / llm / tool_call / tool_result / final / error
        / system). Closes the stream after `final` or after the agent ends.

    GET /
        Serves index.html.

    GET /static/...
        Serves CSS/JS.

The agent itself is unmodified. We pass `on_event` (callback hook added
to ReasoningChainUI) to receive every event and `render_terminal=False`
so the agent doesn't spam the server's stdout.

Bridging sync agent ↔ async server:
    run_agent is sync (blocking on LLM + network). We run it in a thread
    via Thread(target=...). The on_event callback is called from THAT
    thread, so we use loop.call_soon_threadsafe() to push events into
    the asyncio.Queue the SSE stream consumes from. End-of-stream is
    signaled by pushing a sentinel (None).
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from threading import Thread
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# The agent core lives at the repo root. Webapp is a child package, so
# we add the parent to sys.path so `import mini_perplexity` finds it.
HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mini_perplexity import run_agent  # noqa: E402

STATIC_DIR = HERE / "static"

app = FastAPI(
    title="Mini Perplexity",
    description="Web UI for the S03 agentic research loop.",
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    """Serve the chat page."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Cheap liveness probe — used by tests, not by the UI."""
    return {"status": "ok"}


class RunRequest(BaseModel):
    """Body of POST /api/run. Validated by Pydantic before the handler runs."""

    question: str
    max_iterations: int = 8


@app.post("/api/run")
async def run(body: RunRequest) -> StreamingResponse:
    """Run the agent on a question, stream events as they happen.

    Returns a text/event-stream where each event is a one-line JSON
    payload prefixed with `data: ` (the SSE format). The browser
    consumes this with fetch() + ReadableStream rather than EventSource
    because EventSource is GET-only and we need a request body here.
    """
    # asyncio.Queue is the bridge between the sync agent thread (producer)
    # and the async SSE generator (consumer). It's safe to put_nowait
    # from the agent thread via loop.call_soon_threadsafe.
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def on_event(event_dict: dict[str, Any]) -> None:
        """Called by ReasoningChainUI on every emit; pushes to the queue."""
        loop.call_soon_threadsafe(queue.put_nowait, event_dict)

    def agent_thread() -> None:
        """Run the agent, then push the sentinel so the stream closes."""
        try:
            run_agent(
                body.question,
                max_iterations=body.max_iterations,
                on_event=on_event,
                render_terminal=False,
            )
        except Exception as exc:  # noqa: BLE001 — surface anything to the UI
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
            loop.call_soon_threadsafe(queue.put_nowait, None)  # sentinel

    Thread(target=agent_thread, daemon=True).start()

    async def stream():
        """Drain the queue, yield SSE lines until sentinel or final event."""
        while True:
            event = await queue.get()
            if event is None:
                break
            # SSE wire format: `data: <json>\n\n`. The blank line terminates
            # the event. ensure_ascii=False keeps unicode intact for unicode
            # questions / answers.
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            # Closing on `final` is a UX nicety — without it the stream
            # stays open until the agent thread also pushes the sentinel.
            if event.get("kind") == "final":
                break

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disables proxy buffering if any
        },
    )


def main() -> None:
    """Entry point used by `mini-perplexity-web` script."""
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
