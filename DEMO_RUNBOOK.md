# Demo Runbook — S7 / S8 / S9 YouTube videos

> **Goal:** record one short YouTube demo per session so each submission's
> rubric is satisfied. **5-7 min each**, unlisted on YouTube, paste link in
> the assignment form. Each demo just runs the commands below and you
> narrate what's happening on screen.

## Universal prep (do once, before recording)

1. Get OBS / QuickTime / Loom ready. Recommend **Loom** — it's the
   fastest path: just record window, get link, paste. Free tier is fine.
2. Have **two terminals** open side-by-side:
   - Left: the gateway (you'll start once, never touch again).
   - Right: the agent (you'll run the query and narrate the trace).
3. Have your `.env` keys loaded. You should see `GEMINI_API_KEY` already in
   `~/Downloads/agentic/.env` from prior runs.

---

## S7 — Memory & Retrieval (the vector-memory demo)

**Branch:** `s07/memory-retrieval`. **Gateway:** V7 on `:8107`. **Time:** ~5 min.

### Pre-flight (do once)

```bash
# Make sure nomic-embed-text is pulled (one time, takes a minute)
ollama pull nomic-embed-text
```

### Recording sequence

**LEFT terminal (start V7 gateway):**
```bash
cd ~/Downloads/agentic/s07/llm_gatewayV7
env -u ANTHROPIC_API_KEY -u ANTHROPIC_BASE_URL ./run.sh
```
Wait for `Application startup complete.` then leave it alone.

**RIGHT terminal (the demo):**
```bash
cd ~/ws/projects/mini-perplexity
git checkout s07/memory-retrieval
cd agentic
rm -rf state                  # clean start — show it on camera
```

**Demo Query 1 — index a paper + extract its contributions** (~90 s):
```bash
env -u ANTHROPIC_API_KEY -u ANTHROPIC_BASE_URL \
  MCP_SERVER_CMD=".venv/bin/python mcp_server.py" \
  GATEWAY_URL="http://localhost:8107" \
  .venv/bin/python agent6.py "Index papers/attention.md and tell me the three key contributions of the Transformer architecture according to that paper."
```

**Narrate while it runs:**
- "Iter 1 — Perception decomposes into TWO goals: index, then extract. Decision picks `index_document`. Action chunks the paper into 3 facts and embeds each."
- "Iter 2 — Decision now picks `search_knowledge` to query the embedded chunks. The vector index returns the top chunk."
- "Final answer: the 3 contributions of the Transformer."

**Demo Query 2 — the hero shot, semantic recall** (~30 s):
```bash
env -u ANTHROPIC_API_KEY -u ANTHROPIC_BASE_URL \
  MCP_SERVER_CMD=".venv/bin/python mcp_server.py" \
  GATEWAY_URL="http://localhost:8107" \
  .venv/bin/python agent6.py "Use search_knowledge to find what the indexed papers say about parameter-efficient model adaptation, and tell me which paper covers it."
```

**Narrate the punchline:**
- "The query says 'parameter-efficient adaptation'. Those exact words DO NOT
  appear in the answer chunk. nomic-embed-text understood they mean the
  same as LoRA's 'language model adaptation' — that's semantic recall,
  the thing keyword search can't do. THIS is why S7 exists."

**Wrap (10 s):** "All committed on the `s07/memory-retrieval` branch. README has the full output of both queries. Thanks."

---

## S8 — Multi-Agent DAG Orchestration (parallel fan-out)

**Branch:** `s08/dag-orchestration`. **Gateway:** V8 on `:8108`. **Time:** ~6 min.

### Recording sequence

**LEFT terminal (kill V7 if running, start V8):**
```bash
lsof -ti tcp:8107 | xargs kill -9 2>/dev/null
cd ~/Downloads/agentic/s09/llm_gatewayV8
env -u ANTHROPIC_API_KEY -u ANTHROPIC_BASE_URL ./run.sh
```

**RIGHT terminal:**
```bash
cd ~/ws/projects/mini-perplexity
git checkout s08/dag-orchestration
cd s08-dag/code
rm -rf state/sessions
```

**Demo Query 1 — hello (smallest DAG, 10 sec):**
```bash
env -u ANTHROPIC_API_KEY -u ANTHROPIC_BASE_URL .venv/bin/python flow.py "hello"
```
**Narrate:** "Two nodes — Planner correctly decides no research is needed; Formatter answers. Smallest possible DAG."

**Demo Query 2 — the fan-out hero (~60 sec):**
```bash
env -u ANTHROPIC_API_KEY -u ANTHROPIC_BASE_URL .venv/bin/python flow.py "Find the populations of London, Paris, Berlin and tell me which two are closest in size."
```
**Narrate while it runs:**
- "Planner emits THREE researcher nodes plus a formatter."
- "Watch the trace — three researchers running CONCURRENTLY via asyncio.gather. Each scoped to ONE city. None sees the others' work."
- "Sum of the three elapsed times: ~110 s. Wall-clock at the parallel layer: max(branches), about 45 s. That's the speedup — and the gateway cost log shows fewer tokens per node because nothing re-sends history."

**Demo Query 3 — our new skill `fact_checker` (~45 sec):**
```bash
env -u ANTHROPIC_API_KEY -u ANTHROPIC_BASE_URL .venv/bin/python flow.py "Verify these two claims and tell me which are correct: (1) The Eiffel Tower was completed in 1889. (2) Mount Everest is 9,848 metres tall."
```
**Narrate:** "Our student-added skill `fact_checker`. The Planner routed to it (we taught it in planner.md). Watch it call `web_search` and `fetch_url` against Wikipedia and Google to verify each claim. Three-valued verdict (supported / refuted / inconclusive) — the third value prevents the rubber-stamp failure mode the lesson warned about."

**Wrap (10 s):** "Branch `s08/dag-orchestration`. README has all five queries. The Coder prompt and fact_checker are our work; the runtime is the provided patched code."

---

## S9 — Browser Agents (the HF top-3 demo)

**Branch:** `s09/browser-agents`. **Gateway:** V9 on `:8109`. **Time:** ~7 min.

### Pre-flight (one-time)

```bash
# Chromium for Playwright — only once, takes ~2 min
cd ~/Downloads/agentic/s09/S9SharedCode/code
uv run playwright install chromium
```

### Recording sequence

**LEFT terminal (V8 down, V9 up):**
```bash
lsof -ti tcp:8108 | xargs kill -9 2>/dev/null
cd ~/Downloads/agentic/s09/llm_gatewayV9
env -u ANTHROPIC_API_KEY -u ANTHROPIC_BASE_URL ./run.sh
```

**RIGHT terminal:**
```bash
cd ~/ws/projects/mini-perplexity
git checkout s09/browser-agents
cd s09-browser/code
rm -rf state/sessions
```

**Demo (~3 min):**
```bash
env -u ANTHROPIC_API_KEY -u ANTHROPIC_BASE_URL .venv/bin/python flow.py "What are the top 3 most-liked open-source LLMs on Hugging Face right now (text-generation models, sorted by likes)? For each give the model name, parameter count, and a one-line description. Use the Browser skill to interact with huggingface.co/models."
```

**Narrate while it runs:**
- "DAG: Planner → Browser → Distiller → auto-inserted Critic → Formatter."
- "Browser cascade: it's choosing the **a11y** layer — not vision. That's the S9 finding: HuggingFace has ARIA labels, so the cheap text LLM reading the accessibility tree is enough."
- "10 turns of click actions, each scanned, judged, dispatched."
- "Final URL has all 3 filters + sort applied."
- "Auto-inserted Critic — this is the bug-fix from the lesson; pre-planned distiller→formatter edges now get the Critic inserted by reading actual outgoing edges, not just dynamic adds."

**Then generate the replay report (~30 s):**
```bash
sid=$(ls -t state/sessions/ | head -1)
.venv/bin/python make_replay_report.py "$sid"
cat state/sessions/$sid/replay_report.md
```

**Narrate the report:** "Eight items the rubric asks for: goal, planner DAG, browser path chosen (a11y), browser actions taken, page-state logs, extracted data, final comparison table, turn + cost summary. All here."

**Wrap (15 s):** "Branch `s09/browser-agents`. Evidence dir has the captured run. The orchestrator was never modified — extension is via `make_replay_report.py` only, satisfying the S9 hard rule."

---

## Recording tips that matter on camera

- **Terminal font size 16+.** Otherwise no one can read the trace.
- **Dark background** (Solarized Dark / One Dark) — easier on viewer's eyes.
- **Cursor highlight**: Loom does this automatically. Otherwise on macOS, System Settings → Accessibility → Display → "Increase pointer size".
- **Mute system sounds.** Slack notifications mid-demo are the most common cause of re-shoots.
- **State the obvious in the first 10 seconds**: "This is session N. The branch is X. I'm running query Y." That's the chapter mark graders look for.
- **Length**: 5-7 minutes per video. Anything shorter looks rushed; anything longer loses viewers.

## After recording

For each video:
1. Upload to YouTube as **Unlisted** (not Private, not Public — Unlisted means anyone with the link).
2. Paste the link into the LMS submission form for that session.
3. Confirm the form shows "submitted" with the link visible.

---

## Cost & time

- Demos run on Gemini 3.1 Flash-Lite (free tier). **$0** out of pocket.
- All three: ~20 min of agent runtime + ~20 min of recording = **~40 min of work** if nothing goes wrong, ~90 min realistic.
- The gateway versions don't conflict — you can keep all three running on different ports simultaneously if you want zero downtime between recordings.
