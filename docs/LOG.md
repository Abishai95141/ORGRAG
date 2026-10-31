# Decision and eval log

What was decided, why, and what was measured, newest first. Each entry links to the code and the result files; [EVALUATION.md](EVALUATION.md) has the method and the standing results, and [PLAN-WEEKS-3-5.md](PLAN-WEEKS-3-5.md) the plan these entries follow.

## 2026-10-31 — Weeks 3–5 start: Claude Haiku 5.5, latency, BM25, Contextual Retrieval

Plan steps covered: 0 (foundations), 1 (latency), 2a (BM25) and 2b (Contextual Retrieval). Steps 2c (embedding A/B), 3 (Docling), 4 (citations), 5 (knowledge gaps) and 6 (LangExtract) haven't started.

### Decisions

| # | Decision | Why | Evidence | To reverse |
|---|---|---|---|---|
| D1 | **Claude Haiku 5.5 (`claude-haiku-5-5`, Anthropic API) does every LLM job** that ran on NVIDIA or the Experiential Labs gateway: chat default, both demo orgs, graph extraction, eval answers. | Owner's call, with $200 of Anthropic credit. Native API gives prompt caching and (later) citations; the gateway gives neither. | Smoke set: 14/15 pass, 6.9 s mean vs 30.7 s on Nemotron, citation recall 0.65 vs 0.34 (same judge). Graph extraction: 16 entities and 14 relations from one chunk in 9 s. | `LLM_MODEL`, `GRAPH_EXTRACTION_MODEL` in `.env`; org model in Settings. |
| D2 | **The reranker stays on NVIDIA.** | It's a cross-encoder, not a chat model; Haiku can't replace it. | — | — |
| D3 | **Smoke runs are judged by Claude Sonnet 5.5, full runs by the free Nemotron 3 Ultra.** | Smoke runs gate every change and Nemotron takes hours; Sonnet takes ~30 s for ~$0.30. Opus for everything would cost ~$75 over the phase, Sonnet-smoke + free-full ~$10. Sonnet judging Claude answers is same-family grading, so it's recorded (`same_family_judge`) and checked. | On the same 15 Haiku answers: 30/30 fact verdicts and 15/15 pass verdicts agree; 96–97% of 144 grounding labels agree (κ 0.82 supported, 0.94 cited support); both judge 125 statements supported. | `--judge` or `EVAL_JUDGE_MODEL`. |
| D4 | **Claude judges run at low effort.** | Sonnet 5.5 thinks at high effort by default, which would multiply judge cost and time. | 15 answers judged in 27 s. | `effort` in `judge_call`. |
| D5 | **Prompt caching in the agent loop**: a breakpoint on the system prompt (tools + system) plus automatic caching of the growing loop. On by default. | Cache reads cost 0.1× input and cut latency. | Live: a second question read 2,877 cached tokens; 37% of smoke-run input came from cache; cost per answer $0.00148 → $0.00111. | `PROMPT_CACHING=false`. |
| D6 | **The forced final turn keeps `tool_choice: none`.** | The plan said to use a mid-conversation system message instead, but Haiku 5.5 doesn't support those (Opus/Sonnet 5.5 and up only). Costs one cache miss on that turn. | API reference (platform availability table). | — |
| D7 | **Tool calls from one model turn run concurrently** (≤ 4, each with its own DB session, results in call order), on the Claude and NVIDIA paths. | Several searches in one turn ran one after another. | `test_tool_calls_in_one_turn_run_concurrently`. | `MAX_PARALLEL_TOOLS = 1`. |
| D8 | **"Rerank once" dropped.** | The per-query rerank calls already run in parallel: 1.1–2.0 s per search for 1, 2 or 4 queries (NVIDIA reranker, 40 passages). At most ~0.3–0.5 s to gain, and it would lose the best-of-queries score. | Timing script, 3 runs each. | — |
| D9 | **`eager_input_streaming` removed from every tool.** | It streams the model's tool input unvalidated; Sonnet 5.5 sent malformed JSON (e.g. `{"queries": Bayline incident…`) on 60 of 150 questions. Our tool inputs are a few short queries, so it bought nothing. Haiku had 0 such errors. | 60/150 errors with it; 0/8 re-run failures and 0/150 on the full re-run without it. | — |
| D10 | **Strict tool use not adopted (for now).** | It would also guarantee valid tool input, but the API rejects it together with Citations, which step 4 needs. | API reference (structured outputs: "Incompatible with: Citations"). | Revisit after step 4. |
| D11 | **Contextual Retrieval built, off by default.** | The gain on this corpus isn't significant, so by the plan's ship rule it isn't switched on. The corpus is short documents (median ~350 tokens, ~4 chunks each), not the long documents the method targets; re-measure on step 3's PDFs. | recall@5 0.937 → 0.943 (+0.006, 95% CI [0.000, 0.016], p 0.14); 3 questions up, none down on recall. Cost $0.055 for 522 chunks. | `CONTEXTUAL_RETRIEVAL=true`. |
| D12 | **Contextual Retrieval skips only single-chunk documents**, not "under 512 tokens" as planned. | The 512-token rule would have skipped nearly the whole corpus. | Corpus stats: median document ~350 tokens. | `contextualize_document`. |
| D13 | **BM25 (pg_textsearch 1.5.1) ranks keyword search wherever its index exists**; `ts_rank_cd` otherwise. | Quality is equal within noise here; the reason is scale: keyword search now reads its top results from the index instead of scoring every match (audit's first thing to break at 1M passages). | Reranked MRR 0.942 → 0.948 (p 0.13), recall@5 0.939 → 0.936 (n.s.); cross-org recall@5 −1 question of 8. | `KEYWORD_RANKING=ts_rank`. |
| D14 | **The BM25 query drops the `tsv @@` match the plan kept**, filtering on `score < 0` instead. | With the `tsv` match, Postgres filters through the GIN index and sorts every match, never using the BM25 index (EXPLAIN), so it scales like `ts_rank_cd`. The score filter keeps the index scan and still drops non-matching chunks. | EXPLAIN before and after; `test_the_score_expression_matches_the_index`. | — |
| D15 | **Docker's Postgres moves from 16 to 17** (the dev server was already 17.9; `postgres/Dockerfile`, pgvector:pg17 + `postgresql-17-pg-textsearch=1.5.1-*`). | pg_textsearch needs 17+. The package was confirmed in the PGDG apt index (bookworm and trixie). | README has the pg16 → pg17 dump/restore. Image not built locally (Docker wasn't running); CI builds it. | Postgres 16 still works without BM25. |
| D16 | **Each index configuration gets its own eval database** (`strata_eval_<hash>`), and result names carry it (`-ctx`, embedding model). | A contextual run overwrote `retrieval-default.json` (restored from git). | — | — |
| D17 | **`--no-judge` answer-only runs**, judged later with `rejudge`; unjudged rows never count as passed. | Lets a model be used while it's available while the free judge catches up. | Sonnet runs answered in minutes; judging queued. | — |
| D18 | **CI on GitHub Actions**: ruff, format, backend tests on plain pg17 *and* the BM25 image (`REQUIRE_TEST_DB`, `REQUIRE_BM25` make missing services fail), web typecheck and build. | No CI existed. | Suite passes locally on Python 3.12 and 3.14, both Postgres setups. | — |

### Evals and metrics

**Answers, smoke set (15 questions), quick mode, Haiku 5.5**

| Run | Judge | Pass | Faithfulness | Citation recall | Citation precision | Mean / p50 / p95 | $ per answer |
|---|---|---|---|---|---|---|---|
| Nemotron 3 Ultra (earlier baseline) | Mistral Large 4 | 12/15 | 0.933 | 0.337 | 1.000 | 30.7 s / — / 63.8 s | free |
| Haiku baseline | Nemotron | 14/15 | 0.940 | 0.654 | 0.990 | 6.9 / 6.7 / 12.3 s | 0.00148 |
| Haiku baseline | Sonnet 5.5 | 14/15 | 0.969 | 0.690 | 1.000 | 6.9 / 6.7 / 12.3 s | 0.00148 |
| Haiku + step 1 | Sonnet 5.5 | 14/15 | 0.982 | 0.763 | 0.973 | 5.2 / 4.9 / 9.7 s | 0.00111 |

- Baseline vs step 1 (same judge): no question changed outcome (paired bootstrap). The step-1 run asked one question at a time and the baseline three, so part of the latency drop may be less contention.
- The one Haiku failure in every run is q100, the known multi-hop miss (the on-call rota).
- Results: `answers-haiku-baseline-smoke-claude-haiku-5-5-smoke{,-rejudged}.json`, `answers-step1-claude-haiku-5-5-smoke{,-rejudged}.json`.

**Answers, 150 questions, answer-only (judging queued on the free judge)**

| Run | Errors | Facts (substring) | Gold doc cited | Mean / p50 / p95 | $ per answer | $ total |
|---|---|---|---|---|---|---|
| Sonnet 5.5, quick | 0 | 0.967 | 0.841 | 6.0 / 5.8 / 8.7 s | 0.0159 | 2.38 |
| Sonnet 5.5, deep | 0 | 1.000 | 0.870 | 15.5 / 15.2 / 22.0 s | 0.0406 | 6.09 |
| Haiku 5.5, deep | 0 | 1.000 | 0.885 | 16.6 / 15.9 / 24.1 s | 0.0027 | 0.41 |
| Haiku 5.5, quick (baseline) | running | | | | | |

- Sonnet costs ~15× Haiku per answer in deep mode for the same substring facts and gold-citation rate. Whether deep research should route to Sonnet waits for the judged numbers.
- The Sonnet quick run and both deep runs used the step-1 code (prompt caching, concurrent tools); the Haiku quick baseline started before it.

**Retrieval, 135 answerable questions**

| Configuration | recall@5 | recall@10 | MRR | Result file |
|---|---|---|---|---|
| Dev server: ts_rank_cd, reranked (baseline) | 0.937 | 0.967 | 0.942 | `retrieval-default.json` |
| Dev server: + Contextual Retrieval | 0.943 | 0.975 | 0.943 | `retrieval-default-ctx.json` |
| BM25 test server: ts_rank_cd, reranked | 0.939 | 0.970 | 0.942 | `retrieval-pg17-default-ts_rank.json` |
| BM25 test server: BM25, reranked | 0.936 | 0.970 | 0.948 | `retrieval-pg17-default-bm25topk.json` |
| BM25 test server: ts_rank_cd, no reranker | 0.839 | 0.926 | 0.841 | `retrieval-pg17-fusion-ts_rank.json` |
| BM25 test server: BM25, no reranker | 0.855 | 0.937 | 0.847 | `retrieval-pg17-fusion-bm25topk.json` |

- Contextual Retrieval by category (recall@5): cross-org vocabulary 0.828 → 0.883; table MRR 0.967 → 1.0; multi-hop MRR 0.98 → 0.96 (one question). Changed questions: q18, q142, q058 up.
- BM25 by category, reranked (recall@5): cross-org vocabulary 0.844 → 0.861, cross-org 0.875 → 0.812 (one question), multi-hop 0.844 → 0.836; table MRR 0.967 → 1.0. Without the reranker: cross-org 0.438 → 0.625, multi-hop 0.716 → 0.803, alias 0.792 → 0.708, table 1.0 → 0.933.
- Both servers are Postgres 17.9; each comparison is within one server and database. The same configuration scored 0.937 and 0.939 recall@5 on the two, which is run-to-run noise (reranker scores vary slightly).
- No retrieval difference tonight is statistically significant.

**Latency components measured**
- NVIDIA rerank of 40 passages: 1.1–2.0 s per search (1–4 queries, run in parallel).
- Haiku answers with caching, live in the app: 2.5–3.7 s for single-search questions.

### Costs (Anthropic credit)

| Item | Cost |
|---|---|
| Haiku answers so far (smoke runs, live checks, deep run, part of the baseline) | ~$0.9 |
| Contextual Retrieval, whole eval corpus | $0.055 |
| Sonnet: quick 150 + deep 150 | $8.47 |
| Sonnet: run lost to malformed tool input (D9) + probe | ~$1.4 |
| Sonnet judging (two smoke runs) | ~$0.6 |
| **Total tonight** | **≈ $11.5 of $200** |

### Problems found and fixed

- Malformed tool input from Sonnet 5.5 (D9).
- Unjudged rows counted as passed (D17).
- A queued run waited forever: `pgrep -f` matched its own command line. Wait on process IDs instead.
- Migration 0010 ran on the shared eval database while the baseline used it (each harness start migrates). It rebuilt the keyword column with identical content; no answer errored.
- A retrieval result overwrote the baseline file (D16).

### Open items

- Haiku 150-question quick baseline (running), then free judging of the Sonnet quick/deep and Haiku deep runs (queued), then a Postgres restart that loads pg_textsearch and migrates every database (queued).
- 50 human labels for judge κ (needs a person, ~20 min), now for both judges.
- CI's first run, including the first build of `postgres/Dockerfile`.
- Next by the plan: step 4 citations (target citation recall ≥ 0.85), step 3 Docling, step 2c embedding A/B, the quick-mode fast path (p50 is already 4.9 s on the smoke set).
- Rotate the Anthropic API key once this phase's work is done (it was shared in chat).

### Commits

`2a12d49` Haiku 5.5, cost per answer, CI · `95fb862` caching, concurrent tools, eager streaming off · `57de1c3` Contextual Retrieval · `6484e31` BM25 on Postgres 17 · `5bdd3ed` Sonnet smoke judge.
