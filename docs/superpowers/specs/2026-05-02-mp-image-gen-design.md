# Mini Perplexity + Image Gen — Design

**Date:** 2026-05-02
**Status:** Approved (brainstorm complete, awaiting writing-plans)
**Branch:** `s04/mp-image-gen` (fresh, off `main`)
**Supersedes:** the `s04/cat-news-mcp` "Newsroom" experiment (kept for reference; nothing ported wholesale).

## Goal

Add image-generation capability to the existing `mini-perplexity` chat agent via Higgsfield, while keeping the chat surface clean and conversational. No bespoke "news-to-comic" theme — just a normal chat app that happens to be able to generate images on demand.

## Locked decisions (from brainstorm)

| # | Decision | Choice |
|---|---|---|
| 1 | Where the agent's "thinking" lives | **Hybrid**: status pill in chat (collapses to one line when done) + full inputs/outputs on `/dashboard` |
| 2 | How the user triggers image gen | **Toggle button next to Send** (one-shot, resets after each render) **+ `/image` slash command** |
| 3 | Comic strip shape | **Agent picks per prompt** — 1, 3, or 4 panels; default 3 when "comic" implied, 1 otherwise |
| 4 | Scope | **Fresh branch off `main`**; port reusable tools (Higgsfield wrapper, auth, stats); drop everything cat/persona/Newsroom-themed |
| 5 | News-to-comic flow | **Dropped**. Agent uses generic `web_search`; no special UI plumbing for news |
| 6 | LLM backend | **Pluggable** (`d7faeaf` style): prefer Anthropic if `ANTHROPIC_API_KEY` set, fall back to Gemini |

## Repo layout (target)

```
mini-perplexity/                          ← s04/mp-image-gen branch
├── mini_perplexity.py                    ← unchanged from main
├── llm.py                                ← rewritten: pluggable Anthropic/Gemini
├── tools.py                              ← unchanged (web_search, fetch_page, save_answer)
├── tools_image.py                        ← NEW: render_image agent tool
├── parser.py, ui.py, system_prompt.md    ← unchanged
├── higgsfield.py                         ← ported & gutted of cat scene logic
├── auth.py                               ← ported: OAuth bootstrap CLI
├── stats.py                              ← ported: atomic JSON aggregator
├── pyproject.toml                        ← + anthropic, fastmcp, prefab-ui, py-key-value-aio[disk]
├── webapp/
│   ├── server.py                         ← FastAPI; mounts / + /dashboard
│   ├── dashboard.py                      ← NEW: Prefab — Stats / Activity / Auth tabs only
│   └── static/
│       ├── index.html                    ← extended chat shell
│       ├── styles.css                    ← + dark mode, image card, status pill
│       └── app.js                        ← + image toggle, /image parsing, dark toggle
└── docs/superpowers/specs/
    └── 2026-05-02-mp-image-gen-design.md ← this file
```

### What gets dropped from `s04/cat-news-mcp` (NOT ported)

- `scene.py` — `CatPersonaCard`, `CatScene`, `VISUAL_STYLE_PRESETS`
- `prompts.py` — casting director / creative director system prompts
- `server.py` (FastMCP) — replaced by `tools_image.py` (in-process tool, no separate MCP server needed since the agent runs in-process)
- `client.py` — REPL with cat-themed slash commands
- `dashboard.py` (Newsroom) — replaced by `webapp/dashboard.py` (slimmer)
- `personas/`, `news/`, `images/`, `logs/`, `images/test/`
- `docs/PROMPT-DESIGN.md`, README cat-news content

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  Browser                                                              │
│  ┌────────────────────────┐         ┌─────────────────────────────┐ │
│  │ /  (chat — index.html) │         │ /dashboard  (Prefab single  │ │
│  │ - SSE consumer          │         │  HTML, served by FastAPI)   │ │
│  │ - composer + 🖼 toggle  │         │ - Stats / Activity / Auth   │ │
│  │ - status pill           │         │ - polls /api/stats +        │ │
│  │ - dark toggle           │         │   /api/recent-activity      │ │
│  └─────────┬──────────────┘         └─────────────┬───────────────┘ │
│            │ POST /api/run + SSE                   │ GET /api/...    │
└────────────┼──────────────────────────────────────┼─────────────────┘
             │                                       │
       ┌─────▼───────────────────────────────────────▼─────────────┐
       │  webapp/server.py  (FastAPI / Starlette)                   │
       │   - mounts /static, /, /dashboard                          │
       │   - /api/run        (SSE chat — existing)                  │
       │   - /api/stats, /api/recent-activity                       │
       │   - /api/cards/<slug>.png                                  │
       └─────┬──────────────────────────────┬─────────────────────┬─┘
             │                              │                     │
       ┌─────▼─────┐                ┌───────▼────────┐    ┌───────▼───┐
       │ mini_     │                │ tools.py +     │    │ stats.py  │
       │ perplexity│  tool calls →  │ tools_image.py │    │ (atomic   │
       │ (agent    │                │  (web_search,  │    │  JSON)    │
       │  loop)    │                │  fetch_page,   │    └───────────┘
       └─────┬─────┘                │  render_image) │
             │                      └────────┬───────┘
             │ LLM call                      │
       ┌─────▼─────┐                ┌────────▼───────┐
       │ llm.py    │                │ higgsfield.py  │
       │ (pluggable│                │ (FastMCP       │
       │  Anthropic│                │  client +      │
       │  / Gemini)│                │  OAuth/Disk    │
       └───────────┘                │  token cache)  │
                                    └────────┬───────┘
                                             │
                                     mcp.higgsfield.ai/mcp
```

## Chat data flow (one turn)

1. User types prompt; clicks 🖼 toggle (or prefixes `/image`); hits Send.
2. Frontend POSTs `{question, image_mode: bool}` to `/api/run`.
3. Server starts `run_agent(...)` in a thread; streams SSE events to browser.
4. Agent calls tools. Each call:
   - emits `tool_call` event → frontend renders status pill (e.g. `→ render_image`).
   - records to `stats.py` + the in-process `_RECENT_ACTIVITY` deque.
   - on success, emits `tool_result` → pill collapses to one line (`→ render_image · 12.3s ✓`).
5. If `render_image` fired, the agent's final message includes a structured image reference; the server emits an `image` SSE event before `final` so the frontend renders the image inline.
6. `final` event closes the SSE.

### Image-mode contract

- `image_mode=true`: server appends a one-line override to the system prompt for that turn — *"This turn the user wants an image. Call `render_image` with a refined, vivid prompt — comic strip if it suits the request, single image otherwise."*
- `image_mode=false`: agent calls `render_image` only if the user explicitly asked for an image in prose.
- Toggle is **one-shot**: the frontend resets `image_mode` to false on SSE close (success *or* error), so the user has to opt-in again for the next image.
- `/image <prompt>` is sugar: the client strips the prefix, sets `image_mode=true`, sends the rest.

## Component contracts

### `llm.py` — pluggable backend (~120 lines)

```python
class LLMBackend(Protocol):
    def chat(self, messages, system, tools=None) -> LLMResponse: ...

class AnthropicBackend(LLMBackend): ...   # uses anthropic SDK, claude-sonnet-4-6 (or 4-7)
class GeminiBackend(LLMBackend): ...       # google-genai SDK, existing main impl

def auto_backend() -> LLMBackend:
    if os.getenv("ANTHROPIC_API_KEY"): return AnthropicBackend()
    if os.getenv("GEMINI_API_KEY"):    return GeminiBackend()
    raise RuntimeError("no LLM key set")
```

`mini_perplexity.run_agent` is the only consumer.

### `higgsfield.py` — cleaned wrapper (~150 lines)

Public surface (everything else is private):

```python
def auth_status() -> dict[str, Any]: ...
def bootstrap_oauth() -> dict[str, Any]: ...
def render_image(
    prompt: str,
    *,
    model: str | None = None,
    aspect: str = "1:1",
    refs: list[str] | None = None,
) -> ImageRender: ...

@dataclass
class ImageRender:
    slug: str
    url: str
    local_path: str
    model_used: str
    duration_s: float
```

- Drops all scene/persona/cast logic from `s04 higgsfield_mcp.py`.
- Keeps OAuth bootstrap and `DiskStore` token cache at `~/.mini-perplexity/tokens/`.
- Model routing (in priority order):
  1. If `refs` (reference images) provided → `nano_banana_2` (preserves identity across renders).
  2. Else if prompt hints cartoon/comic style → `flux_2`.
  3. Else → `gpt_image_2` (best photoreal text rendering).
- Stub mode preserved (`RENDER_MODE=stub` cycles through fixtures in `tests/fixtures/images/`).

### `tools_image.py` — agent tool (~80 lines)

```python
def render_image(prompt: str, panels: int = 1) -> str:
    """
    Returns JSON string the agent embeds in its final message:
      panels=1 → {"type":"image", "slug":..., "url":..., "alt":...}
      panels>1 → {"type":"comic_strip", "panels":[{...}, {...}, ...]}
    Frontend dispatches on type.
    """
```

- For `panels > 1`: fires N parallel `higgsfield.render_image` calls.
- Strip-aware prompt mutation: panel 1 = setup, panel 2 = turn, panel 3 = punchline (and panel 4 = beat between turn and punch when panels=4).
- A failed panel produces a placeholder entry `{slug:None, url:None, error:"..."}`; the frontend renders a retry card.

### `webapp/server.py` — FastAPI (~250 lines, extends main)

New routes added on top of main's existing `/`, `/api/run`, `/healthz`:

| Method | Path | Purpose |
|---|---|---|
| GET | `/dashboard` | Returns Prefab single HTML |
| GET | `/api/stats` | Stats JSON (existing shape from s04) |
| GET | `/api/recent-activity` | Rolling 100 tool calls, newest first |
| GET | `/api/cards/<slug>.png` | Serve saved render |

`/api/run` body extends to `{question, max_iterations, image_mode}`. New SSE event kind:

```json
{"kind":"image","slug":"...","url":"...","alt":"..."}
```

Emitted just before `final` so the frontend can render the image card inline with the agent's text.

All tool calls go through one decorator that records to `stats.py` + `_RECENT_ACTIVITY`.

### `webapp/static/app.js` — extended chat (~350 lines)

New responsibilities on top of main's existing SSE consumer:

- `imageMode` state + 🖼 toggle button (one-shot reset after Send).
- `/image` parsing: strip prefix, set `imageMode=true`, send remainder.
- Status pill renderer: one DOM element per user message; updates on each `tool_call` / `tool_result`; collapses to one-line summary when the turn ends.
- Image card renderer: `<figure>` with full-width image, caption, "open ↗" link to `/api/cards/<slug>.png`.
- Comic strip renderer: CSS grid auto-chosen by panel count (1×1 / 1×3 horizontal / 2×2).
- Dark-mode toggle: persists in `localStorage`, reads `prefers-color-scheme` on first paint, applied via `data-theme` attribute on `<html>`.
- Sticky composer: `position: sticky; bottom: 0` on the composer, scroll-anchor on the chat column.

### `webapp/dashboard.py` — Prefab dashboard (~200 lines)

Three tabs only:

1. **Stats** — Metric cards (Tool calls / OK-Fail / Tool seconds / Chat turns / Tokens in/out) + per-tool table.
2. **Activity** — rolling 100 tool calls (`ts · tool_name · input_summary · duration · status`), newest first.
3. **Auth** — Higgsfield: status badge, "Check status" + "Connect" buttons.

`on_mount` auto-fetches stats + activity. Tabs `on_change` re-fetch.

## Error handling & edge cases

| Case | Behaviour |
|---|---|
| Both `ANTHROPIC_API_KEY` and `GEMINI_API_KEY` unset | `llm.auto_backend()` raises at startup; `webapp/server.py` returns a clear "no LLM configured" page. |
| Higgsfield OAuth expired | `render_image` catches the 401, emits `tool_result` with `error:"auth_required"`, agent's reply tells user to hit Connect on `/dashboard`. |
| Image render times out (Higgsfield queue) | `MAX_WAIT_SECONDS = 1200` (existing); on timeout, tool returns `{error:"timeout"}`, agent apologises in the chat reply. |
| Comic-panel partial fail (3/4 succeed) | Frontend renders successful panels with an error placeholder for the failed one. Re-rendering requires re-sending the prompt (retry endpoint deferred — see Open questions). |
| User clicks Send while busy | Frontend disables composer + Send while `chat_busy`; SSE confirms when the run ends. |
| Dark/light flicker on first paint | A `<script>` block in `<head>` reads `localStorage.theme` before render and sets `data-theme` synchronously. |
| Browser refreshes mid-stream | In-flight conversation is abandoned; no resume. Stats + activity persist on disk. Chat history is per-tab in `localStorage`. |
| `stats.json` corruption | `stats._load()` already catches `json.JSONDecodeError` and returns a fresh `Stats()`. |

**Concurrency:** `bridge.call()` is lock-guarded. `_RECENT_ACTIVITY` deque appends are atomic under the GIL. Stats writes use `tempfile + os.replace`.

## Testing

Three layers, lean — this is a personal demo, not a shipping product. Cover failure-prone surfaces and ship.

1. **Unit (`pytest`)**
   - `llm.auto_backend()` selection logic (mock env vars).
   - `higgsfield._unwrap()` for both `structured_content` and TextContent fallback shapes.
   - `tools_image.render_image(panels=N)` returns the right `type` and panel count.
   - `stats.record_tool_call` aggregation correctness (counts, totals, durations).

2. **Integration (`pytest-asyncio`)**
   - Spin up `webapp/server.py` against `RENDER_MODE=stub` Higgsfield.
   - POST `/api/run` with `image_mode=true`, consume SSE, assert event order: `tool_call(render_image)` → `tool_result` → `image` → `final`.
   - `/api/stats` reflects the call afterwards.

3. **Browser smoke (chrome-devtools MCP, manual)**
   - Cold load → 🖼 toggle → "a cat in a tux" → Send → image card renders inline; status pill shows then collapses.
   - `/image cat as astronaut` (no toggle) → same outcome.
   - `/dashboard` → Stats + Activity populate on mount.
   - Dark mode toggle persists across reload.

No 80% coverage target — focus on LLM selection, stub render, SSE shape, OAuth recovery.

## Out of scope (explicit non-goals)

- Resuming an interrupted SSE stream after browser refresh.
- Conversation history persistence beyond `localStorage`.
- Multi-user accounts / auth (single-user local app).
- Mobile responsive layout (desktop-first; degrades gracefully).
- Cost dashboard (Higgsfield credit tracking).
- Voice / audio input.
- Real-time collaborative editing.

## Open questions deferred to implementation

1. Exact Anthropic model id — `claude-sonnet-4-6` vs `4-7`. Decide at impl time based on what's available.
2. Default panel count when `image_mode=true` and prompt doesn't hint comic — likely `1`, but agent system prompt may steer.
3. Whether `/api/retry-panel/<slug>` is in v1 or deferred. Lean toward deferring; show error placeholder only.
