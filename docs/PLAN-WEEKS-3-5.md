# Weeks 3–5 plan: quality and speed (2026-10-31)

This is the build plan for [ROADMAP.md](ROADMAP.md) §3 (quality and speed upgrades) and Track B's knowledge-gap feature. It's based on current research into each library (sources at the end), not on the roadmap's first assumptions. Two of those assumptions changed:

- **The small rerankers won't be faster on CPU.** mxbai-rerank-base-v2 and Qwen3-Reranker-0.6B are 0.5–0.6B-parameter models; scoring 40 pairs on CPU is an estimated 5–20 s, against ~0.5 s for the NVIDIA reranker we use. The speed win is reranking **once** (we currently score up to 6 queries × 40 = 240 pairs). The CPU models are only an option for a future GPU worker.
- **granite-embedding-small-r2's headline gain is on long documents** (LongEmbed 61.9 vs 32.1). Our chunks are ≤ 2,600 characters (~512 tokens), where it ties bge-small on MTEB retrieval (53.9 vs 53.9). It's still worth testing (tables 75.5 vs 69.9), but as an A/B test with a gate, not as a sure win.

## How every change is judged

- **Model:** answers on **Claude Haiku 5.5** (`claude-haiku-5-5`) through the Anthropic API, so caching and citations are available. Judged by **Nemotron 3 Ultra** on the free NVIDIA tier (a different model family, as the eval requires).
- **Baseline first** (step 0), then one change at a time, each compared with `eval.harness compare` (paired bootstrap per category). A change ships when no category gets worse and its own metric improves.
- **Smoke set (15) on every change; full set (150) before each merge.** Full runs cost about $0.20–0.40 each on Haiku at $0.10 / $0.50 per million input / output tokens, so the $200 credit covers this plan's eval runs, Contextual Retrieval indexing and the LangExtract spike with a wide margin.
- **Per-answer cost** (input, output, cache read and write) is recorded in every eval row and in `messages.usage`.

### Targets for this phase

| Metric | Now | Target |
|---|---|---|
| recall@5, all 135 answerable | 0.937 | ≥ 0.96 |
| recall@5, cross-org vocabulary | 0.828 | ≥ 0.92 |
| recall@5, multi-hop | 0.844 | ≥ 0.92 |
| MRR, superseded | 0.792 | ≥ 0.90 |
| Citation recall | 0.34 (smoke, Nemotron) | ≥ 0.85 |
| Citation precision / faithfulness | 1.00 / 0.93 | keep ≥ 0.90 / ≥ 0.93 |
| Abstention on unanswerable | 14/15 (smoke) | 15/15 |
| Quick-mode latency | ~8 s on Haiku via gateway | p50 < 5 s |
| Table recall on parsed PDFs/XLSX | not measured | ≥ 0.95 |

## Step 0 — Foundations (days 1–2)

1. **Native Haiku path.**
   - `ANTHROPIC_API_KEY` set, and org model `claude-haiku-5-5`, so `_claude_loop` runs on the Anthropic SDK rather than the gateway.
   - Fixes needed for Haiku 5.5:
     - `request_extras()` sends `fallbacks: "default"`, which does nothing on Haiku. A list would return 400, so send it only to models that support it.
     - Thinking is on by default and billed as output; keep adaptive thinking for answers.
     - API version 2026-10-21 rejects sampling parameters, so no `temperature` anywhere on the Claude path.
2. **Usage and cost.**
   - Record `cache_creation_input_tokens` alongside `cache_read` in `_claude_usage`.
   - Add cost per answer to the eval's `AnswerResult` and summaries.
3. **Baselines on Haiku.** Smoke, then all 150 questions, saved as `answers-haiku-baseline.json`.
4. **Judge agreement (needs you, about 20 min).** `eval.harness label` on the Haiku baseline; κ ≥ 0.7 before we trust small judge-based differences.
5. **Eval databases per index configuration.** Embedding model, contextual-retrieval prompt version and BM25 on/off become part of `corpus_hash`, so variants that change the index don't overwrite each other.
6. **CI.** A GitHub Actions workflow running ruff, the backend tests (twice: with and without pg_textsearch, see step 2), `tsc` and `next build`. The eval smoke set runs only when the API keys are present as secrets.

## Step 1 — Latency bundle (week 3)

Each item is an org setting (or a harness variant), so it can be switched off and A/B-tested.

1. **Prompt caching in the agent loop** (`_claude_loop`).
   - An explicit `cache_control` on the last system block caches tools and system together. Top-level automatic caching (`cache_control={"type": "ephemeral"}`) caches the growing tool loop. Both use the 5-minute TTL; Haiku 5.5 caches prompts from 512 tokens.
   - Known misses: the forced-answer turn (`tool_choice: none`) misses the messages cache for that one turn. Haiku 5.5 doesn't support mid-conversation system messages (only Opus/Sonnet 5.5 and up do), so that turn keeps `tool_choice: none`.
   - Test: on the second turn, `cache_read_input_tokens > 0`.
   - The gateway path can't cache. That's a documented limitation.
   - **Done (2026-10-31).** Live: a second question read 2,877 tokens from cache; answers cost $0.0005–0.0006 (from ~$0.0013) and took 2.5–3.7 s. `PROMPT_CACHING=false` turns it off for comparison.
2. **Rerank once.** ~~Rerank the candidate pool against the main question only.~~ **Dropped after measuring:** the per-query rerank calls already run in parallel, so one search takes 1.1–2.0 s whether it has 1 or 4 queries (NVIDIA reranker, 40 passages). Reranking once would save ~0.3–0.5 s and lose the best-of-queries score. The rerank call itself (1–2 s per search) is the cost to attack, e.g. in the fast path.
3. **Concurrent tool calls.** When the model asks for several tool calls in one turn, run them concurrently, each with its own short DB session (the pattern from audit P2). **Done:** at most 4 at once, results in call order, on both the Claude and NVIDIA paths.
4. **Fast path for simple lookups.**
   - When all of these hold:
     - quick mode, with no attachments and no web;
     - no references back to earlier turns;
     - 25 words or fewer, with no multi-hop cues (compare, each, all, how many, two named entities);
     - the first hybrid search is confident: top-1 rerank ≥ 0.7, and top-1 minus top-5 ≥ 0.2, or the top 3 hits all come from one document.
   - …then answer in one streamed call with the passages and no tools. Otherwise run the agent loop, seeded with the passages already found.
   - Calibrate the thresholds on the eval so the pass rate stays within the confidence interval.

**Gate:** quick p50 < 5 s and lower cost per answer, with no category worse.

**Found while measuring (2026-10-31):** our tool definitions set `eager_input_streaming`, which streams the model's raw tool input unvalidated. Sonnet 5.5 sent malformed JSON on 60 of 150 questions (Haiku on none). Our inputs are a few short queries, so eager streaming bought nothing; it's off. (Strict tool use would also guarantee valid input, but the API rejects it together with Citations, which step 4 needs.)

## Step 2 — Retrieval quality (weeks 3–4)

### 2a. BM25 with pg_textsearch

- **pg_textsearch 1.5.1** (Timescale; PostgreSQL License; GA since 1.0; Postgres 17–18).
- **Install:**
  - Dev: build from source against `/opt/homebrew/opt/postgresql@17/bin/pg_config` (the `pg_config` on PATH is 16), add `shared_preload_libraries = 'pg_textsearch'`, and restart.
  - Prod: `FROM pgvector/pgvector:pg17` plus `apt-get install postgresql-17-pg-textsearch=1.5.1-*` (from the standard Postgres apt repository) and `shared_preload_libraries`. This moves Docker from pg16 to pg17: a documented dump/restore for existing deployments.
- **Migration** (guarded; a no-op where the extension is absent): create `ix_chunks_bm25` with `USING bm25 ((heading || ' ' || content)) WITH (text_config='english')`, with `max_parallel_maintenance_workers=0` during the build. Upstream issue #571: parallel builds can silently omit rows.
- **Query:**
  - `ORDER BY (heading || ' ' || content) <@> to_bm25query(:q, 'ix_chunks_bm25') LIMIT 40`. Scores are negative; the index name must be given explicitly because asyncpg prepares statements.
  - Keep the existing `tsv @@ query` match predicate: it excludes zero-score rows on the non-index plan and keeps the GIN pre-filter.
  - Tenant filtering (`collection_id IN …` and non-hidden documents) stays in SQL and is correct on both plan paths; the extension widens its batch until LIMIT is filled.
- **Fallback:** detect the extension and index once per process, and keep `ts_rank_cd` when absent. Tests run in both modes, plus a test that both modes return the same set of matches.
- **Heading weight:** BM25 has no field weights. Test repeating the heading in the indexed expression as a variant.
- **Known limitation:** IDF is computed over the whole index, across orgs. It only affects ordering, never visibility, which is the same exposure as today.

**Built and measured (2026-10-31); on by default where the index exists.** `app/retrieval/bm25.py`, migration 0011 (a no-op without the extension), `python -m app.retrieval.bm25` to create the index later, `postgres/Dockerfile` (pgvector:pg17 + `postgresql-17-pg-textsearch=1.5.1-*`, confirmed in the PGDG apt index), and CI on both a plain pg17 and that image. `KEYWORD_RANKING=ts_rank` forces the old ranking.

- **Changed from the plan above:** the `tsv @@` match is *not* kept. With it, Postgres finds every match through the GIN index and sorts them all, never using the BM25 index (checked with EXPLAIN), which scales exactly like `ts_rank_cd`. The query now filters on `score < 0` instead, which keeps the BM25 index scan and still drops non-matching chunks. With a very selective collection filter the planner may rightly filter first and score the few rows left.
- **Results, 135 answerable questions, Postgres 17:**

| | recall@5 | recall@10 | MRR |
|---|---|---|---|
| Reranked, ts_rank_cd | 0.939 | 0.970 | 0.942 |
| Reranked, BM25 | 0.936 | 0.970 | 0.948 |
| No reranker, ts_rank_cd | 0.839 | 0.926 | 0.841 |
| No reranker, BM25 | 0.855 | 0.937 | 0.847 |

- None of the differences is significant (reranked MRR +0.006, p 0.13; recall@5 −0.003). Reranked, cross-org vocabulary (r@5 0.844 → 0.861) and tables (MRR 0.967 → 1.0) gain and cross-org r@5 loses one question (0.875 → 0.812). Without the reranker BM25 helps cross-org (0.44 → 0.63) and multi-hop and hurts alias and table questions.
- **Why it's on anyway:** quality is equal within noise here, and keyword search now reads its top results from the index instead of scoring every match, the scaling problem the audit flagged. Re-check at scale and in the four-way comparison below.

### 2b. Contextual Retrieval

Following Anthropic's published recipe and cookbook prompt.

- **Stored separately.** A new `chunks.context` column, plus `context_version` and the document hash it was made from.
- **Where it's used:**
  - prepended to the chunk for **embedding** (`embedding_text`);
  - added to the `tsv` column (weight C);
  - added to the BM25 expression.
  - It's never shown or cited; answers and citations use `content` only.
- **Generation:**
  - Haiku 5.5 with thinking off. The document goes in a cached first block, the chunk in the second; one call per chunk.
  - The first call per document runs alone so the cache is written once, then the rest fan out.
  - Documents under 512 tokens skip the call: title and heading already situate them.
  - Documents over 100K tokens would bill at 5×, so they get a window (the section, its neighbours and an outline) under ~60K tokens.
- **Backfill** through the Message Batches API: 50% off, stacking with caching. Adjacent chunks of a document go together in the batch, and the shared prefix is primed first.
- **New documents** are searchable at once without context; a background job adds context and re-embeds (two-phase indexing). Changed documents regenerate context only for changed chunks, or for all of a document's chunks after a large change.
- **Cost:** about $0.25–0.50 per million document tokens. The eval corpus is under $0.10.

**Built and measured (2026-10-31); off by default.** `CONTEXTUAL_RETRIEVAL=true` turns it on (`app/ingestion/contextual.py`, migration 0010, job `contextualize`). Single-chunk documents are skipped rather than documents under 512 tokens: this corpus's median document is ~350 tokens, so that rule would have skipped nearly everything. On the 135 answerable questions, contexts for all 522 chunks cost $0.055 (Haiku 5.5, 60% of input read from cache):

| | recall@5 | recall@10 | MRR |
|---|---|---|---|
| Baseline | 0.937 | 0.967 | 0.942 |
| + contexts | 0.943 | 0.975 | 0.943 |
| Paired bootstrap, recall@5 | +0.006 [0.000, 0.016], p(not better) 0.14 | | |

Cross-org vocabulary recall@5 rose 0.828 → 0.883 and table MRR 0.967 → 1.0; nothing lost recall; multi-hop MRR dipped 0.98 → 0.96 (one question). Three questions changed, all up on recall. The interval doesn't exclude zero, so by this plan's rule it isn't switched on. Re-measure on the long PDFs and spreadsheets of step 3, which is the case the method is built for.

### 2c. Embedding A/B: granite-embedding-small-english-r2

- Supported natively by fastembed 0.9.1, and the same 384 dimensions, so no schema change; it's a global setting plus a re-embed command.
- Facts: CLS pooling, no prefixes, 8,192-token maximum, Apache-2.0.
- Its output isn't normalized. That's fine for cosine; normalize anyway for safety.
- Run in its own eval database; ship only if recall@5's confidence interval excludes zero.
- Qwen3-Embedding-0.6B (1,024 dimensions) is kept for a multilingual requirement only.

**Gate for step 2:** the retrieval targets above, measured as the four-way comparison {baseline, +BM25, +BM25 + context, + granite}, each change kept only if it earns its place.

## Step 3 — Parsing with Docling (weeks 4–5)

- **Version and models:**
  - docling **2.145.0, pinned exactly** (it releases every few days); MIT code; works on Python 3.14 (torch 2.15 CPU wheels).
  - Models: the default layout model "heron" (Apache-2.0) and TableFormer (CDLA-Permissive-2.0).
  - Prefetched for Docker with `docling-tools models download layout tableformer rapidocr` (~0.55 GB), loaded from `DOCLING_ARTIFACTS_PATH`, with `HF_HUB_OFFLINE=1`.
- **Formats.**
  - **Through Docling:** PDF, DOCX (today's parser moves every table to the end of the document), XLSX, PPTX, EML/MSG and images.
  - **Native as today:** MD, TXT, CSV (row-per-record passages already retrieve well) and JSON.
  - HTML stays native, with Docling behind a flag.
  - Email attachments are listed but not converted by Docling, so the email handler extracts them and parses each one.
- **Pipeline options:**
  - `PdfPipelineOptions(do_ocr=True, do_table_structure=True, TableFormerMode.ACCURATE, document_timeout=120)`.
  - OCR is **pinned to RapidOCR on onnxruntime** in dev and prod alike. The automatic choice would be ocrmac on Mac and RapidOCR on Linux, so dev and prod would differ.
  - Limits: `max_num_pages=500`, `max_file_size=50 MB`.
  - Install with `docling[rapidocr]`; plain `pip install docling` doesn't bring onnxruntime.
- **Isolation (audit S6):**
  - Parsing runs in a pool of **spawn-context** worker processes. Fork hangs with torch, per Docling issue #1277.
  - Each worker keeps a warm converter (`initialize_pipeline` at start), with `OMP_NUM_THREADS` set before importing torch.
  - Workers are recycled after ~50 documents or past an RSS ceiling. Docling has had memory-growth issues: #2209, #2788.
  - The parent hard-kills a worker on wall-clock timeout, and memory is capped by the container (`--memory`), since `RLIMIT_AS` fails with torch.
  - DOCX/XLSX archive limits are built into Docling; PPTX has none, so we keep our own zip-bomb guard.
- **Tables:**
  - Taken from `doc.tables` (`export_to_dataframe`, `caption_text`, page from `prov`) and indexed as their own passages.
  - Each passage is the header row plus as many rows as fit in 2,600 characters, with the header repeated, under the nearest section heading.
  - Prose goes through `iterate_items()`, skipping tables so nothing is indexed twice, into the existing heading chunker. We don't adopt Docling's `HybridChunker` wholesale, so the retrieval baseline stays comparable.
  - Chunks gain page numbers and character offsets, which also lays the groundwork for LangExtract.
- **Eval additions:**
  - About 15 new questions over a scanned PDF, an XLSX, a PPTX and an EML (generated deterministically from the existing corpus facts).
  - A **table recall** check: tables found and cells matched against labels for those files.
  - These start as known failures today and must pass after.
- **Gate:**
  - No change on the 150 existing questions; the new format questions pass; table recall ≥ 0.95.
  - Throughput and RSS per worker are measured and written down.
- **Later, for hard scans:** a GPU path through Docling's `mineru2_pro` VLM preset or an olmOCR (Apache-2.0) vLLM worker.
  - MinerU's licence asks for attribution in online services and a commercial licence at scale, so use olmOCR or Docling's own models first.

## Step 4 — Citations and abstention (week 5)

1. **Claude search-result citations** (the main fix for citation recall).
   - On the Claude path, tool results become `search_result` blocks with `citations: {enabled: true}`.
   - **One content block per sentence, list item or table row**, with the table header repeated, because Claude cites whole blocks.
   - Each result's `source` is `kb://{document_id}#{chunk_id}`; citations are mapped back to our `[n]` refs by `source` (not by index), and the server writes the `[n]` markers at the end of each cited sentence or line. That keeps `MessageCitation`, the UI and the eval's statement splitter unchanged.
   - Structured citations (chunk, block range, `cited_text`) are saved with the message, so the document sheet can highlight the exact sentence.
   - Constraints:
     - Citations are all-or-nothing per request.
     - Search results can't be mixed with other blocks in a tool result, so notes like the "capped" message move inside a result.
     - No citations together with structured outputs.
     - Citations never go back into user turns: the SDK's API version rejects that.
   - The NVIDIA path gets a prompt-only change ("every list item and table row carries its own [n]").
2. **Unknown `[n]` refs** are flagged instead of silently dropped (audit C3), and counted in the eval.
3. **HHEM-2.1-Open** (Vectara; Apache-2.0; ~0.1B; flan-t5-base), as a grounding check after the answer.
   - It runs in the worker after streaming ends, so it adds no time to the answer.
   - It scores each sentence against the passages it cites, flags unsupported sentences, and adds a citation to uncited claims from the best-scoring block above a threshold.
   - Pinned model revision (it uses remote code).
   - **Nothing is shown in the UI until its agreement with the judge's labels is measured** (AUC / κ on the 150).
   - Granite Guardian (8B) needs a GPU and is deferred.
4. **Sufficient-context check** (arXiv 2411.06037).
   - When the agent stops researching, one Haiku call (effort low, structured output — no citations on this call) returns `{sufficient, missing, confidence}`.
   - If the context is insufficient: one more retrieval round in quick mode, otherwise an explicit "may be incomplete" note or an abstention.
   - The rater is validated against hand labels before it can abstain on its own.

**Gate:**
- Citation recall ≥ 0.85, with precision ≥ 0.90 and faithfulness ≥ 0.93.
- 15/15 abstention on unanswerable questions, and no rise in false abstentions on answerable ones.

## Step 5 — Knowledge gaps and doc requests (Track B, week 5)

- **Inputs:** questions per org that ended in an abstention, "may be incomplete", a thumbs-down or no citations, clustered by embedding (we already log questions in `search_events`).
- **Insights → Gaps:**
  - clusters with counts and example questions;
  - **"answered in org B, missing in org A"** for people in both orgs;
  - one click to **request a document** from an admin, using the same request/decide pattern as access requests;
  - closing the loop: fulfilled request → new source → verified answer.

## Step 6 — LangExtract spike (1–2 days, end of week 5)

- LangExtract 1.7.1 (Apache-2.0) has no built-in Claude provider, so we write the ~60-line provider plugin:
  - subclass `BaseLanguageModel`, registered for `claude-*` through the `langextract.providers` entry point;
  - Haiku with effort low, and no `temperature` (the API rejects it).
- Run it on the 60-record vendor register and measure item recall and the share of fields with character offsets.
- Records design for weeks 5–8: each field is its own extraction tied to a record key, so every column has offsets.
  - Docling tables become rows deterministically (no LLM), and LangExtract handles prose.
  - Offsets are stored against the Docling text hash.
- The full structured-records feature and `query_records` stay in weeks 5–8.

## Order and dependencies

```
0 foundations ──► 1 latency ──────────────► 4 citations ──► 5 gaps
              └─► 2a BM25 ─► 2b context ─► 2c embeddings
                          3 Docling (independent; needs chunk offsets from step 3 for step 6)
                                                            └─► 6 LangExtract spike
```

Steps 1, 2 and 3 touch different code (agent loop; retrieval SQL and index; parsers), so they can run in parallel branches. Each lands as its own commit with its eval comparison in [EVALUATION.md](EVALUATION.md).

## Sources

- **pg_textsearch:** github.com/timescale/pg_textsearch, release 1.5.1 and issues #571, #584, #587; apt.postgresql.org (bookworm-pgdg).
- **Contextual Retrieval:** anthropic.com/news/contextual-retrieval; the `claude-cookbooks` contextual-embeddings guide.
- **Claude docs** (platform.claude.com): pricing, prompt caching, batch processing, search results, citations, the API 2026-10-21 migration guide.
- **Models:** huggingface.co/ibm-granite/granite-embedding-small-english-r2; mixedbread-ai/mxbai-rerank-base-v2; Qwen/Qwen3-Reranker-0.6B; vectara/hallucination_evaluation_model; ibm-granite/granite-guardian-4.1-8b.
- **Docling:** pypi.org/project/docling (2.145.0); the docling-project/docling docs (advanced options, supported formats, installation) and issues #1277, #2209, #2788; arXiv 2501.17887 and 2408.09869.
- **Sufficient context:** arXiv 2411.06037.
- **LangExtract:** github.com/google/langextract (providers README, COMMUNITY_PROVIDERS.md); pypi.org/project/langextract (1.7.1).
- **MinerU and olmOCR:** github.com/opendatalab/MinerU; github.com/allenai/olmocr.
- **Routing:** anthropic.com/engineering/building-effective-agents; arXiv 2403.14403 (Adaptive-RAG), 2401.15884 (CRAG).
