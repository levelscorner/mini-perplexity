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
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.json import JSON as RichJSON
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text


@dataclass
class ChainEvent:
    """One entry in the persisted reasoning-chain log."""

    kind: str  # user | llm | tool_call | tool_result | final | error | system
    iteration: int
    payload: Any
    ts: str

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "iteration": self.iteration,
            "kind": self.kind,
            "payload": self.payload,
        }


class ReasoningChainUI:
    """Streams panels to stdout AND captures an event log for submission."""

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
        self.console = Console()
        self.log_path = log_path
        self.events: list[ChainEvent] = []

    # ----- public emitters ----------------------------------------------------

    def banner(self, query: str) -> None:
        self.console.print(Rule("[bold]Skill Builder — S03 Agentic Loop[/bold]"))
        self._emit("user", 0, query)

    def iteration_header(self, iteration: int) -> None:
        self.console.print()
        self.console.print(Rule(f"[bold]Iteration {iteration}[/bold]", style="blue"))

    def llm(self, iteration: int, raw_text: str, parsed: dict | None) -> None:
        payload = {"raw": raw_text, "parsed": parsed}
        self._emit("llm", iteration, payload)

    def tool_call(self, iteration: int, name: str, args: dict) -> None:
        self._emit("tool_call", iteration, {"name": name, "arguments": args})

    def tool_result(self, iteration: int, name: str, result: str) -> None:
        parsed_result: Any = result
        try:
            parsed_result = json.loads(result)
        except (TypeError, ValueError):
            pass
        self._emit(
            "tool_result", iteration, {"name": name, "result": parsed_result}
        )

    def final(self, iteration: int, answer: str) -> None:
        self._emit("final", iteration, answer)

    def error(self, iteration: int, message: str) -> None:
        self._emit("error", iteration, message)

    def system(self, message: str) -> None:
        self._emit("system", 0, message)

    # ----- persistence --------------------------------------------------------

    def save(self) -> Path | None:
        if self.log_path is None:
            return None
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "events": [e.to_dict() for e in self.events],
        }
        self.log_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return self.log_path

    # ----- internals ----------------------------------------------------------

    def _emit(self, kind: str, iteration: int, payload: Any) -> None:
        event = ChainEvent(
            kind=kind,
            iteration=iteration,
            payload=payload,
            ts=datetime.utcnow().isoformat(timespec="seconds") + "Z",
        )
        self.events.append(event)
        self._render(event)

    def _render(self, event: ChainEvent) -> None:
        border, title_style, label = self._STYLE.get(
            event.kind, ("white", "bold white", event.kind)
        )
        title = f"[{title_style}]{label}[/{title_style}]"
        if event.iteration:
            title = f"{title}  [dim](iter {event.iteration})[/dim]"

        body = self._format_body(event.kind, event.payload)
        self.console.print(
            Panel(body, title=title, title_align="left", border_style=border)
        )

    @staticmethod
    def _format_body(kind: str, payload: Any) -> Any:
        if kind == "user":
            return Text(str(payload))
        if kind == "system":
            return Text(str(payload), style="dim")
        if kind == "final":
            return Text(str(payload), style="bold")
        if kind == "error":
            return Text(str(payload), style="red")
        # JSON-ish payloads
        try:
            return RichJSON.from_data(payload)
        except (TypeError, ValueError):
            return Text(str(payload))
