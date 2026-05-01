"""Prefab dashboard — Stats / Activity / Auth tabs.

Served by webapp/server.py at GET /dashboard. Polls JSON endpoints.
"""
from __future__ import annotations

from prefab_ui.actions import Fetch, SetState, ShowToast
from prefab_ui.app import PrefabApp
from prefab_ui.components import (
    Badge, Button, Card, Column, Else, Grid, Heading, If, Metric, Row,
    Tab, Tabs, Text,
)
from prefab_ui.components.control_flow import ForEach


_STATS_FETCH = Fetch(
    url="/api/stats",
    method="GET",
    on_success=[
        SetState("stats_tools", "{{ $result.tools_array }}"),
        SetState("totals_calls", "{{ $result.totals.tool_calls }}"),
        SetState("totals_ok", "{{ $result.totals.tool_calls_ok }}"),
        SetState("totals_fail", "{{ $result.totals.tool_calls_fail }}"),
        SetState("totals_seconds", "{{ $result.totals.tool_seconds_total }}"),
        SetState("chat_turns", "{{ $result.chat_turns }}"),
        SetState("chat_in", "{{ $result.chat_in_tokens }}"),
        SetState("chat_out", "{{ $result.chat_out_tokens }}"),
    ],
)

_ACTIVITY_FETCH = Fetch(
    url="/api/recent-activity",
    method="GET",
    on_success=SetState("activity", "{{ $result.events }}"),
)


def _tab_stats() -> None:
    with Column(gap=3):
        with Row(gap=2, css_class="items-center justify-between"):
            Heading("Runtime stats", level=3)
            Button("Refresh", variant="outline", size="sm", on_click=_STATS_FETCH)
        with Grid(columns=3, gap=3):
            Metric(label="Tool calls", value="{{ totals_calls }}")
            Metric(label="OK / Fail",
                   value="{{ totals_ok }} / {{ totals_fail }}")
            Metric(label="Tool seconds", value="{{ totals_seconds }}")
            Metric(label="Chat turns", value="{{ chat_turns }}")
            Metric(label="Tokens in", value="{{ chat_in }}")
            Metric(label="Tokens out", value="{{ chat_out }}")
        Heading("Per-tool breakdown", level=4)
        with If("stats_tools.length == 0"):
            Text("No tool calls recorded yet.",
                 css_class="text-muted-foreground")
        with ForEach("stats_tools") as t:
            with Card():
                with Row(gap=3, css_class="items-center"):
                    Text(t.name, css_class="font-mono font-medium flex-1")
                    Badge(t.count_total, variant="secondary")
                    Badge(t.count_ok, variant="success")
                    Badge(t.count_fail, variant="destructive")
                    Text(t.duration_ms_avg,
                         css_class="text-xs text-muted-foreground")


def _tab_activity() -> None:
    with Column(gap=3):
        with Row(gap=2, css_class="items-center justify-between"):
            Heading("Tool-call activity", level=3)
            Button("Refresh", variant="outline", size="sm",
                   on_click=_ACTIVITY_FETCH)
        Text("Every tool call (newest first) — timestamp, input, "
             "duration, status.",
             css_class="text-muted-foreground text-sm")
        with If("activity.length == 0"):
            Text("No tool calls yet.", css_class="text-muted-foreground")
        with ForEach("activity") as e:
            with Card(css_class="py-2"):
                with Row(gap=3, css_class="items-center"):
                    Text(e.ts,
                         css_class="font-mono text-xs text-muted-foreground")
                    Text(e.name, css_class="font-mono font-medium text-sm")
                    Text(e.input,
                         css_class="text-xs text-muted-foreground flex-1 truncate")
                    Text(e.duration_ms,
                         css_class="font-mono text-xs text-muted-foreground")
                    with If("$item.status == 'fail'"):
                        Badge("fail", variant="destructive")
                    with Else():
                        Badge("ok", variant="success")


def _tab_auth() -> None:
    with Column(gap=3):
        Heading("Higgsfield connection", level=3)
        Text("First click on Connect opens a browser tab; tokens persist "
             "in ~/.mini-perplexity/tokens/.",
             css_class="text-muted-foreground text-sm")
        with Row(gap=2, css_class="items-center"):
            Badge("{{ auth_state }}", variant="{{ auth_variant }}")
            Text("{{ auth_info }}", css_class="text-sm")
        with Row(gap=2):
            Button("Check status", variant="outline", on_click=Fetch(
                url="/api/tool/higgsfield_auth_status",
                method="POST",
                body={},
                on_success=[
                    SetState("auth_state", "{{ $result.state_label }}"),
                    SetState("auth_variant", "{{ $result.state_variant }}"),
                    SetState("auth_info", "{{ $result.info }}"),
                ],
            ))
            Button("Connect Higgsfield", variant="default", on_click=Fetch(
                url="/api/tool/start_higgsfield_auth",
                method="POST",
                body={},
                on_success=[
                    SetState("auth_state", "{{ $result.state_label }}"),
                    SetState("auth_variant", "{{ $result.state_variant }}"),
                    SetState("auth_info", "{{ $result.info }}"),
                    ShowToast("Higgsfield connected", variant="success"),
                ],
                on_error=ShowToast("{{ $error }}", variant="error"),
            ))


def build_dashboard() -> PrefabApp:
    on_mount = [_STATS_FETCH, _ACTIVITY_FETCH]
    with Column(gap=5, css_class="max-w-[1100px] mx-auto py-6 px-4") as view:
        Heading("Mini Perplexity — Dashboard", level=1)
        Text("Stats, activity, and auth for the chat agent.",
             css_class="text-muted-foreground text-sm")
        with Card(css_class="overflow-hidden"):
            with Tabs(value="stats"):
                with Tab("Stats", value="stats"):
                    _tab_stats()
                with Tab("Activity", value="activity"):
                    _tab_activity()
                with Tab("Auth", value="auth"):
                    _tab_auth()
    return PrefabApp(
        view=view,
        on_mount=on_mount,
        state={
            "stats_tools": [],
            "totals_calls": 0, "totals_ok": 0, "totals_fail": 0,
            "totals_seconds": 0,
            "chat_turns": 0, "chat_in": 0, "chat_out": 0,
            "activity": [],
            "auth_state": "unknown", "auth_variant": "secondary",
            "auth_info": "Click Check status.",
        },
    )
