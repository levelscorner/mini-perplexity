"""FastAPI + SSE front-end for the S6 four-role agent (agent6.run).

    POST /api/run  {query: str}   → text/event-stream, one `data: {json}\\n\\n`
                                     per loop event (user / memory / perception /
                                     answer / tool_call / tool_result / final / error).
    GET  /                        → the single-page UI.
    GET  /healthz                 → liveness probe.

Why this is simpler than the old S03 webapp: `agent6.run` is already async and
takes an `on_event` callback, so we run it as a task on the SSE handler's own
event loop and bridge to the stream with an asyncio.Queue — no worker thread.

Same substrate as the CLI: it spawns the MCP server over stdio (MCP_SERVER_CMD)
and every LLM call goes through the gateway (GATEWAY_URL, AGENT_PROVIDER). Start
the gateway on :8101 first, then launch this from the `agentic/` folder.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# agent6 and its sibling roles live one level up; add it to the import path.
HERE = Path(__file__).resolve().parent
AGENT_DIR = HERE.parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

import agent6  # noqa: E402

STATIC_DIR = HERE / "static"

app = FastAPI(title="S6 Four-Role Agent",
              description="Memory · Perception · Decision · Action — the loop, in the browser.")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


class RunRequest(BaseModel):
    query: str


@app.post("/api/run")
async def run(body: RunRequest) -> StreamingResponse:
    """Stream the four-role loop's events for one query as Server-Sent Events."""
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    def on_event(event: dict[str, Any]) -> None:
        # agent6.run is async on THIS event loop, so on_event fires on the same
        # thread between awaits — a direct put_nowait keeps strict FIFO order
        # (so the `final` event always precedes the sentinel below).
        queue.put_nowait(event)

    async def drive() -> None:
        try:
            await agent6.run(body.query, on_event=on_event)
        except Exception as exc:  # noqa: BLE001 — surface to the UI, never 500 silently
            queue.put_nowait({"kind": "error", "iter": 0,
                              "text": f"{type(exc).__name__}: {exc}"})
        finally:
            queue.put_nowait(None)  # sentinel → close the stream

    task = asyncio.create_task(drive())

    async def stream():
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("kind") == "final":
                    break
        finally:
            task.cancel()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def main() -> None:
    import uvicorn
    uvicorn.run("webapp.server:app", host="127.0.0.1", port=8000, log_level="info")


if __name__ == "__main__":
    main()
