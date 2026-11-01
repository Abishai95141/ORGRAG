<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/hero-dark.svg">
    <img alt="Strata: agentic answers across every organization you belong to" src="docs/assets/hero-light.svg" width="100%">
  </picture>
</p>

<p align="center">
  <a href="https://github.com/Abishai95141/ORGRAG/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/Abishai95141/ORGRAG/actions/workflows/ci.yml/badge.svg"></a>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-d4501e?style=flat-square"></a>
  <img alt="Python 3.12+" src="https://img.shields.io/badge/python-3.12%2B-1b1a17?style=flat-square&logo=python&logoColor=white">
  <img alt="Next.js 16" src="https://img.shields.io/badge/Next.js-16-1b1a17?style=flat-square&logo=nextdotjs&logoColor=white">
  <img alt="Postgres 17 with pgvector" src="https://img.shields.io/badge/Postgres-17%20%C2%B7%20pgvector-1b1a17?style=flat-square&logo=postgresql&logoColor=white">
</p>

<p align="center">
  <a href="#quickstart"><b>Quickstart</b></a> ·
  <a href="#features"><b>Features</b></a> ·
  <a href="#how-it-answers"><b>How it answers</b></a> ·
  <a href="#architecture"><b>Architecture</b></a> ·
  <a href="docs/API.md"><b>API</b></a> ·
  <a href="docs/EVALUATION.md"><b>Evaluation</b></a>
</p>

Strata is a knowledge platform with retrieval-augmented generation for teams that work
across more than one organization: a consultancy and its clients, a holding company and
its portfolio, a lab and its partners. Each organization connects its own sources. One
question can then cover several organizations at once, and every citation shows which
organization it came from.

<p align="center">
  <img alt="Asking a question: the agent searches, then streams an answer with citations" src="docs/assets/demo-answer.gif" width="100%">
  <br>
  <sub>The demo data that ships with Strata. Waits are shortened in the recording; a quick answer takes about 5 seconds.</sub>
</p>

## Features

<table>
  <tr>
    <td width="50%" valign="top">
      <img src="docs/assets/icons/scope.svg" width="40" alt=""><br>
      <b>Many organizations, one question</b><br>
      People belong to several orgs, with roles. A question can span them, and each
      citation carries its org's colour. An org you don't belong to never enters retrieval.
    </td>
    <td width="50%" valign="top">
      <img src="docs/assets/icons/deep.svg" width="40" alt=""><br>
      <b>Agentic research</b><br>
      Quick mode answers in a few searches. Deep mode plans, searches, reads whole
      documents and checks its evidence before it writes. Every step streams as a trace.
    </td>
  </tr>
  <tr>
    <td valign="top">
      <img src="docs/assets/icons/layers.svg" width="40" alt=""><br>
      <b>Hybrid retrieval in Postgres</b><br>
      Vectors (pgvector) and BM25 (pg_textsearch), fused and reranked by a cross-encoder,
      then weighed by recency, admin boosts and reader votes.
    </td>
    <td valign="top">
      <img src="docs/assets/icons/table.svg" width="40" alt=""><br>
      <b>Exact answers</b><br>
      Structured records read from registers and tables, and a scan tool, for the "which"
      and "how many" questions that search alone gets wrong.
    </td>
  </tr>
  <tr>
    <td valign="top">
      <img src="docs/assets/icons/sources.svg" width="40" alt=""><br>
      <b>Six connectors</b><br>
      Upload (PDF, Office, scans with OCR), websites (JavaScript-built ones too), GitHub,
      Google Drive, Slack and Notion. Setup lists the folders and channels a credential reaches.
    </td>
    <td valign="top">
      <img src="docs/assets/icons/lock.svg" width="40" alt=""><br>
      <b>Permission-aware everywhere</b><br>
      Collections are org-wide or restricted to listed people. The allow-list is applied in
      SQL on every query, for people, API keys, Slack and MCP alike.
    </td>
  </tr>
  <tr>
    <td valign="top">
      <img src="docs/assets/icons/verified.svg" width="40" alt=""><br>
      <b>Verified answers</b><br>
      Promote a good answer with an owner and an expiry. It's found and cited ahead of
      documents until it lapses, and can't widen who sees restricted sources.
    </td>
    <td valign="top">
      <img src="docs/assets/icons/gap.svg" width="40" alt=""><br>
      <b>Knowledge gaps</b><br>
      Questions the sources couldn't answer are grouped, so admins see what's missing
      instead of guessing.
    </td>
  </tr>
  <tr>
    <td valign="top">
      <img src="docs/assets/icons/channel.svg" width="40" alt=""><br>
      <b>A Slack bot</b><br>
      Mention <code>@Strata</code> for a cited answer in the thread, with the asker's own
      permissions. A Slack Connect channel can search several organizations.
    </td>
    <td valign="top">
      <img src="docs/assets/icons/command.svg" width="40" alt=""><br>
      <b>An MCP server</b><br>
      Claude, Cursor and other assistants search, read and ask with an org API key, seeing
      exactly what the key's role sees.
    </td>
  </tr>
  <tr>
    <td valign="top">
      <img src="docs/assets/icons/audit.svg" width="40" alt=""><br>
      <b>An audit log</b><br>
      Every read of an org's data and every admin change, including questions asked from
      another org's workspace, with CSV and JSONL export.
    </td>
    <td valign="top">
      <img src="docs/assets/icons/paperclip.svg" width="40" alt=""><br>
      <b>Files in chat</b><br>
      Attach PDFs, Word files and more to a question. They're read and cited, without
      joining the org's knowledge base.
    </td>
  </tr>
</table>

Also: web search (Tavily or Brave) cited like any source, answer links that respect
permissions, agents with their own instructions and collections, Insights on usage and
cost, OpenTelemetry tracing, and light and dark themes.

## How it answers

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/trace-dark.svg">
  <img alt="Plan, search, read, reflect, answer" src="docs/assets/trace-light.svg" width="100%">
</picture>

Each org picks its model: Claude (Opus, Sonnet or Haiku) or, through the NVIDIA API,
Nemotron and other open models. Ask across organizations and the sources come back
grouped by org, each citation tinted with its colour:

<p align="center">
  <img alt="One question across two organizations, with citations from both" src="docs/assets/demo-cross-org.gif" width="100%">
  <br>
  <sub>One question across Northwind Labs and Halcyon Health (demo data; waits shortened).</sub>
</p>

<table>
  <tr>
    <td width="50%"><img alt="The Sources page of an organization" src="docs/assets/screenshot-sources.png"></td>
    <td width="50%"><img alt="A cited answer across two organizations in the dark theme" src="docs/assets/screenshot-answer-dark.png"></td>
  </tr>
  <tr>
    <td align="center"><sub>Each organization's sources feed its collections</sub></td>
    <td align="center"><sub>Light and dark themes, tuned separately</sub></td>
  </tr>
</table>

## Architecture

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/architecture-dark.svg">
  <img alt="Sources feed the worker, which indexes into Postgres; the API answers from Postgres for the web app, Slack and MCP" src="docs/assets/architecture-light.svg" width="100%">
</picture>

One Postgres is the vector index, the keyword index, the job queue and the store, so
`docker compose up` is the whole deployment. More in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

| Layer | Tech |
|---|---|
| Frontend | Next.js 16 (App Router), React 19, TypeScript, Tailwind v4, TanStack Query, Radix primitives |
| Backend | Python 3.12+, FastAPI, SQLAlchemy 2 (async), Alembic, Pydantic v2 |
| Data | PostgreSQL 17, pgvector, pg_textsearch (vectors, BM25 keyword search and the job queue) |
| Models | Anthropic Claude (adaptive thinking, structured outputs) or the NVIDIA API (Nemotron and other open models) |
| Retrieval | fastembed embeddings (local BAAI/bge-small-en-v1.5); NVIDIA Nemotron or a local cross-encoder for reranking |
| Parsing | Docling (layout, tables, OCR), LibreOffice for legacy Office files, headless Chromium for JavaScript-built sites |

## Measured

From the evaluation set of 150 questions over two organizations (single facts, multi-hop,
tables, lists, cross-org vocabulary, superseded policies and unanswerable questions).
Method and every run are in [`docs/EVALUATION.md`](docs/EVALUATION.md).

| | |
|---|---|
| Answers judged correct (quick mode, Claude Haiku) | **94%** |
| Faithfulness to the cited sources | **0.955** |
| Citation precision | **0.98** |
| Retrieval recall@5, reranked | **0.94** |
| Mean time to answer | **5.4 s** |
| Cost per answer | **about $0.001** |
| End-to-end scenario: two orgs, every endpoint, real models | **509 / 509 checks** |

## Quickstart

You need **git** and **Docker Desktop**.

```bash
git clone https://github.com/Abishai95141/ORGRAG.git
cd ORGRAG
cp .env.example .env
```

In `.env`, set `SECRET_KEY` (`openssl rand -hex 32`) and a model key: `ANTHROPIC_API_KEY`
(Claude) or `NVIDIA_API_KEY` (Nemotron, which also enables the Nemotron reranker).
Without either, Strata still searches and shows sources. Add `TAVILY_API_KEY` or
`BRAVE_API_KEY` for web search.

```bash
docker compose up -d --build                  # first build: about 10–15 minutes
docker compose exec api python -m app.seed    # demo orgs, documents and users
```

Open **http://localhost:3000** and sign in:

| Login | Password | Access |
|---|---|---|
| `demo@strata.dev` | `strata-demo` | owner of Northwind Labs and Halcyon Health |
| `sam@strata.dev` | `strata-demo` | member of Northwind (can't see its restricted Leadership collection), viewer of Halcyon |

Stop with `docker compose down` (add `-v` to delete the database). The API image (about
2.3 GB) includes Docling with its models, CPU-only PyTorch, LibreOffice and headless
Chromium, so scans are OCRed, tables stay tables, and JavaScript-built websites can be
crawled.

To try every connector with real data (a website, GitHub repositories, uploads, Drive
files, and Slack and Notion content you can post with your own tokens), see
[`sample-data/`](sample-data/README.md). It comes with 23 questions with known answers.

<details>
<summary><b>Run natively, for development</b></summary>

<br>

You need **git**, **Docker Desktop** (for Postgres only), **uv**
(`curl -LsSf https://astral.sh/uv/install.sh | sh`), **Node 22+** and **pnpm**
(`npm i -g pnpm`).

```bash
git clone https://github.com/Abishai95141/ORGRAG.git
cd ORGRAG
cp .env.example .env          # set SECRET_KEY; add ANTHROPIC_API_KEY or NVIDIA_API_KEY
make setup                    # uv sync (with local embeddings) + pnpm install
make dev-db                   # Postgres 17 + pgvector + pg_textsearch on :5432
make migrate                  # tables and indexes
make seed                     # demo data (the first run downloads a ~70 MB embedding model)
```

Optional extras. `uv sync` keeps only the extras it's given, so name them all:

- `parsing`: rich parsing with Docling (~1 GB of packages including PyTorch, ~0.5 GB of models)
- `browser`: render websites built with JavaScript (then install Chromium's headless shell)

```bash
cd backend && uv sync --extra local-embeddings --extra parsing --extra browser && uv run playwright install --only-shell chromium
```

Then start three processes, each in its own terminal from the repository root:

```bash
make api        # FastAPI on http://localhost:8000
make worker     # ingestion worker: syncs, crawls, parsing, scheduled re-syncs
make web        # Next.js on http://localhost:3000 (proxies /api to :8000)
```

Run the tests:

```bash
make test-db
TEST_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/strata_test make test
```

The end-to-end scenario ([`backend/e2e/`](backend/e2e/README.md)) drives a running API
and worker the way two real organizations would: every endpoint, every file type,
records, scan, roles, gaps, audit, MCP and the Slack bot, with real models (about ten
minutes, under a dollar).

*Postgres without Docker:* install Postgres 17 with pgvector
(macOS: `brew install postgresql@17 pgvector`), create a `strata` database, and set
`DEV_DB_URL=postgresql+asyncpg://<user>:<password>@localhost:5432/strata` for
`make migrate`, `make seed`, `make api` and `make worker`. BM25 ranking also needs
[pg_textsearch](https://github.com/timescale/pg_textsearch) (build it with
`make PG_CONFIG=$(brew --prefix postgresql@17)/bin/pg_config install`), then
`shared_preload_libraries = 'pg_textsearch'` in `postgresql.conf`, a restart, and
`python -m app.retrieval.bm25` to create the index. Without it, keyword search uses
`ts_rank_cd`, and Postgres 16 works too.

*Upgrading from Postgres 16:* a pg16 data volume won't start on pg17. Dump first, then
restore:

```bash
docker compose exec db pg_dump -U postgres -Fc strata > strata.dump   # on the old version
docker compose down && docker volume rm orgrag_pgdata                  # volume name: docker volume ls
docker compose up -d --build db
docker compose exec -T db pg_restore -U postgres -d strata --no-owner < strata.dump
make migrate                                                            # adds the BM25 index
```

After pulling a new version, run `make migrate` (or restart the `api` container, which
migrates on start).

</details>

<details>
<summary><b>Set up the Slack bot</b></summary>

<br>

Slack must reach Strata over HTTPS, so set `PUBLIC_URL` to the public address of the web
app (it proxies `/api`); for local development, a tunnel such as `cloudflared` or `ngrok`
in front of port 3000 works. Answers are written by the worker, so it must be running.

1. At [api.slack.com/apps](https://api.slack.com/apps), **Create New App → From an app
   manifest**, pick your workspace, and paste the manifest below with
   `strata.example.com` replaced by your `PUBLIC_URL` host.
2. From **Basic Information → App Credentials**, copy the Client ID, Client Secret and
   Signing Secret into `.env` as `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET` and
   `SLACK_SIGNING_SECRET`, and restart the API and worker. Slack verifies the events URL
   when you save it, so do this before (or re-save) the Event Subscriptions page.
3. In Strata, an admin opens **Settings → Slack → Connect a workspace** and approves the
   app. Several organizations can connect the same workspace, each from its own settings.
   To install into other workspaces, turn on **Manage Distribution**.
4. In Slack, invite the bot to channels (`/invite @Strata`). Unmapped channels search the
   organization that connected first; under **Settings → Slack → Channel scopes** an
   admin can point a channel at other connected organizations. The bot doesn't answer in
   channels shared with another organization (Slack Connect), whose outside members would
   read the answer; it asks people to DM it there.
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

</details>

<details>
<summary><b>Troubleshooting</b></summary>

<br>

- **Port already in use**: something else is using 3000, 8000 or 5432. Stop it, or move
  Postgres: set `DB_PORT=5433` in `.env`, and when running natively also run the make
  targets with `DEV_DB_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/strata`.
- **`SECRET_KEY` error on `docker compose up`**: `.env` is missing or `SECRET_KEY` is empty.
- **Answers say "Answer generation is off"**: set `ANTHROPIC_API_KEY` or `NVIDIA_API_KEY`
  in `.env`, then restart (`docker compose up -d`, or restart `make api`). Settings → Agent
  shows which providers are connected.
- **"The configured NVIDIA model isn't available for this key"**: some catalogue models
  aren't enabled for every NVIDIA account. Pick another model in Settings → Agent.

</details>

## Documentation

| | |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | How the pieces fit: connectors, indexing, retrieval, the agent, access control |
| [`docs/API.md`](docs/API.md) | The HTTP contract, the MCP server and the Slack bot; OpenAPI at `/api/docs` |
| [`docs/EVALUATION.md`](docs/EVALUATION.md) | The evaluation set, the method and every measured result |
| [`docs/LOG.md`](docs/LOG.md) | Every decision, why it was made and what it measured |
| [`docs/DESIGN.md`](docs/DESIGN.md) | The design language: strata, colour, type, icons and motion |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) · [`docs/RESEARCH.md`](docs/RESEARCH.md) | Where it's going, and the research behind it |

```
backend/      FastAPI app, agent, connectors, ingestion, worker, migrations, tests, e2e/
web/          Next.js app, design system, icons and illustrations
docs/         The documents above
postgres/     Postgres 17 image with pgvector and pg_textsearch
sample-data/  Test sources for every connector, with questions
```

## Credits

Strata was inspired by [Onyx](https://github.com/onyx-dot-app/onyx), the open-source AI platform for teams.

## License

[MIT](LICENSE)
