"""Mini Perplexity — web UI surface.

A FastAPI + SSE server that drives the same `run_agent` loop the CLI
does, streaming each ChainEvent to a vanilla-JS chat page that can
toggle the reasoning panel on/off.

The agent core lives at the repo root (mini_perplexity.py et al.) and
is imported as a regular module — this package adds NO logic of its
own beyond transport + rendering.
"""
