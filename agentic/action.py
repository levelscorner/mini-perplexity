"""Action — pure dispatch. NO LLM.

Receives a ToolCall + a live MCP session, runs the tool, and returns
(descriptor, artifact_id_or_None). Payloads over the threshold are pushed to
the artifact store and replaced by a short descriptor.

This is mechanical (no graded prompt IP), so it ships complete. Two guards
matter and are implemented:
  1. If a `path`/`url` argument starts with 'art:', refuse — artifact handles
     are NOT filesystem paths/URLs (weak Decision models sometimes pass them).
  2. Collapse the MCP result's content blocks into one text string before the
     size check.
"""
from __future__ import annotations

from mcp import ClientSession

from artifacts import ARTIFACT_THRESHOLD_BYTES, ArtifactStore
from schemas import ToolCall

_PATHISH_KEYS = ("path", "url", "file", "filepath")


def _collapse(result) -> str:
    """Join an MCP CallToolResult's content blocks into one text string."""
    parts: list[str] = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
        else:
            parts.append(str(block))
    return "\n".join(parts)


async def execute(session: ClientSession, tool_call: ToolCall,
                  artifacts: ArtifactStore) -> tuple[str, str | None]:
    # Guard: artifact handles must never be passed as a path/url.
    for key in _PATHISH_KEYS:
        v = tool_call.arguments.get(key)
        if isinstance(v, str) and v.startswith("art:"):
            return (f"error: '{key}' was an artifact handle ({v}); "
                    f"artifact handles are not paths/URLs. The bytes are "
                    f"attached to the prompt when needed."), None

    try:
        result = await session.call_tool(tool_call.name, arguments=tool_call.arguments)
    except Exception as exc:  # tool not found / bad args / server error
        return f"error: tool '{tool_call.name}' failed: {type(exc).__name__}: {exc}", None

    text = _collapse(result)
    if len(text.encode("utf-8")) > ARTIFACT_THRESHOLD_BYTES:
        aid = artifacts.put(
            text.encode("utf-8"), content_type="text/plain",
            source=tool_call.name, descriptor=text[:200],
        )
        preview = text[:200].replace("\n", " ")
        return f"[artifact {aid}, {len(text)} bytes] preview: {preview}", aid

    return text, None
