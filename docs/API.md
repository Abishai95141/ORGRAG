# Strata API contract

All routes are under `/api`. The browser calls them through the Next.js rewrite
(`web/next.config.ts`), so the session cookie is same-origin. JSON uses `snake_case`.
IDs are UUID strings. Timestamps are ISO-8601 UTC strings.

Errors: `{"detail": "human readable message", "code": "machine_code"}` with a proper
HTTP status (400 validation, 401 unauthenticated, 403 forbidden, 404 not found,
409 conflict, 422 pydantic validation).

Auth: `POST /api/auth/login` sets an httpOnly cookie `strata_session` (JWT, 14 days).
Every other route requires it. `Authorization: Bearer <jwt>` is also accepted.

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
  created_at: string;
};

type OrgSettings = {
  model: string;               // default "claude-opus-5-5"
  quick_effort: "low" | "medium" | "high";   // default "low"
  deep_effort: "medium" | "high" | "xhigh";  // default "high"
  default_mode: "quick" | "deep";            // default "quick"
  retrieval_top_k: number;     // default 8
  allow_cross_org: boolean;    // default true — may this org's content be included in multi-org questions
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

type SourceKind = "upload" | "web" | "github";
type SourceStatus = "idle" | "queued" | "syncing" | "ready" | "error";
type Source = {
  id: string; org_id: string; collection_id: string; collection_name: string;
  kind: SourceKind; name: string;
  config: Record<string, unknown>;   // web: {url, max_pages, max_depth, include_paths?}
                                      // github: {repo: "owner/name", branch?, paths?: string[], token_set: boolean}
                                      // upload: {}
  status: SourceStatus; error: string | null;
  document_count: number; chunk_count: number;
  sync_interval_minutes: number | null;
  last_synced_at: string | null; created_at: string;
  progress: { done: number; total: number | null; message: string | null } | null;
};

type DocumentRow = {
  id: string; source_id: string; collection_id: string; title: string; url: string | null;
  mime: string; chunk_count: number; token_count: number; updated_at: string;
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
  document_id: string; chunk_id: string;
  title: string; url: string | null; snippet: string;
  org_id: string; org_slug: string; org_name: string; org_hue: number;
  collection_name: string; source_kind: SourceKind; score: number;
};

type TraceStep = {
  id: string;
  kind: "plan" | "search" | "read" | "reflect" | "answer";
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
| GET | `/orgs/{slug}/sources/{id}/documents?limit=50&offset=0&q=` | – | `{items: DocumentRow[], total}` |
| DELETE | `/orgs/{slug}/documents/{id}` `[admin]` | – | `204` |
| GET | `/orgs/{slug}/documents/{id}` | – | `DocumentRow & {content: string}` (Markdown, for the preview drawer) |

Supported upload types: `.pdf .docx .md .markdown .txt .html .htm .csv .json`.

## Search (no LLM)

`POST /orgs/{slug}/search` body
`{query, org_slugs?: string[], collection_ids?: string[], limit?: number}` →
`{results: SourceRef[], latency_ms: number}`.
`org_slugs` defaults to `[slug]`; additional orgs must be ones the caller belongs to and
that have `allow_cross_org`.

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
| GET | `/orgs/{slug}/conversations?limit=50` | – | `Conversation[]` (caller's own, newest first) |
| POST | `/orgs/{slug}/conversations` | `{agent_id?, mode?, scope_org_slugs?: string[]}` | `Conversation` |
| GET | `/orgs/{slug}/conversations/{id}` | – | `{conversation: Conversation, messages: Message[]}` |
| PATCH | `/orgs/{slug}/conversations/{id}` | `{title?, mode?, scope_org_slugs?}` | `Conversation` |
| DELETE | `/orgs/{slug}/conversations/{id}` | – | `204` |
| POST | `/orgs/{slug}/conversations/{id}/messages` | `{content, mode?, scope_org_slugs?, collection_ids?}` | **SSE stream** (below) |
| POST | `/orgs/{slug}/messages/{id}/feedback` | `{feedback: "up" \| "down" \| null}` | `204` |

The first user message sets the conversation title (truncated to 60 chars, then
refined by a short Claude call in the background when a key is configured).

### SSE stream

`Content-Type: text/event-stream`. Each event is
`event: <name>\ndata: <json>\n\n`. A `: ping` comment is sent every 15 s.

| event | data | meaning |
|---|---|---|
| `start` | `{user_message: Message, assistant_message_id: string, mode}` | persisted user message; assistant message reserved |
| `step` | `TraceStep` | a step was created **or updated** (upsert by `id`) |
| `reasoning` | `{step_id, text}` | summarized-thinking delta appended to that step's `detail.reasoning` |
| `sources` | `{sources: SourceRef[]}` | newly registered citation targets (append, unique by `ref`) |
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

## Health

`GET /api/health` → `{status: "ok", llm_configured: boolean, embedding_provider: string}`.
