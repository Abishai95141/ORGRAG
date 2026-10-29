# Strata — Architecture

Strata is a multi-organization knowledge platform with an agentic RAG engine. It is a
ground-up rebuild inspired by [Onyx](https://github.com/onyx-dot-app/onyx), keeping the
ideas that matter (connectors → chunk → embed → hybrid index, permission-filtered
retrieval, a tool-calling chat loop, streamed citations) and changing three things:

1. **Organizations are first-class.** A user belongs to many orgs, each with its own
   sources, collections, agents and settings. A single question can be scoped to
   several orgs at once; every citation carries the org it came from.
2. **The answer path is an agent, not a pipeline.** Claude plans, issues searches,
   reads documents, decides whether it has enough evidence, and only then answers.
   Every step is streamed to the UI as a visible trace.
3. **Lean infrastructure.** One Postgres (with `pgvector` + full-text search) replaces
   Vespa/OpenSearch + Redis + Celery. Jobs run from a Postgres queue
   (`FOR UPDATE SKIP LOCKED`). `docker compose up` is the whole deployment.

## What we took from Onyx

| Onyx concept | Onyx implementation | Strata |
|---|---|---|
| Connector framework | `backend/onyx/connectors/interfaces.py` — Load/Poll/Checkpointed connectors yielding `Document`s | `app/connectors/base.py` — `Connector.iter_documents()` async generator yielding `RawDocument`s; Upload, Web crawler, GitHub included |
| Indexing pipeline | Celery docfetching → docprocessing, chunk + embed into Vespa/OpenSearch | `app/ingestion/pipeline.py` — parse → heading-aware chunking → embed → upsert into `chunks` (pgvector + tsvector) |
| Hybrid search | Vespa/OpenSearch hybrid with ACL filters (`context/search/pipeline.py`) | `app/retrieval/hybrid.py` — vector (HNSW cosine) + `websearch_to_tsquery` BM25-ish rank fused with Reciprocal Rank Fusion, neighbour-chunk expansion into sections |
| Access control | `access/access.py` ACL lists on every chunk | Org scope + collection visibility (`org` / `restricted`) resolved into an allow-list of collection ids before every query |
| Document sets | `DocumentSet` | `Collection` |
| Personas ("Agents") | `Persona` table, prompt + tools + document sets | `Agent` — instructions, default collections, quick/deep mode |
| Chat loop + tools | `chat/llm_loop.py`, `tools/` (internal_search, web_search…) | `app/agent/engine.py` — Claude tool loop over `search_knowledge`, `read_document`, `list_collections` |
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
            │ Postgres 16 + pgvector         │◀──────▶│ Worker           │
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
models/                 SQLAlchemy 2.0 typed models
schemas/                Pydantic request/response models (the API contract)
api/                    Routers: auth, orgs, members, invites, collections, sources,
                        documents, search, agents, chat, insights
connectors/             base.py, upload.py, web.py, github.py, registry.py
ingestion/              parsers.py, chunker.py, pipeline.py
embeddings/             base.py, fastembed_provider.py, voyage_provider.py, factory
retrieval/              hybrid.py (vector + keyword + RRF), access.py (scope resolution)
llm/                    client.py (Anthropic async client, streaming, fallbacks)
agent/                  engine.py, tools.py, prompts.py, events.py, citations.py
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
jobs (org_id, kind, payload, status, progress)
search_events (org_id, user_id, query, latency_ms, hits) — powers Insights
```

Every row that holds tenant data has `org_id` and an index on it. `chunks` has
`embedding vector(EMBEDDING_DIM)` with an HNSW cosine index and a generated
`tsv tsvector` column with a GIN index.

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
                  read_document(ref)          — expand to neighbour chunks
                  list_collections()
                       │  model decides when evidence suffices
                       ▼
                cited answer streamed token-by-token, [n] → source cards
```

* The engine is a manual Claude tool loop (`client.beta.messages.stream`) so every
  model event can be translated into an SSE event as it happens.
* Tool results are rendered as `<source ref="n" org="…" title="…">…</source>` blocks;
  refs come from a per-answer citation registry so the same document always gets the
  same number across steps.
* Thinking uses `{"type": "adaptive", "display": "summarized"}` and is surfaced as the
  collapsible "Reasoning" line in each step.
* Refusal fallbacks are enabled (`fallbacks: "default"`, beta
  `server-side-fallback-2026-07-01`); a `refusal` stop reason is reported cleanly.
* With no `ANTHROPIC_API_KEY`, chat degrades to "retrieval only": the trace and
  sources still render and the answer explains how to configure a key.

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
app/(auth)/login, signup, invite/[token]
app/onboarding                       create first org
app/o/[org]/chat, chat/[id]           conversation + agent trace + sources rail
app/o/[org]/sources, sources/[id]     connectors, sync status, documents
app/o/[org]/collections               collections + access
app/o/[org]/agents                    agent builder
app/o/[org]/members                   members, roles, invites
app/o/[org]/insights                  usage analytics
app/o/[org]/settings                  org profile, model, retrieval settings
```
