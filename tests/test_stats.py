"""Unit tests for stats.py — atomic aggregation, persistence, derived metrics."""
from __future__ import annotations

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
