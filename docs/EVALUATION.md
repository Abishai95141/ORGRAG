# Strata — Evaluation

How Strata's answer quality is measured, what the measurements found, and which
proposed features earned their place. Everything here is reproducible with
`backend/eval/` (see "Re-running" at the end).

## The dataset

- **Corpus:** 73 documents across two orgs. That's the 17 seed documents plus 56 written for evaluation: policies, runbooks, meeting notes, a Slack thread export, an RFC, vendor records, a changelog, and table-heavy inventories. It deliberately includes real-world messiness:
  - the two orgs use different vocabulary for the same things;
  - codenames and people's initials;
  - facts that only connect across 3–5 documents;
  - lists spread over many records;
  - superseded policy versions;
  - near-miss distractors.
- **150 questions** in nine categories: single fact (30), multi-hop (25), list/aggregate (18), cross-org vocabulary (15), table (15), unanswerable (15), superseded (12), alias (12), and cross-org (8: answers that need facts from both orgs, such as comparisons). Each question records its gold documents and the facts a correct answer must contain. `eval/check_dataset.py` verifies that every gold title exists and that every required fact appears in its gold documents.
- **Smoke set:** 15 questions (marked `smoke`), at least one per category, run on every change (`--set smoke`); the full set runs before anything ships.
- **How the 105 new questions were written (2026-10-31):** three authors in parallel (Northwind, Halcyon, cross-org), each reading the whole existing world first so new documents extend it rather than contradict it; then an independent review of all 150 questions against the full corpus. The review found no conflict that changed an expected answer; it fixed four contradictions inside new documents and widened the accepted phrasings of 21 facts. Notes on each changed question start with "Reviewed:".
- **Scale test:** a 60-record vendor register for a third org, generated deterministically by `eval/scale/generate.py`, with 4 list/count questions whose answers span 8–27 records.

Three labels were corrected after review, each noted in the question's `notes`. q16 and q18 asked for facts the question didn't ask about. q36 said "now" about rates that only start on November 3.

## Scoring

- **Retrieval** (deterministic, no LLM): recall@5 and recall@10 of the gold documents, and MRR.
- **Answers:**
  - an LLM judge (temperature 0) decides whether each required fact is stated and whether the answer contradicts any fact;
  - a substring check runs alongside as a cross-check;
  - unanswerable questions pass only if the answer says the information isn't there.
  - A question passes when every fact is present and nothing is contradicted.
- **Grounding** (`eval/grounding.py`): the answer is split into statements (sentences, list items, table rows) with the `[n]` citations each carries, and one judge call labels every statement against the documents the model saw:
  - **faithfulness**: claims supported by the retrieved documents ÷ claims;
  - **citation recall**: claims fully supported by their own citations ÷ claims (an uncited claim counts as unsupported);
  - **citation precision**: citations that support their statement ÷ citations (a `[n]` that isn't a source counts against it).
  A ref stands for a whole document (the agent may have read several parts of it), so the judge sees each cited document in full, up to 12,000 characters.
- **The judge.** Ideally it comes from a different model family than the one answering, because a model grading its own family can be biased (see "Which judge" for where and how that's checked). Every result records the judge model and a hash of each judge prompt, and `compare` warns when two runs were judged differently (`rejudge` re-scores a saved run with the current judge). Replies are cached by model and prompt in `eval/.data`, so re-scoring is free.
- **Which judge.** Smoke runs, which gate every change, are judged by Claude Sonnet 5.5 (about 30 seconds and $0.30 for 15 questions); full runs by the free Nemotron 3 Ultra (hours, $0). `--judge` or `EVAL_JUDGE_MODEL` overrides both. Sonnet judging Claude answers is same-family grading, so results record `same_family_judge`. On the Haiku baseline smoke answers (2026-10-31) the two judges agreed on all 30 fact verdicts and all 15 pass/fail outcomes, and on 96–97% of 144 per-statement grounding labels (κ 0.82 for "supported", 0.94 for "supported by its citations"), with the same number of statements judged supported (125 each): no sign of Sonnet favouring Claude's answers on that sample. Claude judges run at low effort.
- **Judge reliability.** `label` walks a person through 50 judge verdicts without showing them (about 30 fact verdicts and 20 claim verdicts, balanced between the judge's yes and no), and `kappa` reports Cohen's κ against the human. The target is κ ≥ 0.7; until it's measured, read judge-based numbers as approximate.
- **Comparing runs.** `compare a.json b.json` adds a paired bootstrap (10,000 resamples of questions) per category: the difference in pass rate with a 95% interval, and "better", "worse" or "no clear change". Summaries also show p95 latency and, for models with a per-token price, `usd`: the mean cost per answer at list price, counting input, output, cache-read and cache-write tokens (`app/llm/pricing.py`).

`answers --no-judge` only answers (judge later with `rejudge`): for a model you can use now and a judge that is slower or limited.

Each run is a single sample and the models aren't deterministic. With 150 questions, one question is 0.7 points; the bootstrap says whether a difference is bigger than resampling noise.

## Results

### Retrieval, 150 questions (2026-10-31)

Default configuration (hybrid fusion, NVIDIA reranker, low recency bias), 135 answerable questions:

| Category | n | recall@5 | recall@10 | MRR |
|---|---|---|---|---|
| Single fact | 30 | 1.000 | 1.000 | 1.000 |
| Table | 15 | 1.000 | 1.000 | 0.967 |
| Superseded | 12 | 1.000 | 1.000 | **0.792** |
| Alias | 12 | 0.972 | 1.000 | 0.917 |
| List/aggregate | 18 | 0.963 | 1.000 | 1.000 |
| Cross-org | 8 | 0.875 | 0.875 | 0.812 |
| Multi-hop | 25 | **0.844** | 0.933 | 0.980 |
| Cross-org vocabulary | 15 | **0.828** | 0.883 | 0.880 |
| **All** | 135 | **0.937** | **0.967** | **0.942** |

The weak spots are the ones the new questions were written to probe:
- cross-org vocabulary and multi-hop, where the right document doesn't share the question's words;
- superseded policies, where both versions are found but the old one often ranks first.

These are the targets for Contextual Retrieval, BM25 and the embedding swap (roadmap §3). Search takes 652 ms on average, most of it reranking.

### Answers and grounding, smoke set (2026-10-31)

First run with the grounding metrics: the 15 smoke questions, answered by Nemotron 3 Ultra (NVIDIA free tier) in quick mode and judged by Mistral Large 4, a different model family. The full 150-question answer run is still to do.

| | Result |
|---|---|
| Pass | 12/15 before the fix below; the failed superseded question passes after it |
| Facts stated (judge) | 0.933 |
| Faithfulness | **0.933** |
| Citation precision | **1.000** |
| Citation recall | **0.337** |
| Latency | 30.7 s average, 63.8 s p95 |

What it showed:
- **Citations are accurate but sparse.** Every citation supported its statement, but about two thirds of claims carried no citation of their own. The worst cases were list items (0.06) and table rows (0.29), where answers cite the lead sentence and not each item. This is the clearest target for citation work (roadmap §3, item 5).
- **An empty first turn ended the answer.** Nemotron sometimes opens with an empty turn (no text, no tool call), and the run then said it couldn't answer. It's now retried once with tools on (`test_empty_first_turn_is_retried_with_tools`).
- **A real multi-hop miss (q100).** The answer gave the escalation rule but not who was on call that week, which is in the November rota.
- **A judge call to check (q130).** "RSV isn't on the formulary, so there's no published price" was graded as not abstaining. Calls like this are what the κ labelling is for.

### Retrieval, original 45 questions

| Configuration | recall@5 | recall@10 | MRR | ms/search |
|---|---|---|---|---|
| Hybrid fusion only | 0.835 | 0.948 | 0.880 | 69 |
| + NVIDIA reranker (default) | **0.939** | **0.965** | **0.951** | 528 |
| + reranker + stronger recency | 0.939 | 0.965 | 0.963 | 512 |

Reranking earns its 0.46 s: it surfaces the right document first in more cases. Recency had **no effect** at first. Uploads have no "last modified" time, so every document looked brand new. Strata now reads the date a document states about itself, and treats undated documents as 90 days old, as Onyx does.

### Answers (45 questions, Nemotron 3 Ultra, quick mode)

| Category | Before fixes | After fixes |
|---|---|---|
| Single fact | 100% | 100% |
| Cross-org vocabulary | 62.5% | **100%** |
| Multi-hop | 100% | 86% (one miss) |
| List (small, 4–6 items) | 100% | 100% |
| Superseded | 75% | **100%** |
| Alias | 100% | 100% |
| Unanswerable | 50% | **100%** |
| **Overall** | **87% (39/45)** | **97.8% (44/45)** after re-running rate-limited questions |

### Failures the evaluation found, all fixed

| Failure | How it showed | Fix |
|---|---|---|
| Nemotron sometimes writes a tool call as text (`<tool_call><function=…>`) instead of using the tool API | Raw XML shown as the answer in 3/45 questions | The streaming client parses text tool calls (XML and JSON forms) into real calls; tested against the recorded output |
| Model ends a research loop with an empty turn | "I wasn't able to put together an answer" | One retry asking for the answer from what was gathered, with tools off |
| NVIDIA rate limits | 4–24 of 45 questions failed under load | Exponential backoff honouring `Retry-After`, 5 attempts, and at most `NVIDIA_MAX_CONCURRENCY` (default 4) requests in flight per process |
| Recency bias was inert for uploads | Old policy version ranked above the new one | Stated document dates are extracted at ingestion |
| SSE answers didn't stream through the web proxy | "Starting…" until the whole answer arrived | `Cache-Control: no-transform` |
| A model sent a search query as a bare string instead of a list (seen live with Haiku) | "Web search failed"; answer fell back to "not in the knowledge base" | List arguments accept a single string |
| A gateway-only configuration counted as "chat configured" | Every chat failed with 503 | Only providers with a default chat model count; an unavailable saved model falls back to `LLM_MODEL` |
| Web pages blocked plain fetchers (403) | `read_document` on web results failed | Pages are read through Tavily's extract API when a Tavily key is set; direct fetch otherwise. The public-address check runs first either way |

### Chat model choice

| Chat model | Main 45 | Avg time | Cost per question | Notes |
|---|---|---|---|---|
| Nemotron 3 Ultra (NVIDIA free tier) | 44/45 | ~31 s | $0 | about 1 in 8 questions rate-limited under load |
| Claude Haiku 5.5 (Experiential Labs) | 42–43/45 | ~8 s | ~$0.002 | no rate-limit failures; best on the scale test |

The two runs used different judges (Nemotron and Haiku), so read the 1–2 question difference as noise. Haiku is about 4× faster and didn't fail under load.

### Rate limits are the main production constraint on NVIDIA

Even with the fixes, about 1 question in 8 failed after five retries during evaluation, because the API key's per-minute limit was saturated. For real use, any of these will help: a higher-limit NVIDIA plan; an Anthropic key so Claude takes chat; or a lighter model for background graph extraction (`GRAPH_EXTRACTION_MODEL`).

## Which proposed phases earned their place

### Phase 1 — cross-org concept/synonym layer: not built

After the fixes, cross-org vocabulary questions pass 100%. The agent already searches each org in its own terms. There was no measured gap to close, so it wasn't built. Revisit if a larger corpus shows cross-org misses.

### Phase 2 — knowledge graph: built and measured; it doesn't earn a default-on place

The scale test showed a real and dangerous gap. With 60 vendor records, search-based answers confidently undercounted, because each search returns at most 8 passages.

The graph uses LightRAG's extraction prompts and its tolerant output parser, vendored verbatim by `backend/scripts/vendor_lightrag.py` from the pinned 1.5.7 wheel, with the MIT licence. Storage, access control and tools are Strata's own (see `docs/API.md` → Knowledge graph).

**Building it.** 108 documents, 369 chunks, 369 model calls, **7.6 minutes, $0.47**, no failures, using `gpt-5.4-nano` via the Experiential Labs gateway. Nano was chosen by scoring 7 cheap models on the same documents:

| Model | Facts captured | Time (3 docs) | Tokens | Cost per 1k calls |
|---|---|---|---|---|
| gpt-5.4-nano | **20/20** | 46 s | 12.4k | $1.42 |
| claude-haiku-5.5 | 19/20 | 26 s | 16.9k | $1.50 |
| gpt-6-luna (free tier) | 19/20 | 61 s | 12.2k | $0 |
| gemini-3.8-flash | 17/20 | 46 s | 14.0k | $10.79 |
| gemini-3.5-flash-lite | 16/20 | 13 s | 10.1k | $3.51 |
| glm-5.3-flash | 9/20 | 80 s | 20.9k | $2.75 |
| deepseek-v4.1-flash (free tier) | 5/20 | 133 s | 23.5k | $0 |

The same build on NVIDIA's free-tier rate limits managed 7 documents in about an hour.

**Scale test (facts found out of 50, across the 4 list/count questions)**

| Chat model | No graph | Graph |
|---|---|---|
| Nemotron 3 Ultra (NVIDIA) | 22 | 37 |
| Claude Haiku 5.5 (gateway) | **34** | 33 |

**Main 45 questions on Haiku 5.5:** 42/45 without the graph, 40/45 with it, at about 8 s per answer either way. Two "failures" in the runs are judge mistakes: q38 contains the fact, and q42 correctly abstains. Adjusted, both setups land around 42–43. With the graph, one alias and one list question got slightly worse.

**Why it doesn't help a capable model**
- LLM extraction is lossy. Rendering "Record owner: Priya Nair" as a relation only part of the time, it reached 19/27, 9/10, 8/14 and 2/8 of the true items on the four scale questions, before any model reasoning.
- Early versions of the list tool said "this is the complete list". A capable model believed it and stopped searching. The wording now says the graph may be incomplete, and that brought the graph back to parity, not ahead.
- It helps a weaker chat model (Nemotron: 22 → 37 facts), but a better chat model with search alone does as well or better (34), for less money and complexity.

**Decision:** keep the graph as an **opt-in** setting (off by default), and use Haiku 5.5 as the chat model where an Experiential Labs key is available. The real fix for registers and lists is **structured field extraction**: parse table rows and `Field: value` lines into typed columns (owner, status, risk tier, renewal date), then filter and count them exactly in SQL. That's deterministic and costs almost nothing per document. It's proposed as the next step.

**Bugs the graph work found and fixed along the way**
- With tools offered but forbidden, Nemotron sometimes wrote nothing; final turns now offer no tools.
- Name variants ("High Risk Tier" / "Risk Tier High") are merged when looked up.
- A gateway-only configuration was wrongly treated as having a chat model.

### Phase 3 — corpus-wide community summaries: not built

None of the evaluation questions need corpus-wide theme summaries, and Onyx's own move away from that design suggests the cost rarely pays off. Not built.

## Re-running

```bash
cd backend
.venv/bin/python eval/check_dataset.py                        # dataset integrity
.venv/bin/python -m eval.harness retrieval --variant default  # no LLM
.venv/bin/python -m eval.harness answers --set smoke --chat-model explabs:claude-haiku-5.5   # every change
.venv/bin/python -m eval.harness answers --variant default --concurrency 1                   # full set
EVAL_JUDGE_MODEL=mistralai/mistral-large-4-1t-a52b \
  .venv/bin/python -m eval.harness answers --chat-model nvidia/nemotron-3-ultra-550b-a55b   # free, judge of another family
.venv/bin/python -m eval.harness rejudge eval/results/answers-x.json   # re-score with the current judge
.venv/bin/python -m eval.harness label eval/results/answers-x.json --n 50   # hand-label judge verdicts
.venv/bin/python -m eval.harness kappa eval/labels/answers-x.jsonl
.venv/bin/python eval/scale/generate.py                       # scale corpus
.venv/bin/python -m eval.harness build-graph --model nvidia/nemotron-3.5-lightning-30b-a3b
.venv/bin/python -m eval.harness answers --variant graph --questions eval/scale/questions_scale.jsonl
.venv/bin/python -m eval.harness compare eval/results/a.json eval/results/b.json
```

The harness uses its own database (`strata_eval` on the dev Postgres). An index configuration other than the default (for now, another `EMBEDDING_PROVIDER` / `EMBEDDING_MODEL`) gets its own, `strata_eval_<hash>`, so variants that re-index don't overwrite each other; each result records the `index` it ran on. Answers run on the org model (`--chat-model claude-haiku-5-5` needs `ANTHROPIC_API_KEY`); full runs' judge needs `NVIDIA_API_KEY` and smoke runs' judge `ANTHROPIC_API_KEY`.
