"""Higgsfield MCP OAuth bootstrap — run once to grant access.

Usage:
    python auth.py            # opens browser, completes OAuth, persists tokens
    python auth.py --check    # check whether cached tokens already work
    python auth.py --reset    # clear cached tokens, force re-auth

Tokens are persisted via py-key-value-aio's DiskStore at:
    ~/.mini-perplexity/tokens/

Once authed, the chat agent's render_image tool reuses the cached
tokens automatically. No browser interaction needed on subsequent runs.

Same flow is callable from the dashboard's Connect button via the
`start_higgsfield_auth` proxy endpoint on webapp/server.py.
"""
from __future__ import annotations

import shutil
import sys

from rich.console import Console
from rich.panel import Panel

from higgsfield import (
    HIGGSFIELD_URL,
    TOKEN_DIR,
    auth_status,
    bootstrap_oauth,
)


console = Console()


def main() -> int:
    if "--check" in sys.argv:
        result = auth_status()
        if result["ok"]:
            console.print(
                Panel(
                    f"[green]Authenticated[/green]\n{result['info']}",
                    title="Higgsfield auth",
                )
            )
            return 0
        console.print(
            Panel(
                f"[yellow]Not authenticated[/yellow]\n{result['info']}\n\n"
                "Run `python auth.py` (no flags) to start the OAuth flow.",
                title="Higgsfield auth",
            )
        )
        return 1

    if "--reset" in sys.argv:
        if TOKEN_DIR.exists():
            shutil.rmtree(TOKEN_DIR)
            console.print(f"[yellow]Cleared cached tokens at {TOKEN_DIR}[/yellow]")
        else:
            console.print("[dim]No cached tokens to clear.[/dim]")
        return 0

    console.print(
        Panel(
            f"Starting OAuth flow to [bold]{HIGGSFIELD_URL}[/bold].\n"
            f"Tokens will be cached to [dim]{TOKEN_DIR}[/dim].\n\n"
            "[yellow]A browser window will open. Sign in to your "
            "Higgsfield account and approve access.[/yellow]",
            title="Higgsfield OAuth bootstrap",
        )
    )

    result = bootstrap_oauth()
    if result["ok"]:
        console.print(
            Panel(
                f"[green]✅ Authenticated[/green]\n{result['info']}\n\n"
                "Image rendering is now live.",
                title="Higgsfield OAuth bootstrap",
            )
        )
        return 0
    console.print(
        Panel(
            f"[red]OAuth flow failed[/red]\n{result['info']}",
            title="Higgsfield OAuth bootstrap",
        )
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
