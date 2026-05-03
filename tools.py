"""The three custom tools the Mini-Perplexity agent can call.

1. web_search(query, n)               — DuckDuckGo, no API key required
2. fetch_page(url)                    — fetch + extract main text via trafilatura
3. save_answer(question, answer, ...) — persist final markdown with citations

Design contract every tool honors:

    • Returns a **JSON string** (not a dict). The agent loop appends tool
      results to the conversation history as {"role": "tool", "content": str},
      so a string is what the renderer needs. JSON gives the LLM enough
      structure to reason over on the next turn.

    • Catches its own exceptions. Instead of raising, tools return
      {"error": "<message>", "hint": "..."} as JSON. This turns runtime
      failures into data the LLM can read, react to, and recover from
      without the Python loop ever seeing the exception.

    • Validates arguments defensively. Tools can't trust the LLM's
      argument shapes — the LLM may send an empty string, a dict where
      a list was expected, or missing keys. Each tool short-circuits on
      bad input with a clear error message.

At the bottom, TOOLS is a plain dict mapping name → function. The agent
loop dispatches by name: TOOLS[parsed["tool_name"]](**parsed["tool_arguments"]).
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import requests
import trafilatura

# The library rebranded from `duckduckgo-search` → `ddgs` in late 2025.
# New installs should use `ddgs`; we fall back to the old name so anyone
# on a prior checkout without running `pip install ddgs` still works.
try:
    from ddgs import DDGS
except ImportError:  # pragma: no cover
    from duckduckgo_search import DDGS


HERE = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Tool 1: web_search
# ---------------------------------------------------------------------------


def web_search(query: str, n: int | None = None) -> str:
    """Search the web via DuckDuckGo and return ranked results.

    Called by the agent as its first move when a question needs fresh or
    external information. The LLM picks the query string — a bad query here
    is the LLM's problem to fix on the next iteration (by calling again with
    a better one).

    Args:
        query: Free-form search query. Empty / whitespace-only is an error.
        n:     Max results to return. Resolution order:
                   caller-provided  →  SEARCH_MAX_RESULTS env  →  5.
               Clamped to [1, 10] — more than 10 is useless noise in an
               LLM's context window.

    Returns:
        JSON string with shape:
            {"query": str, "count": int, "results": [...], "note": str}
        Each result: {"rank": int, "title": str, "url": str, "snippet": str}.
        On failure: {"error": str, "hint": str} — the LLM reads and adapts.
    """
    # Normalize: strip whitespace, treat None/empty identically.
    # The `or ""` pattern protects against the caller passing None literally.
    q = (query or "").strip()
    if not q:
        return json.dumps({"error": "query is empty"})

    # Resolve `n` through the three-layer priority chain. The nested try
    # handles the case where SEARCH_MAX_RESULTS is set but not an integer
    # (e.g., user typed "five" in .env by mistake).
    if n is None:
        try:
            n = int(os.getenv("SEARCH_MAX_RESULTS", "5"))
        except ValueError:
            n = 5
    # Clamp to a sane range. max(1, min(n, 10)) is a Pythonic "clamp(n, 1, 10)".
    n = max(1, min(int(n), 10))

    # DDGS is a context manager (note the `with`). It opens an HTTP session
    # on enter and closes it on exit, even if an exception fires.
    # ddgs.text() returns a generator — wrapping in list() materializes all
    # results before we leave the context, so the session closes cleanly.
    try:
        with DDGS() as ddgs:
            raw = list(ddgs.text(q, max_results=n))
    except Exception as exc:  # network, rate limit, HTML parsing drift
        # Broad except is intentional here: DDGS is a scraper and its error
        # surface is wide. We return the exception class name so the LLM
        # sees "TimeoutError" vs "RatelimitException" and can adapt.
        return json.dumps(
            {
                "error": f"search failed: {type(exc).__name__}: {exc}",
                "hint": "Retry in a moment or simplify the query.",
            }
        )

    # Normalize DDGS's raw output. The library's keys have drifted across
    # versions, so we defensively check both possibilities with `or` chains.
    results = []
    # enumerate(raw, start=1) gives (1, item), (2, item), ... — 1-indexed
    # because humans and LLMs think in 1-based rankings.
    for i, r in enumerate(raw, start=1):
        url = r.get("href") or r.get("link") or ""
        title = (r.get("title") or "").strip()
        snippet = (r.get("body") or r.get("snippet") or "").strip()
        # Skip results that don't even have a URL — can't fetch_page them.
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

    # The "note" field is a nudge for the LLM. It's not data the *agent loop*
    # reads — the LLM reads it and uses it to decide what to do next.
    # A little prompt engineering embedded in tool output.
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
    """Download a URL and extract its main article text.

    Called by the agent after web_search, on the 2–3 URLs it judges most
    relevant. Uses `trafilatura` — a purpose-built main-content extractor —
    to strip nav bars, sidebars, footers, and ads, leaving just the article
    body. Truncates to keep the LLM's context window manageable across
    multiple fetches in one run.

    Args:
        url:       Must start with http:// or https://. Invalid URLs short-
                   circuit with an error.
        max_chars: Max characters of extracted text to return. Resolution
                   order: caller → FETCH_MAX_CHARS env → 5000.
                   Text is truncated (not summarized); the `truncated` flag
                   in the return tells the LLM whether it saw everything.

    Returns:
        JSON string with shape:
            {"url": str, "title": str, "text": str,
             "truncated": bool, "bytes": int}
        On failure: {"error": str, "url"?: str}.
    """
    # Validate the URL at the boundary. The LLM occasionally hallucinates
    # malformed URLs ("example.com" without scheme) and we want to reject
    # those before hitting the network.
    url = (url or "").strip()
    if not url:
        return json.dumps({"error": "url is empty"})

    # re.match anchors at the start of the string implicitly. We use it
    # here instead of `.startswith("http")` because http:// and https://
    # are both valid and the `s?` makes this one check cover both.
    if not re.match(r"^https?://", url):
        return json.dumps({"error": "url must start with http:// or https://"})

    if max_chars is None:
        try:
            max_chars = int(os.getenv("FETCH_MAX_CHARS", "5000"))
        except ValueError:
            max_chars = 5000

    # HTTP GET with a sane timeout. `allow_redirects=True` means we follow
    # 3xx responses automatically — important because many sites redirect
    # from http → https or www → non-www. The final URL is available as
    # resp.url (which may differ from the input `url`).
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": _UA},  # Identify ourselves politely.
            timeout=15,                   # Total-request cap in seconds.
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        # RequestException is the base class for every requests error
        # (ConnectionError, Timeout, TooManyRedirects, etc.). One catch
        # covers all of them.
        return json.dumps(
            {"error": f"fetch failed: {type(exc).__name__}: {exc}"}
        )

    # 4xx and 5xx are treated as fetch failures. The response body in these
    # cases is usually an error page, not the article we wanted.
    if resp.status_code >= 400:
        return json.dumps(
            {
                "error": f"HTTP {resp.status_code}",
                "url": resp.url,
            }
        )

    html = resp.text

    # trafilatura.extract strips nav, footer, ads, and comments, returning
    # just the main article text. favor_recall=True means "prefer extracting
    # more content even at the risk of including some boilerplate" — for a
    # research agent, recall beats precision.
    extracted = trafilatura.extract(html, include_comments=False, favor_recall=True)
    text = (extracted or "").strip()

    # If trafilatura gives up entirely (rare; happens on extremely unusual
    # page structures), fall back to a crude regex-based HTML-strip. This is
    # worse than trafilatura but better than returning "".
    if not text:
        text = re.sub(r"<[^>]+>", " ", html)     # strip all tags
        text = re.sub(r"\s+", " ", text).strip() # collapse whitespace

    # Truncate deterministically. We track the full length so the LLM knows
    # if there's more it didn't see (and could, e.g., decide to search for
    # a more specific query).
    truncated = len(text) > max_chars
    text_out = text[:max_chars]

    # Extract <title> from the raw HTML (trafilatura returns body text only).
    # re.DOTALL makes "." match newlines — titles occasionally span lines.
    # The (.*?) is a NON-GREEDY match so we stop at the first </title>.
    title_match = re.search(
        r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL
    )
    # group(1) is the first capture group, i.e., what's between the tags.
    # Conditional expression pattern: `x if cond else y` — Python's ternary.
    title = (title_match.group(1).strip() if title_match else "").replace("\n", " ")

    return json.dumps(
        {
            "url": resp.url,          # Final URL after redirects.
            "title": title,
            "text": text_out,
            "truncated": truncated,
            "bytes": len(text.encode("utf-8")),  # full text bytes, not truncated
        }
    )


# ---------------------------------------------------------------------------
# Tool 3: save_answer
# ---------------------------------------------------------------------------


# Precompiled regex for slug generation — "anything that's not a-z or 0-9
# becomes a dash". Compiling once at module load beats compiling on every
# save_answer call. The `r"..."` prefix is a raw string, which means
# backslashes aren't interpreted by Python (important for regex).
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str, max_len: int = 60) -> str:
    """Turn a free-form question into a filesystem-safe filename fragment.

    Example: "What's new in Claude 4.7?" -> "what-s-new-in-claude-4-7"

    Args:
        text:    Any string (typically the user's question).
        max_len: Upper bound on slug length — keeps filenames sane.

    Returns:
        A lowercase dash-separated slug. Returns "answer" if the input
        would reduce to an empty string (e.g., all-punctuation input).
    """
    # 1. lowercase; 2. replace any run of non-alphanumerics with "-";
    # 3. strip leading/trailing dashes; 4. truncate; 5. default if empty.
    s = _SLUG_RE.sub("-", text.lower()).strip("-")
    # `or` evaluates to its RIGHT side when the left side is falsy (empty str).
    # So `s[:max_len] or "answer"` means: "truncate, fall back to 'answer'".
    return s[:max_len] or "answer"


def _answers_dir() -> Path:
    """Resolve the directory where save_answer writes markdown.

    Honors the ANSWERS_DIR env var for sandboxing (critical for tests —
    the smoke test sets it to a tempdir so real `answers/` isn't polluted).
    """
    override = os.getenv("ANSWERS_DIR")
    if override:
        # expanduser() turns "~/foo" into "/Users/you/foo". Standard pattern
        # for any env var that might contain a path.
        return Path(override).expanduser()
    return HERE / "answers"


def save_answer(
    question: str,
    answer: str,
    sources: list[dict[str, Any]] | None = None,
) -> str:
    """Persist a Perplexity-style answer to disk as markdown.

    Called by the agent as its final tool call, once it has a well-cited
    answer. Writes a single .md file with YAML frontmatter, the answer
    body, and a numbered Sources section. Never overwrites — if a file
    with the slug already exists, appends "-2", "-3", etc.

    Args:
        question: Used as the markdown H1 AND to generate the filename slug.
        answer:   Markdown body. Expected to contain inline citation markers
                  like [1], [2] that reference the `sources` list by index.
        sources:  List of {"title": str, "url": str} dicts, in the same
                  order as the citation numbers in the answer. Defaults
                  to an empty list if None.

    Returns:
        JSON string {"saved": true, "path": str, "bytes": int,
                     "source_count": int} on success, or
        {"error": str} on validation failure.
    """
    # Boundary validation — trust nothing the LLM sends.
    question = (question or "").strip()
    answer = (answer or "").strip()
    if not question:
        return json.dumps({"error": "question is empty"})
    if not answer:
        return json.dumps({"error": "answer is empty"})

    # `sources or []` turns None into [] so the loop below always works.
    # Common Python idiom for "default to empty collection".
    sources = sources or []

    # Normalize sources. Type annotations here use the 3.9+ syntax
    # `list[dict[str, str]]` which only works with `from __future__ import
    # annotations` on Python 3.9.
    normalized: list[dict[str, str]] = []
    for i, s in enumerate(sources, start=1):
        # isinstance check guards against the LLM sending e.g. a list of
        # plain strings instead of dicts — we skip malformed entries
        # rather than crashing.
        if not isinstance(s, dict):
            continue
        url = str(s.get("url", "")).strip()
        # Title defaults to URL if missing — never have an empty link label.
        title = str(s.get("title", "")).strip() or url
        if not url:
            continue
        # index is stored as str because we interpolate it into markdown later.
        normalized.append({"index": str(i), "title": title, "url": url})

    # Ensure the output directory exists. `parents=True` creates intermediate
    # dirs if needed, `exist_ok=True` makes it a no-op if it already exists.
    dest = _answers_dir()
    dest.mkdir(parents=True, exist_ok=True)

    # Build the target path and dodge collisions. First collision → "-2",
    # second → "-3", etc. Running a question twice generates two files you
    # can compare.
    slug = _slugify(question)
    path = dest / f"{slug}.md"
    suffix = 2
    while path.exists():
        path = dest / f"{slug}-{suffix}.md"
        suffix += 1

    # Compose the file content as a list of lines, joined at the end.
    # Building lists and joining is faster than repeated string concatenation
    # (and more readable than multi-line f-strings for structured output).
    front = [
        "---",
        # json.dumps(question) gives us a properly-escaped quoted string —
        # handles embedded quotes, newlines, unicode. Using it here means
        # the frontmatter is valid YAML even for weird questions.
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
            # Markdown link syntax: [label](url)
            body_parts.append(f"{s['index']}. [{s['title']}]({s['url']})")
        body_parts.append("")
    content = "\n".join(front + body_parts)

    # write_text handles file open/write/close in one atomic call.
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
#
# The dict maps a string tool name (what the LLM emits in its JSON) to the
# Python callable. This is the entire "tool dispatch" layer — no reflection,
# no decorators, no plugin system. Simple and readable.
#
# To add a 4th tool: define a function above, add it here, and mention it
# in system_prompt.md. That's the whole contract.
# ---------------------------------------------------------------------------

from tools_dashboard import pin_to_dashboard as _pin_to_dashboard
from tools_image import render_image as _render_image

TOOLS = {
    "web_search": web_search,
    "fetch_page": fetch_page,
    "save_answer": save_answer,
    "render_image": _render_image,
    "pin_to_dashboard": _pin_to_dashboard,
}


# ---------------------------------------------------------------------------
# Tool schemas — Anthropic native tool-use format.
#
# These schemas are passed to messages.create(tools=...) when running on
# the Anthropic backend. The agent loop dispatches incoming tool_use
# blocks against the TOOLS dict by name, so the `name` field MUST match
# a key in TOOLS exactly.
#
# When adding a new tool: define it above, register in TOOLS, then add
# its schema here. The Gemini path keeps working unchanged because it
# uses prompt-engineered tool calls and ignores these schemas.
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "web_search",
        "description": (
            "Search the web via DuckDuckGo. Returns up to `n` ranked "
            "results, each with rank/title/url/snippet. Call this FIRST "
            "for any question that needs current or external information."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Free-form search query.",
                },
                "n": {
                    "type": "integer",
                    "description": "Max results (1-10). Defaults to 5.",
                    "minimum": 1,
                    "maximum": 10,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_page",
        "description": (
            "Fetch a URL and extract its main article text (truncated to "
            "~5000 chars). Returns url/title/text/truncated/bytes. Call "
            "this on the 2-3 most relevant URLs from your search."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL to fetch and extract.",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Override the 5000-char truncation cap.",
                    "minimum": 100,
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "save_answer",
        "description": (
            "Persist your final markdown answer to disk as an artifact. "
            "Call this ONCE at the end of a research turn, when you have "
            "a complete, well-cited answer. The `answer` MUST be markdown "
            "with inline citation markers [1], [2], etc., and `sources` "
            "MUST list {title,url} objects in citation-number order."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The original question this answers.",
                },
                "answer": {
                    "type": "string",
                    "description": (
                        "Markdown answer with inline citations [1], [2], …"
                    ),
                },
                "sources": {
                    "type": "array",
                    "description": (
                        "Sources in citation-number order. Each entry "
                        "must have title and url."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                            "url": {"type": "string"},
                        },
                        "required": ["title", "url"],
                    },
                },
            },
            "required": ["question", "answer", "sources"],
        },
    },
    {
        "name": "render_image",
        "description": (
            "Generate an image (or 3/4-panel comic strip) via Higgsfield. "
            "Use panels=1 for a single image, panels=3 or 4 for a comic "
            "strip when the prompt implies a story beat. Returns a JSON "
            "envelope the frontend renders inline as an image card."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": (
                        "Vivid, refined image description. Add visual "
                        "detail (style, lighting, composition) the user "
                        "didn't supply."
                    ),
                },
                "panels": {
                    "type": "integer",
                    "description": (
                        "1 = single image, 3 or 4 = comic strip. Default 1."
                    ),
                    "enum": [1, 3, 4],
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "pin_to_dashboard",
        "description": (
            "Pin a card to the Dashboard's Feed tab so the user can "
            "see it in the Prefab UI. Use this whenever the user asks "
            "to 'show on dashboard', 'pin', or 'display' a result. "
            "Title is the heading; content is markdown body."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short heading for the card.",
                },
                "content": {
                    "type": "string",
                    "description": "Markdown body of the card.",
                },
                "kind": {
                    "type": "string",
                    "description": "Display category.",
                    "enum": ["note", "answer", "image", "link"],
                },
            },
            "required": ["title", "content"],
        },
    },
]
