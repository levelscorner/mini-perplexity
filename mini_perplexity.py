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
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv

from llm import LLMClient
from parser import parse_llm_response
from tools import TOOLS
from ui import ReasoningChainUI

HERE = Path(__file__).resolve().parent
SYSTEM_PROMPT_PATH = HERE / "system_prompt.md"
LOGS_DIR = HERE / "logs"


def _load_system_prompt() -> str:
    """Read the agent's system prompt from disk.

    Kept in an external markdown file (system_prompt.md) instead of a
    Python string literal because:
        - Prompts are content; mixing them into .py is noise.
        - Diffs on the prompt stay clean across tweaks.
        - Editors highlight the markdown properly for readability.
    """
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


def _render_conversation(system: str, messages: list[dict]) -> str:
    """Flatten the conversation into a single prompt string.

    Every iteration rebuilds the full prompt from scratch by flattening
    the messages list into plain text. This is the course-reference
    convention — works with every LLM provider because it doesn't rely
    on any SDK's structured-chat API.

    Layout of the returned string:

        <system prompt>
        <blank line>
        User: <original question>
        Assistant: <iter-1 JSON response>
        Tool Result: <iter-1 tool output>
        Assistant: <iter-2 JSON response>
        Tool Result: <iter-2 tool output>
        ...
        Assistant:              ← trailing prompt; LLM continues from here

    The trailing "Assistant:" with no content is a soft nudge — it tells
    the model "your turn, continue as the assistant" rather than letting
    it drift back into system or user voice.
    """
    # Start with the system prompt and a blank line separator.
    parts = [system.strip(), ""]
    for msg in messages:
        role = msg["role"]
        content = msg["content"]
        # Role labels use the same vocabulary the LLM sees — "User",
        # "Assistant", "Tool Result" — so the model's training on
        # chat-formatted data takes over naturally.
        if role == "user":
            parts.append(f"User: {content}\n")
        elif role == "assistant":
            parts.append(f"Assistant: {content}\n")
        elif role == "tool":
            parts.append(f"Tool Result: {content}\n")
    # Trailing "Assistant:" primes the next completion.
    parts.append("Assistant:")
    return "\n".join(parts)


def run_agent(
    user_query: str,
    max_iterations: int = 8,
    on_event: Callable[[dict], None] | None = None,
    render_terminal: bool = True,
) -> int:
    """Execute the agent loop against one user query.

    This is the whole agent. The shape:

        1. Set up: load .env, build UI, build LLM client.
        2. Seed messages with the user's question.
        3. For up to max_iterations:
           a. Flatten messages into a prompt.
           b. Ask the LLM.
           c. Parse the response.
           d. If it's an answer → done.
           e. If it's a tool call → dispatch, append result, loop.
           f. If neither → coach the model and loop.
        4. Persist the reasoning chain to logs/*.json.

    Args:
        user_query:      The question to research.
        max_iterations:  Safety cap; prevents runaway LLMs from burning quota.
        on_event:        Optional callback invoked for every ChainEvent dict
                         as it's emitted. Used by the webapp to stream the
                         reasoning chain over SSE in real time.
        render_terminal: When False, skip rich Panel rendering. The webapp
                         passes False so its agent runs don't spam stdout.

    Returns:
        Process exit code:
            0 = success (final answer produced)
            1 = config error (e.g., missing API key)
            2 = no answer within iteration budget
    """
    # Pull .env into os.environ. Note: dotenv does NOT override existing
    # shell vars by default — see README / ROADMAP for the precedence gotcha.
    load_dotenv()

    # Timestamp becomes part of the log filename — sortable, collision-free.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = LOGS_DIR / f"run-{stamp}.json"
    ui = ReasoningChainUI(
        log_path=log_path,
        on_event=on_event,
        render_terminal=render_terminal,
    )

    # Print the header rule + record the user query as the first event.
    ui.banner(user_query)

    # LLMClient() validates GEMINI_API_KEY in its __init__ — if missing,
    # we catch here, show the error, save what we have, and exit with rc=1.
    try:
        llm = LLMClient()
    except RuntimeError as exc:
        ui.error(0, str(exc))
        ui.save()
        return 1

    system = _load_system_prompt()

    # `messages` accumulates the full conversation. On each iteration we
    # append the assistant's raw response + the tool result (two entries).
    # This is the "full-history carry" requirement — the LLM sees everything
    # it has said and every tool result from this run on every call.
    messages: list[dict] = [{"role": "user", "content": user_query}]

    # Sentinel: None until we get a final answer, then the answer text.
    # Drives the return code at the end and the for-else branch.
    final_answer: str | None = None

    # range(1, N+1) gives us 1..N inclusive — human-friendly iteration numbers
    # for the UI ("Iteration 1", "Iteration 2", ...).
    for iteration in range(1, max_iterations + 1):
        ui.iteration_header(iteration)

        # ── Step 3a: Build the prompt ────────────────────────────────────
        prompt = _render_conversation(system, messages)

        # ── Step 3b: Call the LLM ────────────────────────────────────────
        try:
            raw = llm.generate(prompt)
        except Exception as exc:
            # Any LLM error (auth, network, rate limit) is fatal for this
            # run — we break out immediately rather than retrying. A more
            # robust agent might implement exponential backoff here.
            ui.error(iteration, f"LLM call failed: {exc}")
            break

        # ── Step 3c: Parse the response ──────────────────────────────────
        try:
            parsed = parse_llm_response(raw)
        except ValueError as exc:
            # Parse failed after all three parser strategies. Instead of
            # crashing, we log it, append a corrective nudge to the
            # conversation, and let the LLM try again on the next iteration.
            # This is the "teach the model within the run" pattern.
            ui.llm(iteration, raw, None)
            ui.error(iteration, f"Parse failed: {exc}")
            messages.append({"role": "assistant", "content": raw})
            messages.append(
                {
                    "role": "user",
                    "content": "Your previous response was not valid JSON. Respond with ONLY a JSON object, no prose, no fences.",
                }
            )
            # `continue` skips to the next for-loop iteration (iteration + 1).
            continue

        # Record what the model said + how we parsed it.
        ui.llm(iteration, raw, parsed)

        # ── Step 3d: Final answer? ───────────────────────────────────────
        # The response contract says the model emits either {"answer": ...}
        # or {"tool_name": ..., "tool_arguments": ...}. isinstance + "in"
        # check handles the case where the model accidentally returns a
        # non-dict (list, scalar) — don't crash.
        if isinstance(parsed, dict) and "answer" in parsed:
            final_answer = str(parsed["answer"])
            ui.final(iteration, final_answer)
            # `break` exits the for loop — skips the `else` branch below.
            break

        # ── Step 3e: Tool call? ──────────────────────────────────────────
        if isinstance(parsed, dict) and "tool_name" in parsed:
            tool_name = parsed["tool_name"]
            # parsed.get(..., {}) returns {} if tool_arguments is missing.
            # The `or {}` on top protects against parsed["tool_arguments"]
            # being explicitly `null` (which .get wouldn't catch).
            tool_args = parsed.get("tool_arguments", {}) or {}
            ui.tool_call(iteration, tool_name, tool_args)

            # Unknown tool → send an error back as data. The LLM sees which
            # tools are available and can self-correct on the next iteration.
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

            # Dispatch: TOOLS[name](**args) unpacks the dict as keyword args.
            # So {"query": "hi", "n": 3} becomes web_search(query="hi", n=3).
            # Tools catch their own errors and return JSON strings, so we
            # only see exceptions for (1) bad args (TypeError), (2) bugs
            # inside the tool itself (anything else).
            try:
                result = TOOLS[tool_name](**tool_args)
            except TypeError as exc:
                # Wrong arg name / missing required arg / extra arg.
                result = json.dumps({"error": f"bad arguments: {exc}"})
            except Exception as exc:
                # Anything else — report the exception class + message as
                # data so the LLM has a fighting chance of adapting.
                result = json.dumps({"error": f"{type(exc).__name__}: {exc}"})

            ui.tool_result(iteration, tool_name, result)
            # Append BOTH the assistant's response AND the tool result.
            # This two-entry append per iteration is what grows `messages`
            # and gives the next iteration its full context.
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "tool", "content": result})
            continue

        # ── Step 3f: Neither answer nor tool_name ────────────────────────
        # Model returned valid JSON but of the wrong shape. Same teaching
        # pattern as the parse failure above — coach and retry.
        ui.error(iteration, "Response had neither 'tool_name' nor 'answer'.")
        messages.append({"role": "assistant", "content": raw})
        messages.append(
            {
                "role": "user",
                "content": "Respond with a JSON object containing either tool_name+tool_arguments or answer.",
            }
        )
    else:
        # for-else: runs only when the loop exhausts naturally (no break).
        # A successful answer breaks out early, so this branch fires only if
        # we ran every iteration without producing one.
        ui.error(max_iterations, "Max iterations reached without a final answer.")

    # ── Step 4: Persist the reasoning chain ──────────────────────────────
    # Always runs, whether we broke out or exhausted — the log captures
    # errors too.
    saved_to = ui.save()
    if saved_to is not None:
        ui.system(f"Full reasoning chain saved to {saved_to}")

    # Conditional expression: (value_if_true) if (condition) else (value_if_false).
    # Python's ternary. rc=0 means "we got an answer", rc=2 means "we didn't".
    return 0 if final_answer is not None else 2


def main() -> int:
    """CLI entry point. Parses argv, delegates to run_agent."""
    parser = argparse.ArgumentParser(
        description="Mini Perplexity — a research agent that searches, reads, cites."
    )
    # nargs="+" means "one or more positional words" — lets the user type
    # the query without quotes (`python mini_perplexity.py what is mcp`).
    # We rejoin with spaces below.
    parser.add_argument(
        "query",
        nargs="+",
        help="The question to research (free-form).",
    )
    parser.add_argument(
        "--max-iterations",
        type=int,     # argparse converts the string from argv into an int
        default=8,
        help="Safety cap on the agent loop (default: 8).",
    )
    args = parser.parse_args()

    query = " ".join(args.query).strip()
    if not query:
        # Write to stderr (not stdout) so it doesn't pollute scriptable output.
        print("error: empty query", file=sys.stderr)
        return 1
    return run_agent(query, max_iterations=args.max_iterations)


# The `if __name__ == "__main__":` idiom runs main() only when this file
# is executed directly (not when it's imported as a module from, e.g.,
# _smoke_test.py). `raise SystemExit(main())` forwards main()'s return
# value to the shell as the process exit code.
if __name__ == "__main__":
    raise SystemExit(main())
