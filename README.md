# Mini Perplexity

A research agent that mimics the core loop of Perplexity AI. Single-agent loop, three custom tools, reasoning chain rendered to the terminal in real time and persisted to `logs/`.

> Ask a question → agent searches the web → reads the top 2–3 sources → synthesizes a cited answer → saves it to disk as markdown.

Originally built as the **S03 submission** for [EAG V3](https://github.com/levelscorner/levelscorner-eva3) (The School of AI). Step-by-step build walkthrough lives in [`docs/WALKTHROUGH.md`](docs/WALKTHROUGH.md).

---

## The three tools

| Tool | Purpose |
|------|---------|
| `web_search(query, n=5)` | DuckDuckGo search via the `duckduckgo-search` library. **No API key required.** Returns ranked `{rank, title, url, snippet}` results. |
| `fetch_page(url)` | Fetches the URL and extracts clean main-article text using `trafilatura`. Returns `{url, title, text, truncated, bytes}`, truncated to ~5000 chars to keep LLM context tight. |
| `save_answer(question, answer, sources)` | Persists the final markdown answer with numbered citations to `answers/<slug>.md`. Refuses to overwrite (appends suffix). |

The system prompt (`system_prompt.md`) enforces a `search → fetch ×2–3 → save → answer` flow. Max 8 iterations.

---

## Setup

```bash
git clone git@github.com:levelscorner/mini-perplexity.git
cd mini-perplexity

# Virtualenv (uv recommended)
uv venv
source .venv/bin/activate
uv pip install -e .
# or: pip install -e .

# API key
cp .env.example .env
$EDITOR .env   # fill in GEMINI_API_KEY
```

Free Gemini key: <https://aistudio.google.com/apikey>
Default model: `gemini-2.5-flash-lite`. Override via `GEMINI_MODEL`.

---

## Run

```bash
python mini_perplexity.py "What's new in Claude 4.7?"
```

Terminal renders the full reasoning chain:

- **You** — the question
- **Iteration N** — LLM thought · tool call (`web_search` / `fetch_page` / `save_answer`) · tool result
- **Final answer** — one-line summary + the path to the saved markdown

Every event also persists to `logs/run-<timestamp>.json` for the submission log paste. The final answer lives in `answers/<slug>.md`.

### Sandbox the answer output

```bash
ANSWERS_DIR=/tmp/perplexity-sandbox python mini_perplexity.py "Your question"
```

---

## Example questions

Each exercises a different agent behavior:

```bash
# Fresh news — exercises search + fetch + synthesis
python mini_perplexity.py "What happened in the latest Apple event?"

# Technical docs — exercises source selection
python mini_perplexity.py "How do I use Gemini's thinking mode in the API?"

# Controversial or multi-source — exercises contradiction handling
python mini_perplexity.py "Is the o1 model better than Claude for reasoning benchmarks?"
```

---

## How the demo maps to the S03 rubric

| S03 Requirement | Where it lives |
|-----------------|----------------|
| Agentic loop calling LLM multiple times | `mini_perplexity.py` → `run_agent()` |
| Each query carries all past interaction | `_render_conversation()` flattens the full `messages[]` history every iteration |
| ≥ 3 custom tool functions | `tools.py` — `web_search`, `fetch_page`, `save_answer` |
| Display the reasoning chain | `ui.py` — `ReasoningChainUI` panels per step |
| YouTube demo + LLM logs | Record terminal; paste `logs/run-*.json` into submission doc |

---

## Layout

```
mini-perplexity/
├── README.md            # this file
├── pyproject.toml       # package metadata + deps
├── .env.example         # copy to .env and fill GEMINI_API_KEY
├── system_prompt.md     # the agent's instructions (external for readability)
├── mini_perplexity.py   # entrypoint + agent loop
├── llm.py               # Gemini client with free-tier throttling
├── parser.py            # fence-stripping + regex-fallback JSON parser
├── tools.py             # web_search, fetch_page, save_answer
├── ui.py                # rich-based reasoning-chain renderer + log writer
├── answers/             # generated markdown answers (gitignored via .gitignore)
└── logs/                # run transcripts (gitignored)
```

`llm.py`, `parser.py`, and `ui.py` are deliberately self-contained — no shared package. Each module reads top-to-bottom on its own.

---

## Demo script

1. Wide terminal so `rich` panels render cleanly.
2. `python mini_perplexity.py "What's new in Claude 4.7?"`
3. Watch the agent: `web_search` → triage to 3 URLs → `fetch_page` ×3 → `save_answer` → final answer with path.
4. `cat answers/what-s-new-in-claude-4-7.md` — show the artifact.
5. Inspect `logs/run-<ts>.json` for the full reasoning chain.

---

## Build walkthrough

For a step-by-step explanation of how this agent was built — what each module does, why the loop is shaped this way, and the design decisions behind it — read [`docs/WALKTHROUGH.md`](docs/WALKTHROUGH.md).
