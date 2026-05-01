"""Unit tests for higgsfield.py — stub mode + model routing + unwrap."""
from __future__ import annotations

from unittest.mock import MagicMock


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
    result.is_error = False
    result.structured_content = {"slug": "abc", "url": "http://x"}
    result.content = []
    assert _unwrap(result) == {"slug": "abc", "url": "http://x"}


def test_unwrap_handles_camelcase_alias():
    from higgsfield import _unwrap
    result = MagicMock()
    result.is_error = False
    result.structured_content = None
    result.structuredContent = {"slug": "abc"}
    result.content = []
    assert _unwrap(result) == {"slug": "abc"}


def test_unwrap_falls_back_to_text_json():
    from higgsfield import _unwrap
    block = MagicMock()
    block.text = '{"slug": "fallback"}'
    result = MagicMock()
    result.is_error = False
    result.structured_content = None
    result.structuredContent = None
    result.content = [block]
    assert _unwrap(result) == {"slug": "fallback"}


def test_render_image_stub_mode(monkeypatch, tmp_path):
    """Stub mode returns a fixture image path without hitting Higgsfield."""
    monkeypatch.setenv("RENDER_MODE", "stub")
    fixture = tmp_path / "stub.png"
    fixture.write_bytes(b"fake-png")
    monkeypatch.setenv("HIGGSFIELD_STUB_PATH", str(fixture))

    from higgsfield import render_image
    out = render_image("a cat in a tux")
    assert out.slug.startswith("stub-")
    assert out.local_path == str(fixture)
    assert out.model_used == "stub"
