"""Dashboard pinning tool — agent writes a card; the Feed tab renders it.

Returns the pinned item's slug + path. The webapp's /api/feed endpoint
walks the same directory and serves the cards newest-first to the
Prefab dashboard's Feed tab.

Storage shape (feed/<slug>.json):

    {
        "slug":       "20260502-tata-sons-ownership",
        "title":      "Tata Sons ownership",
        "content":    "<markdown body>",
        "kind":       "note" | "answer" | "image" | "link",
        "pinned_at":  "2026-05-02T03:14:15Z"
    }
"""
from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path


HERE = Path(__file__).resolve().parent
FEED_DIR = HERE / "feed"
FEED_DIR.mkdir(parents=True, exist_ok=True)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str, max_len: int = 50) -> str:
    """ASCII-safe filename slug from arbitrary text."""
    s = _SLUG_RE.sub("-", (text or "").lower()).strip("-")
    return (s[:max_len] or uuid.uuid4().hex[:8]).strip("-")


def pin_to_dashboard(title: str, content: str, kind: str = "note") -> str:
    """Pin a card to the dashboard Feed tab.

    Args:
        title:   Short heading, also used to build the slug.
        content: Body markdown (rendered as Markdown in Prefab).
        kind:    Display category — "note" | "answer" | "image" | "link".

    Returns:
        JSON string {ok, slug, path} the agent can echo back to the user.
    """
    if not title or not content:
        return json.dumps({
            "error": "pin_to_dashboard requires non-empty title and content",
        })

    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    slug = f"{stamp}-{_slugify(title)}"
    path = FEED_DIR / f"{slug}.json"
    payload = {
        "slug": slug,
        "title": title,
        "content": content,
        "kind": kind if kind in {"note", "answer", "image", "link"} else "note",
        "pinned_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    # Return the path relative to HERE when possible, else absolute. The
    # relative form is friendlier in the agent's chat reply; the absolute
    # form keeps the contract honest under tests / overridden FEED_DIR.
    try:
        path_str = str(path.relative_to(HERE))
    except ValueError:
        path_str = str(path)
    return json.dumps({"ok": True, "slug": slug, "path": path_str})


def list_pinned(limit: int = 50) -> list[dict]:
    """Read every feed/*.json, newest-first. Used by /api/feed."""
    items: list[dict] = []
    for p in sorted(FEED_DIR.glob("*.json"), reverse=True)[:limit]:
        try:
            items.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return items
