"""The three custom tools the Mini-Perplexity agent can call.

1. web_search(query, n)               — DuckDuckGo, no API key required
2. fetch_page(url)                    — fetch + extract main text via trafilatura
3. save_answer(question, answer, ...) — persist final markdown with citations

All tools return JSON strings so they drop straight into the conversation
history as tool-result messages.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import requests
import trafilatura
from duckduckgo_search import DDGS


HERE = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Tool 1: web_search
# ---------------------------------------------------------------------------


def web_search(query: str, n: int | None = None) -> str:
    """DuckDuckGo search. Returns ranked list of {title, url, snippet}.

    No API key required. Uses the `duckduckgo-search` library which scrapes
    DuckDuckGo's HTML endpoint under the hood.
    """
    q = (query or "").strip()
    if not q:
        return json.dumps({"error": "query is empty"})

    if n is None:
        try:
            n = int(os.getenv("SEARCH_MAX_RESULTS", "5"))
        except ValueError:
            n = 5
    n = max(1, min(int(n), 10))

    try:
        with DDGS() as ddgs:
            raw = list(ddgs.text(q, max_results=n))
    except Exception as exc:  # network, rate limit, parsing drift
        return json.dumps(
            {
                "error": f"search failed: {type(exc).__name__}: {exc}",
                "hint": "Retry in a moment or simplify the query.",
            }
        )

    results = []
    for i, r in enumerate(raw, start=1):
        # DDGS keys vary slightly: 'href' or 'link', 'body' or 'snippet'
        url = r.get("href") or r.get("link") or ""
        title = (r.get("title") or "").strip()
        snippet = (r.get("body") or r.get("snippet") or "").strip()
        if not url:
            continue
        results.append(
            {
                "rank": i,
                "title": title,
                "url": url,
                "snippet": snippet,
            }
        )

    return json.dumps(
        {
            "query": q,
            "count": len(results),
            "results": results,
            "note": (
                "No results — reformulate the query."
                if not results
                else "Pick the most relevant URLs and call fetch_page on each."
            ),
        }
    )


# ---------------------------------------------------------------------------
# Tool 2: fetch_page
# ---------------------------------------------------------------------------


_UA = (
    "Mozilla/5.0 (mini-perplexity/0.1; S03 EVA3 assignment; "
    "contact rabhinavcs@gmail.com)"
)


def fetch_page(url: str, max_chars: int | None = None) -> str:
    """Fetch URL, extract main text with trafilatura, truncate.

    Returns {url, title, text, truncated, bytes}. Keeps the LLM context tight.
    """
    url = (url or "").strip()
    if not url:
        return json.dumps({"error": "url is empty"})

    if not re.match(r"^https?://", url):
        return json.dumps({"error": "url must start with http:// or https://"})

    if max_chars is None:
        try:
            max_chars = int(os.getenv("FETCH_MAX_CHARS", "5000"))
        except ValueError:
            max_chars = 5000

    try:
        resp = requests.get(
            url,
            headers={"User-Agent": _UA},
            timeout=15,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        return json.dumps(
            {"error": f"fetch failed: {type(exc).__name__}: {exc}"}
        )

    if resp.status_code >= 400:
        return json.dumps(
            {
                "error": f"HTTP {resp.status_code}",
                "url": resp.url,
            }
        )

    html = resp.text
    # trafilatura extracts the main article body, dropping nav/footer/ads
    extracted = trafilatura.extract(html, include_comments=False, favor_recall=True)
    text = (extracted or "").strip()
    if not text:
        # Fall back to raw text if extraction fails entirely
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()

    truncated = len(text) > max_chars
    text_out = text[:max_chars]

    title_match = re.search(
        r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL
    )
    title = (title_match.group(1).strip() if title_match else "").replace("\n", " ")

    return json.dumps(
        {
            "url": resp.url,
            "title": title,
            "text": text_out,
            "truncated": truncated,
            "bytes": len(text.encode("utf-8")),
        }
    )


# ---------------------------------------------------------------------------
# Tool 3: save_answer
# ---------------------------------------------------------------------------


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str, max_len: int = 60) -> str:
    s = _SLUG_RE.sub("-", text.lower()).strip("-")
    return s[:max_len] or "answer"


def _answers_dir() -> Path:
    override = os.getenv("ANSWERS_DIR")
    if override:
        return Path(override).expanduser()
    return HERE / "answers"


def save_answer(
    question: str,
    answer: str,
    sources: list[dict[str, Any]] | None = None,
) -> str:
    """Persist a Perplexity-style answer to disk.

    Writes markdown to `<ANSWERS_DIR>/<slug>.md` with frontmatter + body +
    numbered sources. Refuses to overwrite; appends a suffix if needed.
    """
    question = (question or "").strip()
    answer = (answer or "").strip()
    if not question:
        return json.dumps({"error": "question is empty"})
    if not answer:
        return json.dumps({"error": "answer is empty"})

    sources = sources or []
    normalized: list[dict[str, str]] = []
    for i, s in enumerate(sources, start=1):
        if not isinstance(s, dict):
            continue
        url = str(s.get("url", "")).strip()
        title = str(s.get("title", "")).strip() or url
        if not url:
            continue
        normalized.append({"index": str(i), "title": title, "url": url})

    dest = _answers_dir()
    dest.mkdir(parents=True, exist_ok=True)

    slug = _slugify(question)
    path = dest / f"{slug}.md"
    suffix = 2
    while path.exists():
        path = dest / f"{slug}-{suffix}.md"
        suffix += 1

    front = [
        "---",
        f"question: {json.dumps(question)}",
        f"sources: {len(normalized)}",
        "---",
        "",
    ]
    body_parts = ["# " + question, "", answer, ""]
    if normalized:
        body_parts.append("## Sources")
        body_parts.append("")
        for s in normalized:
            body_parts.append(f"{s['index']}. [{s['title']}]({s['url']})")
        body_parts.append("")
    content = "\n".join(front + body_parts)

    path.write_text(content, encoding="utf-8")

    return json.dumps(
        {
            "saved": True,
            "path": str(path),
            "bytes": len(content.encode("utf-8")),
            "source_count": len(normalized),
        }
    )


# ---------------------------------------------------------------------------
# Registry — dispatched by name from the agent loop.
# ---------------------------------------------------------------------------

TOOLS = {
    "web_search": web_search,
    "fetch_page": fetch_page,
    "save_answer": save_answer,
}
