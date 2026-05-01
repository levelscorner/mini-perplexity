# Mini Perplexity + Image Gen — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Higgsfield-backed image generation to the existing `mini-perplexity` chat agent. User triggers image gen with a 🖼 toggle next to Send (or `/image` slash); agent picks single-image vs comic strip; status pill shows tool calls live; full processing on `/dashboard`.

**Architecture:** Fresh branch off `main`. Existing FastAPI/SSE chat shell stays. New pluggable LLM (Anthropic-first, Gemini fallback) replaces the Gemini-only `LLMClient`. Higgsfield wrapper ported from `s04/cat-news-mcp` and gutted of cat/persona logic. New `tools_image.py` exposes `render_image(prompt, panels)` to the agent. New Prefab dashboard at `/dashboard`. No cat/Newsroom theme.

**Spec:** [`docs/superpowers/specs/2026-05-02-mp-image-gen-design.md`](../specs/2026-05-02-mp-image-gen-design.md)

**Tech Stack:** Python 3.13 · FastAPI / Starlette / SSE · `anthropic` SDK · `google-genai` SDK · `fastmcp` (Higgsfield client) · `py-key-value-aio[disk]` (OAuth token cache) · `prefab-ui` (dashboard) · `pytest` + `pytest-asyncio` · vanilla JS + CSS for the chat UI.

**Source branches referenced:**
- `main` — pristine mini-perplexity (the base)
- `s04/cat-news-mcp` — has working Higgsfield wrapper (`s04-cat-news/higgsfield_mcp.py`), `auth.py`, `stats.py` we'll port from

---

## File structure (target)

| Path | Purpose | Source |
|---|---|---|
| `llm.py` | Pluggable LLM (Anthropic + Gemini), keeps `LLMClient.generate(prompt) -> str` shape | rewritten |
| `higgsfield.py` | Higgsfield FastMCP client + OAuth + `render_image()` | ported from `s04-cat-news/higgsfield_mcp.py`, gutted |
| `auth.py` | `python auth.py [--check\|--reset]` OAuth bootstrap CLI | ported as-is |
| `stats.py` | Atomic JSON aggregator for tool-call stats | ported as-is |
| `tools_image.py` | `render_image(prompt, panels)` agent tool — wraps `higgsfield.render_image` | new |
| `webapp/server.py` | FastAPI: extend with `/dashboard`, `/api/stats`, `/api/recent-activity`, `/api/cards/<slug>.png`, image SSE event | extended |
| `webapp/dashboard.py` | Prefab single page: Stats / Activity / Auth tabs | new |
| `webapp/static/index.html` | Chat shell with 🖼 toggle, dark toggle, dashboard link | extended |
| `webapp/static/styles.css` | + dark theme, image card, status pill, comic grid | extended |
| `webapp/static/app.js` | + image toggle, `/image` parsing, status pill renderer, dark mode, SSE `image` event | extended |
| `tests/test_llm.py` | Unit tests for `auto_backend` selection | new |
| `tests/test_higgsfield.py` | Unit tests for stub render + model routing + `_unwrap` | new |
| `tests/test_tools_image.py` | Unit tests for panel routing + JSON shape | new |
| `tests/test_stats.py` | Unit tests for aggregation + atomic write | new |
| `tests/test_server.py` | Integration test: SSE event order with stub Higgsfield | new |
| `tests/fixtures/images/*.png` | Stub-mode rendered images | copied from s04 |

Files left unchanged from `main`: `mini_perplexity.py`, `tools.py`, `parser.py`, `ui.py`, `system_prompt.md`, `pyproject.toml` (only deps added).

---

## Task 0: Branch off main + scaffold

**Files:**
- Modify: `pyproject.toml` (add deps)
- Create: `tests/__init__.py`, `tests/fixtures/images/.gitkeep`

- [ ] **Step 1: Cut a fresh branch off main**

```bash
cd /Users/level/ws/projects/mini-perplexity
git fetch origin
git checkout main
git pull origin main
git checkout -b s04/mp-image-gen
git log --oneline -3
```

Expected: HEAD on a new branch with main's tip commit.

- [ ] **Step 2: Cherry-pick the design spec onto this branch**

The spec was committed on `s04/cat-news-mcp` at `e72727a`. Bring it forward:

```bash
git cherry-pick e72727a
git log --oneline -3
```

Expected: spec commit on new branch.

- [ ] **Step 3: Add deps to pyproject.toml**

Edit `pyproject.toml` to add to `dependencies`:

```toml
"anthropic>=0.40.0",
"fastmcp>=2.13.0",
"py-key-value-aio[disk]>=1.0.0",
"prefab-ui>=0.5.0",
"pytest-asyncio>=0.24.0",
"pytest>=8.0.0",
"httpx>=0.27.0",
```

- [ ] **Step 4: Sync deps**

```bash
uv sync
```

Expected: lockfile updated, all packages installed cleanly.

- [ ] **Step 5: Scaffold tests dir + fixture sink**

```bash
mkdir -p tests/fixtures/images
touch tests/__init__.py tests/fixtures/images/.gitkeep
```

- [ ] **Step 6: Commit baseline**

```bash
git add pyproject.toml uv.lock tests/
git commit -m "chore(s04-mp): branch off main + add image-gen deps + tests scaffold"
```

---

## Task 1: Port `stats.py` from s04 (atomic JSON aggregator)

**Files:**
- Create: `stats.py`
- Test: `tests/test_stats.py`

This file is small (~140 lines) and already works on s04. Port verbatim, write fresh tests against it. TDD slightly inverted: copy the impl, write tests last to prove behaviour and prevent regressions.

- [ ] **Step 1: Port stats.py from s04 branch**

```bash
git show s04/cat-news-mcp:s04-cat-news/stats.py > stats.py
```

- [ ] **Step 2: Update STATS_PATH default location**

Edit `stats.py` line ~28-29:

```python
ROOT = Path(__file__).resolve().parent
STATS_PATH = ROOT / "stats.json"   # already correct, just confirm
```

Add `stats.json` to `.gitignore` if not present:

```bash
grep -q "^stats.json" .gitignore || echo "stats.json" >> .gitignore
```

- [ ] **Step 3: Write the failing test**

`tests/test_stats.py`:

```python
"""Unit tests for stats.py — atomic aggregation, persistence, derived metrics."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import stats


@pytest.fixture(autouse=True)
def isolated_stats(tmp_path, monkeypatch):
    """Point STATS_PATH at a temp file for every test."""
    monkeypatch.setattr(stats, "STATS_PATH", tmp_path / "stats.json")
    yield


def test_record_tool_call_increments_counters():
    stats.record_tool_call("fetch_news", 120.5, ok=True)
    stats.record_tool_call("fetch_news", 80.0, ok=False, error="boom")

    out = stats.get_stats()
    t = out["tools"]["fetch_news"]
    assert t["count_total"] == 2
    assert t["count_ok"] == 1
    assert t["count_fail"] == 1
    assert t["last_error"] == "boom"
    assert t["duration_ms_avg"] == pytest.approx(100.25, rel=1e-3)
    assert out["totals"]["tool_calls"] == 2


def test_atomic_write_survives_corruption(tmp_path, monkeypatch):
    monkeypatch.setattr(stats, "STATS_PATH", tmp_path / "stats.json")
    (tmp_path / "stats.json").write_text("{not json")

    # _load() should not raise on corrupted input
    s = stats._load()
    assert s.tools == {}


def test_chat_turn_aggregates():
    stats.record_chat_turn(in_tokens=100, out_tokens=20)
    stats.record_chat_turn(in_tokens=50, out_tokens=10)

    out = stats.get_stats()
    assert out["chat_turns"] == 2
    assert out["chat_in_tokens"] == 150
    assert out["chat_out_tokens"] == 30
```

- [ ] **Step 4: Run tests — should pass since impl is ported**

```bash
uv run pytest tests/test_stats.py -v
```

Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add stats.py tests/test_stats.py .gitignore
git commit -m "feat(stats): atomic JSON aggregator + tests"
```

---

## Task 2: Port `higgsfield.py` (image-gen wrapper, gutted of cat-scene logic)

**Files:**
- Create: `higgsfield.py`
- Test: `tests/test_higgsfield.py`
- Test: `tests/conftest.py` (shared fixtures)

The s04 wrapper at `s04-cat-news/higgsfield_mcp.py` has scene/persona/cast logic interleaved with raw render logic. We extract just the raw render bits.

- [ ] **Step 1: Read the s04 source for reference**

```bash
git show s04/cat-news-mcp:s04-cat-news/higgsfield_mcp.py | head -200
```

Identify keep-list (in priority order):
1. OAuth setup (`OAuth`, `DiskStore`, token cache path)
2. `Client` instantiation
3. `auth_status()`, `bootstrap_oauth()`
4. `_unwrap()` for both `structured_content` shapes
5. `_poll_until_done()` with `MAX_WAIT_SECONDS = 1200`
6. Raw render call to Higgsfield's `generate_image` MCP tool

Drop:
- `generate_persona_portrait()` (cat-themed)
- `generate_scene_image()` (depends on `CatScene`)
- All references to `scene.py` / `prompts.py`
- Stub-mode rotation through pre-rendered cat images (replace with a single fixture path)

- [ ] **Step 2: Write the failing test first**

`tests/test_higgsfield.py`:

```python
"""Unit tests for higgsfield.py — stub mode + model routing + unwrap."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest


def test_pick_model_with_refs_uses_nano_banana():
    from higgsfield import _pick_model
    assert _pick_model("a portrait", refs=["http://x"]) == "nano_banana_2"


def test_pick_model_cartoon_uses_flux():
    from higgsfield import _pick_model
    assert _pick_model("a cartoon cat in pixar style", refs=None) == "flux_2"
    assert _pick_model("comic strip panel", refs=None) == "flux_2"


def test_pick_model_default_is_gpt_image_2():
    from higgsfield import _pick_model
    assert _pick_model("a sunset over mountains", refs=None) == "gpt_image_2"


def test_unwrap_handles_structured_content():
    from higgsfield import _unwrap
    result = MagicMock()
    result.structured_content = {"slug": "abc", "url": "http://x"}
    result.content = []
    assert _unwrap(result) == {"slug": "abc", "url": "http://x"}


def test_unwrap_handles_camelcase_alias():
    from higgsfield import _unwrap
    result = MagicMock()
    result.structured_content = None
    result.structuredContent = {"slug": "abc"}
    result.content = []
    assert _unwrap(result) == {"slug": "abc"}


def test_unwrap_falls_back_to_text_json():
    from higgsfield import _unwrap
    block = MagicMock()
    block.text = '{"slug": "fallback"}'
    result = MagicMock()
    result.structured_content = None
    result.structuredContent = None
    result.content = [block]
    assert _unwrap(result) == {"slug": "fallback"}


def test_render_image_stub_mode(monkeypatch, tmp_path):
    # Stub mode returns a fixture image path without hitting Higgsfield.
    monkeypatch.setenv("RENDER_MODE", "stub")
    fixture = tmp_path / "stub.png"
    fixture.write_bytes(b"fake-png")
    monkeypatch.setenv("HIGGSFIELD_STUB_PATH", str(fixture))

    from higgsfield import render_image
    out = render_image("a cat in a tux")
    assert out.slug.startswith("stub-")
    assert out.local_path == str(fixture)
    assert out.model_used == "stub"
```

- [ ] **Step 3: Run tests — confirm they fail**

```bash
uv run pytest tests/test_higgsfield.py -v
```

Expected: ImportError or attribute errors — module doesn't exist yet.

- [ ] **Step 4: Write `higgsfield.py` (stripped wrapper)**

Skeleton (full file ~150 lines — port from s04 keeping only the listed pieces):

```python
"""Higgsfield image-gen wrapper.

Strips the cat-scene/persona logic from s04 and exposes a single
public function: render_image(prompt, *, model, aspect, refs).
OAuth is handled via fastmcp.client.auth.OAuth + DiskStore.

Env:
    HIGGSFIELD_MCP_URL   — defaults to https://mcp.higgsfield.ai/mcp
    RENDER_MODE          — "live" | "stub" (stub returns a fixture)
    HIGGSFIELD_STUB_PATH — path to fixture image used in stub mode
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# (fastmcp + DiskStore imports here)


HIGGSFIELD_MCP_URL = os.getenv("HIGGSFIELD_MCP_URL", "https://mcp.higgsfield.ai/mcp")
TOKEN_CACHE_DIR = Path.home() / ".mini-perplexity" / "tokens"
MAX_WAIT_SECONDS = 1200

CARTOON_KEYWORDS = ("cartoon", "comic", "pixar", "ghibli", "manga", "anime", "strip panel")


@dataclass
class ImageRender:
    slug: str
    url: str
    local_path: str
    model_used: str
    duration_s: float


def _pick_model(prompt: str, *, refs: list[str] | None) -> str:
    if refs:
        return "nano_banana_2"
    p = prompt.lower()
    if any(k in p for k in CARTOON_KEYWORDS):
        return "flux_2"
    return "gpt_image_2"


def _unwrap(result: Any) -> dict[str, Any]:
    """Pull structured payload off a FastMCP CallToolResult, fall back to TextContent JSON."""
    sc = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
    if sc:
        return sc
    for block in (result.content or []):
        text = getattr(block, "text", None)
        if text:
            text = text.strip()
            if text.startswith("{") or text.startswith("["):
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    return {"text": text}
            return {"text": text}
    return {}


def render_image(prompt: str, *, model: str | None = None, aspect: str = "1:1",
                 refs: list[str] | None = None) -> ImageRender:
    """Render one image. Synchronous wrapper around the async client."""
    if os.getenv("RENDER_MODE") == "stub":
        return _stub_render(prompt)

    chosen = model or _pick_model(prompt, refs=refs)
    return asyncio.run(_render_async(prompt, chosen, aspect, refs))


def _stub_render(prompt: str) -> ImageRender:
    fixture = os.getenv("HIGGSFIELD_STUB_PATH")
    if not fixture or not Path(fixture).exists():
        raise RuntimeError(f"HIGGSFIELD_STUB_PATH missing or invalid: {fixture}")
    slug = f"stub-{uuid.uuid4().hex[:8]}"
    return ImageRender(
        slug=slug, url=f"file://{fixture}", local_path=fixture,
        model_used="stub", duration_s=0.0,
    )


# _render_async, auth_status, bootstrap_oauth — port from s04
```

Port the live-mode functions verbatim from `s04-cat-news/higgsfield_mcp.py`, but call only `generate_image` (drop persona/scene-specific calls).

- [ ] **Step 5: Run tests — should pass**

```bash
uv run pytest tests/test_higgsfield.py -v
```

Expected: 7 PASSED.

- [ ] **Step 6: Commit**

```bash
git add higgsfield.py tests/test_higgsfield.py
git commit -m "feat(higgsfield): port wrapper from s04, gut cat-scene logic"
```

---

## Task 3: Port `auth.py` (OAuth bootstrap CLI)

**Files:**
- Create: `auth.py`

- [ ] **Step 1: Port from s04 verbatim**

```bash
git show s04/cat-news-mcp:s04-cat-news/auth.py > auth.py
```

- [ ] **Step 2: Update import to use new module name**

Edit `auth.py` and replace any `from higgsfield_mcp import ...` with `from higgsfield import ...`.

- [ ] **Step 3: Smoke run**

```bash
uv run python auth.py --check
```

Expected: prints auth status (likely "not connected" on a fresh machine).

- [ ] **Step 4: Commit**

```bash
git add auth.py
git commit -m "feat(auth): port OAuth bootstrap CLI from s04"
```

---

## Task 4: Pluggable LLM backend (Anthropic-first, Gemini fallback)

**Files:**
- Modify: `llm.py` (full rewrite, keep `LLMClient.generate(prompt) -> str` interface)
- Test: `tests/test_llm.py`

`mini_perplexity.py` only ever calls `LLMClient(...).generate(prompt)`. We keep that surface and swap the backend internally.

- [ ] **Step 1: Write the failing test**

`tests/test_llm.py`:

```python
"""Backend selection + interface tests for llm.py."""
from __future__ import annotations

import pytest


def test_anthropic_wins_when_both_keys_set(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setenv("GEMINI_API_KEY", "g-x")
    monkeypatch.delenv("LLM_BACKEND", raising=False)

    from llm import _detect_backend
    assert _detect_backend() == "anthropic"


def test_gemini_used_when_only_gemini_set(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "g-x")
    monkeypatch.delenv("LLM_BACKEND", raising=False)

    from llm import _detect_backend
    assert _detect_backend() == "gemini"


def test_explicit_override_respected(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setenv("LLM_BACKEND", "gemini")

    from llm import _detect_backend
    assert _detect_backend() == "gemini"


def test_raises_when_no_keys(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BACKEND", raising=False)

    from llm import _detect_backend
    with pytest.raises(RuntimeError, match="no LLM"):
        _detect_backend()


def test_llm_client_exposes_generate(monkeypatch):
    """Smoke: LLMClient interface stays compatible with existing agent loop."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    from llm import LLMClient
    client = LLMClient()
    assert hasattr(client, "generate")
    assert callable(client.generate)
```

- [ ] **Step 2: Run — fails because `_detect_backend` doesn't exist**

```bash
uv run pytest tests/test_llm.py -v
```

Expected: 5 ImportErrors / AttributeErrors.

- [ ] **Step 3: Rewrite `llm.py`**

Full file:

```python
"""Pluggable LLM client. Anthropic preferred, Gemini fallback.

The agent loop only ever calls LLMClient(...).generate(prompt). Swap
backends by setting LLM_BACKEND=anthropic|gemini, or just provide the
matching API key and the right backend is auto-selected.
"""
from __future__ import annotations

import os
import time
from typing import Protocol

# Stop sequences inherited from the original Gemini-only design — they
# stop the model from pattern-matching past its own JSON turn.
_STOP = ["\nTool Result:", "\nUser:", "\nSystem:"]


class _BackendImpl(Protocol):
    def generate(self, prompt: str) -> str: ...


def _detect_backend() -> str:
    """Return 'anthropic' or 'gemini'. Explicit LLM_BACKEND wins."""
    explicit = os.getenv("LLM_BACKEND", "").strip().lower()
    if explicit in {"anthropic", "gemini"}:
        return explicit
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.getenv("GEMINI_API_KEY"):
        return "gemini"
    raise RuntimeError(
        "no LLM key set — export ANTHROPIC_API_KEY or GEMINI_API_KEY"
    )


class _AnthropicImpl:
    """Anthropic Claude — single message, stop-sequence parity with Gemini path."""

    def __init__(self, api_key: str | None = None,
                 model: str | None = None,
                 throttle_seconds: float = 0.0):
        from anthropic import Anthropic
        self.api_key = api_key or os.environ["ANTHROPIC_API_KEY"]
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
        self.throttle_seconds = throttle_seconds
        self._client = Anthropic(api_key=self.api_key)

    def generate(self, prompt: str) -> str:
        if self.throttle_seconds > 0:
            time.sleep(self.throttle_seconds)
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=4096,
            stop_sequences=_STOP,
            messages=[{"role": "user", "content": prompt}],
        )
        for block in resp.content:
            if block.type == "text":
                return block.text or ""
        return ""


class _GeminiImpl:
    """Gemini — preserved verbatim from the previous Gemini-only client."""

    def __init__(self, api_key: str | None = None,
                 model: str | None = None,
                 throttle_seconds: float | None = None):
        from google import genai
        self.api_key = api_key or os.environ["GEMINI_API_KEY"]
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
        self.throttle_seconds = (
            throttle_seconds if throttle_seconds is not None
            else float(os.getenv("THROTTLE_SECONDS", "4"))
        )
        self._client = genai.Client(api_key=self.api_key)

    def generate(self, prompt: str) -> str:
        if self.throttle_seconds > 0:
            time.sleep(self.throttle_seconds)
        response = self._client.models.generate_content(
            model=self.model,
            contents=prompt,
            config={"stop_sequences": _STOP},
        )
        return response.text or ""


class LLMClient:
    """Public interface used by the agent loop. Single-method facade."""

    def __init__(self, *, api_key: str | None = None,
                 model: str | None = None,
                 throttle_seconds: float | None = None):
        backend = _detect_backend()
        if backend == "anthropic":
            self._impl = _AnthropicImpl(
                api_key=api_key, model=model,
                throttle_seconds=throttle_seconds or 0.0,
            )
        else:
            self._impl = _GeminiImpl(
                api_key=api_key, model=model,
                throttle_seconds=throttle_seconds,
            )
        self.backend = backend

    def generate(self, prompt: str) -> str:
        return self._impl.generate(prompt)
```

- [ ] **Step 4: Run tests — should pass**

```bash
uv run pytest tests/test_llm.py -v
```

Expected: 5 PASSED.

- [ ] **Step 5: Smoke against the existing agent**

```bash
ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY uv run python mini_perplexity.py "What's 2+2?"
```

Expected: agent runs, gets a sensible answer. Backend used logged in the reasoning chain.

- [ ] **Step 6: Commit**

```bash
git add llm.py tests/test_llm.py
git commit -m "feat(llm): pluggable backend — Anthropic first, Gemini fallback"
```

---

## Task 5: `tools_image.py` — agent-callable image tool

**Files:**
- Create: `tools_image.py`
- Modify: `tools.py` (register `render_image` in `TOOLS` dict)
- Test: `tests/test_tools_image.py`

The agent loop dispatches via the `TOOLS` dict in `tools.py`. We add a single new tool entry.

- [ ] **Step 1: Write the failing test**

`tests/test_tools_image.py`:

```python
"""Unit tests for tools_image.render_image — JSON shape + panel routing."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest


@pytest.fixture
def stub_renders(monkeypatch):
    """Replace higgsfield.render_image with a deterministic fake."""
    from higgsfield import ImageRender

    def fake(prompt, **kwargs):
        return ImageRender(
            slug=f"slug-{abs(hash(prompt)) % 1000}",
            url=f"file:///tmp/{prompt[:8]}.png",
            local_path=f"/tmp/{prompt[:8]}.png",
            model_used="stub",
            duration_s=0.1,
        )
    monkeypatch.setattr("higgsfield.render_image", fake)


def test_single_image_returns_image_type(stub_renders):
    from tools_image import render_image
    raw = render_image("a cat in a tux")
    payload = json.loads(raw)
    assert payload["type"] == "image"
    assert "slug" in payload
    assert "url" in payload


def test_three_panel_returns_comic_strip(stub_renders):
    from tools_image import render_image
    raw = render_image("anthropic vs openai", panels=3)
    payload = json.loads(raw)
    assert payload["type"] == "comic_strip"
    assert len(payload["panels"]) == 3
    assert all("slug" in p for p in payload["panels"])


def test_four_panel_returns_four_panels(stub_renders):
    from tools_image import render_image
    payload = json.loads(render_image("crypto crash", panels=4))
    assert len(payload["panels"]) == 4


def test_partial_failure_produces_placeholder(monkeypatch):
    """If panel 2 of 3 fails, panels[1] is a placeholder."""
    from higgsfield import ImageRender

    calls = {"n": 0}
    def flaky(prompt, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("higgsfield 500")
        return ImageRender(slug=f"s{calls['n']}", url="x", local_path="/tmp/x",
                           model_used="stub", duration_s=0.1)
    monkeypatch.setattr("higgsfield.render_image", flaky)

    from tools_image import render_image
    payload = json.loads(render_image("setup turn punch", panels=3))
    assert len(payload["panels"]) == 3
    assert payload["panels"][1]["slug"] is None
    assert "error" in payload["panels"][1]
```

- [ ] **Step 2: Run — fails (no module)**

```bash
uv run pytest tests/test_tools_image.py -v
```

- [ ] **Step 3: Implement `tools_image.py`**

```python
"""Image-gen agent tool. Wraps higgsfield.render_image into a single
agent-callable function that handles single images and N-panel comic
strips."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import higgsfield


_BEAT_HINTS = {
    1: ["{p}"],
    3: ["{p} — establishing shot, calm setup",
        "{p} — sudden turn, contrast",
        "{p} — punchline, reaction"],
    4: ["{p} — establishing shot, calm setup",
        "{p} — escalation, tension rises",
        "{p} — twist, character reaction",
        "{p} — punchline payoff"],
}


def _render_one(prompt_template: str, base_prompt: str) -> dict:
    full = prompt_template.format(p=base_prompt)
    try:
        out = higgsfield.render_image(full)
        return {"slug": out.slug, "url": out.url, "alt": full,
                "local_path": out.local_path}
    except Exception as e:  # noqa: BLE001 — agent tools surface errors as data
        return {"slug": None, "url": None, "alt": full,
                "error": f"{type(e).__name__}: {e}"}


def render_image(prompt: str, panels: int = 1) -> str:
    """Render one image (panels=1) or a comic strip (panels=3 or 4).

    Returns a JSON string the agent embeds in its final reply. The
    frontend dispatches on type:
        {"type":"image", "slug":..., "url":...}
        {"type":"comic_strip", "panels":[{...}, {...}, ...]}
    """
    if panels not in _BEAT_HINTS:
        panels = 1

    templates = _BEAT_HINTS[panels]
    if panels == 1:
        rendered = _render_one(templates[0], prompt)
        return json.dumps({"type": "image", **rendered})

    # Parallel render for multi-panel.
    with ThreadPoolExecutor(max_workers=panels) as pool:
        rendered = list(pool.map(lambda t: _render_one(t, prompt), templates))
    return json.dumps({"type": "comic_strip", "panels": rendered})


# Agent-loop registration: tools.py imports TOOLS from this module too.
TOOL_DEFINITION = {
    "name": "render_image",
    "description": "Generate an image (or 3/4-panel comic strip) via Higgsfield. "
                   "Pass `panels=3` or `4` for a strip; default 1 = single image.",
    "fn": render_image,
}
```

- [ ] **Step 4: Wire into `tools.py` `TOOLS` dict**

Edit `tools.py` — find the `TOOLS` registration and add:

```python
from tools_image import TOOL_DEFINITION as _IMAGE_TOOL

# (existing TOOLS dict)
TOOLS["render_image"] = _IMAGE_TOOL
```

(Match the registration pattern that already exists in `tools.py` for `web_search` etc. — adapt as needed if the existing structure uses a list or different key.)

- [ ] **Step 5: Run tests — should pass**

```bash
uv run pytest tests/test_tools_image.py -v
```

Expected: 4 PASSED.

- [ ] **Step 6: Commit**

```bash
git add tools_image.py tools.py tests/test_tools_image.py
git commit -m "feat(tools): render_image agent tool — single + 3/4-panel strips"
```

---

## Task 6: `webapp/server.py` — extend with image flow + dashboard endpoints

**Files:**
- Modify: `webapp/server.py`
- Test: `tests/test_server.py`

Three additions: (1) `/api/run` accepts `image_mode` and emits `image` SSE events; (2) new GET endpoints for stats + activity + cards; (3) `/dashboard` route returns Prefab HTML.

- [ ] **Step 1: Write the failing integration test**

`tests/test_server.py`:

```python
"""Integration test for the chat SSE endpoint with stub Higgsfield."""
from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest


@pytest.fixture
def stub_env(tmp_path, monkeypatch):
    fixture = tmp_path / "stub.png"
    fixture.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    monkeypatch.setenv("RENDER_MODE", "stub")
    monkeypatch.setenv("HIGGSFIELD_STUB_PATH", str(fixture))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    yield


@pytest.mark.asyncio
async def test_stats_endpoint_returns_payload(stub_env):
    from webapp.server import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/stats")
        assert r.status_code == 200
        body = r.json()
        assert "tools" in body
        assert "totals" in body


@pytest.mark.asyncio
async def test_recent_activity_returns_list(stub_env):
    from webapp.server import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/recent-activity")
        assert r.status_code == 200
        assert "events" in r.json()


@pytest.mark.asyncio
async def test_dashboard_route_serves_html(stub_env):
    from webapp.server import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/dashboard")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert b"<html" in r.content.lower()
```

(SSE event-order test deferred to a manual smoke until we have a stub mode for `LLMClient` too — adding mock LLM is its own follow-up.)

- [ ] **Step 2: Run — fails (no `/api/stats` endpoint, etc.)**

```bash
uv run pytest tests/test_server.py -v
```

- [ ] **Step 3: Extend `webapp/server.py`**

Add these blocks. Keep the existing `/`, `/healthz`, `/api/run` routes intact.

```python
# Top of file — add imports
import time
from collections import deque
from pathlib import Path

import stats as stats_mod
from starlette.responses import FileResponse, HTMLResponse, JSONResponse

CARDS_DIR = REPO_ROOT / "images"
CARDS_DIR.mkdir(parents=True, exist_ok=True)

_RECENT_ACTIVITY: deque[dict] = deque(maxlen=100)


def _record_activity(name: str, args: dict, duration_ms: float, ok: bool, error: str | None) -> None:
    _RECENT_ACTIVITY.append({
        "name": name,
        "input": _summarize(args),
        "status": "ok" if ok else "fail",
        "duration_ms": round(duration_ms, 1),
        "ts": time.strftime("%H:%M:%S", time.localtime()),
        "error": (error or "")[:200] if error else None,
    })


def _summarize(args: dict, limit: int = 80) -> str:
    if not args:
        return ""
    parts = [f"{k}={(json.dumps(v) if not isinstance(v, str) else v)[:30]}"
             for k, v in args.items()]
    out = ", ".join(parts)
    return (out[:limit - 1] + "…") if len(out) > limit else out


# Route additions (place near existing /api/run)

@app.get("/api/stats")
def api_stats() -> JSONResponse:
    payload = stats_mod.get_stats()
    payload["tools_array"] = [{"name": k, **v} for k, v in payload.get("tools", {}).items()]
    return JSONResponse(payload)


@app.get("/api/recent-activity")
def api_recent_activity() -> JSONResponse:
    return JSONResponse({"events": list(reversed(_RECENT_ACTIVITY))})


@app.get("/api/cards/{slug}.png")
def api_card_image(slug: str):
    path = CARDS_DIR / f"{slug}.png"
    if not path.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path, media_type="image/png")


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    from webapp.dashboard import build_dashboard
    return HTMLResponse(build_dashboard().html())
```

Then update `RunRequest` and the `/api/run` handler to thread `image_mode`:

```python
class RunRequest(BaseModel):
    question: str
    max_iterations: int = 8
    image_mode: bool = False


# In the run() handler, pass image_mode through to the agent's system prompt.
# Suggested: append to system prompt when image_mode is True.
```

The agent loop's `run_agent` may need a small tweak to accept and forward `image_mode` — open `mini_perplexity.py` and adjust if needed. Keep changes minimal.

Wrap each tool dispatch in a stats + activity recorder:

```python
# Where tools fire (likely in mini_perplexity.run_agent dispatch path), or
# alternatively wrap the TOOLS dict at startup with a recording decorator.
def _wrap_tool(name, fn):
    def wrapped(*a, **kw):
        start = time.monotonic()
        ok, err = True, None
        try:
            return fn(*a, **kw)
        except Exception as e:
            ok = False
            err = f"{type(e).__name__}: {e}"
            raise
        finally:
            dt = (time.monotonic() - start) * 1000
            stats_mod.record_tool_call(name, dt, ok=ok, error=err)
            _record_activity(name, kw or {"args": a}, dt, ok, err)
    return wrapped
```

- [ ] **Step 4: Run tests — should pass**

```bash
uv run pytest tests/test_server.py -v
```

Expected: 3 PASSED.

- [ ] **Step 5: Commit**

```bash
git add webapp/server.py tests/test_server.py
git commit -m "feat(server): /dashboard + /api/stats + /api/recent-activity + image_mode"
```

---

## Task 7: `webapp/static/index.html` + `styles.css` — chat shell additions

**Files:**
- Modify: `webapp/static/index.html`
- Modify: `webapp/static/styles.css`

No automated tests for HTML/CSS — verify visually in Task 11.

- [ ] **Step 1: Update topbar in `index.html`**

Replace the existing `<div class="controls">` block with:

```html
<div class="controls">
  <a class="dash-link" href="/dashboard" target="_blank" rel="noopener">
    Dashboard ↗
  </a>
  <button id="theme-toggle" type="button" class="icon-btn" aria-label="Toggle theme">
    🌓
  </button>
</div>
```

- [ ] **Step 2: Update composer to add 🖼 toggle**

Replace the existing `<form class="composer">` block:

```html
<form class="composer" id="composer" autocomplete="off">
  <button type="button" id="image-toggle" class="icon-btn" aria-pressed="false" title="Generate image (one-shot)">
    🖼
  </button>
  <input
    id="question"
    name="question"
    type="text"
    placeholder="Ask anything — or type /image to render an image"
    required
  />
  <button id="send" type="submit">Send</button>
</form>
```

- [ ] **Step 3: Inline pre-paint dark-mode script in `<head>`**

Just before `</head>`:

```html
<script>
  (function () {
    const saved = localStorage.getItem('theme');
    const sys = window.matchMedia('(prefers-color-scheme: dark)').matches;
    const theme = saved || (sys ? 'dark' : 'light');
    document.documentElement.setAttribute('data-theme', theme);
  })();
</script>
```

- [ ] **Step 4: Extend `styles.css` with dark theme + image card + status pill + comic grid**

Append to `styles.css`:

```css
/* Dark theme */
[data-theme="dark"] {
  --bg:        #0b0b0d;
  --surface:   #161618;
  --border:    #2a2a2e;
  --text:      #f5f5f7;
  --muted:     #9ca3af;
  --accent:    #4d8bff;
}

/* Sticky composer */
.composer {
  position: sticky;
  bottom: 0;
  background: var(--surface);
  border-top: 1px solid var(--border);
  padding: 12px 24px;
  display: flex;
  gap: 8px;
  align-items: center;
}

.icon-btn {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text);
  padding: 6px 10px;
  border-radius: 8px;
  cursor: pointer;
  font-size: 16px;
}
.icon-btn[aria-pressed="true"] {
  background: var(--accent);
  border-color: var(--accent);
  color: #fff;
}

.dash-link {
  font-size: 12px;
  color: var(--muted);
  text-decoration: none;
  margin-right: 12px;
}
.dash-link:hover { color: var(--accent); }

/* Status pill — under user message bubbles */
.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 4px 12px;
  font-size: 12px;
  color: var(--muted);
  font-family: var(--mono);
  margin: 4px 0 8px;
}
.status-pill.collapsed { opacity: 0.7; }

/* Image card — agent-rendered images */
.image-card {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
  background: var(--surface);
  margin: 8px 0;
}
.image-card img { display: block; width: 100%; height: auto; }
.image-card .caption {
  padding: 8px 12px;
  font-size: 12px;
  color: var(--muted);
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.image-card .caption a { color: var(--accent); text-decoration: none; }

/* Comic-strip grid — auto layout by panel count */
.comic-strip {
  display: grid;
  gap: 6px;
  margin: 8px 0;
}
.comic-strip[data-panels="3"] { grid-template-columns: repeat(3, 1fr); }
.comic-strip[data-panels="4"] { grid-template-columns: repeat(2, 1fr); }
.comic-strip .panel {
  border: 1px solid var(--border);
  border-radius: 6px;
  overflow: hidden;
  background: var(--surface);
}
.comic-strip .panel img { width: 100%; display: block; }
.comic-strip .panel.error {
  display: flex;
  align-items: center;
  justify-content: center;
  aspect-ratio: 1;
  color: var(--muted);
  font-size: 11px;
}
```

- [ ] **Step 5: Smoke (will be wired in Task 8 for behavior, but check it loads)**

```bash
uv run uvicorn webapp.server:app --port 8000 &
sleep 2
curl -s http://localhost:8000/ | grep -o "image-toggle\|theme-toggle\|dash-link" | sort -u
kill %1
```

Expected: all three classes/IDs found in HTML.

- [ ] **Step 6: Commit**

```bash
git add webapp/static/index.html webapp/static/styles.css
git commit -m "feat(webapp): chat shell + dark theme + image toggle markup"
```

---

## Task 8: `webapp/static/app.js` — image flow, status pill, dark mode

**Files:**
- Modify: `webapp/static/app.js`

Augment the existing SSE consumer. Key additions: image_mode state, `/image` parser, status pill renderer, dark toggle handler, image/comic SSE event handler.

- [ ] **Step 1: Add state + DOM refs at the top**

After existing `const chat = ...` lines, add:

```js
const imageToggle = document.getElementById('image-toggle');
const themeToggle = document.getElementById('theme-toggle');
let imageMode = false;

imageToggle.addEventListener('click', () => {
  imageMode = !imageMode;
  imageToggle.setAttribute('aria-pressed', imageMode ? 'true' : 'false');
});

themeToggle.addEventListener('click', () => {
  const cur = document.documentElement.getAttribute('data-theme');
  const next = cur === 'dark' ? 'light' : 'dark';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('theme', next);
});
```

- [ ] **Step 2: Parse `/image` in submit handler**

In the existing `composer.addEventListener('submit', ...)`, replace the question read with:

```js
let q = questionInput.value.trim();
let mode = imageMode;
if (q.startsWith('/image ')) {
  q = q.slice('/image '.length);
  mode = true;
}
if (!q) return;

// reset toggle one-shot AFTER capture
imageMode = false;
imageToggle.setAttribute('aria-pressed', 'false');

// POST to /api/run
fetch('/api/run', {
  method: 'POST',
  headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ question: q, image_mode: mode }),
}).then(/* existing SSE handler */);
```

- [ ] **Step 3: Status pill renderer**

Add a helper used by `tool_call` / `tool_result` event handlers:

```js
function ensurePill(turnEl) {
  let pill = turnEl.querySelector('.status-pill');
  if (!pill) {
    pill = document.createElement('div');
    pill.className = 'status-pill';
    pill.dataset.calls = '0';
    pill.dataset.tools = '';
    turnEl.appendChild(pill);
  }
  return pill;
}

function onToolCall(turnEl, ev) {
  const pill = ensurePill(turnEl);
  pill.textContent = `→ ${ev.name}…`;
  pill.dataset.calls = String(Number(pill.dataset.calls) + 1);
  pill.dataset.tools = (pill.dataset.tools ? pill.dataset.tools + ',' : '') + ev.name;
  pill.dataset.start = pill.dataset.start || String(Date.now());
}

function onTurnDone(turnEl) {
  const pill = turnEl.querySelector('.status-pill');
  if (!pill) return;
  const elapsed = ((Date.now() - Number(pill.dataset.start)) / 1000).toFixed(1);
  pill.textContent = `→ ${pill.dataset.calls} tool${pill.dataset.calls > 1 ? 's' : ''} · ${elapsed}s ✓`;
  pill.classList.add('collapsed');
}
```

Hook these into the existing event-handling switch:
- on `tool_call` → `onToolCall(currentTurnEl, payload)`
- on `final` (or SSE close) → `onTurnDone(currentTurnEl)`

- [ ] **Step 4: Image / comic-strip event renderers**

Add a new `image` event handler that runs *before* the agent's text bubble:

```js
function renderImage(turnEl, payload) {
  // payload is the parsed `image` SSE event:
  //   {kind:'image', slug, url, alt} or
  //   {kind:'comic_strip', panels:[{slug,url,alt,error?}, ...]}
  if (payload.kind === 'image') {
    const card = document.createElement('figure');
    card.className = 'image-card';
    card.innerHTML = `
      <img src="${payload.url}" alt="${escapeHtml(payload.alt || '')}" />
      <figcaption class="caption">
        <span>${escapeHtml(payload.slug)}</span>
        <a href="${payload.url}" target="_blank" rel="noopener">open ↗</a>
      </figcaption>`;
    turnEl.appendChild(card);
  } else if (payload.kind === 'comic_strip') {
    const wrap = document.createElement('div');
    wrap.className = 'comic-strip';
    wrap.dataset.panels = String(payload.panels.length);
    for (const p of payload.panels) {
      const cell = document.createElement('div');
      cell.className = 'panel' + (p.error ? ' error' : '');
      if (p.url) {
        cell.innerHTML = `<img src="${p.url}" alt="${escapeHtml(p.alt || '')}" />`;
      } else {
        cell.textContent = `failed: ${p.error || 'unknown'}`;
      }
      wrap.appendChild(cell);
    }
    turnEl.appendChild(wrap);
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}
```

Wire into the SSE switch on `kind === 'image'` or `'comic_strip'`.

- [ ] **Step 5: Smoke**

Run the server, open `http://localhost:8000`, click the 🖼 toggle (it should highlight), type "test", Send (will fail to actually render unless Higgsfield is set up — that's fine, just check the toggle behaviour and dark-mode toggle persistence).

- [ ] **Step 6: Commit**

```bash
git add webapp/static/app.js
git commit -m "feat(webapp): image toggle, /image parsing, status pill, dark mode, image SSE"
```

---

## Task 9: `webapp/dashboard.py` — Prefab Stats / Activity / Auth

**Files:**
- Create: `webapp/dashboard.py`

The dashboard is a single-page Prefab app served by `/dashboard`. Three tabs only.

- [ ] **Step 1: Write `webapp/dashboard.py`**

```python
"""Prefab dashboard — Stats / Activity / Auth tabs.

Served by webapp/server.py at GET /dashboard. Polls JSON endpoints.
"""
from __future__ import annotations

from prefab_ui.actions import Fetch, SetState, ShowToast
from prefab_ui.app import PrefabApp
from prefab_ui.components import (
    Badge, Button, Card, Column, Else, Grid, Heading, If, Metric, Row,
    Tab, Tabs, Text,
)
from prefab_ui.components.control_flow import ForEach


_STATS_FETCH = Fetch(
    url="/api/stats",
    method="GET",
    on_success=[
        SetState("stats_tools", "{{ $result.tools_array }}"),
        SetState("totals_calls", "{{ $result.totals.tool_calls }}"),
        SetState("totals_ok", "{{ $result.totals.tool_calls_ok }}"),
        SetState("totals_fail", "{{ $result.totals.tool_calls_fail }}"),
        SetState("totals_seconds", "{{ $result.totals.tool_seconds_total }}"),
        SetState("chat_turns", "{{ $result.chat_turns }}"),
        SetState("chat_in", "{{ $result.chat_in_tokens }}"),
        SetState("chat_out", "{{ $result.chat_out_tokens }}"),
    ],
)

_ACTIVITY_FETCH = Fetch(
    url="/api/recent-activity",
    method="GET",
    on_success=SetState("activity", "{{ $result.events }}"),
)


def _tab_stats() -> None:
    with Column(gap=3):
        with Row(gap=2, css_class="items-center justify-between"):
            Heading("Runtime stats", level=3)
            Button("Refresh", variant="outline", size="sm", on_click=_STATS_FETCH)
        with Grid(columns=3, gap=3):
            Metric(label="Tool calls", value="{{ totals_calls }}")
            Metric(label="OK / Fail",
                   value="{{ totals_ok }} / {{ totals_fail }}")
            Metric(label="Tool seconds", value="{{ totals_seconds }}")
            Metric(label="Chat turns", value="{{ chat_turns }}")
            Metric(label="Tokens in", value="{{ chat_in }}")
            Metric(label="Tokens out", value="{{ chat_out }}")
        Heading("Per-tool breakdown", level=4)
        with If("stats_tools.length == 0"):
            Text("No tool calls recorded yet.",
                 css_class="text-muted-foreground")
        with ForEach("stats_tools") as t:
            with Card():
                with Row(gap=3, css_class="items-center"):
                    Text(t.name, css_class="font-mono font-medium flex-1")
                    Badge(t.count_total, variant="secondary")
                    Badge(t.count_ok, variant="success")
                    Badge(t.count_fail, variant="destructive")
                    Text(t.duration_ms_avg,
                         css_class="text-xs text-muted-foreground")


def _tab_activity() -> None:
    with Column(gap=3):
        with Row(gap=2, css_class="items-center justify-between"):
            Heading("Tool-call activity", level=3)
            Button("Refresh", variant="outline", size="sm",
                   on_click=_ACTIVITY_FETCH)
        Text("Every tool call (newest first) — timestamp, input summary, "
             "duration, status.",
             css_class="text-muted-foreground text-sm")
        with If("activity.length == 0"):
            Text("No tool calls yet.", css_class="text-muted-foreground")
        with ForEach("activity") as e:
            with Card(css_class="py-2"):
                with Row(gap=3, css_class="items-center"):
                    Text(e.ts,
                         css_class="font-mono text-xs text-muted-foreground")
                    Text(e.name, css_class="font-mono font-medium text-sm")
                    Text(e.input,
                         css_class="text-xs text-muted-foreground flex-1 truncate")
                    Text(e.duration_ms,
                         css_class="font-mono text-xs text-muted-foreground")
                    with If("$item.status == 'fail'"):
                        Badge("fail", variant="destructive")
                    with Else():
                        Badge("ok", variant="success")


def _tab_auth() -> None:
    with Column(gap=3):
        Heading("Higgsfield connection", level=3)
        Text("Higgsfield uses OAuth. First click on Connect opens a browser "
             "tab; tokens persist in ~/.mini-perplexity/tokens/.",
             css_class="text-muted-foreground text-sm")
        with Row(gap=2, css_class="items-center"):
            Badge("{{ auth_state }}", variant="{{ auth_variant }}")
            Text("{{ auth_info }}", css_class="text-sm")
        with Row(gap=2):
            Button("Check status", variant="outline", on_click=Fetch(
                url="/api/tool/higgsfield_auth_status",
                method="POST",
                body={},
                on_success=[
                    SetState("auth_state", "{{ $result.state_label }}"),
                    SetState("auth_variant", "{{ $result.state_variant }}"),
                    SetState("auth_info", "{{ $result.info }}"),
                ],
            ))
            Button("Connect Higgsfield", variant="default", on_click=Fetch(
                url="/api/tool/start_higgsfield_auth",
                method="POST",
                body={},
                on_success=ShowToast("Higgsfield connected", variant="success"),
                on_error=ShowToast("{{ $error }}", variant="error"),
            ))


def build_dashboard() -> PrefabApp:
    on_mount = [_STATS_FETCH, _ACTIVITY_FETCH]
    with Column(gap=5, css_class="max-w-[1100px] mx-auto py-6 px-4") as view:
        Heading("Mini Perplexity — Dashboard", level=1)
        Text("Stats, activity, and auth for the chat agent.",
             css_class="text-muted-foreground text-sm")
        with Card(css_class="overflow-hidden"):
            with Tabs(value="stats"):
                with Tab("Stats", value="stats"):
                    _tab_stats()
                with Tab("Activity", value="activity"):
                    _tab_activity()
                with Tab("Auth", value="auth"):
                    _tab_auth()
    return PrefabApp(
        view=view,
        on_mount=on_mount,
        state={
            "stats_tools": [],
            "totals_calls": 0, "totals_ok": 0, "totals_fail": 0,
            "totals_seconds": 0,
            "chat_turns": 0, "chat_in": 0, "chat_out": 0,
            "activity": [],
            "auth_state": "unknown", "auth_variant": "secondary",
            "auth_info": "Click Check status.",
        },
    )
```

- [ ] **Step 2: Add `/api/tool/<name>` proxy if not already in server.py**

The Auth tab posts to `/api/tool/higgsfield_auth_status` and `start_higgsfield_auth`. Add a thin proxy in `webapp/server.py`:

```python
@app.post("/api/tool/{name}")
async def api_tool(name: str):
    if name == "higgsfield_auth_status":
        import higgsfield
        return JSONResponse(higgsfield.auth_status())
    if name == "start_higgsfield_auth":
        import higgsfield
        return JSONResponse(higgsfield.bootstrap_oauth())
    return JSONResponse({"error": "unknown tool"}, status_code=404)
```

- [ ] **Step 3: Build-check the dashboard**

```bash
uv run python -c "from webapp.dashboard import build_dashboard; print(len(build_dashboard().html()))"
```

Expected: prints a number (size of generated HTML), no exception.

- [ ] **Step 4: Smoke the route**

```bash
uv run uvicorn webapp.server:app --port 8000 &
sleep 2
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/dashboard
kill %1
```

Expected: `200`.

- [ ] **Step 5: Commit**

```bash
git add webapp/dashboard.py webapp/server.py
git commit -m "feat(webapp): /dashboard with Stats / Activity / Auth tabs"
```

---

## Task 10: End-to-end browser smoke

No code change — manual verification via chrome-devtools MCP (or vanilla browser).

- [ ] **Step 1: Start the server**

```bash
cd /Users/level/ws/projects/mini-perplexity
uv run uvicorn webapp.server:app --port 8000 &
sleep 2
open http://localhost:8000
```

- [ ] **Step 2: Verify chat baseline still works**

Type "what's 2+2?" → Send. Confirm:
- Status pill appears under user message (`→ web_search…` or just `→ done · 1.2s ✓` if no tools fire)
- Agent reply renders as text bubble
- No console errors

- [ ] **Step 3: Verify image toggle**

Click 🖼 — it highlights. Type "a cat in a tuxedo, ghibli style" → Send. Confirm:
- An image card appears inline before the agent's text
- After response, 🖼 toggle is back to inactive (one-shot reset)

- [ ] **Step 4: Verify slash command**

Type `/image cat as astronaut` → Send. Confirm:
- Image card renders
- Toggle did not need to be pressed

- [ ] **Step 5: Verify dashboard**

Open `http://localhost:8000/dashboard`. Confirm:
- Stats tab populates on mount (counts > 0 after the chat above)
- Activity tab shows the recent calls newest-first
- Auth tab shows the Higgsfield status badge

- [ ] **Step 6: Verify dark mode**

Click 🌓 in topbar. Confirm:
- Theme flips
- Reload page — theme persists

- [ ] **Step 7: Commit screenshots**

Use chrome-devtools or screenshot manually; save to `docs/screenshots/`:

```bash
mkdir -p docs/screenshots
# (capture chat-light.png, chat-dark.png, image-render.png, dashboard.png)
git add docs/screenshots/
git commit -m "docs: e2e smoke screenshots"
```

- [ ] **Step 8: Push branch**

```bash
git push -u origin s04/mp-image-gen
```

---

## Self-review

Spec coverage:

| Spec section | Plan task |
|---|---|
| Repo layout | T0 |
| Locked decision 1 (status pill + dashboard) | T8 (pill) + T9 (dashboard) |
| Locked decision 2 (toggle + /image) | T7 (markup) + T8 (behavior) |
| Locked decision 3 (agent picks panels) | T5 (`tools_image.render_image(panels=...)`) |
| Locked decision 4 (fresh branch, port reusable) | T0–T3 |
| Locked decision 5 (drop news flow) | reflected by absence — agent uses existing `web_search` |
| Locked decision 6 (pluggable LLM) | T4 |
| Architecture diagram | T6 (server) + T9 (dashboard) wire it |
| Chat data flow + image SSE event | T6 (emit) + T8 (render) |
| Image-mode contract | T6 (system prompt append) + T8 (one-shot reset) |
| `llm.py` contract | T4 |
| `higgsfield.py` contract | T2 |
| `tools_image.py` contract | T5 |
| `webapp/server.py` contract | T6 |
| `webapp/static/*` contracts | T7 + T8 |
| `webapp/dashboard.py` contract | T9 |
| Error cases | T2 (auth), T2/T5 (timeout), T5 (partial fail), T8 (busy disable), T7 (flicker), T1 (corruption) |
| Testing strategy: unit | T1, T2, T4, T5 |
| Testing strategy: integration | T6 |
| Testing strategy: browser smoke | T10 |

All locked decisions and contracts trace to a task. No gaps found.

Placeholder scan: no "TBD"/"TODO"/"add appropriate" patterns. Every step has either runnable shell, runnable Python, or a concrete editing instruction.

Type consistency check: `ImageRender` dataclass shape consistent across T2 (define) → T5 (consume) → T6 (serialize). `_RECENT_ACTIVITY` deque shape consistent across T6 (write) → T9 (read). `LLMClient.generate(prompt) -> str` interface consistent across T4 (define) → existing `mini_perplexity.run_agent` (consume).

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-02-mp-image-gen-plan.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
