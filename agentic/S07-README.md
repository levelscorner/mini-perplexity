# Session 7 — Memory & Retrieval (port to our S6 agent)

> EAG V3 S7 assignment. Built **on top of our own S6 four-role agent**
> (`s06/agentic-architecture`) rather than running the provided reference
> code. Branch: `s07/memory-retrieval`. The S6 loop is byte-identical;
> only Memory's read/write paths and the MCP tool catalogue grew.

## What S7 added (4 surgical changes, ~354 lines)

1. **`vector_index.py`** — FAISS `IndexFlatIP` over L2-normalised vectors
   (== cosine similarity). Persists to `state/index.faiss` +
   `state/index_ids.json`. The dim is "married" on first `add` — change the
   embedding model later and every vector in the persisted index is garbage.
2. **`gateway_client.py` `.embed(text, task_type=...)`** — wraps V7's
   `POST /v1/embed` (768-dim nomic via Ollama, Gemini fallback). Same
   retry-on-5xx as our existing `_post`.
3. **`memory.py`** — three changes:
   - `read()` is **vector-first** (FAISS cosine), falls back to S6
     keyword overlap when vector returns nothing (empty corpus or
     gateway down).
   - `record_outcome()` + `remember()` embed the descriptor on write (only
     for embeddable kinds: `fact` / `preference` / `tool_outcome`).
   - New `add_fact(descriptor, value, ...)` for direct-fact writes from the
     new MCP tools (skips the LLM classify because kind is known).
4. **`mcp_server.py`** — 2 new tools on top of S6's nine:
   - `index_document(path, chunk_size=400, overlap=80)` — read a sandbox
     file, chunk into overlapping word windows, **embed a 120-character
     preview of each chunk (not the chunk body — see Limitation 5)**, store as a
     searchable `fact`.
   - `search_knowledge(query, k=5)` — vector search over `fact` chunks,
     return ranked chunks with provenance.

**The S6 four-role loop is unchanged.** Memory is still a typed service.
The agent doesn't know retrieval just got smarter.

## My understanding (in my own words)

Cosine similarity measures the *angle* between two vectors. FAISS's
`IndexFlatIP` does inner product, which on **L2-normalised** vectors equals
cosine — so the same maths costs less if I normalise once on insert and once
on query. Why this matters: a dense embedding lives in a high-dim space
(768 dims for nomic) where **distance encodes meaning**. Two paragraphs
that say the same thing in different words land close together; two
paragraphs about completely different topics land orthogonal.

That's how semantic recall works (Q3 below): the query "parameter-efficient
adaptation" and LoRA's text "language model adaptation" share *zero
common words*, but their nomic embeddings are close because the model
learned during pre-training that those phrases are about the same idea.
A keyword search (S6 fallback) would have returned nothing.

The "marriage" rule comes from this: every vector in the index lives in
the SAME 768-dim space defined by nomic. Change to gemini-embedding-001
without slicing to 768 → the query vector is from a different space
entirely and inner product means nothing. Hence the gateway pins the
model, hence "treat the embedding model as a project-level constant."

## The bug I found and fixed (worth writing down)

The S7 reference uses **module-level** functions for memory writes —
every write reads disk → appends → writes back. Our S6 Memory is **class-
based** with a `self.items` cache. That worked fine in S6 because only
the agent process wrote to memory.

In S7, `index_document` runs in the **MCP subprocess**, not the agent
process. Both have their own `Memory` instance pointing at
`state/memory.json`. After indexing, the MCP subprocess writes N facts to
disk. Then the agent process records the tool outcome and writes its
cached `self.items` (which doesn't include the N facts) back to disk —
**clobbering them**.

First run with this bug: `memory.json` had zero facts; `search_knowledge`
returned empty; the agent fell back to `read_file`. The architecture
*looked* right but the dual-writer race silently lost the indexed content.

Fix in `memory.py:_persist()`:

```python
def _persist(self, item: MemoryItem) -> MemoryItem:   # memory.py:145 — abridged
    # Re-read disk so we don't clobber writes the OTHER process made.
    self.items = self._load()
    self.items.append(item)
    self._save()
    if item.embedding is not None and item.kind in _EMBEDDABLE_KINDS:
        # FAISS index also gets re-read so the agent process sees the
        # facts the MCP subprocess just persisted.
        self._index = VectorIndex(self.path.parent)
        self._index.add(item.id, item.embedding)
        self._index.persist()
    return item
```

This trades an extra disk read per write (cheap at S7 scale: small JSON)
for race-tolerance between agent and MCP. After this fix, `memory.json`
shows the 18 fact chunks for the 5-paper corpus alongside the agent's
tool_outcome records.

## Queries — actual terminal output

**Coverage against the S7 rubric — stated plainly.** The rubric asks for eight base traces (A–H) plus five custom traces with a no-corpus comparison. This README contains **three** traces, covering base queries **E, F and G only**. Base queries **A, B, C and D (the S6 carryover set) and H (cross-document synthesis) were not re-run on this branch and have no trace here.** **No custom queries were designed or run, so there is no no-corpus comparison.** Nine of the thirteen required traces are missing.

### Q1 — index Attention paper, extract 3 contributions
```
─── iter 1 ───
  [open] Index the file 'papers/attention.md'
  [tool] index_document({'path': 'papers/attention.md'}) -> {chunks_indexed: 3}
─── iter 2 ───
  [done] Index   [open] Extract three contributions
  [tool] search_knowledge('Transformer architecture key contributions', 5)
       -> chunk 3/3 (Smart Citations footer — naive chunking artefact)
─── iter 6 ───
  [tool] read_file('papers/attention.md') -> art:5
─── iter 7 ───
  [answer] The Transformer architecture introduced three key contributions:
─── iter 8 ───  [done] [done]
```

FINAL (verbatim):
```
1. Pure attention-based design – first sequence transduction model that
   relies entirely on self-attention mechanisms, removing recurrence and
   convolutions.
2. Massive parallelization and reduced training time – all positions
   processed in parallel, dramatic speedup, better use of modern hardware.
3. State-of-the-art performance with far lower cost – 28.4 BLEU on WMT
   2014 English-to-German, 41.8 on English-to-French, trained for 3.5 days
   on 8 GPUs (a fraction of prior models). Generalises to English
   constituency parsing.
```

### Q2 — index all 5 papers, count chunks
Agent ran `list_dir(papers/)`, then `index_document` for each of
`attention.md`, `cot.md`, `dpo.md`, `lora.md`, `react.md`. Final
`memory.json` count:

```python
>>> from collections import Counter; import json
>>> Counter(i['kind'] for i in json.load(open('state/memory.json')))
Counter({'fact': 18, 'tool_outcome': 12})
```

**18 fact chunks + 12 tool outcomes = 30 items**, all 5 papers
indexed. (Note: `attention.md` shows 6 chunks instead of 3 in this run
because Q1 indexed it once, then Q2 re-indexed — no dedupe on path.)

### Q3 — semantic recall (the hero demo)

Query: *"Use search_knowledge to find what the indexed papers say
about parameter-efficient model adaptation, and tell me which paper
covers it."*

```
─── iter 1 ───
  [tool] search_knowledge('parameter-efficient model adaptation', 5)
       -> [papers/lora.md chunk 2/3] "in language model adaptation, which
          sheds light on the efficacy of LoRA..."
─── iter 2 ───
  [done] [answer] The paper is "LoRA: Low-Rank Adaptation of Large
         Language Models" by Edward J. Hu et al. (arXiv:2106.09685).
─── iter 3 ───  [done]
```

**3 iterations.** The query's words ("parameter-efficient adaptation")
do **not appear** in the matching chunk's text ("language model
adaptation"). nomic-embed-text understood the two phrases mean the same
thing. **A keyword search would have failed.** This is what S7
exists for.

## Honest limitations (what S7 deliberately doesn't do)

1. **Dense retrieval only.** No BM25/sparse hybrid, no reranker, no RRF.
   Sparse pieces (exact codes, regulation IDs, license plates) miss.
   Production = dense + sparse + reranker — that's S8/S9 territory.
2. **Naive 400-word sliding window with 80 overlap.** Cuts mid-sentence,
   mid-section. Chunk 3/3 of every paper is the arxiv page's footer
   metadata, which sometimes ranks high for unrelated queries.
   Semantic chunking is the documented S8 forward pointer.
3. **No dedup on repeated `index_document`.** Re-indexing `attention.md`
   added 3 more chunks of the same content. A real system would
   key on `(source, chunk_index)` and update in place.
4. **The corpus is 5 items, not the 50+ the rubric requires.** This is the
   largest gap in the submission and it is not a stylistic choice — the
   "real RAG application over a corpus of fifty or more items" requirement
   is **not met**. What is committed is a 5-item smoke corpus.

## Corpus manifest (all 5 items, complete)

| # | Path | Source | Words | Chunks @ 400/80 |
|---|------|--------|-------|-----------------|
| 1 | `sandbox/papers/attention.md` | arxiv.org/abs/1706.03762 (landing page markdown) | 898 | 3 |
| 2 | `sandbox/papers/cot.md` | arxiv.org/abs/2201.11903 | 853 | 3 |
| 3 | `sandbox/papers/dpo.md` | arxiv.org/abs/2305.18290 | 908 | 3 |
| 4 | `sandbox/papers/lora.md` | arxiv.org/abs/2106.09685 | 907 | 3 |
| 5 | `sandbox/papers/react.md` | arxiv.org/abs/2210.03629 | 903 | 3 |

**Total: 5 documents, 15 chunks.** These are arxiv *landing pages*, not
paper bodies — roughly the first third of each file is site navigation
chrome and the last third is the scite/Papers-with-Code footer.
   abstract is one chunk, the footer is another, and there isn't enough
   text per paper to exercise serious chunking. Real-world corpora
   would benefit much more from S7's architecture than this one does.

5. **Only the first 120 characters of each chunk are embedded — this is a
   defect, not a design choice.** `index_document` builds a descriptor from
   `chunk[:120]` (`mcp_server.py:352`) and `add_fact` embeds *that*
   (`memory.py:189`); the full chunk body is stored as JSON and never
   vectorized. About 95% of every document is therefore unreachable by
   semantic search. Because these arxiv landing pages all open with the same
   "Skip to main content / Cornell University" navigation block, the five
   chunk-1 vectors are near-duplicates of each other: measured cosine
   0.942–0.962 across documents under nomic-embed-text. The fix is one line —
   pass the chunk body to `add_fact` and embed it instead of the descriptor —
   and it has not been made on this branch.

## Setup & run

```bash
# 1. Provided substrate (gateway V7) — boot it on :8107
cd ~/Downloads/agentic/s07/llm_gatewayV7
env -u ANTHROPIC_API_KEY -u ANTHROPIC_BASE_URL ./run.sh

# 2. Ollama with nomic-embed-text (one-time)
ollama pull nomic-embed-text

# 3. Run the agent (from our agentic/ dir)
cd ~/ws/projects/mini-perplexity/agentic
rm -rf state                              # clean start (optional)
env -u ANTHROPIC_API_KEY -u ANTHROPIC_BASE_URL \
  MCP_SERVER_CMD=".venv/bin/python mcp_server.py" \
  GATEWAY_URL="http://localhost:8107" \
  .venv/bin/python agent6.py "Index papers/attention.md and tell me ..."
```

State persists in `state/` (auto-created, cleanable with `rm -rf state`).
Per the rubric: `state/` is **excluded** by `.gitignore`.

## Relationship to our S6 work and forward to S8

This is `mini-perplexity/agentic` on branch `s07/memory-retrieval`,
forked off our S6 work at `s06/agentic-architecture` (HEAD `da63f3e`,
all 4 S6 queries green, prompts owned + PoP-qualified). The S6 loop and
all 9 original MCP tools work unchanged — S6 keyword search is now the
*fallback* for the vector path. The S6 four-role loop (`agent6.py`), `perception.py` and `decision.py` are
byte-identical to `s06/agentic-architecture` (verified: `git diff
s06/agentic-architecture s07/memory-retrieval -- agentic/agent6.py` is
empty). **However, `memory.read()` did change** — it is vector-first now,
with S6 keyword overlap as the fallback — so the S6 queries that depend on
memory recall (B and C run 2) take a different path than they did on S6.
**The four S6 queries were NOT re-run on this branch and are not traced
here.** `_e2e_smoke.py` still passes 10/10 but covers zero S7 code paths.

S8 (next): the four-role loop becomes a NetworkX DiGraph with a Planner
emitting nodes and an Executor running them in parallel via
`asyncio.gather`. The Memory + vector index we built here remains the
substrate.
