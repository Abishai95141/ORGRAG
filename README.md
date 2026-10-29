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

## Quick start (Docker)

```bash
cp .env.example .env          # set SECRET_KEY (openssl rand -hex 32); add ANTHROPIC_API_KEY to enable answers
make up                       # docker compose up -d --build
make seed                     # optional demo data
```

Open http://localhost:3000. The seed creates two organizations (Northwind Labs and
Halcyon Health), each with sample documents, collections and an agent, plus two demo
users:

| Login | Password | Access |
|---|---|---|
| `demo@strata.dev` | `strata-demo` | owner of both orgs |
| `sam@strata.dev` | `strata-demo` | member of Northwind (cannot see the restricted Leadership collection), viewer of Halcyon |

Without an API key, Strata still searches and shows sources. The answer explains how
to enable the agent.

## Local development

```bash
make dev-db      # Postgres + pgvector on :5432
make migrate     # alembic upgrade head
make seed        # demo orgs and documents
make api         # FastAPI on :8000
make worker      # ingestion worker
cd web && pnpm install && pnpm dev   # Next.js on :3000, proxies /api → :8000
```

Run the tests with `make test`. Unit tests need no services. Integration tests run
against `TEST_DATABASE_URL` and are skipped if it can't be reached.

## Repository layout

```
backend/   FastAPI app, agent engine, connectors, ingestion, worker, migrations, tests
web/       Next.js app, design system, icons and illustrations
docs/      ARCHITECTURE.md · API.md (the contract) · DESIGN.md
```

## License

MIT
