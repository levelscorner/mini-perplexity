"""Unit tests for tools_image.render_image — JSON shape + panel routing."""
from __future__ import annotations

import json

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


def test_invalid_panel_count_falls_back_to_one(stub_renders):
    from tools_image import render_image
    payload = json.loads(render_image("hello", panels=99))
    assert payload["type"] == "image"


def test_partial_failure_produces_placeholder(monkeypatch):
    """If panel 2 of 3 fails, panels[1] is a placeholder."""
    from higgsfield import ImageRender

    calls = {"n": 0}

    def flaky(prompt, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("higgsfield 500")
        return ImageRender(
            slug=f"s{calls['n']}", url="x", local_path="/tmp/x",
            model_used="stub", duration_s=0.1,
        )

    monkeypatch.setattr("higgsfield.render_image", flaky)

    from tools_image import render_image
    payload = json.loads(render_image("setup turn punch", panels=3))
    assert len(payload["panels"]) == 3
    # Find the failed one (order isn't guaranteed because of ThreadPoolExecutor)
    failed = [p for p in payload["panels"] if p["slug"] is None]
    assert len(failed) == 1
    assert "error" in failed[0]
