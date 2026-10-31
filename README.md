# Strata

**Agentic answers across every organization you belong to.**

Strata is a knowledge platform with retrieval-augmented generation (RAG) for teams
that work across more than one organization: a consultancy and its clients, a holding
company and its portfolio, a lab and its partners. Each organization connects its own
sources. One question can then cover several organizations at once, and every
citation shows which organization it came from.

It is a ground-up rebuild inspired by [Onyx](https://github.com/onyx-dot-app/onyx).
See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for what was carried over and
what changed.

---

## What it does

- **Multi-organization by design.** Users belong to many orgs with roles
  (owner / admin / member / viewer). Each org has its own sources, collections,
  agents and settings. Questions can be scoped to one org or several. Data from an
  org you don't belong to never enters retrieval, and orgs can opt out of
  cross-org questions.
- **Agentic RAG on Claude or NVIDIA-hosted open models.** Each org picks its model:
  Claude (Opus, Sonnet, Haiku 5.5) or, through the NVIDIA API, Nemotron 3 Ultra and
  other open models.
  - *Quick* mode: a short loop of searches, then an answer.
  - *Deep* mode: Claude writes a research plan, runs several searches, reads whole
    documents, reflects, and then writes a cited answer.
  - Every step streams to the UI as a visible trace.
- **Hybrid retrieval in Postgres.** `pgvector` cosine search and full-text search are
  merged with Reciprocal Rank Fusion, then expanded with neighbouring chunks. An
  access allow-list (org × collection visibility) is applied in SQL on every query.
- **Ranking you can tune.**
  - A cross-encoder reranks the top candidates (NVIDIA Nemotron rerank, or a local model).
  - Recency bias prefers recently changed documents (off / low / medium / high).
  - Admins boost, lower or hide documents; readers vote sources up or down, and both feed
    into ranking.
- **Connectors.**
  - File upload: PDF, DOCX, Markdown, HTML, CSV, JSON, text.
  - Website crawler: same domain only, respects robots.txt.
  - GitHub repositories.
  - Google Drive (service account), Slack (bot token) and Notion (integration token),
    with incremental syncs.
  - Documents deleted upstream are removed from the index; an empty listing never wipes
    a source.
- **Files in chat.** Attach PDFs, Word files and more to a question; they're read into
  the answer and cited, without joining the org's knowledge base.
- **Web search** (optional, Tavily or Brave): the agent can search the public web when the
  knowledge base can't answer, and cites web pages like any other source.
- **An MCP server** at `/api/mcp`, so Claude, Cursor and other AI assistants can
  search, read and ask with an org API key, seeing exactly what the key's role sees.
  Setup snippets are under *Settings → API keys*.
- **Verified answers.** Admins promote a good answer, with an owner and an expiry; it's
  found and cited ahead of documents until it expires, and verifying can't widen who
  sees restricted sources.
- **Answer links.** Share an answer by link; each person who opens it sees it only if
  they can read every source it cites, and can request access otherwise.
- **Tracing.** OpenTelemetry traces for every answer (model turns, tokens, tools,
  retrieval) to Langfuse or any OTLP collector, and token spend per org in Insights.
- **A consistent, documented API.** Org API keys with capped roles, one error shape with
  request IDs, paginated lists, OpenAPI docs at `/api/docs`, and a non-streaming answer
  mode for scripts. See [`docs/API.md`](docs/API.md).
- **Collections with access control.** A collection is either visible to the whole
  org or restricted to listed people. The agent can only search what the person
  asking can see.
- **Agents.** Custom instructions, default collections, working style, and
  starter prompts.
- **Insights.** Question volume, most-cited sources, answer feedback, and
  recent questions.
- **Design.** A hand-built icon and illustration system, light and dark themes,
  and an editorial type system. See [`docs/DESIGN.md`](docs/DESIGN.md).

## Stack

| Layer | Tech |
|---|---|
| Frontend | Next.js 16 (App Router), React 19, TypeScript, Tailwind v4, TanStack Query, Radix primitives |
| Backend | Python 3.12+, FastAPI, SQLAlchemy 2 (async), Alembic, Pydantic v2 |
| Data | PostgreSQL 17 + pgvector + pg_textsearch (vectors, BM25 keyword search, and the job queue) |
| AI | NVIDIA API (Nemotron 3 Ultra and other open models, OpenAI-compatible) or Anthropic Claude (adaptive thinking, structured outputs, refusal fallbacks) |
| Retrieval | `fastembed` embeddings (local, BAAI/bge-small-en-v1.5); NVIDIA `nemotron-3.5-rerank-8b` or local cross-encoder reranking; Tavily/Brave web search |

## Run on a new laptop

### Option A — Docker (recommended)

Prerequisites: **git** and **Docker Desktop** (running).

```bash
git clone https://github.com/Abishai95141/ORGRAG.git
cd ORGRAG
cp .env.example .env
```

Edit `.env`:

- `SECRET_KEY`: paste the output of `openssl rand -hex 32`
- A model key — at least one of:
  - `NVIDIA_API_KEY` from build.nvidia.com (runs Nemotron 3 Ultra, and enables the
    Nemotron reranker), or
  - `ANTHROPIC_API_KEY` from console.anthropic.com (runs Claude).

  Without either, Strata runs in retrieval-only mode.
- Optional: `TAVILY_API_KEY` or `BRAVE_API_KEY` to enable the web search tool.

```bash
docker compose up -d --build                    # first build takes a few minutes
docker compose exec api python -m app.seed      # demo orgs, documents and users
```

Open **http://localhost:3000** and sign in with a demo login (see below).
Stop with `docker compose down`. Add `-v` to also delete the database.

### Option B — run natively (for development)

Prerequisites: **git**, **Docker Desktop** (for Postgres only), **uv**
(`curl -LsSf https://astral.sh/uv/install.sh | sh`), **Node 22+**, and **pnpm**
(`npm i -g pnpm`).

```bash
git clone https://github.com/Abishai95141/ORGRAG.git
cd ORGRAG
cp .env.example .env          # set SECRET_KEY; add NVIDIA_API_KEY or ANTHROPIC_API_KEY
make setup                    # uv sync (with local embeddings) + pnpm install
make dev-db                   # Postgres 17 + pgvector + pg_textsearch on :5432
make migrate                  # create tables and indexes
make seed                     # demo data (first run downloads the ~70 MB embedding model)
```

Then start three processes, each in its own terminal from the repo root:

```bash
make api        # FastAPI on http://localhost:8000
make worker     # ingestion worker (web crawls, GitHub syncs, scheduled re-syncs)
make web        # Next.js on http://localhost:3000 (proxies /api to :8000)
```

Run the tests:

```bash
make test-db
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/strata_test make test
```

*Postgres without Docker:* install Postgres 17 with the `pgvector` extension
(macOS: `brew install postgresql@17 pgvector`), create a `strata` database, and set
`DEV_DB_URL=postgresql+asyncpg://<user>:<password>@localhost:5432/strata` when
running `make migrate`, `make seed`, `make api` and `make worker`. BM25 keyword
ranking also needs [pg_textsearch](https://github.com/timescale/pg_textsearch)
(build it with `make PG_CONFIG=$(brew --prefix postgresql@17)/bin/pg_config install`),
`shared_preload_libraries = 'pg_textsearch'` in `postgresql.conf`, a restart, and
`python -m app.retrieval.bm25` to create the index. Without it, keyword search uses
`ts_rank_cd`; Postgres 16 still works that way.

*Upgrading to Postgres 17:* the Docker database moved from pg16 to pg17, and a
pg16 data volume won't start on pg17. Dump before pulling, then restore:

```bash
docker compose exec db pg_dump -U postgres -Fc strata > strata.dump   # on the old version
docker compose down && docker volume rm orgrag_pgdata                  # volume name: docker volume ls
docker compose up -d --build db
docker compose exec -T db pg_restore -U postgres -d strata --no-owner < strata.dump
make migrate                                                            # adds the BM25 index
```

### Demo logins

The seed creates two organizations (Northwind Labs and Halcyon Health), each with
sample documents, collections and an agent, plus two demo users:

| Login | Password | Access |
|---|---|---|
| `demo@strata.dev` | `strata-demo` | owner of both orgs |
| `sam@strata.dev` | `strata-demo` | member of Northwind (cannot see the restricted Leadership collection), viewer of Halcyon |

Without a model API key, Strata still searches and shows sources, and the answer
explains how to enable the agent.

If you already ran Strata before this version, run `make migrate` (or restart the
`api` container, which migrates on start) to add the new tables.

### Troubleshooting

- **Port already in use**: something else is using 3000, 8000 or 5432. Stop it, or
  move Postgres: set `DB_PORT=5433` in `.env`, and for Option B also run the make
  targets with `DEV_DB_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/strata`.
- **`SECRET_KEY` error on `docker compose up`**: `.env` is missing or `SECRET_KEY`
  is empty.
- **Answers say "Answer generation is off"**: set `NVIDIA_API_KEY` or
  `ANTHROPIC_API_KEY` in `.env`, then restart (`docker compose up -d`, or restart
  `make api`). Settings → Agent shows which providers are connected.
- **"The configured NVIDIA model isn't available for this key"**: some catalogue models
  aren't enabled for every NVIDIA account. Pick another model in Settings → Agent.

## Repository layout

```
backend/   FastAPI app, agent engine, connectors, ingestion, worker, migrations, tests
web/       Next.js app, design system, icons and illustrations
docs/      ARCHITECTURE.md · API.md (the contract) · DESIGN.md
           EVALUATION.md (measured quality) · AUDIT.md · API_AUDIT.md
           RESEARCH.md (techniques, tools, landscape) · ROADMAP.md (the plan)
           PLAN-WEEKS-3-5.md (current build plan) · LOG.md (decisions and evals)
postgres/  Postgres 17 image with pgvector and pg_textsearch
```

## License

MIT
