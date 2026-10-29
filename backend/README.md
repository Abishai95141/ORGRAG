# Strata backend

FastAPI + SQLAlchemy (async) + Postgres/pgvector. The API contract is
[`docs/API.md`](../docs/API.md); the design is [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md).

## Run it

With Docker (from the repo root):

```bash
cp .env.example .env        # set SECRET_KEY, and ANTHROPIC_API_KEY for answers
make up                     # db, api, worker, web
make seed                   # demo orgs, documents and users
```

On the host (Python ≥ 3.12, [uv](https://docs.astral.sh/uv/)):

```bash
make dev-db                 # Postgres + pgvector on :5432 via Docker
cd backend
uv sync --extra local-embeddings
uv run alembic upgrade head
uv run python -m app.seed
uv run uvicorn app.main:app --reload --port 8000   # API
uv run python -m app.worker                        # ingestion worker (separate shell)
```

Point at any other Postgres with `DATABASE_URL=postgresql+asyncpg://user:pass@host:port/db`
(the `vector` extension must be installable). OpenAPI docs: <http://localhost:8000/api/docs>.

### Demo accounts (from `python -m app.seed`)

| Email | Password | Access |
|---|---|---|
| `demo@strata.dev` | `strata-demo` | Owner of **Northwind Labs** and **Halcyon Health** |
| `sam@strata.dev` | `strata-demo` | Member of Northwind (can't see the restricted *Leadership* collection), viewer of Halcyon |

Re-running the seed resets the two demo orgs.

## Configuration

All settings are environment variables (see `app/core/config.py` and `../.env.example`).

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/strata` | |
| `SECRET_KEY` | dev value | **Set in production.** Signs session JWTs. |
| `ANTHROPIC_API_KEY` | – | Without it chat runs in retrieval-only mode. |
| `LLM_MODEL` | `claude-opus-5-5` | Per-org override in org settings. |
| `LLM_FALLBACKS` | `default` | Server-side refusal fallbacks (beta `server-side-fallback-2026-07-01`). `off` disables. |
| `EMBEDDING_PROVIDER` | `fastembed` | `fastembed` (local ONNX), `voyage`, or `hash` (deterministic, tests/offline). |
| `EMBEDDING_MODEL` / `EMBEDDING_DIM` | `BAAI/bge-small-en-v1.5` / `384` | The dimension is fixed when the first migration runs. |
| `FILE_STORAGE_DIR` | `./data/files` | Uploaded files, one folder per source. |
| `WORKER_CONCURRENCY` | `2` | Job loops per worker process. |

## How a question is answered

`POST /api/orgs/{slug}/conversations/{id}/messages` persists the user message, reserves
the assistant message, and starts `agent/engine.py:AgentRun` in its own task (so it
completes and saves even if the browser disconnects). The response is an SSE stream:

1. **Scope** — `retrieval/access.py` turns the requested orgs into an allow-list of
   collection ids (membership ∩ `allow_cross_org` ∩ collection visibility).
2. **Plan** (deep mode) — a structured-output call returns 2–5 sub-questions.
3. **Tool loop** — Claude (`adaptive` thinking, summarized and streamed as `reasoning`)
   calls `search_knowledge`, `read_document` and `list_collections`. Every tool runs
   inside the allow-list. Search is hybrid: pgvector cosine + Postgres full-text, fused
   with Reciprocal Rank Fusion, capped at two chunks per document, expanded with
   neighbouring chunks.
4. **Citations** — a per-answer registry gives each document one stable `[n]`.
5. **Persist** — content, sources, trace, citation rows (for Insights) and a search event.

Quick mode: ≤ 4 model turns at the org's `quick_effort` (default `low`). Deep mode:
plan + ≤ 10 turns at `deep_effort` (default `high`). The last turn sets
`tool_choice: none` so the model must answer with what it has.

## Ingestion

Sources enqueue `sync_source` jobs in the `jobs` table. `python -m app.worker` claims
them with `FOR UPDATE SKIP LOCKED` (run as many workers as you like), streams documents
from the connector, skips unchanged content by SHA-256, parses to Markdown, chunks by
heading (~350 words, 50-word overlap), embeds, and replaces the document's chunks. A
scheduler re-queues sources with `sync_interval_minutes`, and requeues jobs orphaned by
a crashed worker. Failed jobs retry up to 3 times with backoff; connector errors
(bad URL, missing repo) fail immediately and show on the source.

Connectors live in `app/connectors/`. To add one, subclass `Connector`, implement
`iter_documents`, and register it in `registry.py`; it appears in `GET /api/connectors`.

## Tests

```bash
uv run pytest -q
```

Unit tests need nothing. Integration tests (`-m integration`) rebuild the schema with
Alembic in the database at `TEST_DATABASE_URL`
(default `postgresql+asyncpg://postgres:postgres@localhost:5432/strata_test`) and are
skipped when it isn't reachable. They cover auth, org isolation, roles, invites,
restricted collections, cross-org scoping, upload → index → search, the SSE stream and
Insights, and the Claude tool loop driven by a scripted client.
