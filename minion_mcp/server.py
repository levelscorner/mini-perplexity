"""MINION MCP server.

Exposes the same five tools the in-process chat agent uses, but as a
real FastMCP server you can plug into any MCP-aware host (Claude
Desktop, Inspector, etc.).

Run:
    # stdio (default — for Claude Desktop / Inspector)
    uv run python -m minion_mcp.server

    # streamable-http on port 9000 (for HTTP MCP clients)
    uv run python -m minion_mcp.server --http --port 9000

The tools are imported from the same `tools.py` / `tools_image.py` /
`tools_dashboard.py` modules as the chat agent — single source of truth.
A new tool added in tools.py automatically becomes available in the
chat AND over MCP, as long as it's registered in TOOLS and
TOOL_SCHEMAS.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

# The minion_mcp package sits next to the agent code at the repo root.
HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import TOOLS  # noqa: E402
from tools_dashboard import list_pinned  # noqa: E402


mcp: FastMCP = FastMCP(
    name="minion",
    instructions=(
        "MINION — research + image-gen + dashboard tools. Use web_search "
        "and fetch_page for information; save_answer to persist a cited "
        "markdown answer; render_image to draw something via Higgsfield; "
        "pin_to_dashboard to push a card into the user's dashboard Feed."
    ),
)


# ---------------------------------------------------------------------------
# Tool wrappers — every MCP tool is a thin pass-through to TOOLS[name].
# Keeps the source of truth in tools.py while letting FastMCP introspect
# Python signatures + docstrings for the schema.
# ---------------------------------------------------------------------------
@mcp.tool()
def web_search(query: str, n: int = 5) -> str:
    """Search the web via DuckDuckGo. Returns up to `n` ranked results."""
    return TOOLS["web_search"](query=query, n=n)


@mcp.tool()
def fetch_page(url: str, max_chars: int | None = None) -> str:
    """Fetch a URL and extract its main text (truncated to ~5000 chars)."""
    if max_chars is None:
        return TOOLS["fetch_page"](url=url)
    return TOOLS["fetch_page"](url=url, max_chars=max_chars)


@mcp.tool()
def save_answer(question: str, answer: str, sources: list[dict]) -> str:
    """Persist a final markdown answer with citations to disk."""
    return TOOLS["save_answer"](question=question, answer=answer, sources=sources)


@mcp.tool()
def render_image(prompt: str, panels: int = 1) -> str:
    """Generate an image (or 3/4-panel comic strip) via Higgsfield."""
    return TOOLS["render_image"](prompt=prompt, panels=panels)


@mcp.tool()
def pin_to_dashboard(title: str, content: str, kind: str = "note") -> str:
    """Pin a card to the dashboard Feed tab. kind ∈ note|answer|image|link."""
    return TOOLS["pin_to_dashboard"](title=title, content=content, kind=kind)


@mcp.tool()
def list_dashboard_feed(limit: int = 20) -> dict[str, Any]:
    """Read the current dashboard Feed (newest first) — for inspection."""
    return {"items": list_pinned(limit=limit)}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="MINION MCP server")
    parser.add_argument("--http", action="store_true",
                        help="Serve over streamable-http instead of stdio.")
    parser.add_argument("--port", type=int, default=9000,
                        help="Port for --http mode (default 9000).")
    args = parser.parse_args()

    if args.http:
        # Streamable-http transport — any HTTP MCP client can connect.
        mcp.run(transport="http", port=args.port)
    else:
        # stdio transport — Claude Desktop, MCP Inspector, etc.
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
