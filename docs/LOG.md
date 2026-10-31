# Decision and eval log

What was decided, why, and what was measured, newest first. Each entry links to the code and the result files; [EVALUATION.md](EVALUATION.md) has the method and the standing results, and [PLAN-WEEKS-3-5.md](PLAN-WEEKS-3-5.md) the plan these entries follow.

## 2026-11-01 (later still) — Track B merged: knowledge gaps, audit log, Slack bot; review fixes

Three features were built in parallel on their own branches (gaps and document requests, roadmap §6 #2; the per-org audit log, §6 #4; the Slack bot), merged with records, migrations chained 0013 → 0014 → 0015 → 0016 (round-trip checked). Each branch got an independent review; what it confirmed was fixed here.

| # | Decision | Why | Evidence | To reverse |
|---|---|---|---|---|
| D44 | **"Answered in org B" hints use only the viewer's own conversations, answers shared by permalink, and verified answers.** | The review found hints quoting other members' private questions and answers in B to anyone who joined B: sharing is opt-in everywhere else. | `test_an_admin_outside_org_b_learns_nothing_about_it`: joining B shows nothing until the author shares the answer. | — |
| D45 | **A shared answer returns only the sources it cites.** | Pre-existing: the permalink returned every retrieved passage, but permissions were checked only for cited ones, so a restricted snippet could reach a viewer who can't read it. | `test_a_shared_answer_shows_only_the_sources_it_cites`. | — |
| D46 | **The Slack bot doesn't answer in channels shared with another organization** (`conversations.info`: ext-shared, pending, shared), and tells the asker to DM it. | "Org-wide collections only" in channels assumed everyone in the channel is in the org; Slack Connect channels have outside readers. Guests in ordinary channels aren't detected (documented). | `test_channels_shared_with_other_organizations_get_no_answer`. | — |
| D47 | **Slack email matching is off by default** (`SLACK_MATCH_EMAILS`); when on, only Slack-confirmed emails of the workspace's own members. Link tokens are single-use and the link page shows the Slack email, not the display name. A Slack error while identifying retries the job. | Strata doesn't verify signup emails, so a matching email proved nothing; a link sent by someone else could make you vouch for their Slack account, and stayed reusable after unlinking. | Four new Slack tests; 18 Slack tests in all. | `SLACK_MATCH_EMAILS=true` (with SSO or emailed invites). |
| D48 | **A workspace's default org is stored; when it disconnects, unmapped channels answer nothing** instead of passing to whichever org connected next. | No admin of the next org chose that. | `test_unmapped_channels_stop_answering_when_the_default_org_leaves`. | — |
| D49 | **The features log to each other**: Slack answers carry audit channel `slack`; records queries and record reads are data access; record-type, document-request, Slack, agent and graph changes are admin events (each org's Slack channel events name only that org). The answer's audit insert runs in a savepoint, so it can't lose an answer. | They were built apart and didn't know about each other. | Audit assertions in the records, gaps and Slack tests. | — |
| D50 | **Gaps: listing collections or graph entities isn't research, records lookups are; a race on duplicate document requests returns the first (200), not a 500; "truncated" counts candidates before filtering.** | Review findings, each confirmed in the code. | `test_what_counts_as_researching_an_answer`, `test_the_same_request_sent_twice_at_once_returns_the_first`. | — |
| D51 | **An answer whose opening sentence says the knowledge base doesn't have it is a gap ("unanswered") even when it cites something**, by a rule on that sentence (`says_not_found`, English), mirrored in the chat UI's request button. | The prompt asks the agent to say plainly what's missing *and* what it did find, so these answers cite nearby material; the citation rule missed them (live: a drone-policy question that cited courier vendors). | Against the judge's abstention labels: 600 answers it was tuned on, precision 0.914, recall 0.803; 255 held-out answers, 0.883 / 0.815, where most "false positives" are partial gaps ("doesn't give Northwind's figure"). The live drone question now shows as a gap with its request. | — |

Known and left as follow-ups (from the reviews): Slack guests in ordinary channels; Slack questions have no per-user rate limit and jump ahead of syncs in the queue; two quick questions in one thread can race on its conversation; the gaps page re-scans traces on every load (fine at hundreds of questions per window, not measured at thousands); verified-answer lists and admin document views aren't logged as reads; deleting an org deletes its audit trail.

Tests: 327 passed, 1 skipped (239 before weeks 5–8).

## 2026-11-01 (later) — Weeks 5–8 Track A: structured records (and step 6, LangExtract)

### Decisions

| # | Decision | Why | Evidence | To reverse |
|---|---|---|---|---|
| D39 | **Structured records with a `query_records` tool** (roadmap §4.1): registers, inventories and logs kept as typed rows per collection; the agent queries them exactly (filters, counts, group-bys) and cites each row's document. Also `POST /records/query`, MCP `query_records`, and a Records page. | Search returns the top few passages, so "which / how many / list every" answers were partial however good retrieval got. | Scale questions (60-vendor register): items found **26/50 → 50/50**, mean 9.5 s → 4.2 s, $0.00128 → $0.00078 per answer. List questions: 71/71 → 70/71 (q33 dropped the date "September 12" from its wording; the records tool wasn't involved). | Archive the schemas; the tool is only offered when one is active. |
| D40 | **Record types are suggested deterministically and an admin activates them**; nothing becomes "exact" data unreviewed. Suggestions come from field/value tables recurring in ≥ 2 documents, tables whose header recurs, and repeated `Label: value` lines; field types are inferred from the values. | Suggestion costs nothing and is reproducible; an admin decides what the agent may treat as complete. | Eval corpora: 39 schemas, 331 records found. Vendor register: key, types and the 3 risk tiers right; 60 records extracted in 0.11 s; 10 High-risk, 27 executed BAAs among PHI vendors and 8 Q1 2027 renewals all match the files. | — |
| D41 | **LangExtract (step 6) is the prose path of records**, not a separate feature: a schema an admin writes (`origin: llm`) is extracted by LangExtract 1.7.1 through a Claude provider (`RECORD_MODEL`, Haiku 5.5). Every value must be words of the document aligned to offsets; a normalised reading (yes/no, an option, a date) types those words, and a text value narrows to the reading only when it's written inside the span. | Prose rarely states "Yes" or an option verbatim ("handles protected health information"), and verbatim-only extraction dropped those values; a reading that isn't grounded in the span never becomes a value. | Same 60 vendors. Original table files: **420/420** values right, 420/420 with offsets, $0.020. Rewritten as review-note prose (6 documents, 3 phrasings, 3 date formats): verbatim only **342/420**, with readings **411/420**, offsets 420/420, $0.014. The 9 left are services with a trailing word ("translation vendor"). Through the API and worker: 60 records in 111 s. | `origin: llm` schemas only. |
| D42 | **The records tool returns exact tallies of its choice and yes/no fields, and the prompt says to take totals from them and to cite each record on its own row.** | Live, the agent listed all 16 PHI vendors without an executed BAA correctly but summarised "nine pending, seven expired" (truth: 10 and 6), with all 16 citations stacked on that sentence. | After: "ten pending signature and six expired", one citation per row. | — |
| D43 | **Records lookups are their own trace step kind** (`records`), shown as "N records lookups" in the summary line. | They were counted as searches. | — | — |

### Evals

Records A/B (Haiku 5.5, quick mode, substring fact check, no judge): `eval/results/answers-{scale,list}-{off,records}-claude-haiku-5-5.json`; `python -m eval.harness --records` suggests, activates and extracts every collection's record types first.

| Set | Questions | Without records | With records |
|---|---|---|---|
| Scale (60-vendor register) | 4 | 26/50 items, 9.5 s | **50/50**, 4.2 s |
| List and aggregate | 18 | 71/71, 5.7 s | 70/71, 4.6 s |

## 2026-11-01 — Step 2c and step 3: embeddings, parsing every format

### Decisions

| # | Decision | Why | Evidence | To reverse |
|---|---|---|---|---|
| D27 | **granite-embedding-small-english-r2 is not adopted**; bge-small stays. | No gain at our ~512-token chunks; its advantage is long documents. | BM25 server, 135 questions. Reranked: recall@5 0.936 → 0.932, recall@10 0.970 → 0.966 (CI [−0.010, 0.000]), MRR 0.948 → 0.946. No reranker: MRR 0.847 → 0.822. | `EMBEDDING_MODEL`. |
| D28 | **Docling 2.145.0 parses PDF, Word, Excel, PowerPoint, email and images** (the `parsing` extra), once, at indexing; questions only read stored chunks. Legacy .doc/.ppt/.xls too when LibreOffice is installed. | Today's parsers lose structure (Word tables moved to the end, PDFs split by page, no OCR) and don't read Excel, PowerPoint, email or images at all. | Real files (D32): every format parsed, all 27 target facts found. A Word table stays between its paragraphs (`test_docling_keeps_word_tables_in_place_and_reads_excel`). | `DOCLING=off` (or no extra): the light parsers. |
| D29 | **Docling runs in spawned worker processes**, a warm converter each, replaced after 25 documents; a file past `DOCLING_TIMEOUT_SECONDS` (600) kills the pool. OCR pinned to RapidOCR on onnxruntime. Zip-bomb guard on Office files; 50 MB and 500-page limits. | Fork hangs with torch (Docling #1277); memory growth (#2209, #2788); a hung conversion can't be interrupted; same OCR in dev and production. | First use ~85 s (model load), then a 10-Q in 6 s, a 28-page PDF in 90 s. | Settings `DOCLING_*`. |
| D30 | **Slide decks are parsed as PDF pages** (LibreOffice), so every slide gets layout analysis and OCR. Without LibreOffice, Docling's PowerPoint reader. | Investor and board decks are often pictures of slides: Microsoft's earnings and outlook decks are one picture per slide, and the PowerPoint reader returned 93 and 429 characters for 5 and 22 slides. | As pages: the outlook deck's guidance table and the slides' figures were read (all slide facts found). | — |
| D31 | **Large tables become passages of their own**, row groups under the section heading, each repeating the header row; small tables stay with their prose. Paginated formats mark pages, and each chunk stores the page it starts on (`chunks.page`, migration 0012), shown to the model with the source. | A spreadsheet or PDF table split line by line lost its header after the first piece. | Retrieval on the 150-question set unchanged after re-indexing (recall@5 0.936, recall@10 0.970, MRR 0.948; table category 1.0 on all three). | — |
| D32 | **A public, real-file test set**: Microsoft's FY26 Q1 investor-relations packet (slides, outlook, call transcript, press release, 10-Q, financial statements) and its 2026 environmental data fact sheet and sustainability report, plus a CSV, a scanned page and legacy .doc/.ppt derived from them. Fetched by `eval/public/fetch.py` (URLs and SHA-256 recorded); the files aren't committed. 20 questions (`eval/questions_public.jsonl`), including three that need two file types. | The user's call: synthetic files are too clean to test a parser. It surfaced the picture-slide problem at once. | `EVAL_CORPUS=public`: its own databases (with and without vision). | — |
| D33 | **Optional vision pass for weak pages** (`VISION_PARSING`, off by default; Haiku 5.5): a page is weak when it has pictures but little text, when pictures cover ≥ 30% of it, or when its OCR text is debris (≥ 6% of words are stray fragments or vowelless runs). Weak pages are rendered and transcribed; every other page keeps Docling's text; any failure keeps Docling's. Cost recorded on the document. | Local OCR garbles stylised text and can't read charts; a vision model reads those pages, at indexing only. | Debris signal on the real files: clean pages ≤ 0.048, the scan 0.146, garbled slide OCR up to 0.122. Whole public corpus: 27 weak pages transcribed for **$0.0104**. The scan went from "Ast snsg t a ng r ot o ng s f o the…" to a clean transcript. Parsing recall 27/27 with or without. | `VISION_PARSING=false` (default). |
| D34 | **Picture-slide handling is general, not per document.** An earlier version OCR'd the pictures inside a deck when it looked empty; replaced by slides-as-pages plus the per-page weak signals. | The user asked for parsing that isn't tuned to particular files. | — | — |
| D35 | **The NVIDIA judge run was stopped** (user's call). Its free tier rate-limited (HTTP 429) when two runs shared it: two deep-mode re-judges and half of the public-files answers failed. The harness now backs off exponentially on judge errors (up to 60 s), keeps an answer whose judging failed, and re-judging leaves a failed row unjudged instead of aborting. | — | 10 of 20 public answers lost to 429s in the first run. | — |

| D36 | **Haiku 5.5 stays the model for deep mode too.** | Sonnet's gain isn't significant and costs 15×. | 150 questions, deep mode, Sonnet judge: Haiku 148/150 (multi-hop 0.96), Sonnet 150/150 (judging its own answers); paired +0.013, CI [0.000, +0.033], 2 questions changed. $0.0027 vs $0.0406 per answer, 16.6 vs 15.5 s. These runs predate the coverage check (now on for deep mode). | `LLM_MODEL` / org model. |
| D37 | **The grounding judge sees every passage of a long document that holds a figure or quote the answer uses**, not only its opening and the first hit. After a re-ingest, cited documents are found by title. | On real files the judge saw 12,000 characters of a 209K-character fact sheet and marked correct table figures "unsupported" (e.g. Scope 3 FY25 = 18,243,000, which is in the file). | Public set, Docling run: faithfulness 0.759 → 0.885, citation recall 0.540 → 0.632, with the same answers. | — |
| D38 | **Vision parsing stays off by default.** | No measurable answer gain on the public set (D33 evals), though it fixes garbled pages; a deployment with scans, charts or picture decks should turn it on (about $0.0004 per weak page). | As above. | `VISION_PARSING=true`. |

### Evals

Parsing recall (no LLM: is each fact in the stored text of its source file?):

| Index | Facts found | File types |
|---|---|---|
| Docling only | **27/27** | Word ×3, Excel, CSV, PDF ×2, scanned PDF, picture slides ×2, legacy .doc/.ppt |
| Docling + vision on weak pages | **27/27** | same; 27 pages transcribed, $0.0104 |

Parsing times (this Mac, CPU): 10-Q (Word, 520K characters) 5.7 s; financial statements (Excel, 11 sheets) 0.2 s; fact sheet (PDF, 28 pages) 90 s; sustainability report (PDF, 66 pages) 80 s; scanned page 20 s; 22 picture slides 146 s. Whole public corpus indexed in 10 min 54 s.

Answers on the public questions (Haiku 5.5, quick mode; judged by Sonnet 5.5 after the NVIDIA judge was stopped):

| Index | Pass | Faithfulness | Citation recall | Citation precision | Cross-format | Mean | $ per answer |
|---|---|---|---|---|---|---|---|
| Docling only | **20/20** | 0.885 | 0.632 | 0.889 | 3/3 | 6.0 s | 0.00135 |
| Docling + vision | 19/20 | 0.886 | 0.684 | 0.877 | 3/3 | 4.9 s | 0.00134 |

- Answers that combine a Word press release with picture slides, and slides with a transcript, all passed: the system is cohesive across formats.
- Vision makes no measurable difference here: these questions target facts OCR already captured. The one difference (p12, FY25 Scope 1) is retrieval variance on a page vision didn't touch (the answer retrieved Table 1B instead of 1A).
- The first judging of these runs scored faithfulness 0.76; see D37.

Quick mode, 150 questions, Nemotron judge (judged overnight):

| Model | Pass | Multi-hop | Faithfulness | Citation recall | Mean | $ per answer |
|---|---|---|---|---|---|---|
| Haiku 5.5 | 0.940 | 0.76 | 0.955 | 0.628 | 5.4 s | 0.00114 |
| Sonnet 5.5 | 0.933 | 0.80 | 0.968 | 0.528 | 6.0 s | 0.01585 |

Sonnet costs 14× as much per answer for no better result in quick mode, so Haiku stays the default. The deep-mode comparison wasn't judged (D35).

## 2026-10-31 (later) — Step 4: citations, grounding, sufficient context

### Decisions

| # | Decision | Why | Evidence | To reverse |
|---|---|---|---|---|
| D19 | **Claude's native citations (search_result blocks) are built but off by default.** | On Haiku 5.5 they attach only where the answer near-quotes a source; synthesized content (tables joining two sources, comparisons, summaries) gets no citation. With the prompt telling it not to type numbers, 75 of 136 claims went uncited. The prose also degraded: a point in the model's words, then a near-quote of the source repeating it so the quote carries the citation. | Smoke, Sonnet judge: citation recall 0.375 vs 0.763 (paired −0.39, 95% CI [−0.50, −0.27]); precision 0.887 vs 0.973; 6.3 vs 5.2 s; $0.0016 vs $0.0011 per answer. | `CLAUDE_CITATIONS=true`. |
| D20 | **The typed-[n] prompt now requires a citation on every list item and table row.** | Lists and tables were the weakest spots for citation recall (0.06 and 0.29 on Nemotron). | Smoke: citation recall 0.763 → 0.791 (paired +0.033, CI [−0.006, +0.075], p 0.05), precision 0.973 → 0.990, faithfulness 0.982 both, pass 14/15 both. Live: every step of a 6-item list cited. | `CITE_BY_NUMBER` in `app/agent/prompts.py`. |
| D21 | **Native mode keeps the work that makes it viable**: passages split into one block per sentence, list item or table row (abbreviation-aware); the ref carried in `source` (`strata://source/<n>`); markers appended when a cited block finishes streaming; model-typed `[1-6]` (result-block) markers mapped to `[1]`; repeated markers collapsed. | So the switch can be re-tested on other models (Sonnet/Opus 5.5) or after prompt work without rebuilding it. | Tests: `test_claude_citations_become_markers`, `test_markers_the_model_also_wrote_are_not_doubled`. | — |
| D22 | **A `[n]` that matches no source is shown, struck through, instead of hidden** (audit C3), and each answer's trace records `strata.unknown_refs`. | Hiding it made an unsupported claim look like an uncited one. | `test_a_chat_answer_is_one_trace_with_turns_tools_and_usage`. | — |
| D23 | **HHEM-2.1-Open is not adopted** for the per-sentence grounding check. | Too slow and too many false alarms to flag sentences in the UI. | Against Sonnet's labels on 180 cited claims (plus 120 synthetic negatives): AUC 0.95 vs a wrong document, 0.84 vs a changed number, 0.91 on the judge's 6 real negatives; at threshold 0.5 it flags 25% of supported claims (44/174) while catching 49/60 changed numbers. 1.36 s per claim on CPU (~800-token passages), ~14 s per 10-claim answer. Code reviewed at revision `de9a5e6` before running. | Script: `hhem_eval.py` in the session scratchpad. |

| D24 | **Sufficient-context check** (`app/agent/sufficiency.py`): after a search/read round, Haiku 5.5 (low effort, adaptive thinking) says which facts the sources don't state yet; the answering model gets that note after the tool results and searches again or says what's missing. At most two checks per answer. | The full baseline's failures were mostly multi-hop answers that found the first fact and stopped. | 55 hardest questions (multi-hop, unanswerable, cross-org vocabulary), Sonnet judge: pass 0.836 → 0.909 (paired +0.073, CI [+0.018, +0.145]); multi-hop 0.72 → 0.88 (+0.16, CI [+0.04, +0.32]); fixed q059, q104, q21, q26, broke none. Smoke: 15/15 (q100 fixed). Cost: mean latency 5.8 → 10.9 s on the hard set, 5.7 → 10.8 s on smoke; $0.0011 → $0.0015 per answer. Each check takes 1.8 s (median), 3.5 s (p90). | `SUFFICIENCY_CHECK=off`. |
| D25 | **The check is on for deep mode by default, off for quick mode** (`SUFFICIENCY_CHECK=deep`; `always` adds quick). | Doubling quick-mode latency breaks its < 5 s target; deep research already takes ~16 s and exists for thoroughness. | As D24. | `SUFFICIENCY_CHECK=always` or `off`. |
| D26 | **The check keeps thinking on and runs up to twice.** | A faster variant (thinking off, one check) saved only ~1.3 s and lost the gain. | Faster variant on the hard set: pass 0.855 (paired +0.018, CI [−0.054, +0.091]), fixed 3 and broke 2; mean 9.6 s. | `MAX_COVERAGE_CHECKS`, `think` in `structured_call`. |

### Evals

Smoke set, quick mode, Haiku 5.5, judged by Sonnet 5.5, one question at a time:

| Run | Pass | Faithfulness | Citation recall | Citation precision | Mean / p95 | $ per answer | File |
|---|---|---|---|---|---|---|---|
| Step 1 (typed [n]) | 14/15 | 0.982 | 0.763 | 0.973 | 5.2 / 9.7 s | 0.00111 | `answers-step1-…-smoke-rejudged.json` |
| Native citations | 14/15 | 0.963 | 0.375 | 0.887 | 6.3 / 9.6 s | 0.00160 | `answers-step4-native-…-smoke.json` |
| Typed [n] + per-item rule (shipped) | 14/15 | 0.982 | 0.791 | 0.990 | 5.7 / 13.2 s | 0.00117 | `answers-step4-text-…-smoke.json` |

Still below the 0.85 citation-recall target. HHEM, the planned per-sentence check, was tested and not adopted (D23).

Sufficient-context check, Sonnet judge:

| Run | Questions | Pass | Multi-hop | Faithfulness | Citation precision | Mean / p95 | $ per answer | File |
|---|---|---|---|---|---|---|---|---|
| Check off | 55 hardest | 0.836 | 0.72 | 0.968 | 0.963 | 5.8 / 9.2 s | 0.00108 | `answers-suff-off-hard-…json` |
| Check on (shipped form) | 55 hardest | **0.909** | **0.88** | 0.969 | 0.982 | 10.9 / 18.8 s | 0.00147 | `answers-suff-on-hard-…json` |
| Check on, no thinking, once | 55 hardest | 0.855 | 0.80 | 0.961 | 0.979 | 9.6 / 13.2 s | 0.00155 | `answers-suff-on2-hard-…json` |
| Check on (shipped form) | smoke 15 | **15/15** | — | 0.963 | 1.000 | 10.8 / 20.7 s | 0.00127 | `answers-suff-on-…-smoke.json` |

Cost of step 4 so far: about $6.5 (Haiku answers, Sonnet judging); HHEM ran locally.

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
| Haiku 5.5, quick (baseline, judged by Nemotron) | 0 | 0.974 | 0.857 | 5.4 / — / 8.5 s | 0.00114 | 0.17 |

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
