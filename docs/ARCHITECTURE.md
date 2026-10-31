# Strata — Architecture

Strata is a multi-organization knowledge platform with an agentic RAG engine. It is a
ground-up rebuild inspired by [Onyx](https://github.com/onyx-dot-app/onyx), keeping the
ideas that matter (connectors → chunk → embed → hybrid index, permission-filtered
retrieval, a tool-calling chat loop, streamed citations) and changing three things:

1. **Organizations are first-class.** A user belongs to many orgs, each with its own
   sources, collections, agents and settings. A single question can be scoped to
   several orgs at once; every citation carries the org it came from.
2. **The answer path is an agent, not a pipeline.** The model (Claude, or an open
   model on NVIDIA's API) plans, issues searches, reads documents, decides whether it
   has enough evidence, and only then answers. Every step is streamed to the UI as a
   visible trace.
3. **Lean infrastructure.** One Postgres (with `pgvector` + full-text search) replaces
   Vespa/OpenSearch + Redis + Celery. Jobs run from a Postgres queue
   (`FOR UPDATE SKIP LOCKED`). `docker compose up` is the whole deployment.

## What we took from Onyx

| Onyx concept | Onyx implementation | Strata |
|---|---|---|
| Connector framework | `backend/onyx/connectors/interfaces.py` — Load/Poll/Checkpointed/Slim connectors yielding `Document`s | `app/connectors/base.py` — `iter_documents(progress, since)` covers load and poll; `list_ids()` is the slim listing used for pruning. Upload, Web crawler, GitHub, Google Drive, Slack, Notion |
| Pruning | Celery prune task diffs indexed ids against the slim listing | Full syncs prune by the set of ids seen; incremental syncs prune against `list_ids()`; an empty listing never deletes; a full sync runs at least daily |
| Rerank / recency / boost | Cross-encoder rerank × time decay × boost (v1–v3; current Onyx uses LLM section selection and a hard time filter instead) | `app/retrieval/hybrid.py` + `rerank.py` — top 40 candidates (≤3 per document) reranked by NVIDIA Nemotron or a local cross-encoder, × recency × admin boost × reader feedback; hidden documents filtered in SQL |
| User files | Indexed `UserFile`s; inlined when they fit | `chat_attachments` — parsed once, inlined into the question (with a budget) and paged with `read_document`; never indexed |
| Web search | `web_search` + `open_url` tools, several providers | `search_web` tool (Tavily/Brave) returning snippets; `read_document` fetches a page through an SSRF-guarded fetcher, opening on the snippet |
| API keys | Service-account users with group permissions | Per-org keys acting as their creator with a capped role (`app/core/deps.py`) |
| Indexing pipeline | Celery docfetching → docprocessing, chunk + embed into Vespa/OpenSearch | `app/ingestion/pipeline.py` — parse → heading-aware chunking → embed → upsert into `chunks` (pgvector + tsvector) |
| Hybrid search | Vespa/OpenSearch hybrid with ACL filters (`context/search/pipeline.py`) | `app/retrieval/hybrid.py` — vector (HNSW cosine) + keyword (BM25 via pg_textsearch, or `ts_rank_cd`) fused with Reciprocal Rank Fusion, neighbour-chunk expansion into sections |
| Access control | `access/access.py` ACL lists on every chunk | Org scope + collection visibility (`org` / `restricted`) resolved into an allow-list of collection ids before every query |
| Document sets | `DocumentSet` | `Collection` |
| Personas ("Agents") | `Persona` table, prompt + tools + document sets | `Agent` — instructions, default collections, quick/deep mode |
| Chat loop + tools | `chat/llm_loop.py`, `tools/` (internal_search, web_search…) | `app/agent/engine.py` — Claude tool loop over `search_knowledge`, `read_document`, `list_collections`, plus `query_records` and `scan` for complete lists |
| Deep research | `deep_research/dr_loop.py` | Deep mode: structured plan → iterative search/read/reflect → cited synthesis |
| Streaming protocol | NDJSON `Packet`s with `Placement` | Server-Sent Events with a small typed event vocabulary (see `API.md`) |
| Citations | `chat/citation_processor.py` | Global citation registry per answer; model cites `[n]`; UI resolves to source cards |
| Multi-tenancy | Schema-per-tenant + contextvar (EE/cloud) | Row-level `org_id` on every table, enforced by `OrgContext` dependency; cross-org reads only through the membership allow-list |

## System diagram

```
            ┌───────────────────────── Browser ─────────────────────────┐
            │ Next.js app (web/)  —  /api/* proxied to backend (same    │
            │ origin, httpOnly session cookie)                           │
            └───────────────┬───────────────────────────────────────────┘
                            │ JSON + SSE
            ┌───────────────▼────────────────┐        ┌──────────────────┐
            │ FastAPI (backend/app)          │        │ Anthropic API    │
            │  auth · orgs · sources · chat  │──────▶ │ claude-opus-5-5  │
            │  agent engine (SSE)            │        └──────────────────┘
            └───────┬───────────────┬────────┘
                    │ SQLAlchemy    │ enqueue jobs
            ┌───────▼───────────────▼────────┐        ┌──────────────────┐
            │ Postgres 17 + pgvector + BM25  │◀──────▶│ Worker           │
            │  orgs, users, sources, docs,   │  jobs  │  connectors →    │
            │  chunks(vector, tsvector),     │        │  parse → chunk → │
            │  conversations, jobs           │        │  embed → upsert  │
            └────────────────────────────────┘        └──────────────────┘
```

## Backend layout (`backend/app`)

```
main.py                 FastAPI app, routers, CORS, lifespan
core/config.py          Settings (pydantic-settings, env-driven)
core/db.py              Async engine + session factory
core/security.py        Argon2 password hashing, JWT session tokens
core/deps.py            current_user, OrgContext (membership + role checks)
core/audit.py           Audit log writes (data access, admin actions) and retention
models/                 SQLAlchemy 2.0 typed models
schemas/                Pydantic request/response models (the API contract)
api/                    Routers: auth, orgs, members, invites, collections, sources,
                        documents, search, agents, chat, insights, audit
connectors/             base.py, upload.py, web.py, github.py, registry.py
ingestion/              parsers.py, chunker.py, pipeline.py
embeddings/             base.py, fastembed_provider.py, voyage_provider.py, factory
retrieval/              hybrid.py (vector + keyword + RRF + scoring), rerank.py, web.py (search + safe fetch),
                        access.py (scope resolution)
llm/                    client.py (Anthropic client, provider-neutral structured calls, error mapping),
                        nvidia.py (OpenAI-compatible streaming + reranker), models.py (catalogue)
agent/                  engine.py, tools.py, prompts.py, events.py, citations.py,
                        records_tool.py (query_records), scan_tool.py (scan)
records/                tables.py (tables and field lines with offsets), values.py (typing),
                        suggest.py, extract.py (deterministic + LangExtract), query.py, jobs.py
slackbot/               client.py (Slack Web API), signing.py, mrkdwn.py, scope.py, answer.py
worker/                 __main__.py (job loop + scheduler), jobs.py
alembic/                migrations
```

## Data model

```
users ──< memberships >── organizations ──< invitations
                              │
          ┌───────────────────┼──────────────────────┬─────────────┐
     collections ──< sources ──< documents ──< chunks  agents   conversations ──< messages
          │
   collection_access (restricted collections → users)
   record_schemas ──< records (values JSONB, provenance offsets; per document)
                  └─< record_runs (schema, document → content hash, cost, error)
jobs (org_id, kind, payload, status, progress)
search_events (org_id, user_id, query, latency_ms, hits) — powers Insights
audit_access (org_id = the org read, actor, kind, query, documents) — the audit log
audit_events (org_id, actor, action, target, details)              — admin actions
slack_workspaces ──< slack_connections >── organizations
       ├──< slack_channels (channel_id → org_ids)   ├──< slack_user_links (→ users)
       └──< slack_threads (→ conversations)          slack_events (event_id dedupe)
```

Every row that holds tenant data has `org_id` and an index on it. `chunks` has
`embedding vector(EMBEDDING_DIM)` with an HNSW cosine index and a generated
`tsv tsvector` column (heading A, content B, generated context C) with a GIN index.
Keyword search matches on `tsv`, then ranks with BM25 through a pg_textsearch index
(`ix_chunks_bm25`, `app/retrieval/bm25.py`) when the server has the extension, and
with `ts_rank_cd` otherwise.

## Permission model

| Role | Can |
|---|---|
| owner | everything, incl. delete org and transfer ownership |
| admin | manage members, invites, sources, collections, agents, settings |
| member | chat, search, create personal conversations, upload to collections they can see |
| viewer | chat and search only |

A collection is `org` (every member) or `restricted` (admins + listed users).
Retrieval scope = ∪ over the requested orgs the user belongs to of the collections they
can see in that org. This allow-list of collection ids is computed once per request
(`retrieval/access.py`) and applied as a SQL filter on every search — the model never
sees anything outside it, regardless of which tool arguments it sends.

## Audit log

Each org's admins see every read of the org's data — including reads by its members
working in another org's workspace, which is what cross-org questions make possible —
and the sensitive changes made in it (`app/core/audit.py` writes, `app/api/audit.py`
reads and exports; routes in [API.md](API.md#audit-log-admin)).

* **Written, not derived.** Access rows are written as the read happens rather than
  reconstructed from messages and search events later: `search_events` belongs to the
  org the request came from and doesn't say which documents (or which orgs) a search
  returned, a member can delete the conversation that holds an answer's sources, and
  shared-answer views and document reads leave no other trace. So `audit_access` gets
  one row per request **per org whose documents it returned**, listing only that org's
  documents. Org A's log can then show a B-workspace question that read A's documents
  (actor, query, A's documents, `cross_org: true`) without anything of B's: B's
  documents are in B's row, and the origin org is stored but only ever shown as
  "another organization". `search_events` stays as it was, Insights' usage record for
  the asking org.
* **"Touched" means shown.** For an answer, everything in its citation registry —
  every document retrieved and put in front of the model, cited or not (`cited` marks
  the difference); for a search, the results; a document opened in full (REST or MCP);
  a shared answer opened with full access. The engine's only hook is one call in
  `_persist`, in the same transaction as the message. Who is asking (user, API key or
  MCP) reaches the run through a context variable set where the run is spawned
  (`chat.begin_answer`), so the engine knows nothing about keys.
* **Admin actions** (`audit_events`) are added to the request's session before it
  commits, so the event and the change commit or roll back together. Details hold
  before/after values and names, never secrets: invite and share tokens, API keys and
  connector secrets are left out by the callers, and a key-name filter strips anything
  credential-like as a backstop.
* **Snapshots.** Actor name and email, API key name and document titles are copied in;
  message and origin-org ids carry no foreign key. Revoking a key (which deletes it), a
  member leaving, deleting a conversation or deleting the other org leaves the record
  intact. Only deleting the org itself removes its log.
* **Reading at scale.** Lists are newest first with keyset pagination on
  `(created_at, id)` over `(org_id, created_at, id)` indexes; a GIN index on the
  documents column answers "who read this document"; exports stream through a
  server-side cursor in their own session, so a year of log is never in memory.
* **Retention.** The worker's scheduler deletes entries older than
  `AUDIT_RETENTION_DAYS` (default 365, 0 = keep) every minute, in batches, using BRIN
  indexes on `created_at`.

## Agentic RAG engine

```
user question
   │
   ├─ quick mode ──────────────► tool loop (≤3 steps, effort=low)
   │
   └─ deep mode ─► PLAN (structured output: sub-questions)
                       │
                       ▼
                tool loop (≤8 steps, effort=high, adaptive thinking summarized)
                  search_knowledge(queries[], collections?, orgs?, after?)
                  query_records(record_type, filters, mode) — when record types are active
                  scan(question, filter)      — every matching document, one check each
                     │   SQL filter (scope, title, edit dates, must_mention) ──▶ ≤ limit?
                     │       no ─▶ count by collection and date, "narrow it" (+1 turn)
                     │       yes ▼
                     │   Haiku per document, 8 at once ─▶ {match, evidence, answer}
                     │   quote found in the text? ─▶ its chunk registered as [n]
                  read_document(ref)          — expand to neighbour chunks
                  list_collections()
                       │  model decides when evidence suffices
                       ▼
                cited answer streamed token-by-token, [n] → source cards
```

* The engine runs a manual tool loop per provider — Claude through
  `client.beta.messages.stream`, NVIDIA models through streaming chat completions — so
  every model event becomes an SSE event as it happens. Both share one tool runner.
  An org's model picks the provider (`claude-*` → Anthropic, `vendor/model` → NVIDIA);
  if that provider has no key, the run falls back to the configured one.
* Tool results are rendered as `<source ref="n" org="…" title="…">…</source>` blocks;
  refs come from a per-answer citation registry so the same document always gets the
  same number across steps.
* Thinking uses `{"type": "adaptive", "display": "summarized"}` and is surfaced as the
  collapsible "Reasoning" line in each step.
* Refusal fallbacks are enabled (`fallbacks: "default"`, beta
  `server-side-fallback-2026-07-01`); a `refusal` stop reason is reported cleanly.
* With no `ANTHROPIC_API_KEY`, chat degrades to "retrieval only": the trace and
  sources still render and the answer explains how to configure a key.

## Structured records

Search returns the top few passages, so "which vendors…", "how many incidents…" and
"list every…" came back partial however good retrieval got (on a 60-vendor register,
26 of 50 items). Records keep those registers as typed rows the agent queries exactly.

* **Finding record types** (`records/suggest.py`, no LLM): Markdown tables and
  `Label: value` lines are read with their character offsets (`records/tables.py`). A
  field/value table that recurs in ≥ 2 documents (`fields_table`), a table whose header
  recurs (`rows_table`) or repeated labelled lines (`field_lines`) become a suggested
  schema; each field's type is inferred from its values (`records/values.py`: dates
  only with a stated or document year, identifiers like INC-2264 aren't numbers, few
  distinct values make a choice). An admin reviews, edits and activates it.
* **Extraction** (`records/extract.py`, worker job `extract_records`): table and
  labelled-line schemas are read deterministically. Schemas an admin writes for prose
  (`llm`) go through LangExtract with a Claude provider (`langextract_claude.py`): every
  extraction must be words from the document, aligned to offsets, plus a normalised
  reading (yes/no, an option, a date) used only to type those words. Unaligned
  extractions are dropped. A run per (schema, document) records the content hash, so a
  sync re-extracts only changed documents.
* **Querying** (`records/query.py`): filters by type (ranges on numbers and dates,
  case-insensitive text), counts and group-bys in SQL over JSONB, scoped to the
  caller's readable collections and skipping hidden documents. `latest_only` keeps one
  record per key from the newest document. The agent's `query_records` tool returns a
  table with a `[n]` source per row (the chunk where the key is written) and exact
  tallies of choice fields, so breakdowns aren't counted by the model. The same query
  is `POST /records/query` and the MCP `query_records` tool.

## Exhaustive scans

Records need a schema; most "which incidents blamed X / every contract that says Y"
questions have none. `scan` (`agent/scan_tool.py`, roadmap §4.2) answers them the
LOTUS/DocETL way: take every document a cheap filter matches, ask a fast model one
structured question per document, keep the matches. Slower than search (≈1 s per
document, 8 in parallel), but complete, and it says how many documents it checked.

* **Same scope as search.** The filter starts from the request's collection allow-list
  and `NOT hidden`, the rule `retrieval/hybrid.py` applies; collection names only
  narrow it (an unknown or unreadable name is an error, not "everything").
  `must_mention` terms are full-text matches on the title or any chunk (the `tsv` GIN
  index), so the prefilter costs no model call.
* **Refuse rather than truncate.** More than `limit` matching documents (default 100,
  `SCAN_MAX_DOCUMENTS` 200) checks nothing and returns the count by collection and
  edit-date range. A truncated scan's list looks complete, the failure scan exists to
  fix; a refusal costs one SQL count, and the engine gives that turn back once.
* **Grounded matches only.** Each check returns `{match, evidence, answer}`; the
  evidence must be the document's own words (folded comparison, `...` allowed between
  pieces), or the match is dropped and counted. The chunk containing the quote is
  registered in the answer's citation registry, so `[n]` opens on the passage.
* **Long documents** are read through their most relevant chunks (RRF of vector and
  keyword rank within the document, plus the opening) up to 12,000 characters.
* **Cost and connections.** Checks report usage through `structured_call(on_usage=…)`
  into the answer's `usage`, so its cost includes the scan. The read transaction is
  committed before the checks start, so no connection is held while the model reads.
* Search results that fill top-k say so and name scan, since the prompt alone wasn't
  enough (docs/LOG.md D54). The MCP `scan` tool runs the same function within the key's
  collections and logs its matches like a search.

## Tracing

`app/core/telemetry.py` exports OpenTelemetry spans over OTLP/HTTP when
`LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY` (Langfuse) or `OTEL_EXPORTER_OTLP_ENDPOINT`
(any collector) is set; otherwise tracing is a no-op.

```
chat  (agent; its own trace, session = conversation, user, org_id, status, total tokens)
 ├─ plan                     (deep mode)
 ├─ llm turn  (generation: model, input/output tokens, finish reason)
 ├─ tool search_knowledge
 │    └─ retrieve  (retriever: queries, collections, hits)
 │         └─ rerank
 └─ llm turn …
job <kind>  (worker; its own trace, org_id, job id, attempt)
```

* An answer is a root trace rather than a child of its HTTP request, because it can
  outlive the request (closing the stream doesn't stop it). FastAPI adds its own
  request spans.
* Attributes follow the GenAI semantic conventions plus Langfuse's `langfuse.*`
  names, so Langfuse shows generations, tools and sessions, and can filter by
  `metadata.org_id`.
* Text is kept out by default: spans carry timings, ids, models and token counts.
  `TRACE_CONTENT=true` adds the question, answer, tool arguments and results.
* Token usage per model is also saved on each answer (`messages.usage`) and summed per
  org in Insights, so spend per org needs no tracing backend.

## Slack

People ask from Slack (a mention in a channel, or a DM to the bot) and get a cited answer
in the thread, with the permissions they have in Strata. A workspace is installed once
(`slack_workspaces`, bot token encrypted like connector secrets) and connected to orgs by
their admins (`slack_connections`); a Slack Connect channel shared between companies maps
to several orgs (`slack_channels.org_ids`).

```
Slack ──POST /api/slack/events──▶ API: verify signature → dedupe event_id → enqueue slack_answer → 200
                                         (slack_events)        (jobs, claimed ahead of syncs)
worker: slack_answer
  1. who:   slack_user_links, else users.info email ↔ a member of a connected org
            (nobody → ephemeral link to /slack/link/{signed token})
  2. scope: place orgs (DM: all connected; channel: mapping or the default org)
            ∩ the asker's memberships; channel → org-wide collections only
  3. answer: begin_answer → run_chat → wait_for_answer, in the thread's conversation
  4. reply: placeholder → chat.update with mrkdwn, [n] → /a/{share token}?source=n
```

* **Channels are public to their members.** An answer posted in a channel is read by
  everyone there, so a channel's retrieval scope is narrowed to collections with
  `visibility = 'org'` (`org_wide_collection_ids` in `retrieval/access.py`), passed as the
  run's `collection_ids` and intersected with what the asker can read. It is the same
  SQL allow-list every search applies, so neither the model nor the question can reach a
  restricted collection from a channel, admins included. Restricted collections are
  searched only in DMs, and the reply's footer says so when it mattered.
* **Identity.** A confirmed link wins; otherwise the email Slack reports is matched on
  every question (so a changed Slack email isn't trusted forever), and only to members
  of orgs connected to the workspace. Links are made by the Strata user, signed in,
  from a one-hour signed token the bot sent only to that Slack user.
* **Multi-org channels.** Every org in a channel's scope must have connected the workspace
  (checked when mapped and again when answering); adding an org takes an admin of it.
  The asker searches only the orgs they belong to, and the footer names the others.
* **History.** One Strata conversation per thread and person. A thread keeps it only
  while nothing else was added to it: once it went on in the web app, where restricted
  collections may be in scope, the thread starts a new one so that history can't be
  carried into a channel.
* **Delivery.** The job records its progress (placeholder, finished reply, messages
  posted) in its payload, so when Slack is unreachable the queue's retry reposts the
  reply instead of running the model again.
* `app/slackbot/client.py` is the only code that calls Slack; tests replace its transport.

## Ingestion

1. `sources` row created → `jobs` row `sync_source` enqueued.
2. Worker claims it (`SKIP LOCKED`), instantiates the connector, streams `RawDocument`s.
3. Each document: content hash compared — unchanged docs are skipped.
4. Parse (PDF/DOCX/HTML/Markdown/CSV/text) → normalized Markdown.
5. Heading-aware chunking (~350 words, 50-word overlap), each chunk prefixed with its
   title + heading path for better recall.
6. Batch embed → replace chunks for that document in one transaction.
7. Progress written to the job row; the UI polls `/sources/{id}` and shows live status.
8. Scheduler re-enqueues sources whose `sync_interval_minutes` elapsed.

## Frontend layout (`web/`)

Next.js App Router, React 19, TypeScript, Tailwind v4, TanStack Query, Radix primitives.
All iconography and illustration is a hand-built SVG set in `web/src/components/icons`
and `web/src/components/illustrations` — no icon library. See `docs/DESIGN.md`.

```
app/(auth)/login, signup, invite/[token], slack/link/[token]
app/onboarding                       create first org
app/o/[org]/chat, chat/[id]           conversation + agent trace + sources rail
app/o/[org]/sources, sources/[id]     connectors, sync status, documents
app/o/[org]/collections               collections + access
app/o/[org]/agents                    agent builder
app/o/[org]/members                   members, roles, invites
app/o/[org]/insights                  usage analytics
app/o/[org]/settings                  org profile, model, retrieval settings
app/o/[org]/settings/slack            Slack workspaces and channel scopes
```
