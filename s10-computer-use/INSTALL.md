# Installing cua-driver (~3 minutes)

The Computer-Use skill talks to a Rust binary called `cua-driver` which
exposes 34 native-OS automation tools (click, type, scan AX tree,
screenshot, record trajectory) over a Unix socket. This file is the
exact install sequence I followed, with the macOS TCC dance.

## Step 1 — Install the binary (sudo-free)

Paste this into a terminal. It downloads `CuaDriver.app` to
`/Applications` and symlinks `cua-driver` into `~/.local/bin`:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/trycua/cua/main/libs/cua-driver/scripts/install.sh)"
```

Verify:

```bash
~/.local/bin/cua-driver --version          # prints version
~/.local/bin/cua-driver list-tools         # prints ~34 tool names
~/.local/bin/cua-driver doctor             # full system check
```

If `cua-driver` isn't on PATH, add it:

```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

## Step 2 — Grant macOS TCC permissions (the HITL step)

This is the only step a human must do; clicking through dialogs can't
be scripted (by design — the OS would refuse).

```bash
~/.local/bin/cua-driver permissions grant
```

This **launches CuaDriver.app**, which triggers two system prompts in
sequence:

1. **Accessibility** — required for reading the AX tree and synthesising
   clicks/keystrokes. Click **Allow**.
2. **Screen Recording** — required for screenshots used by Layer 3
   vision. Click **Allow**.

> **Important:** the grant binds to the bundle ID that triggered the
> prompt. The `permissions grant` command launches CuaDriver.app
> specifically so the grant attaches to `com.trycua.driver`. **Don't**
> manually run `cua-driver` from Terminal first — that would attach the
> grant to Terminal instead and the driver would silently get empty AX
> trees later.

Confirm both grants in **System Settings → Privacy & Security**:
- Accessibility → `CuaDriver` toggled ON
- Screen Recording → `CuaDriver` toggled ON

Reboot is NOT required.

## Step 3 — Start the daemon

```bash
~/.local/bin/cua-driver serve &
~/.local/bin/cua-driver status               # confirms daemon up
```

The daemon holds the element-index cache that makes `click_index` work
across `scan/act/verify` turns. Without the daemon, every cua-driver
call spawns a fresh process and the cache is lost between calls.

## Step 4 — Smoke test

Compute 7 × 8 in Calculator end-to-end as a one-liner sanity check:

```bash
~/.local/bin/cua-driver call launch_app '{"bundle_id":"com.apple.calculator"}'
osascript -e 'tell application "Calculator" to activate'
sleep 1
PID=$(~/.local/bin/cua-driver call list_apps '{}' | python3 -c "import json,sys; d=json.load(sys.stdin); print([a['pid'] for a in d.get('apps',[]) if 'Calculator' in str(a)][0])")
WID=$(~/.local/bin/cua-driver call list_windows '{}' | python3 -c "import json,sys; d=json.load(sys.stdin); print([w['window_id'] for w in d.get('windows',[]) if w.get('pid')==$PID][0])")
echo "Calculator pid=$PID wid=$WID"
~/.local/bin/cua-driver call get_window_state "{\"pid\":$PID,\"window_id\":$WID,\"capture_mode\":\"ax\",\"query\":\"button\"}" | python3 -c "import json,sys; d=json.load(sys.stdin); print('elements:', d.get('element_count'))"
```

If `elements:` prints a non-zero number (typically 237 for Calculator),
the install + grants are good. If it prints `0`, re-check the
Accessibility grant.

## Troubleshooting

- **`cua-driver: command not found`** — PATH issue. `export PATH="$HOME/.local/bin:$PATH"`.
- **`element_count: 0` on first scan after launch** — the app was launched
  in the background. Add `osascript -e 'tell application "<Name>" to
  activate'` before the scan and sleep 0.5s.
- **`element_count: 0` always** — TCC grants didn't attach. Reset and
  retry: `tccutil reset Accessibility com.trycua.driver` then re-grant.
- **`Element index N not found in cache`** — daemon isn't running.
  `cua-driver serve &`.

## Once everything above passes

Run the boot check from this directory:

```bash
cd code
./boot_s10.sh
```

It verifies the same things automatically and starts the V9 gateway if
needed. Then run the demo tasks with `uv run python run_s10_tasks.py`.
