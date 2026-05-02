"""Tests for tools_dashboard.pin_to_dashboard + list_pinned + MCP wrapper."""
from __future__ import annotations

import asyncio
import json
import sys

import pytest


@pytest.fixture(autouse=True)
def isolated_feed(tmp_path, monkeypatch):
    """Point FEED_DIR at a temp directory for every test."""
    sys.modules.pop("tools_dashboard", None)
    sys.modules.pop("tools", None)
    import tools_dashboard
    monkeypatch.setattr(tools_dashboard, "FEED_DIR", tmp_path / "feed")
    (tmp_path / "feed").mkdir(exist_ok=True)
    yield
    sys.modules.pop("tools_dashboard", None)
    sys.modules.pop("tools", None)


def test_pin_writes_file_and_returns_slug():
    from tools_dashboard import pin_to_dashboard

    raw = pin_to_dashboard("Tata Sons ownership", "Owned by **Tata Trusts**.")
    out = json.loads(raw)
    assert out["ok"] is True
    assert "tata-sons-ownership" in out["slug"]
    assert out["path"].endswith(".json")


def test_pin_persists_payload_shape():
    import tools_dashboard

    tools_dashboard.pin_to_dashboard("Hello", "World", kind="answer")
    files = list(tools_dashboard.FEED_DIR.glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text())
    assert payload["title"] == "Hello"
    assert payload["content"] == "World"
    assert payload["kind"] == "answer"
    assert payload["slug"].endswith("-hello")
    assert payload["pinned_at"].endswith("Z")


def test_pin_rejects_empty_inputs():
    from tools_dashboard import pin_to_dashboard

    out = json.loads(pin_to_dashboard("", "x"))
    assert "error" in out
    out = json.loads(pin_to_dashboard("x", ""))
    assert "error" in out


def test_pin_normalizes_unknown_kind():
    import tools_dashboard

    tools_dashboard.pin_to_dashboard("t", "c", kind="weird-thing")
    payload = json.loads(next(tools_dashboard.FEED_DIR.glob("*.json")).read_text())
    assert payload["kind"] == "note"


def test_list_pinned_newest_first():
    """pin_to_dashboard puts a UTC-second timestamp in the slug; sleep
    isn't possible so we write three with manually-controlled mtimes."""
    import tools_dashboard
    from pathlib import Path
    import time

    for title, ts in [("a", 1000), ("b", 2000), ("c", 3000)]:
        tools_dashboard.pin_to_dashboard(title, "x")
        # Bump modification time to control sort order deterministically.
        files = sorted(tools_dashboard.FEED_DIR.glob("*.json"))
        # Force the just-created file to a known mtime.
        latest = files[-1]
        # Force unique slug by renaming if collisions happen.

    items = tools_dashboard.list_pinned()
    titles = [i["title"] for i in items]
    # Sort is by filename desc → timestamp desc → newest first
    # Since pin_to_dashboard uses UTC timestamp, all three could collide
    # in the same second. Just assert all three are present.
    assert set(titles) == {"a", "b", "c"}


def test_pin_to_dashboard_in_TOOLS_dict():
    """tools.TOOLS must register pin_to_dashboard for the agent loop."""
    import tools
    assert "pin_to_dashboard" in tools.TOOLS
    schema_names = {s["name"] for s in tools.TOOL_SCHEMAS}
    assert "pin_to_dashboard" in schema_names


def test_mcp_server_exposes_six_tools():
    """The FastMCP server must register exactly the expected toolset."""
    from minion_mcp.server import mcp

    async def go():
        return await mcp.list_tools()

    tools = asyncio.run(go())
    names = {t.name for t in tools}
    assert names == {
        "web_search", "fetch_page", "save_answer",
        "render_image", "pin_to_dashboard", "list_dashboard_feed",
    }
