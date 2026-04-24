# Mini Perplexity — Build Walkthrough

A step-by-step explanation of how this agent works, why each piece exists, and how the pieces fit together.

Originally built for **Session 3** ("Developer Foundations & Your First Agent") of [EAG V3](https://github.com/levelscorner/levelscorner-eva3) — a 19-session course on agentic AI from The School of AI. The S3 rubric requires:

1. An **agentic loop** that calls the LLM more than once.
2. **Each LLM call carries the full prior conversation** (history, not last message only).
3. **At least three custom tools** the agent can call.
4. **Render the reasoning chain** so a viewer can watch the agent think.

This walkthrough is the build, decision by decision.

---

## 0. The mental model

Perplexity AI is, stripped down, four things in a loop:

```
question → search → read → cite → answer
```

The interesting part is **the loop is driven by the LLM**, not by a hard-coded script. The LLM picks the search query. The LLM decides which URLs to read. The LLM stops when it has enough.

So the agent has exactly three powers (= three tools):

| Tool | Power |
|------|-------|
| `web_search`  | "let me look that up" |
| `fetch_page`  | "let me read that source" |
| `save_answer` | "I'm done — write it down with citations" |

Everything else — the `if/else`, the picking, the synthesis — happens inside the LLM, prompted by a strict system prompt and held together by a Python loop that is dumb on purpose.

---

## 1. Project shape

```
mini-perplexity/
├── README.md
├── pyproject.toml          # deps + entrypoint
├── .env.example            # GEMINI_API_KEY template
├── system_prompt.md        # the agent's instructions (markdown for readability)
├── mini_perplexity.py      # entrypoint + the agent loop
├── llm.py                  # Gemini client wrapper
├── parser.py               # robust JSON parser for LLM output
├── tools.py                # web_search, fetch_page, save_answer
├── ui.py                   # rich panels + JSON event log
├── _smoke_test.py          # offline smoke (no LLM, no network)
├── answers/                # generated markdown answers
└── logs/                   # per-run JSON event logs
```

Five load-bearing modules. Each reads top-to-bottom in one screen. No clever inheritance, no plugin system, no abstract base classes.

---

## 2. The five modules — what each does and why

### 2.1 `system_prompt.md` — the agent's brain stem

Markdown, not Python. Three reasons:

- **Readable.** Prompts are content; code is plumbing. Don't mix them.
- **Diff-friendly.** When the agent misbehaves, you tweak prose, not strings.
- **Loadable as text.** `mini_perplexity.py` reads it once at startup.

The prompt does four jobs:

1. **Declares the role** — "You are Mini Perplexity, a research agent."
2. **Lists the three tools** with exact signatures.
3. **Pins the response contract** — every turn returns *exactly one JSON object*, either a tool call or a final answer.
4. **Prescribes the workflow** — `web_search → fetch_page ×2–3 → save_answer → answer`.

The contract is the load-bearing part. If the LLM sometimes returns prose and sometimes JSON, the loop becomes a parser-resilience nightmare. By making the contract strict and repeating it, drift drops to near-zero.

### 2.2 `tools.py` — the hands

Three pure functions. Each one:

- Takes simple positional/keyword args.
- Returns a **JSON string** (not a dict).
- Catches its own exceptions and returns `{"error": "..."}` JSON instead of raising.

Returning JSON strings is deliberate. The loop appends tool results to `messages[]` as `{"role": "tool", "content": <string>}` and then re-renders the whole conversation as text on the next iteration. JSON strings round-trip through that pipeline without escape-hell.

```python
TOOLS = {
    "web_search": web_search,
    "fetch_page": fetch_page,
    "save_answer": save_answer,
}
```

The registry is a plain dict. The loop dispatches by name: `TOOLS[parsed["tool_name"]](**parsed["tool_arguments"])`. No reflection, no decorators, no schema parsing.

**Why these three?**

- `web_search` — DuckDuckGo via `duckduckgo-search`. **No API key.** Critical for a portable demo: anyone who clones the repo can run it with just a Gemini key.
- `fetch_page` — `requests` + `trafilatura`. Trafilatura strips nav/footer/ads and keeps the article body. Truncated to ~5000 chars so the conversation history doesn't blow up the context window after 2–3 reads.
- `save_answer` — writes markdown to `answers/<slug>.md`. Refuses to overwrite (appends `-2`, `-3`). Honors `ANSWERS_DIR` env var for sandboxing.

### 2.3 `llm.py` — the mouth

Tiny wrapper around Google's `google-genai` SDK. Three responsibilities:

1. **Read `GEMINI_API_KEY`** from env. Fail loud at startup if missing.
2. **Read `GEMINI_MODEL`** (default `gemini-2.5-flash-lite`). Free tier, fast, cheap.
3. **Throttle** — `time.sleep(THROTTLE_SECONDS)` before each call. The Gemini free tier caps at 15 RPM. A 4-second sleep keeps you safely under without manual rate-limit handling.

The whole class is ~45 lines. If you want to swap in OpenAI or Anthropic later, this is the only file you change.

### 2.4 `parser.py` — surviving LLM drift

LLMs claim to return strict JSON. They lie sometimes. They wrap it in ```` ```json ... ``` ````. They prepend "Here is the JSON:". They append "Hope this helps!".

The parser tries three strategies in order:

1. Strip markdown code fences if present.
2. `json.loads` the whole thing.
3. Regex-extract the first `{...}` block and parse that.

If all three fail, raise `ValueError` with the first 200 chars. The loop catches it, sends a corrective message back to the LLM ("respond with ONLY a JSON object"), and continues. **The loop never crashes from bad JSON — it teaches the model.**

### 2.5 `ui.py` — the visible reasoning chain

This is what satisfies the rubric's "display the reasoning chain" rule. The `ReasoningChainUI` class does two things at once:

1. **Streams to the terminal** as `rich` panels — one panel per event (user query, LLM thought, tool call, tool result, final answer, errors).
2. **Captures every event** to `logs/run-<timestamp>.json` for later inspection.

Each panel has a color and a label:

| Event | Color | Label |
|-------|-------|-------|
| user        | cyan          | You |
| llm         | magenta       | LLM thought |
| tool_call   | yellow        | Tool call |
| tool_result | green         | Tool result |
| final       | bright green  | Final answer |
| error       | red           | Error |

The chronology of panels reads like the agent's inner monologue. The JSON log is the same data, machine-readable.

---

## 3. The loop — `mini_perplexity.py`

This is the whole agent in 30-ish lines of meaningful logic. The shape:

```python
messages = [{"role": "user", "content": user_query}]

for iteration in range(1, max_iterations + 1):
    prompt = _render_conversation(system_prompt, messages)   # 1. build prompt
    raw    = llm.generate(prompt)                            # 2. ask LLM
    parsed = parse_llm_response(raw)                         # 3. parse JSON

    if "answer" in parsed:                                   # 4a. done
        ui.final(iteration, parsed["answer"])
        break

    if "tool_name" in parsed:                                # 4b. tool call
        result = TOOLS[parsed["tool_name"]](**parsed["tool_arguments"])
        messages.append({"role": "assistant", "content": raw})
        messages.append({"role": "tool", "content": result})
        continue

    # 4c. neither — coach the model and try again
    messages.append({"role": "user", "content": "Respond with JSON ..."})
```

### Why this shape?

- **Full history every iteration.** `_render_conversation` flattens `messages[]` into one big string and prefixes the system prompt. The LLM sees everything that has happened so far on every call. This is the rubric's "each query carries all past interaction" requirement — and it's also what lets the LLM reason about *which URLs it has already fetched* without the loop tracking it.
- **No streaming, no async.** The loop is sync, blocking, easy to read in one pass. Streaming would add nothing for a CLI demo.
- **Bounded iterations.** Default cap = 8. If the model gets stuck, the loop exits with a clean error rather than burning quota forever.
- **JSON contract is enforced by the loop, not by the LLM.** Bad JSON → corrective message. Unknown tool → `{"error": "unknown tool ..."}` fed back. Bad arguments → `{"error": "bad arguments: ..."}` fed back. The model learns within the run.
- **Tool errors are data, not exceptions.** Every tool catches its own exceptions and returns JSON. The loop never has to know the difference between "search failed" and "page returned 404" — both are just tool results the LLM can read and recover from.

---

## 4. The conversation rendering trick

The line that does the most work in the simplest way:

```python
def _render_conversation(system: str, messages: list[dict]) -> str:
    parts = [system.strip(), ""]
    for msg in messages:
        role = msg["role"]
        if role == "user":      parts.append(f"User: {msg['content']}\n")
        elif role == "assistant": parts.append(f"Assistant: {msg['content']}\n")
        elif role == "tool":      parts.append(f"Tool Result: {msg['content']}\n")
    parts.append("Assistant:")
    return "\n".join(parts)
```

This is the course's canonical "agent loop" pattern: instead of using the SDK's structured chat API (which differs across providers), serialize the whole conversation as plain text and ask the model to continue. Works on every model from every provider. Trivial to debug — print the string and you see exactly what the model sees.

The trailing `"Assistant:"` is a soft prompt to make the model continue as the assistant rather than restate the system message.

---

## 5. The flow on a real query

Say `python mini_perplexity.py "What's new in Claude 4.7?"`:

```
Iteration 1
  prompt:        system_prompt + "User: What's new in Claude 4.7?"
  llm response:  {"tool_name":"web_search","tool_arguments":{"query":"Claude 4.7 new features"}}
  tool call:     web_search(query="Claude 4.7 new features")
  tool result:   {"results":[{"rank":1,"title":"...","url":"https://...", ...}, ...]}

Iteration 2
  prompt:        system + iter1 history
  llm response:  {"tool_name":"fetch_page","tool_arguments":{"url":"https://..."}}
  tool call:     fetch_page(url="https://...")
  tool result:   {"url":"...","title":"...","text":"...", "truncated":false, ...}

Iteration 3
  fetch_page on second URL

Iteration 4
  fetch_page on third URL

Iteration 5
  llm response:  {"tool_name":"save_answer","tool_arguments":{"question":"...","answer":"... [1] ... [2] ...","sources":[{"title":"...","url":"..."}, ...]}}
  tool call:     save_answer(...)
  tool result:   {"saved":true,"path":"answers/what-s-new-in-claude-4-7.md", ...}

Iteration 6
  llm response:  {"answer":"Claude 4.7 introduced ... — full answer at answers/what-s-new-in-claude-4-7.md"}
  final answer rendered, loop exits.
```

5–6 iterations. ~25 seconds (mostly throttle + network). One markdown artifact on disk. One JSON log for the transcript.

---

## 6. Design decisions explained

### Why DuckDuckGo and not Google/Bing/Tavily/Serper?

Zero API key. The demo runs anywhere with just `pip install` and a free Gemini key. Trade-off: results are less curated than paid APIs. Acceptable for a teaching demo.

### Why trafilatura over BeautifulSoup or readability-lxml?

Trafilatura is purpose-built for **main-content extraction** (article body, no nav/footer/ads). BeautifulSoup gives you the whole DOM and makes you write the heuristics yourself. Readability is older, less maintained. Trafilatura wins on signal-per-line-of-code.

### Why truncate fetched pages to 5000 chars?

After 3 fetches, conversation history can hit 30k+ tokens. The Gemini Flash Lite context is generous but not infinite, and *cost* scales with input tokens. 5000 chars is enough to capture the lede + first few sections of most articles — which is where the answer usually is.

### Why JSON contract instead of native tool calling?

Two reasons:

1. **Portability.** Native tool calling (Gemini's `function_calling`, OpenAI's `tools`, Anthropic's `tools`) all have different schemas. JSON-in-text works with every model.
2. **Pedagogy.** The course teaches the *primitive* — a loop that prompts an LLM to emit JSON. Once you understand the primitive, native tool calling is just a wire-format optimization.

The trade-off is that you need a robust parser. Hence `parser.py`.

### Why throttle in the client and not in the loop?

The throttle is a property of the LLM provider's rate limit, not of the agent's logic. Putting it in `llm.py` means swapping providers also swaps the throttle policy. The loop stays provider-agnostic.

### Why no tests, just `_smoke_test.py`?

This is a teaching artifact, not a library. The smoke test stubs the LLM with canned JSON responses and replaces `web_search` with a fixture, so you can run the full loop end-to-end **without an API key and without network**. That's enough confidence for a CLI demo. Real test suite would be overengineering.

### Why store the answer as markdown on disk?

- Markdown is reviewable by a human at a glance.
- Citations as `[1]`, `[2]` with a `## Sources` block matches how Perplexity itself renders.
- Refusing overwrite (and appending `-2`, `-3`) means you can re-run on the same question and compare results.

---

## 7. Extension hooks

Things you could add without ripping the loop apart:

| Idea | Where it lives |
|------|----------------|
| Extra tool (e.g., `arxiv_search`) | Add a function to `tools.py`, register in `TOOLS`, mention in `system_prompt.md`. |
| Different LLM (Claude / OpenAI) | Replace `LLMClient.generate` body in `llm.py`. |
| Streaming UI | Replace `ReasoningChainUI` with a live-updating `rich.live.Live`. |
| Persistent multi-turn (chat) | Move `messages` out of `run_agent` into a session object, replay across CLI calls. |
| Web UI | Wrap `run_agent` in FastAPI, push events via SSE, render panels in the browser. |
| Eval harness | Feed in a list of (question, expected-fact) pairs; check that `answers/<slug>.md` contains the fact. |

---

## 8. What this teaches (the meta point)

An agent is not a framework. An agent is **a loop, a prompt, and a few tools**. The loop is dumb. The prompt is strict. The tools are pure. The intelligence lives inside the LLM, scaffolded by all three.

Once you've built one of these by hand — without LangChain, without LlamaIndex, without the AI SDK — every other "agentic framework" you encounter is just sugar on top of this same shape. You'll know exactly which problem each abstraction is solving, and you'll know when not to reach for it.

That's the point of the S3 assignment. This walkthrough is the proof.

---

## Appendix — file-by-file reading order

If you've never seen this codebase before, read in this order:

1. `system_prompt.md` — what the agent is told to do.
2. `tools.py` — what the agent can do.
3. `mini_perplexity.py` → `run_agent` — how the loop holds it together.
4. `parser.py` — why the loop survives drift.
5. `ui.py` — how you watch it think.
6. `llm.py` — the LLM swap point.
7. `_smoke_test.py` — how to run it without burning quota.
