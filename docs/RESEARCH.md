# Strata — Research findings (2026-10-31)

What the research found about improving Strata, in three parts:
- retrieval and answer quality;
- tooling (parsing, evaluation, observability, safety, Postgres search);
- the product landscape (commercial and open-source peers, and features that compound with multi-org).

The plan built on this is in [ROADMAP.md](ROADMAP.md); the code audit is in [AUDIT.md](AUDIT.md).

**How to read this.** Repo stars, licences and latest releases were pulled from the GitHub and Hugging Face APIs on 2026-10-31. Benchmark numbers are as published and mostly self-reported by the authors; where a party with a stake in the result ran the benchmark, it's marked **(vendor)**. Claims taken from secondary sources are marked **(2°)**. Nothing here has yet been validated on Strata's own evaluation set; every adoption is gated on a before/after run.

---

## Part 1 — Retrieval and answer quality

Context from our evaluation: answers are already strong (42–44/45). The weak spots are:
- (a) exhaustive list and count questions over many records;
- (b) multi-hop questions that need resolving a role to a person;
- (c) scanned PDFs and tables;
- (d) latency of 8–30 s.

### 1.1 Chunking and context

| Technique | Evidence | Verdict for Strata |
|---|---|---|
| **Contextual Retrieval** (Anthropic): an LLM writes a 50–100-token context for each passage, prepended before both embedding and keyword indexing | Top-20 retrieval failures 5.7% → 3.7% with contextual embeddings (−35%), → 2.9% adding contextual BM25 (−49%), → 1.9% with reranking (−67%). About $1.02 per million document tokens with prompt caching. [anthropic.com/news/contextual-retrieval](https://www.anthropic.com/news/contextual-retrieval) | **Adopt.** One cheap model call per passage in the ingestion worker; the context also helps role→person questions. Effort S–M. |
| Late chunking (Jina): embed the whole document, pool per passage | Gains on long-document BEIR sets, none on short text. [arXiv 2409.04701](https://arxiv.org/abs/2409.04701); reference code (Apache-2.0) idle since Dec 2024 | Weaker, cheaper alternative to Contextual Retrieval. Skip. |
| Propositions / semantic chunking | Dense X: +10.1 Recall@20 for unsupervised retrievers but only +2.2–2.7 for supervised ones, and it multiplies index size. [arXiv 2312.06648](https://arxiv.org/abs/2312.06648). Vectara found semantic chunking "not justified" on realistic corpora. [arXiv 2410.13070](https://arxiv.org/abs/2410.13070) | Skip. Heading-aware passages plus neighbour expansion already cover parent-document retrieval. |

### 1.2 Retrieval models

**Embedding models.** All of these load in [fastembed](https://github.com/qdrant/fastembed/releases) (Apache-2.0, v0.9.1, 2026-10-24).

| Model | Licence | Size / dims / context | Evidence | Fit |
|---|---|---|---|---|
| **ibm-granite/granite-embedding-small-english-r2** | Apache-2.0 | 47M, **384-d**, 8,192 tokens | Against our current model on the same card: MTRAG 48.9 vs 38.2, LongEmbed 61.9 vs 32.1, Table IR 75.5 vs 69.9; MTEB-v2 retrieval tied at 53.9. [model card](https://huggingface.co/ibm-granite/granite-embedding-small-english-r2) | **Adopt.** Same dimensions, so no schema change, only a re-embed. Effort S. |
| Qwen3-Embedding-0.6B | Apache-2.0 | 1,024-d (Matryoshka down to 32), 32k context | MTEB Eng v2 retrieval 61.83; multilingual retrieval 64.64. [model card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) | Best if multilingual becomes a requirement. About 18× the parameters, so slower CPU ingestion. |
| EmbeddingGemma-300m | Gemma licence (gated) | 768-d, 2,048 context | MTEB Eng v2 69.67. [model card](https://huggingface.co/google/embeddinggemma-300m) | Licence and context length make it a weaker fit. |

**Late interaction and sparse retrieval.** LightOn LateOn (149M, Apache-2.0) reports BEIR 57.22 nDCG@10 (self-reported, [card](https://huggingface.co/lightonai/LateOn)). [PyLate](https://github.com/lightonai/pylate) (MIT, v1.6.0) is the maintained ColBERT library. [RAGatouille](https://github.com/AnswerDotAI/RAGatouille) has had no release since Feb 2025. SPLADE is possible natively in Postgres via pgvector `sparsevec`.
**Verdict: low priority.** Strata already runs a cross-encoder reranker, so these add little.

**BM25 inside Postgres.** `ts_rank` has no IDF and no BM25 length saturation, and it scores every matching row before the LIMIT. ParadeDB's benchmark shows ~29× throughput and ~47× lower P99 than GIN + `ts_rank` with rotating terms, and parity on a single rare term ((vendor), [paradedb.com](https://paradedb.com/blog/benchmarker-iteration)). This matches audit finding P1.

| Extension | Licence | Status | Notes |
|---|---|---|---|
| **[pg_textsearch](https://github.com/timescale/pg_textsearch)** (Tiger Data) | PostgreSQL | v1.5.1 (2026-10-02), ~4.0k stars | **Recommended.** Requires PG 17+ and `shared_preload_libraries`. At 1.0 it had no phrase queries and OR-only matching. Vendor benchmark on 138M documents: p50 2.3× faster than ParadeDB ([blog](https://tigerdata.com/blog/pg-textsearch-bm25-full-text-search-postgres), [release news](https://www.postgresql.org/about/news/pg_textsearch-v10-3264)). |
| [ParadeDB pg_search](https://github.com/paradedb/paradedb) | **AGPL-3.0** | v0.26.4 (2026-10-29), 9.5k stars | More features (phrase queries, highlighting, filters); licence is the cost. |
| [VectorChord-bm25](https://github.com/tensorchord/VectorChord-bm25) | AGPL / ELv2 | v0.3.0 (Dec 2025) | Licence and maintenance risk. |

### 1.3 Query side

- **Multi-query + RRF, HyDE, step-back:** the evidence is mostly practitioner reports. HyDE helps fact lookup but can mislead retrieval when it invents a wrong premise; decomposition helps multi-hop ([write-up](https://dev.to/gabrielanhaia/hyde-multi-query-decomposition-which-query-rewrite-actually-moves-recall-1m08)). Strata already fuses with RRF, so the cheap move is 2–3 parallel sub-queries per hop.
- **IRCoT** (retrieve again after each reasoning step): up to +21 retrieval recall and +15 QA F1 on multi-hop benchmarks ([arXiv 2212.10509](https://arxiv.org/abs/2212.10509)). Strata's agent loop approximates it. Making "next-hop query" an explicit step in the prompt is S effort.
- **Search-R1 / RL-trained search agents:** +24–41% relative over RAG baselines for a 7B model ([arXiv 2503.09516](https://arxiv.org/abs/2503.09516)). It needs training our own model, which doesn't fit API models. **Not practical.**

### 1.4 Exhaustive list and count questions (our biggest gap)

Top-k retrieval can't answer "find all" questions. Benchmarks confirm what our 60-record scale test showed:
- **AGGBench / DFA (Feb 2026):** RAG and text-to-SQL both fail on completeness; a disambiguate → filter → aggregate agent improves coverage. [arXiv 2602.01355](https://arxiv.org/abs/2602.01355)
- **AI21:** RAG scored 5% and 11% on aggregate questions over a 22-document corpus; their fix was schema extraction at ingestion plus SQL. [talk](https://ai.engineer/talks/rag-evaluation-is-broken-here-s-why-and-how-to-fix-it)
- **DocETL:** 1.34–4.6× better output quality than baselines. [arXiv 2410.12189](https://arxiv.org/abs/2410.12189), [repo](https://github.com/ucbepic/docetl) (MIT)
- **LOTUS:** semantic operators (sem_filter, sem_agg) over whole tables. [arXiv 2407.11418](https://arxiv.org/abs/2407.11418), [repo](https://github.com/lotus-data/lotus) (Apache-2.0)

**Design these point to:**
1. Per-collection record schemas (vendor, contract, person/role, policy, project) as typed models.
2. Extraction at ingestion with **[Google LangExtract](https://github.com/google/langextract)** (Apache-2.0, v1.7.1, ~39k stars). Every field is grounded to character offsets, so every row is citable. [Instructor](https://github.com/567-labs/instructor) (MIT) is the alternative.
3. Rows in typed Postgres tables with `org_id` and access filtering.
4. A `query_records` agent tool with exact filters, COUNT and DISTINCT.
5. A `scan` tool for questions no schema covers: every matching document (no top-k), checked per document in parallel.

Our LightRAG graph failed because free-form extraction is lossy. Schema-constrained, offset-grounded extraction validated field by field is the difference.

### 1.5 Multi-hop (resolving a role to a person)

- **HippoRAG 2:** passage + phrase graph with synonym edges and Personalized PageRank. MuSiQue F1 48.6 vs 45.7 for strong dense retrieval; recall@5 74.7. [arXiv 2502.14802](https://arxiv.org/abs/2502.14802), [repo](https://github.com/OSU-NLP-Group/HippoRAG) (MIT; only release Feb 2025).
- **GraphRAG-Bench (ICLR 2026):** graphs help complex reasoning but often lose to plain RAG on simple facts, at ~2.3× latency. [arXiv 2506.05690](https://arxiv.org/abs/2506.05690)
- **LazyGraphRAG:** indexing at ~0.1% of full GraphRAG's cost; only the noun-phrase indexer is open source. [Microsoft Research](https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/)
- **Graphiti:** facts with validity dates ("who held role X *when*"). Apache-2.0, v0.31.2, 32k stars. [repo](https://github.com/getzep/graphiti)

**Fit:** reuse the §1.4 extraction to fill people, alias and dated role-assertion tables. Alias matching uses pg_trgm plus LLM canonicalisation at ingestion. Add a `resolve_role(role, date)` mode to the explorer. Effort M.

### 1.6 Scanned PDFs and tables

olmOCR-Bench overall scores, from the [olmOCR README](https://github.com/allenai/olmocr):

| System | Score | Licence |
|---|---|---|
| Chandra 0.1 | 83.1 | OpenRAIL, with commercial limits |
| olmOCR 2 | 82.4 | Apache-2.0 |
| PaddleOCR-VL 0.9B | 80.0 | Apache-2.0 |
| Marker | 76.1 | Code Apache-2.0; weights restricted (see Part 2) |
| DeepSeek-OCR | 75.7 | MIT |
| MinerU 2.5 | 75.2 | Custom licence (see Part 2) |
| Mistral OCR | 72.0 | API |

**Fit:** Docling as the orchestrator. Route pages with no or poor text layer to PaddleOCR-VL or olmOCR 2. Emit tables both as passages and as structured rows (§1.4). See Part 2 for licences.

### 1.7 Faithfulness and citations

- **Anthropic Citations** with `search_result` blocks (Claude path): up to 15% better recall accuracy than citing through the prompt. [announcement](https://claude.com/resources/articles/introducing-citations-api)
- **Sentence-level grounding check**, ALCE-style ([arXiv 2305.14627](https://arxiv.org/abs/2305.14627)):
  - MiniCheck-Flan-T5-L (770M, MIT): roughly GPT-4-level grounding at a fraction of the cost. [arXiv 2404.10774](https://arxiv.org/abs/2404.10774)
  - Granite Guardian 3.3 (Apache-2.0): about 76.5 on [LLM-AggreFact](https://llm-aggrefact.github.io), against about 77.5 for Bespoke-MiniCheck-7B, whose weights are non-commercial per its card (verify).
  - Vectara HHEM-2.1-open (Apache-2.0).
- **Abstention:** a "sufficient context" autorater plus confidence raised accuracy among answered questions by 2–10%. [arXiv 2411.06037](https://arxiv.org/abs/2411.06037)
- **CRAG:** PopQA 50.5 → 54.9 on LLaMA2-7B. [arXiv 2401.15884](https://arxiv.org/abs/2401.15884)
- **Self-RAG:** needs a fine-tuned model, so skip.

### 1.8 Latency

- **Prompt caching** for the system prompt, tools and conversation prefix: cache reads cost 0.1×, and long prompts get up to 85% lower latency. [coverage](https://www.maginative.com/article/anthropics-new-prompt-caching-feature-can-cut-costs-up-to-90/)
- **Parallel tool calls**, and a **fast path**: simple lookups get one retrieve-and-answer pass with no agent loop.
- **Smaller rerankers** on 30–50 candidates (BEIR from [Jina's comparison](https://jina.ai/models/jina-reranker-v3)):

| Reranker | Size | Licence | BEIR |
|---|---|---|---|
| mxbai-rerank-base-v2 | 0.5B | Apache-2.0 | 58.40 |
| bge-reranker-v2-m3 int8 (in fastembed) | — | — | 56.51 |
| Qwen3-Reranker-0.6B | 0.6B | Apache-2.0 | 56.28 |
| jina-reranker-v3 | — | **CC-BY-NC**, not commercially usable | 61.94 |

- **Semantic cache** ([GPTCache](https://github.com/zilliztech/GPTCache), [RedisVL](https://github.com/redis/redis-vl-python)): only with keys that include org, permissions and corpus version, and invalidation on ingest. Do it last.

### 1.9 Ranked shortlist

| # | Change | Expected impact | Effort |
|---|---|---|---|
| 1 | Schema extraction into typed tables + `query_records` | (a) exact lists and counts; (b) moderate | L |
| 2 | Latency bundle: caching, parallel tools, fast path, smaller reranker | (d) ~2–3× faster | S–M |
| 3 | Contextual Retrieval + real BM25 (pg_textsearch) | Broad recall gain; helps (b) | S–M |
| 4 | People, aliases and dated roles + explicit hop-by-hop sub-queries | (b) large | M |
| 5 | Docling with OCR fallback; tables to rows | (c) large; feeds #1 | M |
| 6 | Exhaustive `scan` tool | (a) the long tail | M |
| 7 | granite-embedding-small-r2 (same 384-d) | Modest recall gain; long docs and tables | S |
| 8 | Citations API + sentence grounding check + sufficient-context abstention | Fewer unsupported claims; calibrated "don't know" | S–M |

The evaluation also needs to grow: **45 questions can't detect 2–5-point gains**, so grow to ~150 labelled by category.

---

## Part 2 — Tooling

### 2.1 Document parsing and OCR

| Tool | Licence | Maintenance | Quality evidence | Compute | xlsx / pptx / email |
|---|---|---|---|---|---|
| **[Docling](https://github.com/docling-project/docling)** | MIT (LF AI & Data) | 69.9k stars, v2.145.0 (2026-10-29) | [BizDocBench](https://github.com/DocSlicer/BizDocBench): 2nd overall (0.704), best open-source table score (0.747). olmOCR-Bench only 50.3, or 64.0 on born-digital PDFs ((vendor) Datalab, [summary](https://builderai.tools/blog/pdf-parsing-for-rag-mineru-docling-marker-compared)) | CPU; optional VLM pipeline | **Yes**: PDF, DOCX, PPTX, XLSX, HTML, EML, MSG, images ([PyPI](https://pypi.org/project/docling/)) |
| [Unstructured](https://github.com/Unstructured-IO/unstructured) | Apache-2.0 | 15.6k, 0.27.36 | BizDocBench 0.580; tables 0.481 | CPU | Yes, broadest; use ≥0.18.18 (MSG path-traversal [CVE](https://releasealert.dev/github/Unstructured-IO/unstructured/cves)) |
| [MinerU](https://github.com/opendatalab/MinerU) | Apache-2.0 plus commercial terms above $20M monthly revenue or 100M MAU, and **mandatory attribution for online services** ([LICENSE](https://raw.githubusercontent.com/opendatalab/MinerU/master/LICENSE.md)) | 82.8k, 4.1.3 | Best pipeline table score on OmniDocBench (TEDS 77.4 vs Marker 67.6, Docling 61.3; [PaddleOCR-VL paper](https://arxiv.org/pdf/2510.14528)); weak on business docs (BizDocBench 0.411) | ~4 GB VRAM (pipeline), ~8 GB (VLM) | PDF and images |
| [Marker](https://github.com/datalab-to/marker) / [Surya](https://github.com/datalab-to/surya) | Code Apache-2.0; **weights restricted for companies over $5M revenue or funding, or competing with Datalab** ([MODEL_LICENSE](https://github.com/datalab-to/marker/blob/master/MODEL_LICENSE)) | 40.7k / 21.6k, v2.1.0 | olmOCR-Bench 76.0 (vendor) | GPU for the good mode | Some |
| [olmOCR 2](https://github.com/allenai/olmocr) | Apache-2.0 | 19.8k, v0.4.27 (2026-03); no pushes since March | olmOCR-Bench 82.4 ([arXiv 2510.19817](https://arxiv.org/abs/2510.19817)) | 7B VLM, ≥12 GB GPU | OCR only |
| [pymupdf4llm](https://github.com/pymupdf/pymupdf4llm) | **AGPL-3.0** | Active | BizDocBench 0.452; tables 0.355 | Fast CPU | No |
| LlamaParse (hosted) | Proprietary | Active | 84.9% on ParseBench, which LlamaIndex built ([arXiv 2604.08538](https://arxiv.org/abs/2604.08538)) | — | Yes; about $12.5 per 1k pages at the Agentic tier ([pricing](https://developers.llamaindex.ai/llamaparse/general/pricing/)) |
| Azure DI / Google Document AI | Proprietary | Active | Mature OCR | — | About $10 per 1k pages (2°) ([Azure](https://azure.microsoft.com/de-de/pricing/details/document-intelligence/)) |

Some parsers silently drop whole tables ([arXiv 2603.18652](https://arxiv.org/html/2603.18652v1)), so measure **table recall**.

**Recommendation:**
- **Default: Docling.** It covers every format Strata lacks and keeps table structure.
- **Heavy path for scanned or table-heavy PDFs:** MinerU on a GPU worker via the existing job queue (comply with its attribution clause). Without a GPU, Azure DI Layout.
- **Avoid** Marker's weights and pymupdf4llm unless licensed.

### 2.2 RAG evaluation

| Tool | Licence / status | Notes |
|---|---|---|
| **[DeepEval](https://github.com/confident-ai/deepeval)** | Apache-2.0, 19.2k stars, v4.2.4 (2026-09), very active | Runs under pytest. Faithfulness, contextual precision/recall, and new `CitationFaithfulnessMetric`, `ToolPermissionMetric` and `AgentLoopDetectionMetric`. Reports token usage per metric ([changelog](https://deepeval.com/changelog/changelog-2026)). |
| [RAGAS](https://github.com/vibrantlabsai/ragas) | Apache-2.0, 16.2k; last release Jan 2026, last push Feb 2026; repo moved organisations | Reference metric definitions; appears to be stalling. |
| [promptfoo](https://github.com/promptfoo/promptfoo) | MIT, 26.7k, active. **OpenAI acquired it in March 2026**; MIT licence kept for now ([coverage](https://securityboulevard.com/2026/03/openai-acquires-security-startup-promptfoo-to-fortify-ai-agents)) | Best for **red-teaming** (prompt injection, RAG poisoning) and YAML regression suites. |
| [Inspect](https://github.com/UKGovernmentBEIS/inspect_ai) (UK AISI) | MIT, 3.1k, active | Strong for agent evals with sandboxes; a different way of working. |
| [TruLens](https://github.com/truera/trulens) | MIT, 3.6k | "RAG triad" metrics; Snowflake-oriented. |
| [ARES](https://github.com/stanford-futuredata/ARES) | Last push March 2025 | Effectively unmaintained. |

**Can LLM judges be trusted?**
- Raw agreement overstates chance-corrected agreement by 33–41 points across all 21 judges tested, and high test–retest consistency hid position bias. [arXiv 2606.19544](https://arxiv.org/abs/2606.19544)
- Local judges were 92–97% self-consistent yet agreed poorly with humans. [arXiv 2609.13824](https://arxiv.org/abs/2609.13824)
- An enterprise RAG judge agreed with the human majority 88–91% of the time. [arXiv 2602.20379](https://arxiv.org/html/2602.20379v1)
- Explicit criteria and sampled scoring improve agreement with humans. [arXiv 2506.13639](https://arxiv.org/html/2506.13639v1)

**In practice:**
- Hand-label ~50 items and report Cohen's κ.
- Use a judge from a different model family than the generator.
- Pin the judge model and the prompt version.
- **In CI:** a 15-question smoke set per PR; the full set nightly; cached verdicts; release gating on a paired bootstrap.

### 2.3 Observability

| Tool | Licence / status | Self-host footprint | Notes |
|---|---|---|---|
| **[Langfuse](https://github.com/langfuse/langfuse)** | MIT core, 36.8k, v4.72.0 (2026-10-29). ClickHouse acquired it (Jan 2026); MIT and self-hosting unchanged ([FAQ](https://langfuse.com/resources/engineering/clarifications)) | Postgres + ClickHouse + Redis + S3 ([docs](https://langfuse.com/self-hosting)) | Cost dashboards, per-user and per-org metadata, datasets and scores; OTel-native. |
| [Arize Phoenix](https://github.com/Arize-ai/phoenix) | **ELv2**: self-hosting OK, offering it as a managed service not ([licence](https://arize.com/docs/phoenix/self-hosting/license)) | One container + Postgres | Light; built-in evals. |
| [OpenLLMetry](https://github.com/traceloop/openllmetry) | Apache-2.0, 7.5k. Traceloop joined ServiceNow in March 2026 ([blog](https://traceloop.com/blog/traceloop-is-joining-servicenow)) | Instrumentation only | Auto-instruments Anthropic and OpenAI SDKs. |
| OTel GenAI semantic conventions | Still "Development" status ([spec](https://opentelemetry.io/docs/specs/semconv/gen-ai/)) | — | Defines `invoke_agent` and `execute_tool` spans; expect churn. |
| Helicone | Acquired by Mintlify; **maintenance mode** | — | Avoid. |
| [Laminar](https://github.com/lmnr-ai/lmnr) | Apache-2.0, 3.5k | Postgres + ClickHouse | Smaller ecosystem. |

**Pick:** OpenTelemetry instrumentation exported to self-hosted Langfuse, with `org_id` on every trace, plus Prometheus metrics for queue depth and latency targets. Phoenix if the four-service footprint is too much.

### 2.4 Safety

**Prompt injection: what the research shows**
- Spotlighting (delimiting, datamarking, encoding) cut attack success from >50% to <2% on GPT models ([Hines et al.](https://arxiv.org/abs/2403.14720)), but results vary by model from <1% to >65% ([arXiv 2510.05244](https://arxiv.org/html/2510.05244v1)).
- **"The Attacker Moves Second":** adaptive attacks bypassed 12 defenses, including Spotlighting, at over 90% success. [arXiv 2510.09023](https://arxiv.org/abs/2510.09023)
- 2026 work favours **deterministic enforcement outside the model**:
  - Progent cut AgentDojo attack success from 25.8% to 4.2% and held under adaptive attack, but utility dropped from ~45% to 26%. [arXiv 2606.26479](https://arxiv.org/html/2606.26479v1)
  - [CaMeL](https://arxiv.org/abs/2503.18813) requires rewriting the agent.
- Design principle: avoid the "lethal trifecta" of private data + untrusted content + an exfiltration channel ([Willison](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/), [design patterns](https://arxiv.org/abs/2506.08837)). **Strata's web-search tool is a possible exfiltration channel**, and Markdown images are another (audit S3).

**Classifiers and filters**

| Tool | Status | Use |
|---|---|---|
| Llama Prompt Guard 2 (86M / 22M) | Llama 4 Community Licence; 512-token window ([card](https://huggingface.co/meta-llama/Llama-Prompt-Guard-2-86M), [LlamaFirewall](https://arxiv.org/abs/2505.03574)) | Only 7–37% detection on indirect, agentic injection in a third-party test ([promptfoo](https://promptfoo.dev/lm-security-db/vuln/llm-guardrail-benchmark-lies-10b59e81/)). Use as an **ingestion flag**, not a boundary. |
| [NeMo Guardrails](https://github.com/NVIDIA-NeMo/Guardrails) | 7.4k, v0.24.1 | Heavy (its own DSL); not needed with a custom agent loop. |
| [LLM Guard](https://github.com/protectai/llm-guard) | **Archived July 2026** | Avoid. |
| **[Presidio](https://github.com/data-privacy-stack/presidio)** | MIT, 11.6k, 2.2.366 (2026-10-27); moved to an independent org ([site](https://microsoft.github.io/presidio)) | PII tagging at ingestion and scrubbing of trace exports. |
| Nemotron Safety Guard 8B v3 | Through the NVIDIA API Strata already uses ([card](https://build.nvidia.com/nvidia/llama-3_1-nemotron-safety-guard-8b-v3/modelcard)) | Moderation of inputs and outputs. |

**Pick, as layers:**
1. A tool-call policy layer (search queries checked for retrieved private text; link and image allowlists; per-org tool permissions).
2. Datamarking.
3. Prompt Guard 2 flags at ingestion.
4. Presidio.
5. A promptfoo red-team suite tracking attack success rate.

### 2.5 Postgres search and infrastructure

- **BM25:** see §1.2. When does BM25 beat `ts_rank`?
  - Always on ranking quality (measure recall@5 and MRR on keyword-heavy questions).
  - On latency, once an org reaches hundreds of thousands of passages or common terms are queried. **At small per-org corpora, `ts_rank` latency is fine.**
- **[pgvector](https://github.com/pgvector/pgvector)** 0.8.7 (0.9 adds async I/O for HNSW, [CHANGELOG](https://github.com/pgvector/pgvector/blob/master/CHANGELOG.md)):
  - **halfvec** halves storage and index size with minimal recall loss.
  - Binary quantization plus halfvec rerank gives ~32× less memory; only needed when the index no longer fits in RAM ([AWS guide](https://aws.amazon.com/blogs/database/scale-pgvector-with-binary-quantization-on-amazon-aurora-postgresql/)).
  - Iterative scans already handle filtering by org.
- **[pgvectorscale](https://github.com/timescale/pgvectorscale)** (PostgreSQL licence, 0.9.1): StreamingDiskANN wins when the index exceeds RAM or under heavy filtering ([Timescale](https://timescale.com/blog/how-we-made-postgresql-as-fast-as-pinecone-for-vector-data), [independent test](https://thebuild.com/blog/on-pgvectorscale-and-hybrid-search-without-an-elasticsearch-sidecar/)).
- **[VectorChord](https://github.com/supervc-stack/VectorChord)** (AGPLv3/ELv2): strong compression, but maintenance and licence risk.
- **Semantic caching:** see §1.8. In multi-tenant RAG a false cache hit leaks data, so key every entry by org, permissions, corpus version and model ([Redis guide](https://redis.io/docs/latest/develop/use-cases/semantic-cache/)).

### 2.6 Recommended tool stack

| Category | Pick | Why |
|---|---|---|
| Parsing | Docling (+ MinerU on a GPU worker) | Permissive, CPU, every missing format, good tables |
| Evaluation | Our harness + DeepEval metrics + promptfoo red-team | Active, permissive, CI-friendly, has citation faithfulness |
| Observability | OpenTelemetry → self-hosted Langfuse | MIT, cost per org, agent and tool spans |
| Safety | Tool policy + egress allowlist, datamarking, Prompt Guard 2, Presidio, Nemotron Safety Guard | Deterministic controls are the only ones that hold under adaptive attack |
| Postgres | pgvector halfvec HNSW + pg_textsearch BM25 | Permissive, minimal new infrastructure |

---

## Part 3 — Product landscape and compounding features

### 3.1 Commercial products (2025–2026)

- **Glean:**
  - Monthly releases. 85+ agent actions; admin Insights and chat ([Feb 2026](https://www.glean.com/product-drop/february-2026)).
  - Sharing agents by identity-provider groups; Slack question detection ([Mar 2026](https://docs.glean.com/release-notes/releases/2026-03-25-march-release)).
  - A remote **MCP server** that enforces each user's permissions ([docs](https://docs.glean.com/administration/platform/mcp/security)).
  - Triggers API (webhooks on content events); access requests for protected chats ([Aug 2026](https://docs.glean.com/release-notes/releases/2026-08-18-august-release)).
  - Curated **Answers** ([docs](https://docs.glean.com/user-guide/knowledge/answers/what-are-answers-and-how-do-they-work)), a Slack **daily digest** ([docs](https://docs.glean.com/administration/platform/embedded-integrations/slackbot/use-glean-in-slack/daily-digest)), expert detection ([VentureBeat](https://venturebeat.com/ai/glean-launches-new-generative-ai-capabilities-to-enhance-search-and-discovery-across-organizations)).
- **Onyx** v5.0.0 (2026-10-23):
  - MCP server with OAuth; plugins page for MCP, OpenAPI actions and apps, with per-tool approvals.
  - LLM gateway; usage and cost budgets; Teams/OneDrive with permission sync; voice mode ([changelog](https://docs.onyx.app/changelog)).
  - Slack bot with citations ([docs](https://docs.onyx.app/overview/onyx_anywhere/slack)).
- **Dust:** open and restricted "spaces" with separate data-access and agent-use controls ([docs](https://docs.dust.tt/docs/access-controls-and-permissions)); Slack bot.
- **Guru:** Knowledge Agents (Jan 2026) verify content against plain-language rules, route critical content to experts and hide unverified content from search ([blog](https://getguru.com/blog/knowledge-agents-improve-your-info), [blog](https://www.getguru.com/blog/introducing-the-agentic-knowledge-base)).
- **Microsoft 365 Copilot:**
  - Researcher agent; SharePoint knowledge agent ([Mar 2026](https://techcommunity.microsoft.com/blog/microsoft365copilotblog/whats-new-in-microsoft-365-copilot--march-2026/4506322)).
  - Notebook sharing; scheduled briefs "coming" (2°) ([report](https://windowsreport.com/?p=1512038)).
  - Purview audit of prompts and responses ([guide](https://www.aguidetocloud.com/blog/auditing-microsoft-365-copilot/)).
  - **Copilot doesn't search tenants where the user is a B2B guest** ([Microsoft Learn](https://learn.microsoft.com/en-us/training/modules/examine-data-security-microsoft-365-copilot/3-examine-copilot-protects-sensitive-business-data)). This is the clearest multi-org gap in the market.
- **Notion:** AI connectors, Research mode, model choice ([product](https://www.notion.com/product/enterprise-search)). Custom Agents (Feb 2026) run on schedule, database or Slack triggers ([coverage](https://alternativeto.net/news/2026/2/notion-3-3-launches-custom-agents-for-autonomous-team-automation)).
- **Perplexity Enterprise:** internal knowledge search, connectors, Spaces, audit logs (2°) ([overview](https://www.godofprompt.ai/blog/perplexity-enterprise-pro)).
- **Hebbia Matrix** (2°): documents as rows, questions as columns, every cell cited ([overview](https://www.datastudios.org/post/hebbia-ai-document-research-for-finance-matrix-workflows-and-pricing), [FactSet](https://techintelpro.com/news/ai/enterprise-ai/hebbia-partners-with-factset-to-enhance-ai-driven-financial-research)).
- **Credal:** permission mirroring, human approval for actions, a SIEM-exportable audit log of every tool call ([blog](https://credal.ai/blog/how-does-credal-create-secure-scalable-actions)).
- **Others:**
  - Sana (Workday): Self-Service Agent and Agent Harness ([newsroom](https://newsroom.workday.com/2026-03-17-Introducing-Sana-from-Workday-Superintelligence-for-Work-That-Finds-Answers,-Takes-Action,-and-Automates-Workflows)).
  - Coveo: Knowledge Hub with an Answer Manager ([docs](https://docs.coveo.com/en/p59f0295/)).
  - Elastic: Agent Builder GA ([press](https://www.businesswire.com/news/home/20260122544127/en/Elastic-Announces-General-Availability-of-Agent-Builder-with-Expanded-Capabilities)); still no chat sharing or knowledge base ([docs](https://www.elastic.co/docs/explore-analyze/ai-features/ai-chat-experiences/ai-agent-or-ai-assistant)).

**Evidence that the problem is real:**
- Atlassian: teams spend about 25% of their time searching ([report](https://unleash.ai/artificial-intelligence/news/atlassian-ai-could-help-workers-stop-wasting-2-4-billion-hours-searching-for-information-every-year)).
- Coveo 2025: 42% of employees fact-check AI answers ([SiliconANGLE](https://siliconangle.com/2025/07/01/coveo-report-finds-employee-interest-ai-high-employee-frustration/)).
- Slite 2026: 81% interrupt colleagues for help, and only 11% find what they need on the first try ([survey](https://slite.com/learn/enterprise-search-survey-findings)). Slite sells search; small samples.
- MCP moved to the Linux Foundation's Agentic AI Foundation in Dec 2025, with ~97M monthly SDK downloads (2°) ([CData](https://www.cdata.com/blog/2026-enterprise-mcp-adoption-roadmap)).

### 3.2 Open-source peers (GitHub API, 2026-10-31)

| Repo | Licence | Stars | Activity / standout |
|---|---|---|---|
| [onyx-dot-app/onyx](https://github.com/onyx-dot-app/onyx) | MIT core + `ee/` enterprise licence | 33.1k | Active; MCP server, Slack bot, permission sync |
| [infiniflow/ragflow](https://github.com/infiniflow/ragflow) | Apache-2.0 | 93.2k | v1.0.0-rc3; deep document parsing, agent memory ([changelog](https://ragflow.io/changelog)) |
| [langgenius/dify](https://github.com/langgenius/dify) | Modified Apache: **forbids running it multi-tenant without a commercial licence** | 160k | Workflow builder |
| [open-webui/open-webui](https://github.com/open-webui/open-webui) | Custom BSD + branding clause | 157k | Chat UI, knowledge, RBAC |
| [Mintplex-Labs/anything-llm](https://github.com/Mintplex-Labs/anything-llm) | MIT | 67.8k | Workspaces exposed as MCP; scheduled jobs single-user only (2°) ([review](https://baeseokjae.github.io/posts/anythingllm-review-2026/)) |
| [zylon-ai/private-gpt](https://github.com/zylon-ai/private-gpt) | Apache-2.0 | 57.6k | Private local RAG |
| [LibreChat-AI/LibreChat](https://github.com/LibreChat-AI/LibreChat) | MIT | 46.5k | Agents, MCP, roles and groups (2°) |
| [The-Vibe-Company/quivr](https://github.com/The-Vibe-Company/quivr) | MIT | 39.6k | **Pivoted** to monitoring content streams with alerts |
| [khoj-ai/khoj](https://github.com/khoj-ai/khoj) | AGPL-3.0 | 37.9k | Scheduled research automations by email ([docs](https://docs.khoj.dev/category/features/)) |
| [Cinnamon/kotaemon](https://github.com/Cinnamon/kotaemon) | Apache-2.0 | 25.8k | Last push Jul 2026; citations highlighted in a PDF viewer, low-relevance warnings |
| [SciPhi-AI/R2R](https://github.com/SciPhi-AI/R2R) | MIT | 8.1k | Stalled (last release Jun 2025) |
| [weaviate/Verba](https://github.com/weaviate/Verba) | BSD-3 | 7.7k | **Archived** |
| [truefoundry/cognita](https://github.com/truefoundry/cognita) | Apache-2.0 | 4.4k | **Archived** |
| [morphik-org/morphik-core](https://github.com/morphik-org/morphik-core) | BSL 1.1 | 3.7k | Active; multimodal |

**None is natively multi-org** with org-labelled citations across organizations.

### 3.3 Compounding features: candidates and ranking

| Rank | Feature | Who ships it | Why it compounds for Strata | Effort |
|---|---|---|---|---|
| 1 | **MCP server** | Glean, Onyx, Guru, AnythingLLM | One permission-aware endpoint across a user's orgs with org-labelled citations; wraps existing search, chat and keys | S |
| 2 | **Knowledge-gap detection + doc requests** | Guru, Coveo, Glean Insights | Unanswered or low-confidence clusters per org; unique cross-org view "answered in org B, missing in org A" | S–M |
| 3 | **Verified answers** | Glean Answers, Guru | Promote an answer with an owner and expiry; boosted in retrieval; reuses boost, hide and votes | S |
| 4 | **Per-org audit log + compliance export** | Credal, Perplexity, Purview | Each org sees every query touching its data, including from other orgs; needed before regulated clients join | M |
| 5 | **Slack / Teams bot** | Glean, Dust, Onyx | Slack is already a connector; channels map to org scopes; Slack Connect channels map to multi-org scopes | M |
| 6 | **Digests and watch alerts** | Glean, Copilot, Notion, Khoj, Quivr | Weekly change summary across orgs; reuses change detection and the job queue | M |
| 7 | **Answer permalinks** | Glean, Copilot Notebooks | Each viewer sees only the citations they can access; request-access flow | S |
| 8 | **Stale / contradictory content detection** | Guru | Conflicts within and across orgs using dates and records; feeds hide and verify | M |
| 9 | **Shared collections between orgs** | Slack Connect pattern ([docs](https://api.slack.com/connect)); Copilot can't | Partner-visible collections with expiry, audit trail and revocation; touches access control, so after #4 | L |
| 10 | **Matrix extraction** | Hebbia | The same questions across many documents and orgs as a grid of cited answers; builds on structured records | M–L |

**Deprioritised:**
- Expert finding: identities don't align across orgs.
- Actions with approvals: large and generic.
- Browser extension, voice, multilingual UI: little compounding.

---

## What we decided not to pursue (and why)

| Item | Reason |
|---|---|
| Search-R1 / RL-trained search agents | Needs training our own model; we use API models |
| Full GraphRAG | Loses to plain RAG on simple facts at ~2.3× latency (GraphRAG-Bench) |
| ColBERT / SPLADE | Marginal next to the existing cross-encoder |
| Semantic or proposition chunking | Not justified on realistic corpora (Vectara) |
| Semantic cache (now) | Only once traces show repeated queries, and keyed by org, permissions and corpus version |
| Dify as a base | Licence forbids running it multi-tenant without a commercial licence |
| Marker weights, pymupdf4llm | Licence restrictions for commercial or SaaS use |
| RAGAS, ARES | Stalling / unmaintained |
| Helicone, LLM Guard | Maintenance mode / archived |
