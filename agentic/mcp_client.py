"""stdio MCP session to the PROVIDED mcp_server.py (9 tools).

Uses the official `mcp` Python SDK. The agent spawns the server as a
subprocess and talks over stdio — no reimplementing tool dispatch (the
assignment forbids that).

Provided server tools: web_search, fetch_url, get_time, currency_convert,
read_file, list_dir, create_file, update_file, edit_file.

⚠️ ALIGN ME: set MCP_SERVER_CMD to however the provided server is launched
(e.g. `uv run python mcp_server.py`). Default assumes mcp_server.py sits
next to this file.
"""
from __future__ import annotations

import os
import shlex
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE = Path(__file__).resolve().parent
MCP_SERVER_CMD = os.getenv(
    "MCP_SERVER_CMD",
    f"uv run python {HERE / 'mcp_server.py'}",
)


@asynccontextmanager
async def mcp_session():
    """Yield a connected, initialized MCP ClientSession."""
    parts = shlex.split(MCP_SERVER_CMD)
    params = StdioServerParameters(command=parts[0], args=parts[1:], env=dict(os.environ))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def load_tools(session: ClientSession) -> list:
    """tools/list — discover what the server exposes."""
    resp = await session.list_tools()
    return list(resp.tools)


def mcp_tools_for_decision(tools: list) -> list[dict]:
    """Convert MCP tool defs into the gateway's `tools=[...]` shape
    ({name, description, input_schema})."""
    out = []
    for t in tools:
        out.append({
            "name": t.name,
            "description": (t.description or "").strip(),
            "input_schema": t.inputSchema,
        })
    return out
