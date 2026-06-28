#!/usr/bin/env bash
# Single command to bring up S10 prerequisites.
#
# Run this AFTER cua-driver is installed and TCC permissions are granted.
# Exits non-zero with a useful message on any check failure.
#
# Usage:  ./boot_s10.sh
set -uo pipefail

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; RESET='\033[0m'
ok()   { printf "  ${GREEN}✓${RESET} %s\n" "$1"; }
fail() { printf "  ${RED}✗${RESET} %s\n" "$1"; exit 1; }
warn() { printf "  ${YELLOW}!${RESET} %s\n" "$1"; }

echo "=== S10 boot check ==="

# 1. cua-driver binary on PATH
if ! command -v cua-driver >/dev/null 2>&1; then
  CUA=$HOME/.local/bin/cua-driver
  [ -x "$CUA" ] || fail "cua-driver not installed. Run the install command from /Users/level/ws/projects/mini-perplexity/s10-computer-use/INSTALL.md"
  export PATH="$HOME/.local/bin:$PATH"
fi
ok "cua-driver on PATH ($(cua-driver --version 2>&1 | head -1))"

# 2. cua-driver doctor (permissions, daemon path, etc.)
DOCTOR_OUT=$(cua-driver doctor 2>&1 || true)
if echo "$DOCTOR_OUT" | grep -qi "permissions denied\|not granted\|missing grant"; then
  fail "cua-driver doctor reports missing permissions. Run: cua-driver permissions grant — and click Allow on both system dialogs."
fi
ok "cua-driver doctor passes"

# 3. cua-driver daemon (start if not running)
if ! cua-driver status >/dev/null 2>&1; then
  warn "daemon not running — starting cua-driver serve in background"
  cua-driver serve >/tmp/cua-driver.log 2>&1 &
  sleep 2
  cua-driver status >/dev/null 2>&1 || fail "cua-driver serve failed to start. Check /tmp/cua-driver.log"
fi
ok "cua-driver daemon running"

# 4. quick smoke: list_apps (no TCC needed) → confirms we can talk to the daemon
if ! cua-driver call list_apps '{}' >/dev/null 2>&1; then
  fail "cua-driver call list_apps failed — daemon up but not responding"
fi
ok "cua-driver responds to list_apps"

# 5. V9 gateway on :8109 (start if not running)
if ! curl -sf http://localhost:8109/v1/providers >/dev/null 2>&1; then
  warn "V9 gateway not on :8109 — starting"
  GATEWAY_V9_DIR=$HOME/Downloads/agentic/s09/llm_gatewayV9
  [ -d "$GATEWAY_V9_DIR" ] || fail "V9 gateway dir not found at $GATEWAY_V9_DIR"
  ( cd "$GATEWAY_V9_DIR" && env -u ANTHROPIC_API_KEY -u ANTHROPIC_BASE_URL ./run.sh >/tmp/v9.log 2>&1 & )
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    sleep 1
    if curl -sf http://localhost:8109/v1/providers >/dev/null 2>&1; then break; fi
  done
  curl -sf http://localhost:8109/v1/providers >/dev/null 2>&1 || fail "V9 gateway failed to come up. Check /tmp/v9.log"
fi
ok "V9 gateway listening on :8109"

# 6. providers actually configured (.env loaded)
PROV=$(curl -sf http://localhost:8109/v1/providers | python3 -c 'import json,sys; print(",".join(json.load(sys.stdin).get("order",[])))' 2>/dev/null || echo "")
[ -n "$PROV" ] || fail "V9 gateway has zero providers — .env not loaded. Check $HOME/Downloads/agentic/s09/.env"
ok "V9 providers: $PROV"

echo
echo "=== READY ==="
echo "  Next:  uv run python run_s10_tasks.py        # all 3 tasks"
echo "  or:    uv run python run_s10_tasks.py calc   # just Calculator (fastest)"
echo
