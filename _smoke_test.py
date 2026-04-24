"""End-to-end smoke test with stubbed LLM and network. No keys required.

Run: python _smoke_test.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# Stub network BEFORE tools.py is imported so it picks up our fakes.
# ---------------------------------------------------------------------------


class _FakeDDGS:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def text(self, q, max_results=5):
        return [
            {
                "title": "Claude 4.7 release notes — Anthropic",
                "href": "https://www.anthropic.com/news/claude-4-7",
                "body": "Claude 4.7 adds 1M context and thinking mode.",
            },
            {
                "title": "Claude 4.7 launch thread — X",
                "href": "https://x.com/AnthropicAI/status/fake",
                "body": "We're launching Claude 4.7 with big improvements.",
            },
            {
                "title": "Hacker News discussion",
                "href": "https://news.ycombinator.com/item?id=fake",
                "body": "Community reactions to Claude 4.7.",
            },
        ][:max_results]


import duckduckgo_search  # noqa: E402

duckduckgo_search.DDGS = _FakeDDGS


class _FakeResponse:
    def __init__(self, url, text, status=200):
        self.url = url
        self.text = text
        self.status_code = status


_CANNED_PAGES = {
    "https://www.anthropic.com/news/claude-4-7": (
        "<html><head><title>Claude 4.7 — Anthropic</title></head>"
        "<body><article>"
        "<h1>Claude 4.7</h1>"
        "<p>Claude 4.7 launches with a 1-million-token context window, "
        "an improved thinking mode for complex reasoning, and faster output "
        "on Sonnet and Haiku tiers.</p>"
        "<p>Pricing remains unchanged from 4.6.</p>"
        "</article></body></html>"
    ),
    "https://x.com/AnthropicAI/status/fake": (
        "<html><head><title>Launch tweet</title></head>"
        "<body><article>"
        "<p>Claude 4.7 is here. 1M context. Better thinking. Same price.</p>"
        "</article></body></html>"
    ),
}


import requests  # noqa: E402

_real_get = requests.get


def _fake_get(url, **kwargs):
    if url in _CANNED_PAGES:
        return _FakeResponse(url, _CANNED_PAGES[url])
    return _FakeResponse(url, "<html><body>placeholder</body></html>")


requests.get = _fake_get


# ---------------------------------------------------------------------------
# Stub the LLMClient.
# ---------------------------------------------------------------------------


import llm as llm_mod  # noqa: E402

CANNED_LLM = [
    # Iter 1: search
    '{"tool_name": "web_search", "tool_arguments": {"query": "Claude 4.7 release", "n": 3}}',
    # Iter 2: fetch first URL
    '{"tool_name": "fetch_page", "tool_arguments": {"url": "https://www.anthropic.com/news/claude-4-7"}}',
    # Iter 3: fetch second URL
    '{"tool_name": "fetch_page", "tool_arguments": {"url": "https://x.com/AnthropicAI/status/fake"}}',
    # Iter 4: save — use json.dumps-safe inline for test clarity
    json.dumps(
        {
            "tool_name": "save_answer",
            "tool_arguments": {
                "question": "What's new in Claude 4.7?",
                "answer": (
                    "Claude 4.7 launches with three headline changes: a "
                    "1-million-token context window [1], an improved internal "
                    "thinking mode for complex reasoning [1], and faster "
                    "output on Sonnet and Haiku tiers [1]. Pricing is "
                    "unchanged from 4.6 [1][2]."
                ),
                "sources": [
                    {
                        "title": "Claude 4.7 — Anthropic",
                        "url": "https://www.anthropic.com/news/claude-4-7",
                    },
                    {
                        "title": "Launch tweet",
                        "url": "https://x.com/AnthropicAI/status/fake",
                    },
                ],
            },
        }
    ),
    # Iter 5: final
    '{"answer": "Saved answer about Claude 4.7 to answers/. Two sources cited."}',
]
_idx = [0]


class _StubLLM:
    def __init__(self, *a, **kw):
        pass

    def generate(self, prompt: str) -> str:
        resp = CANNED_LLM[_idx[0]]
        _idx[0] += 1
        return resp


llm_mod.LLMClient = _StubLLM


# ---------------------------------------------------------------------------
# Run the agent into a sandbox answers dir.
# ---------------------------------------------------------------------------


def main() -> int:
    td = tempfile.mkdtemp(prefix="mini-perplexity-smoke-")
    os.environ["ANSWERS_DIR"] = td
    os.environ["GEMINI_API_KEY"] = "stub-key-not-used"

    from mini_perplexity import run_agent

    rc = run_agent("What's new in Claude 4.7?", max_iterations=8)
    if rc != 0:
        print(f"FAIL: agent returned {rc}", file=sys.stderr)
        return 1

    # Verify a markdown file was written
    import glob

    written = glob.glob(os.path.join(td, "*.md"))
    if not written:
        print(f"FAIL: no markdown written to {td}", file=sys.stderr)
        return 2

    content = open(written[0], "r", encoding="utf-8").read()
    if "[1]" not in content or "anthropic.com" not in content:
        print(f"FAIL: citations or sources missing in {written[0]}", file=sys.stderr)
        print(content, file=sys.stderr)
        return 3

    print()
    print(f"SMOKE GREEN — answer at {written[0]}")
    print(f"SMOKE GREEN — {len(content)} bytes, citations present")

    # Restore requests.get in case the process is reused
    requests.get = _real_get
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
