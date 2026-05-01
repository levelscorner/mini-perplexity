"""Integration tests for webapp/server.py routes."""
from __future__ import annotations

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
        assert "tools_array" in body


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
        assert b"<html" in r.content.lower() or b"<!doctype" in r.content.lower()


@pytest.mark.asyncio
async def test_card_image_404_when_missing(stub_env):
    from webapp.server import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/api/cards/nonexistent.png")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_run_request_accepts_image_mode(stub_env):
    """Just verify the schema accepts image_mode without 422."""
    from webapp.server import RunRequest
    body = RunRequest(question="test", image_mode=True)
    assert body.image_mode is True
    assert body.question == "test"
