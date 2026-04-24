"""Rich-based terminal UI for rendering the agent's reasoning chain.

Every event the agent emits — user query, LLM thought, tool call, tool result,
final answer — is drawn as a labeled panel in the terminal. The result is a
chronological, visually distinct chain that reads exactly like an agent's
inner monologue.

This is what satisfies the S03 rubric's "display the reasoning chain" rule.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.json import JSON as RichJSON
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text


@dataclass
class ChainEvent:
    """One entry in the persisted reasoning-chain log.

    The @dataclass decorator auto-generates __init__, __repr__, and
    __eq__ from the class-level type annotations below — saves boilerplate
    you'd otherwise write by hand.

    Attributes:
        kind:      Event category; drives color/label in the terminal and
                   filtering downstream. Values: "user" | "llm" | "tool_call"
                   | "tool_result" | "final" | "error" | "system".
        iteration: Which agent-loop iteration produced this event. 0 for
                   pre-loop events (banner, system messages).
        payload:   The event's data. Shape depends on `kind`:
                       user/final/error/system: a string
                       llm:          {"raw": str, "parsed": dict | None}
                       tool_call:    {"name": str, "arguments": dict}
                       tool_result:  {"name": str, "result": str | dict}
                   Using Any here because the shape is kind-dependent.
        ts:        ISO-8601 UTC timestamp. Useful for replay / wall-clock
                   analysis of where time went.
    """

    kind: str
    iteration: int
    payload: Any
    ts: str

    def to_dict(self) -> dict:
        """Convert to a plain dict suitable for json.dumps.

        Order matches the log-file schema (ts first for scannability).
        Kept explicit rather than relying on dataclasses.asdict() because
        we want a specific key order.
        """
        return {
            "ts": self.ts,
            "iteration": self.iteration,
            "kind": self.kind,
            "payload": self.payload,
        }


class ReasoningChainUI:
    """Streams rich panels to stdout AND captures an event log for replay.

    Two responsibilities at once, on purpose:
        1. Real-time terminal rendering — each emitted event becomes a
           colored Panel so the human watching the run can follow along.
        2. Structured event capture — the same events are appended to
           `self.events` and serialized to JSON at the end of the run.

    Keeping both behaviors in one class means the terminal output and the
    log are guaranteed to stay in sync: every render also records, and
    every record is a replay of what was rendered.

    The public API is a set of `emit` methods (banner, iteration_header,
    llm, tool_call, tool_result, final, error, system). The agent loop
    calls these — the UI handles all the formatting.
    """

    # Class-level constant mapping event kind → (border_color, title_style, label).
    # Putting it on the class (not the instance) means it's shared across all
    # instances and lives in memory exactly once. Leading underscore marks it
    # as implementation detail.
    _STYLE = {
        "user": ("cyan", "bold cyan", "You"),
        "llm": ("magenta", "bold magenta", "LLM thought"),
        "tool_call": ("yellow", "bold yellow", "Tool call"),
        "tool_result": ("green", "bold green", "Tool result"),
        "final": ("bright_green", "bold bright_green", "Final answer"),
        "error": ("red", "bold red", "Error"),
        "system": ("dim", "dim", "System"),
    }

    def __init__(self, log_path: Path | None = None) -> None:
        """Create a UI.

        Args:
            log_path: Where to write the JSON transcript at the end of the
                      run. If None, events are still captured in memory
                      but nothing is persisted — useful for tests.
        """
        self.console = Console()                 # rich Console handles ANSI / width detection.
        self.log_path = log_path
        self.events: list[ChainEvent] = []       # Accumulates every emit.

    # ----- public emitters ----------------------------------------------------
    #
    # Each emitter is a thin wrapper around `_emit()`. They exist separately
    # (instead of one emit(kind, ...)) so the agent loop's call sites read
    # naturally: `ui.tool_call(...)` > `ui.emit("tool_call", ...)`.

    def banner(self, query: str) -> None:
        """Print the session banner and record the initial user query."""
        self.console.print(Rule("[bold]Mini Perplexity — S03 Agentic Loop[/bold]"))
        self._emit("user", 0, query)

    def iteration_header(self, iteration: int) -> None:
        """Print a horizontal rule separating each loop iteration.

        Not recorded in the event log — it's a visual marker only, reproducible
        from the iteration numbers already on other events.
        """
        self.console.print()  # Blank line for breathing room.
        self.console.print(Rule(f"[bold]Iteration {iteration}[/bold]", style="blue"))

    def llm(self, iteration: int, raw_text: str, parsed: dict | None) -> None:
        """Record what the LLM returned + how we parsed it.

        `parsed` is None when parsing failed. Keeping both `raw` and `parsed`
        lets the log reader see exactly what drift the model produced.
        """
        payload = {"raw": raw_text, "parsed": parsed}
        self._emit("llm", iteration, payload)

    def tool_call(self, iteration: int, name: str, args: dict) -> None:
        """Record a tool invocation (before it runs)."""
        self._emit("tool_call", iteration, {"name": name, "arguments": args})

    def tool_result(self, iteration: int, name: str, result: str) -> None:
        """Record a tool's return value.

        Tools return JSON strings (see tools.py contract). We try to parse
        back to a dict so the log is structured, but fall back to the raw
        string if parsing fails — never crash the UI for a malformed result.
        """
        parsed_result: Any = result
        try:
            parsed_result = json.loads(result)
        except (TypeError, ValueError):
            # TypeError if result isn't a string at all; ValueError if it
            # isn't valid JSON. Either way, keep the raw string.
            pass
        self._emit(
            "tool_result", iteration, {"name": name, "result": parsed_result}
        )

    def final(self, iteration: int, answer: str) -> None:
        """Record the agent's final answer. Terminates a successful run."""
        self._emit("final", iteration, answer)

    def error(self, iteration: int, message: str) -> None:
        """Record an error. Non-fatal; the loop may recover on the next iter."""
        self._emit("error", iteration, message)

    def system(self, message: str) -> None:
        """Emit a neutral system note (e.g., 'log saved to ...')."""
        self._emit("system", 0, message)

    # ----- persistence --------------------------------------------------------

    def save(self) -> Path | None:
        """Write the captured events to disk as pretty-printed JSON.

        Returns:
            The path written, or None if no log_path was configured.
        """
        if self.log_path is None:
            return None
        # Ensure parent dir exists (the logs/ folder is gitignored and not
        # tracked, so we create it on demand).
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            # Timezone-aware "now" in UTC. The replace trick swaps
            # "+00:00" for "Z" so the timestamp matches Zulu-time convention.
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            # List comprehension — `[expression for item in iterable]` —
            # builds a new list by mapping each event through to_dict().
            "events": [e.to_dict() for e in self.events],
        }
        # ensure_ascii=False preserves unicode (accents, emoji) as-is.
        # indent=2 pretty-prints so the log is skimmable by humans.
        self.log_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return self.log_path

    # ----- internals ----------------------------------------------------------
    #
    # Leading underscore = "not part of the public API". Callers go through
    # the emit methods above.

    def _emit(self, kind: str, iteration: int, payload: Any) -> None:
        """Create a ChainEvent, append it, render it. The unified pipeline."""
        event = ChainEvent(
            kind=kind,
            iteration=iteration,
            payload=payload,
            ts=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        )
        self.events.append(event)
        self._render(event)

    def _render(self, event: ChainEvent) -> None:
        """Draw a single event as a rich Panel in the terminal."""
        # .get(key, default) returns the default tuple for unknown kinds —
        # defensive in case a new kind is added upstream without updating _STYLE.
        border, title_style, label = self._STYLE.get(
            event.kind, ("white", "bold white", event.kind)
        )
        title = f"[{title_style}]{label}[/{title_style}]"
        if event.iteration:
            # Only add the "(iter N)" suffix on events that belong to a
            # specific iteration — skips it for banner / system events.
            title = f"{title}  [dim](iter {event.iteration})[/dim]"

        body = self._format_body(event.kind, event.payload)
        self.console.print(
            Panel(body, title=title, title_align="left", border_style=border)
        )

    @staticmethod
    def _format_body(kind: str, payload: Any) -> Any:
        """Turn a payload into a rich renderable (Text or JSON).

        @staticmethod means this function doesn't need `self` — it's a pure
        function that happens to live inside the class for organization.
        """
        # Plain-text kinds get rendered as styled Text.
        if kind == "user":
            return Text(str(payload))
        if kind == "system":
            return Text(str(payload), style="dim")
        if kind == "final":
            return Text(str(payload), style="bold")
        if kind == "error":
            return Text(str(payload), style="red")
        # Everything else — LLM thoughts, tool calls, tool results — is
        # structured data. RichJSON pretty-prints it with syntax highlighting.
        try:
            return RichJSON.from_data(payload)
        except (TypeError, ValueError):
            # Fallback for anything RichJSON can't handle (rare).
            return Text(str(payload))
