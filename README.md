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
- **Agentic RAG with Claude.**
  - *Quick* mode: a short loop of searches, then an answer.
  - *Deep* mode: Claude writes a research plan, runs several searches, reads whole
    documents, reflects, and then writes a cited answer.
  - Every step streams to the UI as a visible trace.
- **Hybrid retrieval in Postgres.** `pgvector` cosine search and full-text search are
  merged with Reciprocal Rank Fusion, then expanded with neighbouring chunks. An
  access allow-list (org × collection visibility) is applied in SQL on every query.
- **Connectors.**
  - File upload: PDF, DOCX, Markdown, HTML, CSV, JSON, text.
  - Website crawler: same domain only, respects robots.txt.
  - GitHub repositories.
  - All three sit behind a small connector interface, so more can be added.
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
| Data | PostgreSQL 16 + pgvector (vectors, full-text search, and the job queue) |
| AI | Anthropic Claude (`claude-opus-5-5` by default) with adaptive thinking, structured outputs and server-side refusal fallbacks |
| Embeddings | `fastembed` (local, BAAI/bge-small-en-v1.5) by default; Voyage AI optional; deterministic `hash` provider for tests |

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
- `ANTHROPIC_API_KEY`: your key from console.anthropic.com. Optional: without it
  Strata runs in retrieval-only mode.

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
cp .env.example .env          # set SECRET_KEY; add ANTHROPIC_API_KEY to enable answers
make setup                    # uv sync (with local embeddings) + pnpm install
make dev-db                   # Postgres 16 + pgvector on :5432
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

*Postgres without Docker:* install Postgres 16+ with the `pgvector` extension
(macOS: `brew install postgresql@17 pgvector`), create a `strata` database, and set
`DEV_DB_URL=postgresql+asyncpg://<user>:<password>@localhost:5432/strata` when
running `make migrate`, `make seed`, `make api` and `make worker`.

### Demo logins

The seed creates two organizations (Northwind Labs and Halcyon Health), each with
sample documents, collections and an agent, plus two demo users:

| Login | Password | Access |
|---|---|---|
| `demo@strata.dev` | `strata-demo` | owner of both orgs |
| `sam@strata.dev` | `strata-demo` | member of Northwind (cannot see the restricted Leadership collection), viewer of Halcyon |

Without an API key, Strata still searches and shows sources, and the answer
explains how to enable the agent.

### Troubleshooting

- **Port already in use**: something else is using 3000, 8000 or 5432. Stop it, or
  move Postgres: set `DB_PORT=5433` in `.env`, and for Option B also run the make
  targets with `DEV_DB_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/strata`.
- **`SECRET_KEY` error on `docker compose up`**: `.env` is missing or `SECRET_KEY`
  is empty.
- **Answers say "Answer generation is off"**: set `ANTHROPIC_API_KEY` in `.env`,
  then restart (`docker compose up -d`, or restart `make api`).

## Repository layout

```
backend/   FastAPI app, agent engine, connectors, ingestion, worker, migrations, tests
web/       Next.js app, design system, icons and illustrations
docs/      ARCHITECTURE.md · API.md (the contract) · DESIGN.md
```

## License

MIT
