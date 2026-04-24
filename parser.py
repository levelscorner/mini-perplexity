"""Robust JSON parser for LLM output.

Ported from the S03 reference implementation (reference/10_full_agent.py).
LLMs drift on formatting — wrap in markdown fences, add stray prose, etc.
This parser is resilient to all of it.
"""
from __future__ import annotations

import json
import re


def parse_llm_response(text: str) -> dict:
    """Parse the LLM's response, handling common formatting issues.

    Strategy (in order):
    1. Strip markdown code fences (```json ... ``` or ``` ... ```).
    2. Try direct json.loads.
    3. Regex-extract the first {...} block.
    4. Raise ValueError with the first 200 chars of the bad input.
    """
    text = text.strip()

    # 1. Strip markdown fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]  # drop opening fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]  # drop closing fence
        text = "\n".join(lines).strip()
        if text.startswith("json"):
            text = text[4:].strip()

    # 2. Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 3. Regex fallback — grab the first JSON object
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not parse LLM response: {text[:200]}")
