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
  - File upload: PDF, DOCX, Markdown, HTML, CSV, JSON, text; with the `parsing` extra (Docling), also Excel, PowerPoint, email and images, scanned PDFs through OCR, tables kept as tables, and page numbers on citations (legacy .doc/.ppt/.xls need LibreOffice).
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
- **A Slack bot.** Mention `@Strata` in a channel or DM it, and get a cited answer in the
  thread, with the asker's own Strata permissions. Channels only search collections the
  whole organization can see; restricted collections are searched only in DMs. A Slack
  Connect channel can search several organizations. See [Slack bot](#slack-bot).
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
- **Audit log.** Each org's admins see every read of its data — answers, searches,
  opened documents, shared answers, by people or API keys, including questions asked
  from another org's workspace — and every admin change, with CSV/JSONL export and a
  retention window (`AUDIT_RETENTION_DAYS`).
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
docker compose up -d --build                    # first build: about 10–15 minutes
docker compose exec api python -m app.seed      # demo orgs, documents and users
```

Open **http://localhost:3000** and sign in with a demo login (see below).
Stop with `docker compose down`. Add `-v` to also delete the database.

The API image (about 2 GB) includes rich parsing: Docling with its layout, table and
OCR models, CPU-only PyTorch, and LibreOffice, so PDFs keep their tables, scans are
OCRed, and Excel, PowerPoint, email and images can be uploaded.

### Option B — run natively (for development)

Prerequisites: **git**, **Docker Desktop** (for Postgres only), **uv**
(`curl -LsSf https://astral.sh/uv/install.sh | sh`), **Node 22+**, and **pnpm**
(`npm i -g pnpm`).

```bash
git clone https://github.com/Abishai95141/ORGRAG.git
cd ORGRAG
cp .env.example .env          # set SECRET_KEY; add NVIDIA_API_KEY or ANTHROPIC_API_KEY
make setup                    # uv sync (with local embeddings) + pnpm install
# Optional: rich parsing (Docling: ~1 GB of packages incl. PyTorch, ~0.5 GB of models): cd backend && uv sync --extra parsing
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

The end-to-end scenario ([backend/e2e/README.md](backend/e2e/README.md)) drives a
running API and worker the way two real organizations would: every endpoint, every file
type, records, scan, roles, gaps, audit, MCP and the Slack bot, with real models
(about ten minutes, under a dollar).

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

### Slack bot

Optional. Slack must reach Strata over HTTPS, so set `PUBLIC_URL` to the public address
of the web app (it proxies `/api`); for local development, a tunnel such as `cloudflared`
or `ngrok` in front of port 3000 works. Answers are written by the worker, so it must be
running (`make worker`, or the `worker` container).

1. At [api.slack.com/apps](https://api.slack.com/apps), **Create New App → From an app
   manifest**, pick your workspace, and paste the manifest below with
   `strata.example.com` replaced by your `PUBLIC_URL` host.
2. From **Basic Information → App Credentials**, copy the Client ID, Client Secret and
   Signing Secret into `.env` as `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET` and
   `SLACK_SIGNING_SECRET`, and restart the API and worker. Slack verifies the events URL
   when you save it, so do this before (or re-save) the Event Subscriptions page.
3. In Strata, an admin opens **Settings → Slack → Connect a workspace** and approves the
   app. Several organizations can connect the same workspace, each from its own settings.
   To install into workspaces other than the app's own, turn on **Manage Distribution**.
4. In Slack, invite the bot to channels (`/invite @Strata`). Unmapped channels search the
   organization that connected first; under **Settings → Slack → Channel scopes** an
   admin can point a channel at other connected organizations. The bot doesn't answer in
   channels shared with another organization (Slack Connect), whose outside members
   would read the answer; it asks people to DM it there.
5. People are answered once they link their Slack account (a one-time link the bot sends
   them). If your Strata accounts come from SSO or emailed invitations, set
   `SLACK_MATCH_EMAILS=true` to also answer people whose confirmed Slack email matches.

Each asker is answered with their own Strata permissions; in channels, only collections
the whole organization can read are searched.

```yaml
display_information:
  name: Strata
  description: Cited answers from your organization's knowledge
features:
  app_home:
    home_tab_enabled: false
    messages_tab_enabled: true
    messages_tab_read_only_enabled: false
  bot_user:
    display_name: Strata
    always_online: true
oauth_config:
  redirect_urls:
    - https://strata.example.com/api/slack/oauth/callback
  scopes:
    bot:
      - app_mentions:read   # mentions in channels
      - channels:read       # the channel picker in Settings → Slack
      - groups:read         # … including private channels
      - chat:write          # answers in threads
      - im:history          # direct messages to the bot
      - im:write            # the account link, when it can't be shown in the channel
      - users:read
      - users:read.email    # match the asker to their Strata account
settings:
  event_subscriptions:
    request_url: https://strata.example.com/api/slack/events
    bot_events:
      - app_mention
      - message.im
      - app_uninstalled
      - tokens_revoked
  org_deploy_enabled: false
  socket_mode_enabled: false
  token_rotation_enabled: false
```

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
backend/   FastAPI app, agent engine, connectors, ingestion, worker, migrations, tests,
           e2e/ (the end-to-end scenario)
web/       Next.js app, design system, icons and illustrations
docs/      ARCHITECTURE.md · API.md (the contract) · DESIGN.md
           EVALUATION.md (measured quality) · AUDIT.md · API_AUDIT.md
           RESEARCH.md (techniques, tools, landscape) · ROADMAP.md (the plan)
           PLAN-WEEKS-3-5.md (current build plan) · LOG.md (decisions and evals)
postgres/  Postgres 17 image with pgvector and pg_textsearch
```

## License

MIT
