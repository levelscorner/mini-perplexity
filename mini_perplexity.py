"""Mini Perplexity — S03 agentic assignment (alt).

A research agent: searches the web, reads the top results, synthesizes a
cited answer, and persists it to disk.

Usage:
    python mini_perplexity.py "What's new in Claude 4.7?"

The reasoning chain renders to the terminal via `rich` AND persists to
`logs/run-<timestamp>.json` for the S03 submission log paste.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from llm import LLMClient
from parser import parse_llm_response
from tools import TOOLS
from ui import ReasoningChainUI

HERE = Path(__file__).resolve().parent
SYSTEM_PROMPT_PATH = HERE / "system_prompt.md"
LOGS_DIR = HERE / "logs"


def _load_system_prompt() -> str:
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def _render_conversation(system: str, messages: list[dict]) -> str:
    """Flatten messages into a single prompt string, course-reference style."""
    parts = [system.strip(), ""]
    for msg in messages:
        role = msg["role"]
        content = msg["content"]
        if role == "user":
            parts.append(f"User: {content}\n")
        elif role == "assistant":
            parts.append(f"Assistant: {content}\n")
        elif role == "tool":
            parts.append(f"Tool Result: {content}\n")
    parts.append("Assistant:")
    return "\n".join(parts)


def run_agent(user_query: str, max_iterations: int = 8) -> int:
    """Run the S03 loop until the agent returns a final answer or exhausts."""
    load_dotenv()

    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    log_path = LOGS_DIR / f"run-{stamp}.json"
    ui = ReasoningChainUI(log_path=log_path)

    ui.banner(user_query)

    try:
        llm = LLMClient()
    except RuntimeError as exc:
        ui.error(0, str(exc))
        ui.save()
        return 1

    system = _load_system_prompt()
    messages: list[dict] = [{"role": "user", "content": user_query}]

    final_answer: str | None = None

    for iteration in range(1, max_iterations + 1):
        ui.iteration_header(iteration)
        prompt = _render_conversation(system, messages)

        try:
            raw = llm.generate(prompt)
        except Exception as exc:
            ui.error(iteration, f"LLM call failed: {exc}")
            break

        try:
            parsed = parse_llm_response(raw)
        except ValueError as exc:
            ui.llm(iteration, raw, None)
            ui.error(iteration, f"Parse failed: {exc}")
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {
                    "role": "user",
                    "content": "Your previous response was not valid JSON. Respond with ONLY a JSON object, no prose, no fences.",
                }
            )
            continue

        ui.llm(iteration, raw, parsed)

        if isinstance(parsed, dict) and "answer" in parsed:
            final_answer = str(parsed["answer"])
            ui.final(iteration, final_answer)
            break

        if isinstance(parsed, dict) and "tool_name" in parsed:
            tool_name = parsed["tool_name"]
            tool_args = parsed.get("tool_arguments", {}) or {}
            ui.tool_call(iteration, tool_name, tool_args)

            if tool_name not in TOOLS:
                err = json.dumps(
                    {
                        "error": f"unknown tool '{tool_name}'",
                        "available": sorted(TOOLS.keys()),
                    }
                )
                ui.tool_result(iteration, tool_name, err)
                messages.append({"role": "assistant", "content": raw})
                messages.append({"role": "tool", "content": err})
                continue

            try:
                result = TOOLS[tool_name](**tool_args)
            except TypeError as exc:
                result = json.dumps({"error": f"bad arguments: {exc}"})
            except Exception as exc:
                result = json.dumps({"error": f"{type(exc).__name__}: {exc}"})

            ui.tool_result(iteration, tool_name, result)
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "tool", "content": result})
            continue

        ui.error(iteration, "Response had neither 'tool_name' nor 'answer'.")
        messages.append({"role": "assistant", "content": raw})
        messages.append(
            {
                "role": "user",
                "content": "Respond with a JSON object containing either tool_name+tool_arguments or answer.",
            }
        )

    if final_answer is None:
        ui.error(max_iterations, "Max iterations reached without a final answer.")

    saved_to = ui.save()
    if saved_to is not None:
        ui.system(f"Full reasoning chain saved to {saved_to}")

    return 0 if final_answer is not None else 2


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Mini Perplexity — a research agent that searches, reads, cites."
    )
    parser.add_argument(
        "query",
        nargs="+",
        help="The question to research (free-form).",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=8,
        help="Safety cap on the agent loop (default: 8).",
    )
    args = parser.parse_args()
    query = " ".join(args.query).strip()
    if not query:
        print("error: empty query", file=sys.stderr)
        return 1
    return run_agent(query, max_iterations=args.max_iterations)


if __name__ == "__main__":
    raise SystemExit(main())
