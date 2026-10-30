# Strata API contract

All routes are under `/api`. The browser calls them through the Next.js rewrite
(`web/next.config.ts`), so the session cookie is same-origin. JSON uses `snake_case`.
IDs are UUID strings. Timestamps are ISO-8601 UTC strings.

Interactive reference (OpenAPI): `GET /api/docs`, schema at `/api/openapi.json`.

Errors: always `{"detail": "human readable message", "code": "machine_code", "request_id": "…"}`
with a proper HTTP status (400 bad request, 401 unauthenticated, 403 forbidden, 404 not
found, 409 conflict, 413 too large, 422 validation, 429 rate limited — with `Retry-After`,
500 internal, 502/503/504 model provider). Every response carries `X-Request-ID`; send
your own `X-Request-ID` to have it echoed.

Pagination: unbounded lists return `Page<T> = {items: T[], total, limit, offset}` and take
`?limit=&offset=`. Small bounded lists (members, collections, agents…) return plain arrays.

Auth, two ways:
- **Session** — `POST /api/auth/login` sets an httpOnly cookie `strata_session` (JWT,
  14 days). `Authorization: Bearer <jwt>` is also accepted. Login is rate limited
  (10 attempts per 15 minutes per IP + email).
- **API key** — `Authorization: Bearer strata_sk_…`. A key belongs to one org and acts as
  the admin who created it, with at most the key's role (`admin`, `member` or `viewer`).
  It can call `/api/orgs/{its slug}/…` plus `GET /auth/me`, `/connectors`, `/models`;
  anything else is 403. Keys can't manage keys. Revoked or expired keys get
  401 `invalid_api_key`.

Org-scoped routes use the org **slug**: `/api/orgs/{slug}/...`. The caller must be a
member; required role is noted as `[admin]` etc. (`owner` > `admin` > `member` > `viewer`).

---

## Shared shapes

```ts
type Role = "owner" | "admin" | "member" | "viewer";

type User = { id: string; email: string; name: string; avatar_hue: number; created_at: string };

type OrgSummary = {
  id: string; slug: string; name: string;
  hue: number;                 // 0-359, org identity colour
  role: Role;                  // caller's role
  member_count: number; document_count: number;
};

type Org = OrgSummary & {
  description: string | null;
  settings: OrgSettings;
  effective_model: string | null;  // what runs: settings.model, or the configured provider's default
  created_at: string;
};

type OrgSettings = {
  model: string;               // a model id from GET /models; default depends on configured keys
  quick_effort: "low" | "medium" | "high";   // default "low"
  deep_effort: "medium" | "high" | "xhigh";  // default "high"
  default_mode: "quick" | "deep";            // default "quick"
  retrieval_top_k: number;     // default 8
  allow_cross_org: boolean;    // default true — may this org's content be included in multi-org questions
  rerank: boolean;             // default true — cross-encoder reranking of search candidates
  recency_bias: "off" | "low" | "medium" | "high";  // default "low"
  web_search: boolean;         // default false — offer the agent the search_web tool by default
};

type Member = { user: User; role: Role; joined_at: string };
type Invite = { id: string; email: string; role: Role; token: string; expires_at: string; created_at: string; accepted_at: string | null };

type Collection = {
  id: string; org_id: string; name: string; description: string | null;
  visibility: "org" | "restricted";
  glyph: string;               // one of the illustration glyph keys, e.g. "stack", "book", "gear", "chart", "shield", "people", "code", "globe"
  document_count: number; source_count: number;
  allowed_user_ids: string[];  // only meaningful when restricted
  created_at: string;
};

type SourceKind = "upload" | "web" | "github" | "gdrive" | "slack" | "notion";
type SourceStatus = "idle" | "queued" | "syncing" | "ready" | "error";
type Source = {
  id: string; org_id: string; collection_id: string; collection_name: string;
  kind: SourceKind; name: string;
  config: Record<string, unknown>;   // web: {url, max_pages, max_depth, include_paths?}
                                      // github: {repo: "owner/name", branch?, paths?: string[], token_set}
                                      // gdrive: {folder_ids, include_shared_drives, impersonate_email?, service_account_json_set}
                                      // slack: {channels, lookback_days, token_set}
                                      // notion: {root_page_ids, token_set}
                                      // upload: {}
                                      // Secret fields (schema `"secret": true`) are write-only: send them
                                      // on create/patch, read back only `<name>_set: boolean`.
  status: SourceStatus; error: string | null;
  document_count: number; chunk_count: number;
  sync_interval_minutes: number | null;
  last_synced_at: string | null; created_at: string;
  progress: { done: number; total: number | null; message: string | null } | null;
};

type DocumentRow = {
  id: string; source_id: string; collection_id: string; title: string; url: string | null;
  mime: string; chunk_count: number; token_count: number;
  boost: number;               // -3..3, admin ranking adjustment
  hidden: boolean;             // hidden documents never appear in search or answers
  feedback_up: number; feedback_down: number;   // reader votes
  source_updated_at: string | null;             // last change in the source system (drives recency)
  updated_at: string;
};

type Agent = {
  id: string; org_id: string; name: string; description: string | null;
  instructions: string; mode: "quick" | "deep"; glyph: string;
  collection_ids: string[]; starter_prompts: string[];
  created_by: string | null; created_at: string;
};

type Conversation = {
  id: string; org_id: string; agent_id: string | null; title: string;
  scope_org_ids: string[]; mode: "quick" | "deep";
  created_at: string; updated_at: string;
};

type SourceRef = {               // a citation target
  ref: number;                   // [n] in the answer
  origin: "knowledge" | "web" | "attachment";
  document_id: string | null; chunk_id: string | null;   // knowledge only
  attachment_id: string | null;                          // attachment only
  title: string; url: string | null; snippet: string;
  org_id: string | null; org_slug: string | null; org_name: string | null; org_hue: number | null;
  collection_name: string | null; source_kind: SourceKind | null;  // knowledge only
  score: number;                 // relative to the best hit (1.0)
  updated_at: string | null;
};

type Attachment = {
  id: string; conversation_id: string | null; filename: string; mime: string;
  size_bytes: number; token_count: number; created_at: string;
};

type TraceStep = {
  id: string;
  kind: "plan" | "search" | "web" | "read" | "reflect" | "answer";
  label: string;                 // human label, e.g. "Searching 3 queries"
  status: "running" | "done" | "error";
  detail: {
    queries?: string[];
    refs?: number[];             // sources this step surfaced
    reasoning?: string;          // summarized thinking text
    plan?: { question: string; rationale: string }[];
    document_title?: string;
  };
  started_at: string; finished_at: string | null;
};

type Message = {
  id: string; conversation_id: string; role: "user" | "assistant";
  content: string;
  sources: SourceRef[];          // assistant only
  attachments: {id, filename, mime, size_bytes, token_count}[];  // user only
  trace: TraceStep[];            // assistant only
  feedback: "up" | "down" | null;
  status: "complete" | "streaming" | "error";
  created_at: string;
};
```

---

## Auth

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/auth/signup` | `{email, password, name}` | `{user}` + cookie |
| POST | `/auth/login` | `{email, password}` | `{user}` + cookie |
| POST | `/auth/logout` | – | `204` |
| GET | `/auth/me` | – | `{user, orgs: OrgSummary[]}` |

Password ≥ 8 chars. Signup email unique (409).

## Organizations

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/orgs` | – | `OrgSummary[]` |
| POST | `/orgs` | `{name, slug?, description?}` | `Org` (caller becomes owner; a default collection "General" is created) |
| GET | `/orgs/{slug}` | – | `Org` |
| PATCH | `/orgs/{slug}` `[admin]` | `{name?, description?, hue?, settings?: Partial<OrgSettings>}` | `Org` |
| DELETE | `/orgs/{slug}` `[owner]` | – | `204` |

## Members & invites

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/orgs/{slug}/members` | – | `Member[]` |
| PATCH | `/orgs/{slug}/members/{user_id}` `[admin]` | `{role}` | `Member` (cannot demote last owner → 409) |
| DELETE | `/orgs/{slug}/members/{user_id}` `[admin]` | – | `204` (self-removal allowed for any role) |
| GET | `/orgs/{slug}/invites` `[admin]` | – | `Invite[]` |
| POST | `/orgs/{slug}/invites` `[admin]` | `{email, role}` | `Invite` |
| DELETE | `/orgs/{slug}/invites/{id}` `[admin]` | – | `204` |
| GET | `/invites/{token}` | – (no auth needed) | `{org: {name, slug, hue}, email, role, expired: boolean}` |
| POST | `/invites/{token}/accept` | – | `OrgSummary` |

## Collections

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/orgs/{slug}/collections` | – | `Collection[]` (only those visible to caller) |
| POST | `/orgs/{slug}/collections` `[admin]` | `{name, description?, visibility, glyph?, allowed_user_ids?}` | `Collection` |
| PATCH | `/orgs/{slug}/collections/{id}` `[admin]` | same, partial | `Collection` |
| DELETE | `/orgs/{slug}/collections/{id}` `[admin]` | – | `204` (cascades sources/documents) |

## Sources (connectors)

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/connectors` | – | `{kind, name, description, config_schema}[]` — catalogue for the "Add source" dialog |
| GET | `/orgs/{slug}/sources` | – | `Source[]` |
| POST | `/orgs/{slug}/sources` `[admin]` | `{kind, name, collection_id, config, sync_interval_minutes?}` (github `config.token` accepted, never returned) | `Source` (sync job enqueued for web/github) |
| GET | `/orgs/{slug}/sources/{id}` | – | `Source` |
| PATCH | `/orgs/{slug}/sources/{id}` `[admin]` | `{name?, config?, sync_interval_minutes?}` | `Source` |
| DELETE | `/orgs/{slug}/sources/{id}` `[admin]` | – | `204` |
| POST | `/orgs/{slug}/sources/{id}/sync` `[admin]` | – | `Source` (status `queued`) |
| POST | `/orgs/{slug}/sources/{id}/files` `[member]` | multipart `files[]` (upload sources only; ≤ 25 MB each) | `{accepted: number, source: Source}` |
| GET | `/orgs/{slug}/sources/{id}/documents?limit=50&offset=0&q=` | – | `Page<DocumentRow>` |

Supported upload types: `.pdf .docx .md .markdown .txt .html .htm .csv .json`.

Syncs: creating a source and `POST …/sync` run a **full** sync, which also removes
documents that no longer exist upstream. Scheduled syncs are **incremental** for Drive,
Slack and Notion (only changes since the last sync, with a 30-minute overlap) and prune
against the connector's id listing where it has one; a full sync runs at least daily. A
listing that comes back empty never deletes anything (it usually means lost access).

## Documents

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/orgs/{slug}/documents?q=&collection_id=&source_id=&hidden=&sort=title\|updated\|feedback\|boost&limit=&offset=` | – | `Page<DocumentRow>` (only collections the caller can see) |
| GET | `/orgs/{slug}/documents/{id}` | – | `DocumentRow & {content: string, my_vote: "up" \| "down" \| null}` |
| PATCH | `/orgs/{slug}/documents/{id}` `[admin]` | `{boost?: -3..3, hidden?: boolean}` | `DocumentRow & {content}` |
| POST | `/orgs/{slug}/documents/{id}/feedback` | `{vote: "up" \| "down" \| null, message_id?}` | `{document_id, vote, feedback_up, feedback_down}` (one vote per person; `null` clears) |
| DELETE | `/orgs/{slug}/documents/{id}` `[admin]` | – | `204` |

Ranking: hybrid search (vector + full text, RRF) → up to 40 candidates (at most 3 per
document) reranked by a cross-encoder (NVIDIA Nemotron, or a local model) when
`settings.rerank` → score × recency (`settings.recency_bias`) × boost (×0.55–×1.45) ×
reader feedback (×0.75–×1.25). Hidden documents are filtered in SQL.

## Search (no LLM)

`POST /orgs/{slug}/search` body
`{query, org_slugs?: string[], collection_ids?: string[], limit?: number}` →
`{results: SourceRef[], latency_ms: number}`.
`org_slugs` defaults to `[slug]`; additional orgs must be ones the caller belongs to and
that have `allow_cross_org`.

## Chat attachments

Files attached to a question are parsed to text on upload and read straight into the
model's context (up to 14k characters each, 40k per message; the agent pages through
the rest with `read_document`). They are never added to the org's knowledge base.

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/orgs/{slug}/attachments` | multipart `file` (+ optional `conversation_id`); same types and size limit as uploads | `Attachment` (201) |
| GET | `/orgs/{slug}/attachments/{id}` | – | `Attachment & {content: string}` (owner only) |
| DELETE | `/orgs/{slug}/attachments/{id}` | – | `204` |
| GET | `/orgs/{slug}/conversations/{id}/attachments` | – | `Attachment[]` |

Send them with a message via `attachment_ids`; an attachment belongs to one
conversation (400 `attachment_in_use` otherwise).

## API keys `[admin, session only]`

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/orgs/{slug}/api-keys` | – | `{id, name, prefix, role, user_id, last_used_at, expires_at, created_at}[]` |
| POST | `/orgs/{slug}/api-keys` | `{name, role: "admin" \| "member" \| "viewer", expires_in_days?}` | the key plus `key: "strata_sk_…"` — shown once |
| DELETE | `/orgs/{slug}/api-keys/{id}` | – | `204` |

A key's role can't exceed its creator's. Only a SHA-256 of the key is stored.

## Agents

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/orgs/{slug}/agents` | – | `Agent[]` |
| POST | `/orgs/{slug}/agents` `[admin]` | `{name, description?, instructions, mode, glyph?, collection_ids, starter_prompts?}` | `Agent` |
| PATCH | `/orgs/{slug}/agents/{id}` `[admin]` | partial | `Agent` |
| DELETE | `/orgs/{slug}/agents/{id}` `[admin]` | – | `204` |

## Chat

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/orgs/{slug}/conversations?limit=50&offset=0` | – | `Conversation[]` (caller's own, newest first) |
| POST | `/orgs/{slug}/conversations` | `{agent_id?, mode?, scope_org_slugs?: string[]}` | `Conversation` |
| GET | `/orgs/{slug}/conversations/{id}` | – | `{conversation: Conversation, messages: Message[]}` |
| PATCH | `/orgs/{slug}/conversations/{id}` | `{title?, mode?, scope_org_slugs?}` | `Conversation` |
| DELETE | `/orgs/{slug}/conversations/{id}` | – | `204` |
| POST | `/orgs/{slug}/conversations/{id}/messages` | `{content, mode?, scope_org_slugs?, collection_ids?, attachment_ids?, web_search?}` | **SSE stream** (below), or with `?stream=false` the final `{message: Message}` as JSON |
| POST | `/orgs/{slug}/messages/{id}/feedback` | `{feedback: "up" \| "down" \| null}` | `204` |

The first user message sets the conversation title (truncated to 60 chars, then
refined by a short model call in the background when a key is configured).

`web_search` overrides the org's `settings.web_search` for one message; it only takes
effect when the server has a search API key (`TAVILY_API_KEY` or `BRAVE_API_KEY`).
With `stream=false`, a model failure returns the matching error status and code
(e.g. 502 `llm_model`, 429 `llm_rate_limited`, 503 `llm_not_configured`).

### SSE stream

`Content-Type: text/event-stream`, `Cache-Control: no-cache, no-transform` (so proxies
don't compress-buffer it). Each event is `event: <name>\ndata: <json>\n\n`. A `: ping`
comment is sent every 15 s.

| event | data | meaning |
|---|---|---|
| `start` | `{user_message: Message, assistant_message_id: string, mode}` | persisted user message; assistant message reserved |
| `step` | `TraceStep` | a step was created **or updated** (upsert by `id`) |
| `reasoning` | `{step_id, text}` | summarized-thinking delta appended to that step's `detail.reasoning` |
| `sources` | `{sources: SourceRef[]}` | newly registered citation targets (append, unique by `ref`); attached files arrive first |
| `delta` | `{text}` | answer token(s) |
| `done` | `{message: Message}` | final persisted assistant message |
| `error` | `{detail, code}` | fatal error; the assistant message is persisted with `status: "error"` |

Clients render: steps as a timeline, `sources` as cards, `delta` appended to the answer,
and `[n]` markers resolved against `sources`.

## Insights

`GET /orgs/{slug}/insights?days=30` `[admin]` →

```ts
{
  totals: { questions: number; conversations: number; documents: number; chunks: number; active_users: number };
  questions_per_day: { date: string; count: number }[];   // zero-filled, oldest first
  top_sources: { source_id: string; name: string; kind: SourceKind; citations: number }[];
  feedback: { up: number; down: number };
  recent_questions: { query: string; user_name: string; created_at: string; hits: number }[];
}
```

## Health & models

`GET /api/health` (no auth) →
`{status: "ok", version, llm_configured, llm_providers: ("anthropic"|"nvidia")[], default_model, embedding_provider, rerank_provider: string | null, web_search_provider: string | null}`.

`GET /api/models` → `{id, label, provider: "anthropic" | "nvidia", hint, available}[]` —
the models an org can pick; `available` is false when that provider has no key.
