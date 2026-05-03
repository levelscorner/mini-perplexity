# MINION

A research, image-generation, and dashboard-pinning agent. Native Anthropic tool use, real FastMCP server, Prefab dashboard, Higgsfield image rendering.

> Ask anything → MINION searches the web, reads sources, can generate images via Higgsfield, pin findings to a Prefab dashboard, and persist cited markdown answers to disk.

Originally [`mini-perplexity`](https://github.com/levelscorner/mini-perplexity) (S03 EAG V3 submission). The S04 build extends it with image generation, MCP tooling, and the Prefab dashboard.

---

## Five tools

| Tool | Purpose |
|------|---------|
| `web_search(query, n=5)` | DuckDuckGo search via `ddgs`. Ranked `{rank, title, url, snippet}` results. |
| `fetch_page(url)` | Extract main-article text via `trafilatura`, truncated to ~5000 chars. |
| `save_answer(question, answer, sources)` | Persist final markdown answer with numbered citations to `answers/<slug>.md`. |
| `render_image(prompt, panels=1)` | Generate 1 image or a 3/4-panel comic strip via Higgsfield (OAuth, cached tokens). Frontend renders inline. |
| `pin_to_dashboard(title, content, kind)` | Push a card to the Prefab dashboard's Feed tab. `kind ∈ note\|answer\|image\|link`. |

All five are exposed two ways:
- **In-process** to the chat agent via `tools.TOOLS`
- **As a real MCP server** via `minion_mcp/server.py` (FastMCP, stdio or streamable-http)

---

## What's in the box

```
minion/                       (repo dir is mini-perplexity for legacy reasons)
├── llm.py                    pluggable LLM (Anthropic-first, Gemini fallback)
├── tools.py + TOOL_SCHEMAS   web_search · fetch_page · save_answer + Anthropic tool schemas
├── tools_image.py            render_image (single or comic strip)
├── tools_dashboard.py        pin_to_dashboard + feed JSON storage
├── higgsfield.py             OAuth + Higgsfield render wrapper
├── auth.py                   OAuth bootstrap CLI
├── stats.py                  atomic JSON aggregator
├── mini_perplexity.py        agent entrypoint (native + prompt-engineered paths)
├── system_prompt.md          MINION's prompt
├── minion_mcp/server.py      FastMCP server (stdio | --http)
└── webapp/
    ├── server.py             FastAPI: /, /api/run (SSE), /api/stats, /api/feed, /dashboard
    ├── dashboard.py          Prefab dashboard (Feed · Stats · Activity · Auth)
    └── static/               chat shell (image toggle, dark mode, status pill)
```

---

## Setup

```bash
git clone git@github.com:levelscorner/mini-perplexity.git
cd mini-perplexity
git checkout s04/minion

uv sync --extra web --extra test
cp .env.example .env
$EDITOR .env   # set ANTHROPIC_API_KEY (preferred) or GEMINI_API_KEY

# One-time: bootstrap Higgsfield OAuth (opens browser, caches tokens)
uv run python auth.py
uv run python auth.py --check
```

---

## Run

### Web app (chat + dashboard)

```bash
uv run uvicorn webapp.server:app --port 8000
open http://localhost:8000          # chat
open http://localhost:8000/dashboard # Feed · Stats · Activity · Auth
```

### CLI agent

```bash
uv run python mini_perplexity.py "Find ownership details of Tata Sons, save to a file, pin to my dashboard"
```

### Standalone MCP server

```bash
# stdio (for Claude Desktop / MCP Inspector)
uv run python -m minion_mcp.server

# streamable-http on port 9000
uv run python -m minion_mcp.server --http --port 9000
```

---

## The "all 3 tools" demo

The S04 spec wants one prompt that exercises an internet tool, a file-CRUD tool, and a Prefab UI tool.

```bash
"Find the ownership details of Tata Sons, save them to a text file, and pin to my dashboard"
```

What the agent fires (visible in the chat status pill + dashboard Activity tab):

```
web_search        → "Tata Sons ownership"          (internet)
fetch_page        → top-1 / top-2 result URLs       (internet)
save_answer       → answers/tata-sons-ownership.md  (file CRUD)
pin_to_dashboard  → feed/<ts>-tata-sons-...json     (Prefab UI)
final answer
```

Watch:
- The chat reply lands.
- `/dashboard` Feed tab now has a new card.
- `/dashboard` Stats tab shows the four tool counts.
- `answers/<slug>.md` exists on disk.

---

## Image gen

```
🖼 toggle next to Send  →  one-shot image mode for the next message.
/image <prompt>          →  same effect inline.
```

Examples:
- `/image a cat in a tuxedo, ghibli style` → single image card inline
- `/image a corgi astronaut on the moon, 3-panel comic strip style` → 3-panel grid

The agent decides single vs comic-strip from the prompt. Stub mode (`RENDER_MODE=stub`, `HIGGSFIELD_STUB_PATH=/path/to.png`) skips Higgsfield entirely.

---

## How the demo maps to the rubric

| S04 requirement | Where |
|---|---|
| Real MCP server with 3+ tools | `minion_mcp/server.py` (6 tools exposed) |
| Internet tool | `web_search` + `fetch_page` |
| File CRUD | `save_answer` (write), `tools_dashboard.list_pinned` (read), `feed/<slug>.json` files (R/U/D via filesystem) |
| Push UI tool | `pin_to_dashboard` → Prefab Feed tab |
| Prefab UI consumes the tools | `webapp/dashboard.py` (Feed / Stats / Activity / Auth tabs) |
| Single prompt fires all three | "Find ownership of Tata Sons, save to file, pin to dashboard" → 4 tool calls |
| Native Anthropic tool use | `llm.LLMClient.chat_with_tools` + `mini_perplexity._run_native_tool_loop` |

---

## Branches

- `main` — original mini-perplexity (Gemini, 3-tool research agent)
- `s04/cat-news-mcp` — original S04 sketch (Newsroom theme; superseded)
- `s04/mp-image-gen` — clean image-gen + dashboard build
- `s04/native-tools` — native Anthropic tool use on top of the above
- **`s04/minion`** ← current; adds the FastMCP server, `pin_to_dashboard`, Feed tab, brand rename
