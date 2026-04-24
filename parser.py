"""Robust JSON parser for LLM output.

Ported from the S03 reference implementation (reference/10_full_agent.py).

Why this exists:
    LLMs are asked (by the system prompt) to return JSON only. They *mostly*
    comply, but they drift. Common drift modes seen in practice:

        • Wrap the JSON in a ```json ... ``` markdown fence.
        • Wrap it in an unlabeled ``` ... ``` fence.
        • Add a short prose preamble: "Here is the JSON:\n{...}".
        • Add a trailing "Hope this helps!" after the JSON.
        • Add stray whitespace or an unbalanced trailing comma.

    The agent loop needs a parser that survives all of this, because a
    single malformed response on iteration 4 of an 8-iteration run would
    otherwise crash the whole agent. Instead we try a three-tier strategy,
    each more forgiving than the last, and only give up at the end.
"""
from __future__ import annotations

import json
import re


def parse_llm_response(text: str) -> dict:
    """Parse an LLM's response string into a Python dict.

    Strategy (applied in order, short-circuiting on first success):

        1. Strip markdown code fences if the text starts with ``` — handles
           both ```json and plain ``` variants.
        2. Try a direct `json.loads` on the cleaned text.
        3. If that fails, regex-extract the first `{...}` block anywhere in
           the text and try `json.loads` on just that.
        4. If *all* three fail, raise `ValueError` with the first 200 chars
           of the offending input so the agent loop can show it and recover.

    Args:
        text: Raw string as returned by the LLM.

    Returns:
        The parsed JSON object as a dict. (Technically json.loads can also
        return lists / scalars, but the agent only ever emits objects, so
        callers can treat the return as `dict`.)

    Raises:
        ValueError: If none of the three strategies can extract valid JSON.
    """
    # Trim leading/trailing whitespace first — the LLM occasionally prepends
    # a newline or two, which doesn't affect content but complicates our
    # startswith("```") check below.
    text = text.strip()

    # ── Strategy 1: Strip markdown fences ────────────────────────────────
    #
    # If the model wraps its response like this:
    #
    #   ```json
    #   {"tool_name": "web_search", ...}
    #   ```
    #
    # we need to remove the first and last lines before json.loads will work.
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]  # drop opening fence line (e.g. ```json)
        # Drop the closing fence if present. `lines[-1]` might be "" if the
        # response ends with a newline before the fence, so we use strip().
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
        # If the opening fence was ```json (common), after dropping the
        # first line the content still starts with the word "json" in some
        # models' outputs. Strip that prefix too.
        if text.startswith("json"):
            text = text[4:].strip()

    # ── Strategy 2: Direct parse ─────────────────────────────────────────
    #
    # The happy path — the model returned pure JSON (fences already stripped,
    # if any). `json.loads` either succeeds or raises JSONDecodeError.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Fall through to the raw-decode fallback below. `pass` here is a
        # common Python idiom for "catch this specific exception and keep
        # going" — it's not ignoring errors, it's acknowledging them.
        pass

    # ── Strategy 3: raw_decode — extract first valid JSON object ─────────
    #
    # The hardest real-world failure mode: the model emits valid JSON, then
    # CONTINUES with trailing prose or a hallucinated next turn. For example:
    #
    #     {"tool_name": "fetch_page", "tool_arguments": {...}}
    #
    #     Tool Result: {"url": "...", "title": "...", ...}
    #
    # A greedy regex like `\{.*\}` with DOTALL would match from the first
    # `{` to the LAST `}`, producing malformed JSON. Instead, we use
    # json.JSONDecoder().raw_decode(), which parses the FIRST valid JSON
    # value starting at position 0 and returns where it ended — letting us
    # ignore anything after.
    #
    # Step one: skip any leading non-JSON prose to find the first `{`.
    idx = text.find("{")
    if idx >= 0:
        try:
            # raw_decode returns (parsed_object, end_index). We only need
            # the parsed object. Trailing text is silently ignored.
            obj, _end = json.JSONDecoder().raw_decode(text[idx:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

    # ── Strategy 4: Greedy regex fallback (last resort) ──────────────────
    #
    # Very rare at this point, but kept for historical parity with the
    # course reference parser. If the first-object strategy missed (e.g.,
    # the text has a stray `{` before the real JSON), this can still win.
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # ── All strategies failed ────────────────────────────────────────────
    #
    # Raise with a truncated preview of the bad input so the agent loop can
    # log it + ask the LLM to retry. We cap at 200 chars so malformed
    # multi-kilobyte responses don't flood the terminal.
    raise ValueError(f"Could not parse LLM response: {text[:200]}")
