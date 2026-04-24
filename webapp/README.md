# Mini Perplexity — Web UI

A simple chat web app that drives the same agent loop as the CLI.
Toggleable reasoning panel shows every tool call and LLM thought — the
browser version of the terminal's `rich` panels.

## Architecture

```
Browser                    FastAPI server                Agent core
───────                    ──────────────                ──────────
┌──────────────┐           ┌──────────────────┐          ┌─────────────┐
│  index.html  │           │  GET /           │          │             │
│  + app.js    │ ─ POST ─▶ │  POST /api/run   │ ──────▶  │ run_agent() │
│              │           │                  │          │             │
│  fetch()     │ ◀─ SSE ── │  StreamingResp   │ ◀─ cb ── │ on_event    │
│  + stream    │           │  text/event-     │          │ (hook)      │
│              │           │  stream          │          │             │
└──────────────┘           └──────────────────┘          └─────────────┘
```

- Agent code is **unchanged**; the web path uses the `on_event`
  callback that was added to `ReasoningChainUI` for exactly this.
- Events are routed in the browser: `user` + `final` → chat bubbles,
  everything else → reasoning panel (hidden by default).
- SSE over `fetch()` + ReadableStream (EventSource doesn't support
  POST bodies).

## Run

```bash
# From the repo root:
uv pip install -e ".[web]"     # one-time — installs fastapi, uvicorn, pydantic
source .venv/bin/activate

mini-perplexity-web            # or: python -m webapp.server
```

Open <http://127.0.0.1:8000>.

The same `.env` powers both the CLI and the webapp. If Gemini is set
up for the CLI, the web UI works too.

## Files

| Path | Purpose |
|------|---------|
| `server.py` | FastAPI app; one `/api/run` endpoint that streams SSE |
| `static/index.html` | Chat page + reasoning panel markup |
| `static/styles.css` | CLI-mirror palette (cyan/magenta/yellow/green/red event chips) |
| `static/app.js` | Vanilla JS — submit, consume SSE stream, render bubbles + events |

No framework. ~200 lines JS, ~80 lines Python, under 400 lines total.

## Limits / knowns

- Single-session server — the agent can only run one question at a
  time. A second POST while one is in flight will still work, but
  they'll share stdout. Fine for a demo.
- No auth; bind to `127.0.0.1` only. Do not expose to a network.
- First call of a session blocks on the LLM throttle (~4s default) —
  same as the CLI.
