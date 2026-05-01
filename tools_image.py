"""Image-gen agent tool. Wraps `higgsfield.render_image` for the agent loop.

Returns a JSON string (per tools.py contract):
    panels=1  → {"type": "image", "slug", "url", "alt", "local_path"}
    panels=3  → {"type": "comic_strip", "panels": [..., ..., ...]}
    panels=4  → {"type": "comic_strip", "panels": [..., ..., ..., ...]}

Frontend dispatches on `type`. Failed panels surface as `{slug:None, error:"..."}`
so the strip can render with a placeholder rather than failing the whole turn.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import higgsfield


# Beat-aware prompt mutations per panel count. Picked at run time so the
# strip reads as a coherent gag (setup → tension → punchline).
_BEAT_HINTS: dict[int, list[str]] = {
    1: ["{p}"],
    3: [
        "{p} — establishing shot, calm setup",
        "{p} — sudden turn, contrast",
        "{p} — punchline, reaction",
    ],
    4: [
        "{p} — establishing shot, calm setup",
        "{p} — escalation, tension rises",
        "{p} — twist, character reaction",
        "{p} — punchline payoff",
    ],
}


def _render_one(prompt_template: str, base_prompt: str) -> dict[str, Any]:
    """Render a single panel. Errors surface as data, not exceptions."""
    full = prompt_template.format(p=base_prompt)
    try:
        out = higgsfield.render_image(full)
        return {
            "slug": out.slug,
            "url": out.url,
            "alt": full,
            "local_path": out.local_path,
        }
    except Exception as e:  # noqa: BLE001 — agent tools surface errors as data
        return {
            "slug": None,
            "url": None,
            "alt": full,
            "error": f"{type(e).__name__}: {e}",
        }


def render_image(prompt: str, panels: int = 1) -> str:
    """Render one image (panels=1) or a comic strip (panels=3 or 4).

    Args:
        prompt: Text description of the desired scene/joke.
        panels: 1 → single image; 3 → 3-panel strip; 4 → 4-panel grid.
                Other values fall back to 1.

    Returns:
        JSON string. See module docstring for shape contract.
    """
    if panels not in _BEAT_HINTS:
        panels = 1

    templates = _BEAT_HINTS[panels]
    if panels == 1:
        rendered = _render_one(templates[0], prompt)
        return json.dumps({"type": "image", **rendered})

    # Parallel render across panels — most of the wallclock is Higgsfield queue time.
    with ThreadPoolExecutor(max_workers=panels) as pool:
        rendered = list(pool.map(lambda t: _render_one(t, prompt), templates))
    return json.dumps({"type": "comic_strip", "panels": rendered})
