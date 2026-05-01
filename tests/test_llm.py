"""Backend selection + interface tests for llm.py."""
from __future__ import annotations

import importlib
import sys

import pytest


@pytest.fixture(autouse=True)
def reload_llm():
    """Drop cached llm module so each test sees fresh env reads."""
    sys.modules.pop("llm", None)
    yield
    sys.modules.pop("llm", None)


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
    monkeypatch.setenv("GEMINI_API_KEY", "g-x")

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
    monkeypatch.delenv("LLM_BACKEND", raising=False)

    from llm import LLMClient
    client = LLMClient()
    assert hasattr(client, "generate")
    assert callable(client.generate)
    assert client.backend == "anthropic"
