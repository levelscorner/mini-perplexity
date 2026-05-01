"""Higgsfield image-gen wrapper.

Single public function: `render_image(prompt, *, model, aspect, refs)`.
OAuth via fastmcp.client.auth.OAuth + DiskStore at ~/.mini-perplexity/tokens/.

Env:
    HIGGSFIELD_MCP_URL   — defaults to https://mcp.higgsfield.ai/mcp
    RENDER_MODE          — "live" | "stub" (stub returns a fixture image)
    HIGGSFIELD_STUB_PATH — path to fixture PNG used in stub mode
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

import requests
from fastmcp import Client
from fastmcp.client.auth import OAuth
from key_value.aio.stores.disk import DiskStore


HIGGSFIELD_URL = os.getenv("HIGGSFIELD_MCP_URL", "https://mcp.higgsfield.ai/mcp")
TOKEN_DIR = Path.home() / ".mini-perplexity" / "tokens"
TOKEN_DIR.mkdir(parents=True, exist_ok=True)

POLL_INTERVAL_SECONDS = 5
# Higgsfield jobs queue when many fire in parallel; 1200s headroom proven on s04.
MAX_WAIT_SECONDS = 1200

# Keywords that bias toward flux_2 (illustrated / drawn output).
_CARTOON_KEYWORDS = (
    "cartoon", "comic", "comic strip", "strip panel",
    "pixar", "ghibli", "manga", "anime",
    "sketch", "illustration", "drawn",
)


@dataclass(frozen=True)
class ImageRender:
    """Public contract — what render_image returns."""
    slug: str
    url: str
    local_path: str
    model_used: str
    duration_s: float


# ---------------------------------------------------------------------------
# Model routing
# ---------------------------------------------------------------------------
def _pick_model(prompt: str, *, refs: list[str] | None) -> str:
    """Priority: refs → nano_banana_2; cartoon → flux_2; else gpt_image_2."""
    if refs:
        return "nano_banana_2"
    p = prompt.lower()
    if any(k in p for k in _CARTOON_KEYWORDS):
        return "flux_2"
    return "gpt_image_2"


# ---------------------------------------------------------------------------
# OAuth + token persistence
# ---------------------------------------------------------------------------
def _token_storage() -> DiskStore:
    """Disk-backed token cache. Survives across runs."""
    return DiskStore(directory=str(TOKEN_DIR))


def _oauth() -> OAuth:
    """FastMCP OAuth helper preconfigured for Higgsfield."""
    return OAuth(
        mcp_url=HIGGSFIELD_URL,
        client_name="mini-perplexity",
        scopes=None,
        token_storage=_token_storage(),
        callback_port=None,
    )


def auth_status() -> dict[str, Any]:
    """Probe whether cached tokens authenticate against Higgsfield.

    Returns a dashboard-friendly dict:
        {ok, info, state_label, state_variant}
    """
    try:
        ok, info = asyncio.run(_auth_status_async())
    except Exception as e:  # noqa: BLE001
        ok, info = False, f"{type(e).__name__}: {e}"
    return {
        "ok": ok,
        "info": info,
        "state_label": "connected" if ok else "not connected",
        "state_variant": "success" if ok else "destructive",
    }


async def _auth_status_async() -> tuple[bool, str]:
    async with Client(HIGGSFIELD_URL, auth=_oauth()) as client:
        tools = await client.list_tools()
        names = [t.name for t in tools[:6]]
        return True, (
            f"Higgsfield reachable. {len(tools)} tools available; "
            f"first few: {', '.join(names)}{'…' if len(tools) > 6 else ''}"
        )


def bootstrap_oauth() -> dict[str, Any]:
    """Run the OAuth browser flow once. Idempotent — no-op if tokens fresh."""
    try:
        asyncio.run(_bootstrap_async())
        return auth_status()
    except Exception as e:  # noqa: BLE001
        return {
            "ok": False,
            "info": f"{type(e).__name__}: {e}",
            "state_label": "failed",
            "state_variant": "destructive",
        }


async def _bootstrap_async() -> None:
    async with Client(HIGGSFIELD_URL, auth=_oauth()) as client:
        await client.list_tools()


# ---------------------------------------------------------------------------
# Result unwrapping (FastMCP CallToolResult -> dict)
# ---------------------------------------------------------------------------
def _unwrap(result: Any) -> dict[str, Any]:
    """Pull structured payload off a CallToolResult.

    Tries:
      1. result.structured_content (FastMCP newer SDK)
      2. result.structuredContent (older official MCP SDK alias)
      3. result.content[0].text parsed as JSON
      4. result.content[0].text returned as {"text": ...}
    """
    if getattr(result, "is_error", False):
        raise RuntimeError(
            f"Higgsfield tool reported error: "
            f"{getattr(result, 'data', None) or getattr(result, 'content', None)}"
        )
    sc = getattr(result, "structured_content", None) or getattr(
        result, "structuredContent", None
    )
    if sc:
        return sc
    for block in (getattr(result, "content", None) or []):
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


# ---------------------------------------------------------------------------
# Live render path
# ---------------------------------------------------------------------------
def _download(url: str, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    resp = requests.get(url, stream=True, timeout=60)
    resp.raise_for_status()
    with open(dst, "wb") as f:
        for chunk in resp.iter_content(chunk_size=64 * 1024):
            f.write(chunk)
    return dst


async def _poll_until_done(client: Client, job_id: str,
                           timeout_s: int = MAX_WAIT_SECONDS) -> dict[str, Any]:
    """Poll Higgsfield's job_display tool until completed/failed."""
    start = time.monotonic()
    while time.monotonic() - start < timeout_s:
        result = await client.call_tool("job_display", {"ids": [job_id]})
        payload = _unwrap(result)
        items = payload.get("results", [])
        if items:
            job = items[0]
            status = job.get("status")
            if status == "completed":
                return job
            if status == "failed":
                raise RuntimeError(
                    f"Higgsfield job {job_id} failed (model={job.get('model')})"
                )
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    raise TimeoutError(f"Higgsfield job {job_id} did not complete in {timeout_s}s")


async def _render_async(prompt: str, model: str, aspect: str,
                        refs: list[str] | None,
                        save_to: Path) -> tuple[str, str, str]:
    """Single Higgsfield call. Returns (job_id, image_url, local_path_str)."""
    params: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "aspect_ratio": aspect,
        "resolution": "2k",
    }
    if refs:
        params["medias"] = [{"role": "image", "value": url} for url in refs]
    if model == "gpt_image_2":
        params["quality"] = "high"

    async with Client(HIGGSFIELD_URL, auth=_oauth()) as client:
        result = await client.call_tool("generate_image", {"params": params})
        payload = _unwrap(result)
        items = payload.get("results", [])
        if not items:
            raise RuntimeError(f"no results from generate_image: {payload}")
        job_id = items[0].get("id")
        if not job_id:
            raise RuntimeError(f"no job_id from Higgsfield: {payload}")
        done = await _poll_until_done(client, job_id)
        image_url = done.get("results", {}).get("rawUrl")
        if not image_url:
            raise RuntimeError(f"no rawUrl in completed job: {done}")
        local = _download(image_url, save_to)
        return job_id, image_url, str(local)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def render_image(prompt: str, *, model: str | None = None,
                 aspect: str = "1:1",
                 refs: list[str] | None = None) -> ImageRender:
    """Render one image. Synchronous wrapper around the async client.

    Args:
        prompt: Text description of what to render.
        model: Override Higgsfield model. None = auto-route via _pick_model.
        aspect: Aspect ratio string ("1:1", "16:9", etc).
        refs: Optional reference image URLs to condition on (forces nano_banana_2).
    """
    if os.getenv("RENDER_MODE", "live").lower() == "stub":
        return _stub_render(prompt)

    chosen = model or _pick_model(prompt, refs=refs)
    slug = f"{int(time.time())}-{uuid.uuid4().hex[:8]}"
    save_to = Path(__file__).resolve().parent / "images" / f"{slug}.png"

    start = time.monotonic()
    _job_id, url, local_path = asyncio.run(
        _render_async(prompt, chosen, aspect, refs, save_to)
    )
    return ImageRender(
        slug=slug,
        url=url,
        local_path=local_path,
        model_used=chosen,
        duration_s=round(time.monotonic() - start, 2),
    )


def _stub_render(prompt: str) -> ImageRender:
    """Stub mode: copy a fixture into images/<slug>.png so the regular
    /api/cards/<slug>.png route serves it. No Higgsfield call."""
    import shutil

    fixture = os.getenv("HIGGSFIELD_STUB_PATH")
    if not fixture or not Path(fixture).exists():
        raise RuntimeError(
            f"HIGGSFIELD_STUB_PATH missing or invalid: {fixture}. "
            "Set it to a valid PNG path."
        )
    slug = f"stub-{uuid.uuid4().hex[:8]}"
    images_dir = Path(__file__).resolve().parent / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    dst = images_dir / f"{slug}.png"
    shutil.copy(fixture, dst)
    return ImageRender(
        slug=slug,
        # Relative URL — the browser hits the FastAPI server's
        # /api/cards/<slug>.png route, which serves the file.
        url=f"/api/cards/{slug}.png",
        local_path=str(dst),
        model_used="stub",
        duration_s=0.0,
    )
