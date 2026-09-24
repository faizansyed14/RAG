# Models

`.env.dev` (or `.env.prod`) is the **only** place these are set.
`backend/app/core/config.py` has no Python-side default for any of the five
model fields below -- the app raises a `pydantic.ValidationError` and
refuses to start if one is missing, on purpose, so a model choice can never
be silently wrong. To change a model, edit `.env.dev` and restart the
backend container; nothing in the Python code needs to change.

## The five settings

| Env var | Current value | Drives |
|---|---|---|
| `RAG_INDEX_MODEL` | `openai/gpt-4o-mini` | Tree building: node summaries + document description, at ingestion time |
| `RAG_CHAT_MODEL` | `openai/gpt-5-mini` | The tree-search agent that answers a chat query |
| `MODEL_VISION` | `qwen/qwen3-vl-235b-a22b-instruct` | Diagram OCR/caption/description at ingestion; citation verification at query time |
| `MODEL_EMBEDDING` | `openai/text-embedding-3-small` | Diagram vectors (Qdrant) |
| `MODEL_EMBEDDING_DIMENSION` | `1536` | Must match `MODEL_EMBEDDING`'s real output size -- Qdrant's collection is created with this dimension |

Index runs `openai/gpt-4o-mini`; chat runs `openai/gpt-5-mini`. Vision is its
own model (`qwen/qwen3-vl-235b-a22b-instruct`), used only for diagram
OCR/caption/description at ingestion and citation verification at query time.
**`MODEL_EMBEDDING` can't be either of those** -- it's a chat-completion
model, not an embedding model, and OpenRouter's `/embeddings` endpoint will
reject it. It has to stay a real embedding model; `text-embedding-3-small`
is the only one currently wired up (`app/retrieval/embeddings.py`).

Every one of these routes through OpenRouter, using your `OPENROUTER_API_KEY`.
`RAG_INDEX_MODEL`/`RAG_CHAT_MODEL` get an extra `openrouter/` prefix applied
internally (`app/rag_service.py`) so the vendored tree engine's litellm
calls route through OpenRouter instead of straight to the provider named in
the slug -- `.env.dev` keeps the bare slug either way.

## Exact call sites (grep-verified, not assumed)

```
RAG_INDEX_MODEL / RAG_CHAT_MODEL -> app/rag_service.py (client construction only)
MODEL_VISION                     -> app/ingestion/diagram_pipeline.py, app/retrieval/vision_verify.py
MODEL_EMBEDDING                  -> app/ingestion/diagram_pipeline.py, app/retrieval/embeddings.py, app/retrieval/vector_store.py
```

`RAG_INDEX_MODEL`/`RAG_CHAT_MODEL` are only ever read in one place
(`rag_service.get_client()`) -- from there on, every actual LLM call goes
through the **vendored tree engine's own code** (`app/rag_core/`), not
anything this app wrote. See `docs/ARCHITECTURE.md` for what's vendored
vs. custom.

## How many LLM calls, per operation

These come straight from the vendored engine's source
(`app/rag_core/utils.py::summarize_tree`, `local_chat.py`), not a guess:

**Ingesting one document (RAG_INDEX_MODEL):**
- Flash mode calls `_index_flash()` with `optimize="full"`
  (`app/rag_core/local_api.py`), which runs an **expand pass** before
  summarization: any node whose page span exceeds `TRIGGER_PAGES = 5`
  (`rag_core/tree_optimize.py`) gets one model call proposing subsection
  splits (`propose_children` / `EXPAND_PROMPT`). Most short/flat documents
  (our CSV/XLSX/EML/TXT/JSON/XER renders, and short DOCX/PDF files) never
  trigger this -- every node stays under 5 pages. Long, unstructured
  documents can.
- One call per tree node for its summary -- *except* a leaf node whose raw
  text is under ~200 GPT-4o tokens, which reuses that text as its summary
  with **no model call** (`SUMMARY_RAW_TEXT_TOKENS` in `rag_core/utils.py`).
  Node summary calls run concurrently, up to 64 at a time
  (`SUMMARY_CONCURRENCY`).
- Plus exactly **one** more call for the document's one-sentence description
  (`generate_doc_description`).
- So: **(expand-pass calls, usually 0) + (tree nodes above the small-leaf
  threshold) + 1.** A short document (a few sections) might be 1-6 calls
  total; a long, deeply-sectioned one could be dozens.
- **Failure mode, not just a cost variable:** if flash mode finds no layout
  structure at all, it falls back to one node per page
  (`_page_nodes()`, `flash/api.py`). If that fallback produces **more than
  `FLAT_TREE_MAX_NODES = 10`** nodes, `page_index_flash()` returns early
  -- skipping optimize *and* summarize entirely -- and `local_api.py`
  raises, so the document **fails to ingest** rather than costing more.
  This only bites a long, genuinely unstructured document (no headings
  detected); confirmed by reading `flash/api.py` and
  `flash_rejection_reason()` directly, not inferred from behavior.

**Ingesting one scanned/image-only page (MODEL_VISION + MODEL_EMBEDDING):**
- **One** vision call per page (OCR text + caption + description + figure
  id + callouts, all in one call -- `diagram_pipeline.py`), run in
  parallel across pages, four at a time
  (`_MAX_CONCURRENT_VISION_CALLS = 4`).
- **One** embedding call per page.
- The resulting OCR text is then synthesized into a PDF and goes through
  the same tree-indexing pass above (so add that document's node-count
  calls on top).

**One chat query (RAG_CHAT_MODEL):**
- The tree-search agent runs 1 to 10 turns before answering (`max_turns`
  defaults to 10 in the vendored engine) -- each turn is roughly one model
  call, and the agent stops as soon as it has enough to answer, so a
  simple question is often 1-3 calls, not 10. There's no way to pin this
  to an exact number ahead of time; it's genuinely agentic.
- If the query looks visual ("diagram", "drawing", "show me", ...): +1
  embedding call (the query) + up to 5 vision calls in parallel (one per
  Qdrant candidate, verifying each against its real page image before it's
  allowed to become a citation -- `vision_verify.py`).

Nothing here is metered or capped beyond `max_turns` -- if you need a hard
ceiling on spend, that's a gap to close before any real usage, not
something the current code enforces.
