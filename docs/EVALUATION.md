# Strata — Evaluation

How Strata's answer quality is measured, what the measurements found, and which
proposed features earned their place. Everything here is reproducible with
`backend/eval/` (see "Re-running" at the end).

The standard set below is near its ceiling, so it now guards against regressions. Measure improvements on [the hard set](#the-hard-set).

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
  - unanswerable questions pass only if the answer says the information isn't there;
  - on the hard set, a `forbidden` value anywhere in the answer fails it, whatever else it says.
  - A question passes when every fact is present and nothing is contradicted.
- **Grounding** (`eval/grounding.py`): the answer is split into statements (sentences, list items, table rows) with the `[n]` citations each carries, and one judge call labels every statement against the documents the model saw:
  - **faithfulness**: claims supported by the retrieved documents ÷ claims;
  - **citation recall**: claims fully supported by their own citations ÷ claims (an uncited claim counts as unsupported);
  - **citation precision**: citations that support their statement ÷ citations (a `[n]` that isn't a source counts against it).
  A ref stands for a whole document (the agent may have read several parts of it), so the judge sees each cited document in full, up to 12,000 characters.
- **The judge.** Ideally it comes from a different model family than the one answering, because a model grading its own family can be biased (see "Which judge" for where and how that's checked). Every result records the judge model and a hash of each judge prompt, and `compare` warns when two runs were judged differently (`rejudge` re-scores a saved run with the current judge, against the question file the run recorded, or `--questions`, and refuses a file that lacks any of the run's questions). Replies are cached by model and prompt in `eval/.data`, so re-scoring is free. The judge sees at most 6 facts a call: asked about 11 at once it answered 10, even when told there were 11. A verdict shorter than the facts it was asked about is refused, not padded with "no", because padding failed correct answers.
- **Which judge.** Smoke runs, which gate every change, are judged by Claude Sonnet 5.5 (about 30 seconds and $0.30 for 15 questions); full runs by the free Nemotron 3 Ultra (hours, $0). `--judge` or `EVAL_JUDGE_MODEL` overrides both. Sonnet judging Claude answers is same-family grading, so results record `same_family_judge`. On the Haiku baseline smoke answers (2026-10-31) the two judges agreed on all 30 fact verdicts and all 15 pass/fail outcomes, and on 96–97% of 144 per-statement grounding labels (κ 0.82 for "supported", 0.94 for "supported by its citations"), with the same number of statements judged supported (125 each): no sign of Sonnet favouring Claude's answers on that sample. Claude judges run at low effort.
- **Judge reliability.** `label` walks a person through 50 judge verdicts without showing them (about 30 fact verdicts and 20 claim verdicts, balanced between the judge's yes and no), and `kappa` reports Cohen's κ against the human. The target is κ ≥ 0.7; until it's measured, read judge-based numbers as approximate.
- **Comparing runs.** `compare a.json b.json` adds a paired bootstrap (10,000 resamples of questions) per category: the difference in pass rate with a 95% interval, and "better", "worse" or "no clear change". Summaries also show p95 latency and, for models with a per-token price, `usd`: the mean cost per answer at list price, counting input, output, cache-read and cache-write tokens (`app/llm/pricing.py`).

`answers --no-judge` only answers (judge later with `rejudge`): for a model you can use now and a judge that is slower or limited.

Each run is a single sample and the models aren't deterministic. With 150 questions, one question is 0.7 points; the bootstrap says whether a difference is bigger than resampling noise. It doesn't see run-to-run flipping, so to compare two configurations use repeats: see [What counts as a real difference](#what-counts-as-a-real-difference).

## The hard set

The 150-question set can no longer show an improvement. On 2026-11-07 Haiku 5.5 got 132 of 135 answerable questions fully right, and reruns of one configuration move by 3 to 5 questions, so anything smaller is invisible. The hard set is built to measure from here:

| | |
|---|---|
| Today's system (Haiku 5.5, quick mode, records on), mean of 6 repeats | **57.1%** |
| The same model, handed each question's gold documents and nothing else | **97.7%** |
| A true gain of 5 points, measured with 3 repeats against the frozen baseline | detected **95%** of the time |
| The same system compared with itself | called different **1.3%** of the time |

### What's in it

- **Corpus:** 1,153 documents in their own database, `strata_eval_hard`. 523 are real: a pruned copy of the `strata` org (the GOV.UK Service Manual and crawl, GDS Way, GOV.UK RFCs, GDS publications, Drive files, and the Harbourline sample Slack and Notion). 630 are generated by `eval/hard/world.py` and `render.py` for Port Systems, a fictional sibling division in the same org.
- **Why generate any:** a first pilot on the real documents alone scored about 90%, four-hop chains included. Harbourline is about 30 short documents, so one search returns all of it. Real organisations have hundreds of look-alike documents, so the world has them: a page and a runbook per service, a postmortem per incident, 22 monthly reports and rotas, fortnightly leads notes, 343 Slack channel-days.
- **How its documents mislead, the way real ones do:**
  - a service page is stale, because the handover was announced in Slack and nobody edited the page;
  - people write project names in Slack ("Kestrel moves to Harbour Dues on the 1st"), and only the service page says what they mean;
  - postmortems change format twice, so structured records cover only some of them;
  - a correction arrives weeks later in a thread, and the report is never amended;
  - a rota swap exists only in Slack, and rotas use initials;
  - a contract variation says "extended by 12 months" and gives no date;
  - the newest version of a policy was withdrawn before it took effect;
  - an announced handover was called off days later;
  - restricted collections hold newer or more exact figures than the public documents.
- **146 test questions and 56 dev questions** (`eval/hard/questions.jsonl`, `questions_dev.jsonl`), computed from the world's truth, plus 14 hand-written over the real documents (`questions_real.jsonl`):

| Category | Test | What it asks |
|---|---|---|
| Chain | 31 | Several steps across document types: postmortem, rota, Slack swap, directory |
| Aggregate | 27 | Facts spread over many documents, joins, sums with corrections |
| As of | 27 | What was true on a date, not what's true now |
| Superseded | 23 | Which source wins when a page and a newer statement disagree |
| Real corpus | 11 | Counts over 100+ RFCs, near-identical minutes, a 295 KB contract |
| Near duplicate | 10 | The right page among look-alikes |
| Permissions | 9 | The same question from people with different access, in pairs |
| Unanswerable | 8 | A question whose sibling has an answer |

Permission questions run as a named user (`asker`), and a `forbidden` value in an answer fails it outright. Near duplicate, permissions and unanswerable pass almost always: they're kept small, as guards against regressions.

### How the gold was verified

1. **Computed, not transcribed.** Gold answers come from the world's truth. A count or a sum carries its derivation.
2. **Proved from stored text.** `eval/hard/check.py` reads the database and fails unless every quote behind every step is in its document word for word, every derivation recomputes, every asker may read their gold documents, each `forbidden` value is hidden from its asker, and what makes a question unanswerable is true (the reference is in no document, the field is missing from the page that would hold it). It also checks that the database still matches `manifest.json`.
3. **Reviewed independently, twice.** A reviewer who hadn't written the questions worked out each answer from the documents before reading the gold.
   - The first pass covered all 214 questions of an earlier build. No generated value was wrong. It did find a duplicated question, a false premise, facts the question never asked for, hints that named their own trap, and two real-corpus questions with a defensible second answer. All were fixed before the test split was first run.
   - The world was rebuilt after that, so those verdicts don't carry over to generated values. A second pass on the final build took 60 questions: every instance of the three families added since, and one test question from each other generated family, weighted to the ones that never pass (17 of them). It also read outside the gold documents for anything that contradicts them, which the proof and the oracle can't see. No gold answer was wrong. Two questions required a fact they never asked for (fixed, see below), and one has a mild second reading (see "Limits").
   - So 57 of the 135 generated test questions were reviewed on the final build. The other 78 rest on the proof, the oracle, and a review of their family.
4. **Handed to a model.** `eval/hard/oracle.py` gives a model only a question's gold documents: Haiku 5.5 answers 128 of 131, Sonnet 5.5 129 of 131 (questions with nothing to read are skipped). Of the 45 questions the system never passes, 44 can run this way and Sonnet passes 43, so they're hard to reach, not wrong.

**Dev and test.** The generator and the mix of question families were tuned against dev only. The test split was first run for the baseline below, so its score isn't inflated by keeping questions the system happened to fail. After that run two questions' facts changed, and every run was rejudged: `perm-budget-2`'s wording, where a correct answer failed, and `chain-lead-pay`, which required naming who led when the question only asks how much. No question was dropped.

### Baseline (2026-11-07)

Haiku 5.5, quick mode, records on, default variant; Sonnet 5.5 judge, facts only (no grounding); 6 repeats of the 146 test questions. $0.25 per repeat for answers, 8 seconds per answer on average.

| Category | n | Mean | SD | Min | Max | Noise floor |
|---|---|---|---|---|---|---|
| All | 146 | 57.1% | 2.2 | 54.1% | 61.0% | ±2.0 |
| Aggregate | 27 | 30.2% | 6.4 | 22.2% | 40.7% | ±5.4 |
| Chain | 31 | 36.6% | 5.6 | 29.0% | 45.2% | ±5.0 |
| Superseded | 23 | 39.1% | 2.7 | 34.8% | 43.5% | ±5.2 |
| As of | 27 | 72.2% | 4.5 | 66.7% | 77.8% | ±5.2 |
| Real corpus | 11 | 80.3% | 3.7 | 72.7% | 81.8% | ±4.2 |
| Near duplicate | 10 | 95.0% | 8.4 | 80.0% | 100% | ±7.4 |
| Permissions | 9 | 100% | 0 | 100% | 100% | 0 |
| Unanswerable | 8 | 100% | 0 | 100% | 100% | 0 |

SD is across the 6 repeats. The noise floor is defined below. 69 questions always pass, 45 never do, and 32 flip between repeats.

### What the difficulty turned out to be

| Suspected source | Real? | Evidence |
|---|---|---|
| Chains of four or more hops | Only when a hop is easy to miss | On Harbourline they pass. Here chains score 36.6%, but it depends on the hop: following a page's own links scores 100% (`chain-dependency`, `chain-decision`), and hops that exist only in a Slack post score 0–13% (`chain-handover-oncall`, `chain-escalation`, `chain-oncall`, `chain-handover`). Pass rate falls with the gold documents needed: 81% for one, 63% for two, 51% for three, 28% for four. |
| A bigger corpus with near-duplicates | No, not alone | Near duplicates score 95.0%. Service names match exactly, so look-alike pages don't confuse hybrid search. |
| Facts spread over many documents | Yes | Aggregates score 30.2%, the lowest. One condition per document works (`agg-cause`, `agg-lead`: scan, 100%). Joins and anything needing a correction fail (`agg-owner`, `agg-impact`, `agg-renewals`, `agg-actions`: 0–10%). |
| Contradictions with superseded sources | Yes, when the newer source doesn't share the page's words | Superseded scores 39.1%. A correction naming an incident reference is found (100%); one using a project name isn't (`latest-rto`, `latest-availability`: 0%). |
| Who's asking | No | Permissions score 100%. None of the 42 answers checked for a restricted value contained one, nor did any of the 42 in the two controls. Access is a SQL filter, so this category only guards against regressions. |
| "As of" a date | Partly | As of scores 72.2%. Versioned documents with effective dates pass (100%). Reconstructing the past from Slack doesn't (`asof-rto` 8%, `asof-announced` 50%). |

Two things cut across the categories. Questions whose evidence is a post using a project name score 17%, against 65% for the rest, and those needing a rota swap 17% against 57%: the fact is there, but no search for the question's words reaches it. That's the gap an entity layer would have to close, and this set can now measure it.

The 376 failed answers in the 6 repeats:

| Reason | Answers | Share |
|---|---|---|
| A gold document never reached the model | 273 | 73% |
| The model had every gold document and still got it wrong | 52 | 14% |
| No answer at all: the forced final turn came back empty | 51 | 14% |

So retrieval and research strategy cost far more than reading (97.7% with the documents in hand). The last row is a defect, not a difficulty: 51 of 876 answers said "I wasn't able to put together an answer" with sources in hand. The NVIDIA loop retries that case and the Claude loop doesn't.

### What counts as a real difference

One run proves little: a repeat of this baseline ranged from 54.1% to 61.0%. Compare repeat sets, with `eval/repeats.py`:

- **Noise floor.** How far apart two runs of the same system land, 95% of the time. It's 1.96 standard errors of the difference, from how much each question flips between repeats. A question that always passes or always fails adds nothing.
- **Paired interval.** Each question's pass rate over its repeats is one paired observation, and a bootstrap resamples whole questions. Repeats of a question aren't independent evidence, so they aren't counted as such.
- **The rule.** A difference is **real** when it's past the noise floor **and** its 95% interval is clear of zero. Inside the floor it's **noise**. Past the floor with an interval touching zero it's **unclear**: more than random flipping, but resting on too few questions to expect it elsewhere.

The two tests answer different questions: "is this more than these questions flipping at random?" and "would it carry over to other questions of this kind?". A category needs the same test, and with fewer questions its floor is wider.

| Repeats on each side | Noise floor, all 146 | In questions |
|---|---|---|
| 1 and 1 | ±5.0 points | 7.2 |
| 3 and 3 | ±2.9 points | 4.2 |
| 6 and 6 | ±2.0 points | 2.9 |
| 3 new against the frozen 6 | ±2.4 points | 3.5 |

These come from the baseline's own flipping. `compare` uses the flipping of both sets, so a new set that flips more has a wider floor (±2.8 and ±2.9 for the two controls below). The floor checks out: splitting the 6 repeats in half every way (10 splits, 3 against 3) gave differences of 1.6 points on average and 2.7 at most, inside ±2.9.

`eval/repeats.py power` draws made-up configurations from the baseline's own pass rates and applies the rule to them:

| True gain, 3 repeats against the frozen 6 | Called better |
|---|---|
| None (the same system) | 1.3% (either way) |
| +2 points (3 questions) | 28% |
| +3 points (4 questions) | 55% |
| +4 points (6 questions) | 81% |
| +5 points (7 questions) | 95% |
| +6 points (9 questions) | 100% |

So plan on **5 points overall**, with 3 repeats. The simulation assumes a change only helps. One that also breaks questions has a wider interval and needs more, as the first control below shows.

**Controls.** Two changes with a known direction, 3 repeats each, against the 6-repeat baseline:

| Change | Pass | Difference | 95% interval | Floor | Verdict |
|---|---|---|---|---|---|
| Reranker and recency bias off (`fusion`) | 51.6% | −5.5 points | −10.6 to −0.3 | ±2.8 | worse |
| Deep mode | 76.5% | +19.4 points | +14.2 to +25.0 | ±2.9 | better |

Both land where they should, and each says something the score alone doesn't.

- **Fusion** is only just called worse, at 5.5 points, because it's a mixed change: 15 questions fell by half or more and 6 rose. A mixed change of that size is near the edge of what 3 repeats can call. Aggregate (−10.5) and as of (−9.3) are worse on their own, and near duplicate (−15, 10 questions) is unclear.
- **Deep mode** gains in every category that has room: chain +29.0, superseded +30.4, aggregate +24.1, as of +15.4. It costs 3.6 times as much ($0.91 a repeat) and takes 29 seconds an answer, not 8. The empty final turn almost disappears (1 of 438 answers). Research rounds were much of the gap, then, but not all of it: at 76.5% it's still 21 points short of the gold-documents score. Rota swaps nearly close (61% against 76% for the rest), while project-name posts don't (40% against 85%), and `chain-escalation` stays at 8%.

### Limits

- **It's mostly synthetic.** 630 of the 1,153 documents and 135 of the 146 test questions are generated, from about 35 question families, so instances of a family aren't independent. The bootstrap treats them as if they were, which makes intervals a little too narrow. The real-corpus category is the check on this.
- **Some questions overlap.** Three dev questions share an answer and evidence with a test question, because two incidents fell in one week or two services moved together (`chain-oncall-1` and `-2`, `chain-handover-1` and `-3`, `chain-handover-oncall-1` and `-3`). Nothing in the system can be tuned to one person's name, so they stay, but don't copy a dev question's specifics into a prompt. In test, four pairs across families share their answer and documents (`asof-rto-2` and `latest-rto-2`, for one), which is a further reason the intervals are a little narrow.
- **One question has a second reading.** In `chain-handover-oncall-2` the swap is dated before the rota it changes, and the rota still shows the old name. The gold takes the swap; newest-wins alone would take the rota.
- **It measures the failure modes built into it.** A gain on project names shows up here because that's modelled. Near duplicates are easy here partly because exact service names are always in the question.
- **The base isn't reproducible from the repository.** The 523 real documents come from a live org, crawled and synced at a point in time. `manifest.json` detects drift, and `check.py` fails on it, but a fresh clone has to build the copy from its own `strata` database.
- **The judge is strict about hedging.** An answer that gives the right value and then says it can't confirm it fails, by the judge's rule. Of 428 passes on questions with facts, all but one also contain every fact as a substring, so passes are safe. A sample of 16 failures with every fact as a substring had one judge error (since fixed) and one borderline hedge. Sonnet judging Haiku is same-family grading, and κ against a person hasn't been measured on this set.
- **A count of zero has nothing to quote.** `agg-count-5` (no severity 1 incidents in a quarter) rests on the world's truth alone, so `check.py` recomputes it but can't prove it from text.
- **Fact-only baseline.** Grounding and citation metrics weren't run, to keep the judge's cost down.
- **Harbourline's Slack dates are seed dates.** Its Slack documents are all titled 2026-11-01, whatever they say. One dev question (`real-asof-assessment`) depends on the leads notes winning over that date.

## Results

Decisions behind each result, and costs, are in [LOG.md](LOG.md).

### Answers on Claude Haiku 5.5, smoke set (2026-10-31)

Quick mode, 15 questions. The Haiku baseline is the native Anthropic API with no prompt caching; step 1 adds prompt caching and concurrent tool calls.

| Run | Judge | Pass | Faithfulness | Citation recall | Citation precision | Mean / p95 | $ per answer |
|---|---|---|---|---|---|---|---|
| Haiku baseline | Nemotron 3 Ultra | 14/15 | 0.940 | 0.654 | 0.990 | 6.9 / 12.3 s | 0.00148 |
| Haiku baseline | Sonnet 5.5 | 14/15 | 0.969 | 0.690 | 1.000 | 6.9 / 12.3 s | 0.00148 |
| Haiku + step 1 | Sonnet 5.5 | 14/15 | 0.982 | 0.763 | 0.973 | 5.2 / 9.7 s (p50 4.9) | 0.00111 |

Against the Nemotron answers above (citation recall 0.337, 30.7 s mean), Haiku roughly doubles citation recall and answers 4–6× faster. Step 1 changed no question's outcome. The only failure is q100, the multi-hop rota question. The full 150-question Haiku run and the Sonnet comparison runs are in [LOG.md](LOG.md#evals-and-metrics) until they're judged.

### Real files in every format (2026-11-01)

`EVAL_CORPUS=public`: Microsoft's public FY26 Q1 investor-relations packet and 2026 sustainability PDFs (fetched by `eval/public/fetch.py`, not committed), plus a CSV, a scanned page and legacy .doc/.ppt derived from them; 20 questions in `eval/questions_public.jsonl`. `parse-recall` checks, without any LLM, that each question's facts are in the stored text of its source file:

| | Facts found |
|---|---|
| Docling | 27/27 |
| Docling + vision on weak pages (27 pages, $0.0104) | 27/27 |

The earnings and outlook decks are pictures of slides; parsed as pages they OCR well enough that every targeted figure was found, but stylised text garbles, which the vision pass fixes (a garbled scanned page came back as a clean transcript).

Answers (Haiku 5.5, judged by Sonnet 5.5): Docling only 20/20 (faithfulness 0.885, citation precision 0.889), with vision 19/20 (0.886, 0.877); all three cross-format questions pass on both. Vision makes no measurable difference on these questions, so it stays off by default.

### Deep mode, Haiku vs Sonnet (2026-11-01)

150 questions, Sonnet judge: Haiku 5.5 148/150 (multi-hop 0.96, $0.0027 per answer); Sonnet 5.5 150/150 (multi-hop 1.00, $0.041; judging its own answers). Paired difference +0.013 (CI [0.000, +0.033]): not significant at 15× the cost, so Haiku stays the default. Details: [LOG.md](LOG.md#2026-11-01--step-2c-and-step-3-embeddings-parsing-every-format).

### Sufficient-context check (2026-10-31)

After a research round, Haiku 5.5 names the facts the sources don't state yet, and the answering model is told before it writes. On the 55 hardest questions (multi-hop, unanswerable, cross-org vocabulary), Sonnet judge:

| | Pass | Multi-hop | Mean / p95 latency | $ per answer |
|---|---|---|---|---|
| Off | 0.836 | 0.72 | 5.8 / 9.2 s | 0.00108 |
| On | **0.909** (paired +0.073, CI [+0.018, +0.145]) | **0.88** (+0.16, CI [+0.04, +0.32]) | 10.9 / 18.8 s | 0.00147 |

It fixed four questions and broke none; the smoke set went to 15/15. Because it doubles latency, it's on for deep mode by default and one setting away for quick mode (`SUFFICIENCY_CHECK`).

### Answers on Claude Haiku 5.5, all 150 questions (2026-10-31)

Quick mode, native Anthropic API, before prompt caching; judged by Nemotron 3 Ultra.

| Category | n | Pass | Faithfulness | Citation recall | Citation precision | Mean s |
|---|---|---|---|---|---|---|
| **All** | 150 | **0.940** | 0.955 | 0.628 | 0.982 | 5.4 (p95 8.5) |
| Alias | 12 | 1.000 | 0.948 | 0.636 | 0.983 | 4.9 |
| Cross-org | 8 | 1.000 | 0.963 | 0.675 | 1.000 | 6.3 |
| Cross-org vocabulary | 15 | 0.867 | 0.959 | 0.550 | 0.991 | 6.7 |
| List / aggregate | 18 | 1.000 | 0.963 | 0.587 | 0.985 | 6.0 |
| Multi-hop | 25 | **0.760** | 0.969 | 0.664 | 0.977 | 5.9 |
| Single fact | 30 | 1.000 | 0.939 | 0.675 | 0.962 | 4.6 |
| Superseded | 12 | 1.000 | 0.989 | 0.638 | 0.986 | 4.9 |
| Table | 15 | 1.000 | 0.971 | 0.729 | 1.000 | 4.1 |
| Unanswerable | 15 | 0.933 | 0.902 | 0.554 | 0.986 | 6.3 |

$0.00114 per answer ($0.17 for the run). The failures concentrate in multi-hop (6 of 9): answers that find the first fact and stop before the second, such as q100 (the escalation rule, but not who was on call that week). Result file: `answers-haiku-baseline-claude-haiku-5-5.json`.

### Citations: native vs typed (2026-10-31)

Smoke set, Haiku 5.5, Sonnet judge. Claude's native citations (search_result blocks) cite only near-quotes, so synthesized claims went uncited; a typed-[n] prompt that requires a citation on every list item and table row is what ships. Details in [LOG.md](LOG.md#2026-10-31-later--step-4-citations-grounding-sufficient-context).

| | Citation recall | Citation precision | Faithfulness | Pass |
|---|---|---|---|---|
| Typed [n] (step 1) | 0.763 | 0.973 | 0.982 | 14/15 |
| Native citations | 0.375 | 0.887 | 0.963 | 14/15 |
| Typed [n] + per-item rule | **0.791** | **0.990** | 0.982 | 14/15 |

### Retrieval: Contextual Retrieval and BM25 (2026-10-31)

135 answerable questions, reranked unless noted. Neither change is statistically significant here; [LOG.md](LOG.md) has why BM25 is on (D13) and Contextual Retrieval is off (D11, D55).

| | recall@5 | recall@10 | MRR |
|---|---|---|---|
| ts_rank_cd (baseline) | 0.937 | 0.967 | 0.942 |
| + Contextual Retrieval | 0.943 | 0.975 | 0.943 |
| BM25 (separate server; its own ts_rank_cd run: 0.939 / 0.970 / 0.942) | 0.936 | 0.970 | 0.948 |
| BM25, no reranker (ts_rank_cd, no reranker: 0.839 / 0.926 / 0.841) | 0.855 | 0.937 | 0.847 |

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

Reranking earns its 0.46 s: it surfaces the right document first in more cases. Recency had **no effect** at first. Uploads have no "last modified" time, so every document looked brand new. Strata now reads the date a document states about itself, and treats undated documents as 90 days old.

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

None of the evaluation questions need corpus-wide theme summaries, and the cost of building and refreshing them rarely pays off. Not built.

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

### The hard set

```bash
cd backend
export EVAL_CORPUS=hard                                  # uses strata_eval_hard, and refuses to truncate anything not named strata_eval*
.venv/bin/python -m eval.hard.questions                  # regenerate the questions from the world
.venv/bin/python -m eval.hard.build --source strata      # once: copy one org, add the world
.venv/bin/python -m eval.hard.check                      # prove every gold answer from the stored text
.venv/bin/python -m eval.hard.oracle --model claude-haiku-5-5 --judge claude-sonnet-5-5
.venv/bin/python -m eval.harness answers --name try --variant default --records \
  --chat-model claude-haiku-5-5 --judge claude-sonnet-5-5 --no-grounding --concurrency 4 --repeats 3
.venv/bin/python -m eval.repeats summary eval/results/answers-try-hard-claude-haiku-5-5-r*.json
.venv/bin/python -m eval.repeats compare --a eval/hard/baseline.json --b eval/results/answers-try-hard-claude-haiku-5-5-r*.json
.venv/bin/python -m eval.repeats power eval/hard/baseline.json --repeats 3
.venv/bin/python -m eval.repeats freeze eval/results/answers-try-hard-claude-haiku-5-5-r*.json --out eval/hard/baseline.json
```

Three repeats take about 20 minutes and cost about $0.75 for answers, plus the Sonnet judge. `eval/hard/baseline.json` holds the 6 baseline repeats as pass or fail per question. The two controls are `eval/results/hard-fusion.json` and `hard-deep.json`. Freeze a new baseline only after a change ships, and say so in [LOG.md](LOG.md). A baseline is only comparable with runs on the same corpus digest and judge, and `compare` warns when they differ.
