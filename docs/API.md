# Strata API contract

All routes are under `/api`. The browser calls them through the Next.js rewrite
(`web/next.config.ts`), so the session cookie is same-origin. JSON uses `snake_case`.
IDs are UUID strings. Timestamps are ISO-8601 UTC strings.

Interactive reference (OpenAPI): `GET /api/docs`, schema at `/api/openapi.json`.

Errors: always `{"detail": "human readable message", "code": "machine_code", "request_id": "…"}`
with a proper HTTP status (400 bad request, 401 unauthenticated, 403 forbidden, 404 not
found, 405 method not allowed — with an accurate `Allow`, 409 conflict, 413 too large,
422 validation, 429 rate limited — with `Retry-After`, 500 internal, 502/503/504 model
provider). This holds for framework errors too (bad JSON, unknown routes, oversize
bodies, unexpected failures). `code` is stable and meant for programs; `detail` is for
people and may change. Generic codes: `bad_request`, `unauthenticated`, `forbidden`,
`not_found`, `method_not_allowed`, `conflict`, `request_too_large`, `validation_error`,
`rate_limited`, `internal_error`; routes add specific ones (`email_taken`, `slug_taken`,
`last_owner`, `invalid_api_key`, `unsupported_file`, `file_too_large`, `too_many_files`,
`unknown_model`, `attachment_in_use`, `llm_*`…). Every response carries `X-Request-ID`;
send your own (1–64 of `A-Z a-z 0-9 . _ : -`) to have it echoed, otherwise one is generated.

A resource the caller can't see (another org's id, a restricted collection's document,
someone else's conversation or attachment) is a **404**, never a 403, so ids can't be
probed. 403 means "you can see it but your role can't do that".

Pagination: unbounded lists return `Page<T> = {items: T[], total, limit, offset}` and take
`?limit=&offset=` (`limit` ≤ 200, `offset` ≤ 10 000 000). Small bounded lists (members,
collections, agents…) return plain arrays. The audit log is the exception: it is
cursor-paginated (`{items, next_cursor, limit}`, see [Audit log](#audit-log-admin)).

Paths: a trailing slash is ignored (`/orgs/acme/agents/` = `/orgs/acme/agents`), no redirect.

Limits: JSON bodies ≤ 1 MB, multipart bodies ≤ 100 MB (413 `request_too_large`). Names are
trimmed and must be non-empty (≤ 120 chars; source names ≤ 160), descriptions ≤ 2000,
agent instructions ≤ 20 000, message content ≤ 20 000, id lists (`collection_ids`, …)
≤ 100 entries, org slug lists ≤ 20.

Auth, two ways:
- **Session** — `POST /api/auth/login` sets an httpOnly cookie `strata_session` (JWT,
  14 days). `Authorization: Bearer <jwt>` is also accepted. Login is rate limited
  (10 attempts per 15 minutes per IP + email).
- **API key** — `Authorization: Bearer strata_sk_…`. A key belongs to one org and acts as
  the admin who created it, with at most the key's role (`admin`, `member` or `viewer`).
  It can call `/api/orgs/{its slug}/…` plus `GET /auth/me` (whose `orgs` then lists only
  the key's org), `/connectors`, `/models`; anything else is 403. Search and chat stay
  inside the key's org (`org_slugs` / `scope_org_slugs` naming other orgs are ignored) and
  see only the collections the key's role can see. Keys can't manage keys, and can't
  remove their own user from the org. Revoked or expired keys — and keys whose creator
  has left the org — get 401 `invalid_api_key`.

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
  knowledge_graph: boolean;    // default false — extract an entity graph (one model call per chunk) and give the agent list_entities / explore_entity
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

type SourceKind = "upload" | "web" | "github" | "gdrive" | "slack" | "notion" | "verified";  // verified: a verified answer
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
  weak_document_count: number;       // documents with weak pages vision parsing didn't re-read
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
  parse: DocumentParse | null;                  // null: parsed without pages (text, Markdown, Word, HTML…)
};

// How a page-by-page parse went (PDFs, slides, images), from the last time the document was indexed.
type DocumentParse = {
  pages: number;
  weak_pages: number[];          // pages that may have been read poorly (see below), 1-based
  vision_pages: number[] | null; // weak pages the vision model re-read; null when that pass didn't run
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
  kind: "plan" | "scope" | "search" | "records" | "scan" | "web" | "read" | "reflect" | "answer";
  label: string;                 // human label, e.g. "Searching 3 queries", "Found 8 of 60"
  status: "running" | "done" | "error";
  detail: {
    queries?: string[];          // scan: its per-document question
    refs?: number[];             // sources this step surfaced
    reasoning?: string;          // summarized thinking text
    plan?: { question: string; rationale: string }[];
    document_title?: string;
    record_type?: string;        // records
    total?: number; scanned?: number; matched?: number;  // scan: matched the filter, checked, matches
    failed?: number; unverified?: number;  // scan: checks that failed; matches dropped (quote not in the document)
    restricted_skipped?: number; // scope: restricted collections the asker can read that a Slack channel didn't search
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
  status: "complete" | "streaming" | "error" | "stopped";
  created_at: string;
  verified_answer_id?: string | null;  // conversation detail: set when verified
  share_token?: string | null;         // conversation detail: the active permalink
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
| PATCH | `/orgs/{slug}` `[admin]` | `{name?, description?, hue?, settings?: Partial<OrgSettings>}` | `Org` (`description: null` clears it; `settings.model` must be an id from `GET /models` or the current/default model, else 422 `unknown_model`) |
| DELETE | `/orgs/{slug}` `[owner]` | – | `204` |

## Members & invites

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/orgs/{slug}/members` | – | `Member[]` |
| PATCH | `/orgs/{slug}/members/{user_id}` `[admin]` | `{role}` | `Member` (cannot demote last owner → 409) |
| DELETE | `/orgs/{slug}/members/{user_id}` `[admin]` | – | `204` (self-removal allowed for any role, but not with an API key; the member's API keys for the org and restricted-collection grants are removed too) |
| GET | `/orgs/{slug}/invites` `[admin]` | – | `Invite[]` |
| POST | `/orgs/{slug}/invites` `[admin]` | `{email, role}` | `Invite` |
| DELETE | `/orgs/{slug}/invites/{id}` `[admin]` | – | `204` |
| GET | `/invites/{token}` | – (no auth needed) | `{org: {name, slug, hue}, email, role, expired: boolean}` |
| POST | `/invites/{token}/accept` | – | `OrgSummary` (an existing membership is kept; an already-used invite is 400 `invite_expired`) |

## Collections

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/orgs/{slug}/collections` | – | `Collection[]` (only those visible to caller) |
| POST | `/orgs/{slug}/collections` `[admin]` | `{name, description?, visibility, glyph?, allowed_user_ids?}` | `Collection` (409 `name_taken`; `allowed_user_ids` must be members, else 400 `not_members`) |
| PATCH | `/orgs/{slug}/collections/{id}` `[admin]` | same, partial | `Collection` (`description: null` clears it) |

`glyph` is one of `stack book gear chart shield people code globe compass leaf` (422 otherwise;
the same list applies to agents).
| DELETE | `/orgs/{slug}/collections/{id}` `[admin]` | – | `204` (cascades sources/documents) |

## Sources (connectors)

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/connectors` | – (signed in) | `{kind, name, description, config_schema, checkable}[]` — catalogue for the "Add source" dialog |
| POST | `/orgs/{slug}/connectors/{kind}/check` `[admin]` | `{config, source_id?}` (the form's values, secrets included; with `source_id`, that source's stored secrets fill in the ones not sent) | `{identity, field, items: {id, name, kind, detail?, ready, note?}[], warnings: string[]}`: what the credentials reach (Drive shared drives and folders, Slack channels), for the form to offer as a list; picked `id`s go into `config[field]`. Nothing is saved. Only `checkable` kinds (`400 check_unsupported`); the service's refusal is `400 connector_error`, a timeout (45 s) or network failure `502 connector_unreachable` |
| GET | `/orgs/{slug}/sources` | – | `Source[]` |
| POST | `/orgs/{slug}/sources` `[admin]` | `{kind, name, collection_id, config, sync_interval_minutes?}` (github `config.token` accepted, never returned; `sync_interval_minutes` 15–43200) | `Source` (sync job enqueued for web/github) |
| GET | `/orgs/{slug}/sources/{id}` | – | `Source` |
| PATCH | `/orgs/{slug}/sources/{id}` `[admin]` | `{name?, config?, sync_interval_minutes?}` | `Source` |
| DELETE | `/orgs/{slug}/sources/{id}` `[admin]` | – | `204` |
| POST | `/orgs/{slug}/sources/{id}/sync` `[admin]` | – | `Source` (status `queued`) |
| POST | `/orgs/{slug}/sources/{id}/files` `[member]` | multipart `files[]` (upload sources only; ≤ 25 MB each, ≤ 20 per request — 400 `too_many_files`) | `{accepted: number, source: Source}` (the whole batch is checked first; a rejected batch stores nothing) |
| GET | `/orgs/{slug}/sources/{id}/documents?limit=50&offset=0&q=&weak=` | – | `Page<DocumentRow>` (`weak`: as on `/documents`) |

Supported upload types: `.pdf .docx .md .markdown .txt .html .htm .csv .json`.

Syncs: creating a source and `POST …/sync` run a **full** sync, which also removes
documents that no longer exist upstream. Scheduled syncs are **incremental** for Drive,
Slack and Notion (only changes since the last sync, with a 30-minute overlap) and prune
against the connector's id listing where it has one; a full sync runs at least daily. A
listing that comes back empty never deletes anything (it usually means lost access).

How connectors read:

- **GitHub** downloads the repository as one archive (`github.com/{repo}/archive/{ref}.tar.gz`,
  or the API's tarball with a token), so a public repository needs no API request and no
  token (GitHub allows 60 unauthenticated API requests an hour per IP, often shared).
  Markdown, text and the parseable types are indexed (under 1 MB each, not images); links
  point at the indexed commit. A private repository needs a token with read access.
- **Websites** are crawled from the start URL's links and its sitemaps (robots.txt
  `Sitemap:` lines, else `/sitemap.xml`), on the same host and under `include_paths` if
  set, up to `max_pages` and `max_depth`. A page that is a
  JavaScript app shell (scripts, almost no text) is rendered in headless Chromium when
  the backend's `browser` extra is installed; every request the page makes goes through
  the crawler's own fetcher (public addresses only, size-capped), and images, fonts and
  media aren't loaded. Without the extra, a site of shells fails the sync with what to
  install instead of indexing nothing.
- **Google Drive** reads what is shared with the service account (or, with
  domain-wide delegation, a user's drive); `folder_ids` limits it, empty is everything.
- **Slack** reads the channels the bot is in (`channels` limits it). With the
  `channels:join` scope it joins the public channels it's asked for; private ones need
  `/invite`.

## Documents

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/orgs/{slug}/documents?q=&collection_id=&source_id=&hidden=&weak=&sort=title\|updated\|feedback\|boost&limit=&offset=` | – | `Page<DocumentRow>` (only collections the caller can see; `q` is a literal substring of title or URL, ≤ 200 chars; `weak=true` keeps documents with weak pages vision parsing didn't re-read, `false` the rest) |
| GET | `/orgs/{slug}/documents/{id}` | – | `DocumentRow & {content: string, my_vote: "up" \| "down" \| null}` (each read is recorded in the [audit log](#audit-log-admin)) |
| PATCH | `/orgs/{slug}/documents/{id}` `[admin]` | `{boost?: -3..3, hidden?: boolean}` | `DocumentRow & {content, my_vote}` |
| POST | `/orgs/{slug}/documents/{id}/feedback` | `{vote: "up" \| "down" \| null, message_id?}` | `{document_id, vote, feedback_up, feedback_down}` (one vote per person; `null` clears; `message_id` must be an answer in one of the caller's own conversations, else 404) |
| DELETE | `/orgs/{slug}/documents/{id}` `[admin]` | – | `204` |

**Weak pages.** A page is weak when it yielded little text but has pictures, when
pictures cover ≥ 30% of it, or when its OCR text is debris (≥ 6% of words are stray
fragments): a scan read badly, a chart, a slide that is a picture. With
`VISION_PARSING=true` the vision model re-reads them as the file is indexed; with it off
(the default) their text may be incomplete. The web app tells admins on the document and
on its source. An unchanged file isn't parsed again, so after turning vision parsing on,
remove the document and upload or sync it again to re-read it.

Ranking: hybrid search (vector + full text, RRF) → up to 40 candidates (at most 3 per
document) reranked by a cross-encoder (NVIDIA Nemotron, or a local model) when
`settings.rerank` → score × recency (`settings.recency_bias`) × boost (×0.55–×1.45) ×
reader feedback (×0.75–×1.25). Hidden documents are filtered in SQL.

## Verified answers

An admin can promote a good answer, with an owner and an expiry. It's indexed as a
document in the chosen collection (through a hidden source of kind `verified` that
`/sources` doesn't list), so whoever can read that collection finds it in search, chat
and MCP, cited with `source_kind: "verified"` and ranked ×1.3 while valid. Past its
expiry it's hidden from search until someone re-verifies it (the worker checks every
minute). Deleting its document deletes the verified answer.

```ts
type VerifiedAnswer = {
  id: string; collection_id: string; collection_name: string; document_id: string;
  message_id: string | null;           // the chat answer it came from
  question: string; answer: string;    // Markdown; [n] markers are dropped when indexed
  sources: SourceRef[];                // what the original answer cited
  owner: Person | null; verified_by: Person | null;   // Person = {id, name, email, avatar_hue}
  verified_at: string; expires_at: string | null;      // null: never expires
  status: "active" | "expiring" | "expired";           // expiring: ≤ 14 days left
  created_at: string; updated_at: string;
};
```

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/orgs/{slug}/verified-answers?status=all\|active\|expiring\|expired&q=&collection_id=` | – | `VerifiedAnswer[]` (collections the caller can see; newest verification first) |
| POST | `/orgs/{slug}/verified-answers` `[admin]` | `{message_id?, question?, answer?, collection_id, owner_id?, expires_in_days?: 1..3650 \| null}` | `201 VerifiedAnswer` |
| GET | `/orgs/{slug}/verified-answers/{id}` | – | `VerifiedAnswer` |
| PATCH | `/orgs/{slug}/verified-answers/{id}` `[admin]` | `{question?, answer?, collection_id?, owner_id?, expires_at?: string \| null}` | `VerifiedAnswer` (re-indexed; doesn't re-verify) |
| POST | `/orgs/{slug}/verified-answers/{id}/reverify` `[admin]` | `{expires_in_days?: 1..3650 \| null}` | `VerifiedAnswer` (caller becomes the verifier; expiry restarts; back in search) |
| DELETE | `/orgs/{slug}/verified-answers/{id}` `[admin]` | – | `204` |

With `message_id`, the question defaults to the user message before it, the answer to
its text and `sources` to the sources it cited; it must be a `complete` answer in one
of the caller's own conversations (else 404 / 400 `answer_incomplete`). Without it,
`question` and `answer` are required (400 `missing_answer`). `expires_in_days` defaults
to 90; `owner_id` (default: the caller) must be a member (400 `invalid_owner`).

**Permissions.** 400 `sources_not_visible` when the answer cites another org's
documents, or a restricted collection whose readers don't include everyone who can read
the target collection — verifying can't widen who sees restricted content. Conversation
detail marks a verified answer with `verified_answer_id` on its `Message`.

## Structured records

Registers, inventories, logs and directories in a collection's documents, kept as typed
rows so "which / how many / list every" questions are answered exactly instead of from
the top few passages. A **record schema** (record type) belongs to one collection.
Admins find suggested schemas (`suggest` looks for field/value tables that recur across
documents, tables whose rows recur, and repeated `Label: value` lines; no LLM), review
and edit the fields, and activate them. Admins can also write a schema for facts stated
in prose; its records are extracted by the model with
[LangExtract](https://github.com/google/langextract) (`RECORD_MODEL`, Haiku 5.5), keeping
only values tied to the exact words they came from. In those documents, a table whose
columns name the key field and another field (by name, label, alias, or the same words:
"Notice Period (days)" → `notice_days`) is read row by row without the model, and a value
the model places under a column named for a different field is dropped. Activating extracts in the worker;
every later sync of the collection re-extracts changed documents.

Members see active schemas in collections they can read and query their records. The
agent's `query_records` tool and the MCP `query_records` tool run the same query.

```ts
type RecordField = {
  name: string;                 // ^[a-z][a-z0-9_]{0,63}$; the key in `values`
  label: string; description?: string;
  type: "text" | "number" | "date" | "boolean" | "choice";
  options?: string[] | null;    // required for "choice"
  aliases?: string[];           // other column labels this field reads from
};
type RecordSchema = {
  id: string; collection_id: string; collection_name: string;
  name: string; label: string; description: string;
  fields: RecordField[]; key_field: string;   // the field that names a record
  origin: "fields_table" | "rows_table" | "field_lines" | "llm";
  status: "suggested" | "active" | "archived";
  stats: { records?: number; documents?: number; extracted?: number; unchanged?: number;
           failed?: number; cost_usd?: number;
           samples?: Record<string, string>[] };  // samples: suggested schemas only
  extracting: boolean;          // an extraction job is queued or running
  extracted_at: string | null; created_at: string; updated_at: string;
};
type RecordRow = {
  id: string; schema_id: string; key: string;       // key: normalised key value
  values: Record<string, string | number | boolean>; // dates as YYYY-MM-DD
  provenance: Record<string, { text: string; start: number; end: number }>; // offsets into the document
  document: { id: string; title: string; updated_at: string | null };
};
```

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/orgs/{slug}/record-schemas?collection_id=` | – | `RecordSchema[]` (members: active schemas in readable collections; admins: every status) |
| GET | `/orgs/{slug}/record-schemas/{id}` | – | `RecordSchema & {failures: {document_id, title, error, extracted_at}[]}` (failures for admins only; members get `[]`) |
| POST | `/orgs/{slug}/collections/{id}/record-schemas/suggest` `[admin]` | – | `RecordSchema[]` found in the collection (saved as `suggested`; active and archived schemas are returned unchanged) |
| POST | `/orgs/{slug}/record-schemas` `[admin]` | `{collection_id, name, label, description?, fields, key_field}` | `201 RecordSchema` (origin `llm`, status `suggested`); 409 `name_taken` |
| PATCH | `/orgs/{slug}/record-schemas/{id}` `[admin]` | `{label?, description?, fields?, key_field?, status?}` | `RecordSchema`. Activating, or changing an active schema's fields or key, re-extracts every document; deactivating deletes its records. 400 `invalid_fields` |
| DELETE | `/orgs/{slug}/record-schemas/{id}` `[admin]` | – | `204` (records cascade) |
| POST | `/orgs/{slug}/record-schemas/{id}/extract?force=` `[admin]` | – | `202 {queued: true}`: changed documents only, all with `force`; 400 `schema_inactive` |
| POST | `/orgs/{slug}/records/query` | `RecordQuery` | `{total, schemas: string[], rows: RecordRow[], groups: {value, count}[]}` |
| GET | `/orgs/{slug}/records/{id}` | – | `RecordRow` (404 when its collection isn't readable or its document is hidden) |
| POST | `/orgs/{slug}/records/resolve` | `{role, of?}` | `{record_type, field, value, by: "column" \| "value", record: RecordRow}[]`: who holds a role, from columns named for it ("Change approver") and values naming it ("Tomasz Wierzbicki (Head of Security)"), across active record types you can read; `of` keeps rows mentioning a team, system or code ("PLAT") |

```ts
type RecordQuery = {
  schema: string;               // a schema name; the same name in several readable collections is queried together
  schema_id?: string;           // only this schema (one collection's record type)
  filters?: { field: string; op: Op; value?: unknown }[];   // ANDed, at most 12
  mode?: "list" | "count" | "group";                         // default list
  group_by?: string;            // mode=group: count per value of this field
  sort?: string; descending?: boolean;
  limit?: number;               // 1–300, default 100 (list mode)
  latest_only?: boolean;        // one record per key and record type: the newest document's
};
// Op by type — text: eq ne contains in exists missing; choice: eq ne in exists missing;
// number, date: eq ne lt lte gt gte exists missing; boolean: eq exists missing.
// Text and choice compare case-insensitively; dates are YYYY-MM-DD.
```

Unknown schema → 404; a field or operator that doesn't apply → 400 `invalid_query` with
a readable message. Records of hidden documents are never returned.

## Scan

For "which / every / find all" questions that no record type covers, the agent's `scan`
tool and the MCP `scan` tool check **every** document matching a filter, one cheap model
call each (`SCAN_MODEL`, Haiku 5.5, thinking off, 8 at a time), instead of the top few
passages search returns. No REST route.

* **Filter** (SQL, no top-k): the caller's readable collections (optionally fewer), never
  hidden documents; `title_contains`; `updated_after` / `updated_before` (the document's
  last edit, `YYYY-MM-DD`, inclusive); `must_mention`, up to 6 words or phrases every
  match must contain (Postgres full-text on the title and chunks, `websearch_to_tsquery`
  syntax: `"exact phrase"`, `a or b`).
* **Limit**: `limit` documents (default 100, at most `SCAN_MAX_DOCUMENTS`, 200). A filter
  matching more checks **nothing** and returns the count, split by collection and edit
  dates, so the caller narrows it; in chat that refusal gives the agent its turn back.
* **Each check** reads the question and the document (a long one: its most relevant
  chunks plus its opening, ≤ 12,000 characters) and answers `{match, evidence, answer}`.
  A match is kept only if `evidence` is the document's own words (compared with case,
  whitespace, quotes, dashes, emphasis and table pipes evened out; `...` may join pieces);
  the chunk holding the quote is the match's citation. Failed checks are counted and the
  result says it may be incomplete.
* **Cost**: in chat the checks are added to the answer's `usage` (and so its cost); MCP
  returns `cost_usd`. About $0.0001 and 1 s per document, 8 in parallel (60 documents:
  8.5 s, $0.005).
* **Audit**: an MCP scan logs its matches like a search's results (`kind: "search"`,
  query `scan: …`); a chat answer logs its sources as usual.

## Shared answers (permalinks)

A link to one answer, `{PUBLIC_URL}/a/{token}`. Opening it requires signing in, and
**every viewer's permissions are checked when they open it**: they get the answer only
if they can read every source it cites (the answer is made of those sources), and
`sources` holds only those: passages it retrieved but didn't cite aren't returned. Otherwise
they get the question and what they'd need, and can request access. Web sources are
public; a conversation attachment counts as readable only by the asker.

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/orgs/{slug}/messages/{id}/share` | – | `{token, url, created_at}` (one active link per answer; the asker's own `complete` answers only, else 404 / 400 `answer_incomplete`) |
| DELETE | `/orgs/{slug}/messages/{id}/share` | – | `204` (turns the link off for everyone; sharing again makes a new token) |
| GET | `/shared/{token}` `[session]` | – | `SharedAnswer`; `404 share_not_found` when turned off (a `full` view is recorded in the audit log of each org whose documents it shows) |
| POST | `/orgs/{slug}/collections/{id}/access-requests` | `{note?: string ≤ 500}` | `201 AccessRequest` (a pending request is returned as is; `409 already_has_access`) |
| GET | `/orgs/{slug}/access-requests?status=pending\|approved\|dismissed\|all` `[admin]` | – | `AccessRequest[]` |
| POST | `/orgs/{slug}/access-requests/{id}/approve` `[admin]` | – | `AccessRequest` (grants read access to the collection; `409 already_decided`, `409 not_a_member`) |
| POST | `/orgs/{slug}/access-requests/{id}/dismiss` `[admin]` | – | `AccessRequest` |

```ts
type SharedAnswer = {
  token: string;
  access: "full" | "restricted" | "none";  // none: not a member of the answer's org
  org: { name: string; slug: string; hue: number };
  author_name: string | null; question: string | null;   // null when access is "none"
  answer: string | null; sources: SourceRef[];             // only when access is "full"
  missing: { kind: "org" | "collection" | "attachment" | "hidden"; org_name: string;
             org_slug: string | null; collection_id: string | null; collection_name: string | null;
             requested: boolean }[];                      // what the viewer can't read
  answered_at: string | null; shared_at: string;
};
type AccessRequest = {
  id: string; collection_id: string; collection_name: string; user: Person; note: string | null;
  status: "pending" | "approved" | "dismissed"; created_at: string; decided_at: string | null;
};
```

## Slack bot

People mention `@Strata` in a channel, or message it directly, and get a cited answer in
the thread, answered as their Strata user with that user's permissions. An org admin
connects a workspace (OAuth v2); a workspace can be connected to several orgs, each by
an admin of that org. Needs `SLACK_CLIENT_ID`, `SLACK_CLIENT_SECRET` and
`SLACK_SIGNING_SECRET` (else `503 slack_not_configured`); setup is in the README.

- **Who asks.** A Slack user is the Strata user they linked (below). With
  `SLACK_MATCH_EMAILS=true` (off by default: Strata doesn't verify signup emails, so turn
  it on only when accounts come from SSO or emailed invitations), also the Strata user
  whose email matches the one Slack reports (`users.info`), when Slack has confirmed that
  email, the user belongs to this workspace (not Slack Connect) and the Strata user is in
  an org connected to it. Nobody: the bot replies, only to them (ephemeral; a DM if that
  can't be shown), with a signed, single-use link to `{PUBLIC_URL}/slack/link/{token}`
  (valid an hour) where they confirm, signed in, that the Slack account is theirs; the
  page shows the account's Slack email, not its self-chosen display name. A Slack error
  while identifying them retries the job rather than asking them to link.
- **Shared channels get no answer.** A channel shared with another organization (Slack
  Connect: `is_ext_shared`, `is_pending_ext_shared` or `is_shared` in
  `conversations.info`) has readers outside the org, so the bot tells the asker, only
  them, to ask in a DM. Slack guests in an ordinary channel aren't detected: map such
  channels to orgs whose org-wide content guests may read, or keep the bot out.
- **What it searches.** A DM: every connected org the asker belongs to. A channel: its
  mapping's orgs (in order), or unmapped, the workspace's default org (the first to
  connect; when it disconnects, unmapped channels search nothing until mapped, rather than
  passing to another org); only orgs that connected the workspace, and of those only the ones the asker
  belongs to. Non-first orgs with `allow_cross_org: false` are skipped, as in chat.
- **Channels are public to their members.** In a channel only collections with
  `visibility: "org"` are searched (an allow-list in the retrieval scope, intersected with
  what the asker can read); restricted collections are searched only in DMs. The reply
  footer says so when it left something out, and the answer's trace starts with a
  `scope` step counting them (`detail.restricted_skipped`), which knowledge gaps read.
- **The answer** runs the chat path (`stream=false`) in a Strata conversation per thread
  and person, so it's in the asker's history, Insights and citations. A follow-up in the
  thread continues that conversation, unless it went on in the web app (then a new one
  starts). The reply replaces a "Searching…" placeholder, in Slack mrkdwn, split into
  several messages when long; `[n]` link to `{PUBLIC_URL}/a/{token}?source=n`, a
  permalink for the answer that checks each reader's permissions when opened (see
  *Shared answers*). Errors are posted in the thread.

```ts
type SlackOrg = { slug: string; name: string; hue: number; role: Role };   // only orgs you belong to
type SlackWorkspace = {
  id: string; team_id: string; team_name: string; bot_user_id: string;
  connected_at: string; connected_by: Person | null;    // this org's connection
  default_org: SlackOrg | null;      // what unmapped channels search; null if you're not in it
  orgs: SlackOrg[]; other_org_count: number;   // connected orgs; others are only counted
};
type SlackChannel = {
  channel_id: string; channel_name: string | null;
  orgs: SlackOrg[]; other_org_count: number;    // in search order
  updated_by: Person | null; updated_at: string;
};
type SlackLink = { id: string; team_id: string; team_name: string; slack_user_id: string; slack_user_name: string | null; created_at: string };
```

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/orgs/{slug}/slack` `[admin]` | – | `{configured: boolean, events_url, redirect_url, workspaces: SlackWorkspace[]}` (the URLs to enter in the Slack app) |
| GET | `/orgs/{slug}/slack/install` `[admin, session only]` | – | `307` to Slack's authorize page with a signed `state` bound to the org and admin (10 minutes) |
| GET | `/slack/oauth/callback?code=&state=` | – (Slack redirects the admin's browser here) | `303` to `/o/{slug}/settings/slack?connected={team}` or `?slack_error={code}`. The browser must be signed in as the admin who started (`wrong_user`), still an admin (`not_admin`); `oauth.v2.access` errors pass through (e.g. `access_denied`, `invalid_code`); Enterprise Grid org installs are refused (`enterprise_install`). A forged or expired state is `400 invalid_state`. The bot token is stored encrypted and never returned |
| DELETE | `/orgs/{slug}/slack/workspaces/{id}` `[admin]` | – | `204`: the org stops answering there and leaves every channel's scope (a channel left with no org answers nothing rather than falling back). The last org to leave revokes the token (`auth.revoke`) and the workspace, its mappings and links are deleted |
| GET | `/orgs/{slug}/slack/workspaces/{id}/channels` `[admin]` | – | `SlackChannel[]` that search this org |
| GET | `/orgs/{slug}/slack/workspaces/{id}/bot-channels` `[admin]` | – | `{id, name, is_private, mapped}[]`: channels the bot was added to, from Slack (`502 slack_error` if Slack fails) |
| PUT | `/orgs/{slug}/slack/workspaces/{id}/channels/{channel_id}` `[admin]` | `{org_slugs: string[] (1–20, in order), channel_name?}` | `SlackChannel` |
| DELETE | `/orgs/{slug}/slack/workspaces/{id}/channels/{channel_id}` `[admin]` | – | `204`: back to the default org |
| POST | `/slack/events` | Slack Events API (signed) | `200` |
| GET | `/slack/link/{token}` `[session]` | – | `{team_id, team_name, slack_user_id, slack_user_name, slack_email, expired, used, linked: "none" \| "you" \| "other", eligible}` (`404 link_not_found` for a forged token or a disconnected workspace) |
| POST | `/slack/link/{token}` `[session]` | – | `201 SlackLink` (`200` if already yours; `400 link_expired`; `409 link_used` once the link has connected an account, even after a disconnect; `409 slack_user_linked` when linked to someone else; `403` when none of your orgs connected the workspace) |
| GET | `/slack/links` `[session]` | – | `SlackLink[]`: Slack accounts linked to you |
| DELETE | `/slack/links/{id}` `[session]` | – | `204` (the Slack user is matched by email again) |

**Channel mappings.** `channel_id` is a Slack channel id (`C…`/`G…`, else `400
invalid_channel`). Every org must be one you belong to (`400 unknown_org`) that connected
the workspace (`400 org_not_connected`). Adding an org to a channel's scope takes an admin
of that org (403); removing orgs takes an admin of an org already in the scope (403). Orgs
in the scope you don't belong to are kept, after yours. The first org in the list that
the asker belongs to holds the conversation.

**Events.** Every request must carry a valid `X-Slack-Signature` (`v0=` HMAC-SHA256 of
`v0:{timestamp}:{raw body}` with the signing secret) and an `X-Slack-Request-Timestamp`
within five minutes, else `401 invalid_signature`; nothing is read before that.
`url_verification` returns `{challenge}`. `app_mention` (in channels) and `message` in a
DM with the bot (`channel_type: "im"`) are queued as a `slack_answer` job and acknowledged
at once; bots, the bot itself, edits and other subtypes are ignored. Each `event_id` is
queued once, so Slack's retries (`X-Slack-Retry-Num`) are acknowledged without asking
again. `app_uninstalled`, and `tokens_revoked` for the bot, delete the workspace.

## Search (no LLM)

`POST /orgs/{slug}/search` body
`{query, org_slugs?: string[], collection_ids?: string[], limit?: number}` →
`{results: SourceRef[], latency_ms: number}`.
`org_slugs` defaults to `[slug]`; additional orgs must be ones the caller belongs to and
that have `allow_cross_org` (others are ignored). With an API key only the key's org is
searched, as the key's role sees it. `query` ≤ 2000 chars, `limit` 1–50.

## Knowledge graph

When `settings.knowledge_graph` is on, every sync is followed by an `extract_graph` job:
each changed document's chunks go through LightRAG's extraction prompts (vendored in
`backend/app/third_party/lightrag`), producing entities (person, team, organization,
system, policy, project, role, event, concept, other) and the relations between them.
Facts are stored per source chunk, so they disappear with the chunk when a document is
re-indexed or deleted, and every read is limited to chunks in collections the caller can
see — an entity known only from a restricted document doesn't exist for others.
Extraction uses `GRAPH_EXTRACTION_MODEL` if set, else the org's model.

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/orgs/{slug}/graph` | – | `{enabled, entities, relations, documents_extracted, documents_pending}` |
| POST | `/orgs/{slug}/graph/rebuild` `[admin]` | – | `202` + stats; queues extraction for changed documents (400 `graph_disabled` / `llm_not_configured`) |
| GET | `/orgs/{slug}/graph/entities?type=&q=&limit=&offset=` | – | `Page<{id, name, type, description, document_ids}>`; `q` matches names, descriptions and relation text |
| GET | `/orgs/{slug}/graph/entities/{id}` | – | entity + `relations: {entity, keywords, description}[]`, `relation_count` |

The agent's graph tools return the full set and its count ("27 entities match … This is
the complete list"), unlike search's top-k; each line cites the documents behind it.

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

## MCP server

`POST /api/mcp` is a [Model Context Protocol](https://modelcontextprotocol.io) server
(streamable HTTP, stateless), built on the official MCP Python SDK, so Claude Code,
Claude Desktop, Cursor and other MCP clients can use Strata as a tool. Authenticate
with an org API key: `Authorization: Bearer strata_sk_…`. Session cookies are not
accepted. Each call acts exactly like the REST API with that key: its creator, in its
org, with at most its role. A missing or invalid key gets `401` with a
`WWW-Authenticate: Bearer` challenge.

```bash
claude mcp add --transport http strata https://strata.example.com/api/mcp \
  --header "Authorization: Bearer strata_sk_…"
```

| Tool | Arguments | Returns |
|---|---|---|
| `search` | `query`, `collection_ids?`, `limit?` (1–20, default 8) | best passage per document: `ref, title, organization, collection, url, snippet, document_id` (no LLM; logged like `/search`) |
| `read_document` | `document_id`, `page?` | `{title, organization, collection, url, page, pages, content}` in ~14k-character pages; hidden and out-of-scope documents are "No such document" |
| `list_collections` | – | the collections the key can read, with document counts |
| `list_record_types` | – | the active record types the key can query: name, label, collection, fields, record count |
| `query_records` | `record_type`, `filters?`, `mode?`, `group_by?`, `sort?`, `descending?`, `latest_only?`, `limit?` | the matching records (or count, or groups), each with its document; same query as `/records/query` |
| `resolve_role` | `role`, `of?` | who holds the role, with the record and its document; same lookup as `/records/resolve` |
| `scan` | `question`, `collection_ids?`, `title_contains?`, `must_mention?`, `updated_after?`, `updated_before?`, `limit?` (1–200, default 100) | `{documents_matching_filter, checked, complete, note, matches: {title, collection, url, document_id, answer, evidence}[], cost_usd}`: every matching document checked (see [Scan](#scan)); more than `limit` → nothing checked, `note` says how to narrow. Uses the org's model budget; matches logged like `search` |
| `ask` | `question`, `mode?` (`quick` \| `deep`), `collection_ids?` | `{answer, status, sources, conversation_id}`: the agent's cited answer, with only the cited sources. Uses the org's model budget and appears in the key owner's chat history |

Tools return structured content (and the same JSON as text). Anticipated failures
(bad ids, no access, model errors) come back as tool errors with a readable message.
Every tool except `ask` is annotated read-only.

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
| GET | `/orgs/{slug}/conversations?limit=50&offset=0` | – | `Page<Conversation>` (caller's own, most recently active first) |
| POST | `/orgs/{slug}/conversations` | `{agent_id?, mode?, scope_org_slugs?: string[]}` | `Conversation` |
| GET | `/orgs/{slug}/conversations/{id}` | – | `{conversation: Conversation, messages: Message[]}` |
| PATCH | `/orgs/{slug}/conversations/{id}` | `{title?, mode?, scope_org_slugs?}` | `Conversation` |
| DELETE | `/orgs/{slug}/conversations/{id}` | – | `204` |
| POST | `/orgs/{slug}/conversations/{id}/messages` | `{content, mode?, scope_org_slugs?, collection_ids?, attachment_ids?, web_search?}` | **SSE stream** (below), or with `?stream=false` the final `{message: Message}` as JSON |
| POST | `/orgs/{slug}/messages/{id}/feedback` | `{feedback: "up" \| "down" \| null}` | `204` |
| POST | `/orgs/{slug}/messages/{id}/cancel` | – | `Message`: stops an answer still being written in one of the caller's conversations; what was written so far is kept with `status: "stopped"`. Idempotent (a finished answer is returned unchanged). `409 run_not_local` if another API process is running it |

The first user message sets the conversation title (truncated to 60 chars, then
refined by a short model call in the background when a key is configured).

`web_search` overrides the org's `settings.web_search` for one message; it only takes
effect when the server has a search API key (`TAVILY_API_KEY` or `BRAVE_API_KEY`).
With `stream=false`, a model failure returns the matching error status and code
(e.g. 502 `llm_model`, 429 `llm_rate_limited`, 503 `llm_not_configured`, 504
`run_timeout` after `CHAT_RUN_TIMEOUT_SECONDS`). Closing the stream does **not** stop
the answer (it finishes and is saved); use `…/cancel` to stop it. An answer whose server
process died is marked `error` within about two minutes. Answers never contain images:
Markdown and HTML images are replaced by their alt text before the answer is saved.

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
| `stopped` | `{message: Message}` | the answer was cancelled; the message is persisted with `status: "stopped"` |

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
  // Model tokens spent by answers in this org's conversations, per model, busiest first.
  // cost_usd is at list price, priced per call; null for models with no per-token price
  // (the NVIDIA free tier, the Experiential Labs gateway).
  usage: { model: string; answers: number; calls: number; input_tokens: number; output_tokens: number; cost_usd: number | null }[];
}
```

## Knowledge gaps

`GET /orgs/{slug}/gaps?days=30&limit=50` `[admin]` → `Gaps`: questions the knowledge base
didn't answer in the last `days` (1–365, calendar days in UTC like Insights), grouped by
meaning, biggest first (`limit` 1–100 clusters).

A question counts when its answer is `complete` and
- **unanswered** — the agent searched or read the knowledge base but cites none of it (it
  abstained, said it isn't there, or cited only the web);
- **incomplete** — the sufficient-context check's last verdict in the trace (a `reflect`
  step with `detail.missing`) still names missing facts; a later check that found them
  clears it;
- **downvoted** — the asker gave it a thumbs-down;
- **no_results** — a direct search (`POST /search`, MCP `search`) found nothing;
- **restricted_in_channel** — would be unanswered or incomplete, but was asked in a
  Slack channel that left out restricted collections the asker can read (its trace's
  `scope` step). Channels search org-wide collections only, so the answer may be in
  one the asker would have searched in a DM: it doesn't count as unanswered or
  incomplete, and its coverage check's missing facts aren't listed. Asked where nothing
  was left out (a DM, the web app, or by someone who reads no restricted collection),
  the same question is unanswered as usual.

Answers that cite a file attached to the conversation, small talk (no search), and
failed or stopped runs never count; chat questions are counted from their answers, not
from their search events. The 500 most recent signals of each kind are considered
(`truncated` says so).

Clustering is deterministic and uses no LLM: identical questions (case, spacing and
end punctuation folded) are merged and weighted, embedded with the org's embedding
model (cached in-process per text), then grouped by weighted average-linkage on cosine
similarity until no two groups average `similarity` or more (`GAP_SIMILARITY`; default
0.8 for fastembed, 0.7 for Voyage, 0.5 for the hash embedder). A cluster's `label` is
its most central question. 503 `embedding_error` when the embedding model is down.

Open and fulfilled document requests attach to the cluster their question (or title)
is most similar to, above the same threshold; a request fulfilled after the cluster's
last question marks it `addressed`, and addressed clusters sort last. Clusters whose
every question is `restricted_in_channel` sort after the other open ones: they may not
be missing knowledge at all.

**Answered elsewhere.** `answered_elsewhere` lists (up to 3) similar questions with a
good answer in *another* org — complete, citing the knowledge base, not downvoted,
nothing reported missing, from the last 180 days, and either the caller's own or shared
by its author with a permalink (other people's private conversations are never used) —
or a live verified answer there. A
hint appears only if the caller is a member of that org, that org allows cross-org reads
(`allow_cross_org`), and the caller can read every source the answer cites (the shared-
answer rule: membership of each cited document's org, its collection visible to them,
not hidden; attachments only for the asker; a verified answer's own collection too).
It must cite a document outside this org (or be a verified answer). Otherwise nothing
about the other org is returned — not its name, questions, answers or documents. An API
key never gets hints: it's bound to its org.

```ts
type GapReason = "unanswered" | "incomplete" | "downvoted" | "no_results" | "restricted_in_channel";
type Gaps = {
  days: number; since: string;
  questions: number;          // gap questions considered
  truncated: boolean;         // more than the cap: only the most recent were clustered
  similarity: number;         // the clustering threshold used
  total_clusters: number;
  clusters: GapCluster[];
};
type GapCluster = {
  id: string;                 // a hash of the label (stable while the label is)
  label: string;              // the most central question
  count: number; askers: number;           // questions (repeats included), distinct people
  first_seen: string; last_seen: string;
  reasons: Record<GapReason, number>;      // a question can count under two reasons
  examples: string[];         // up to 5 distinct phrasings, the label first
  missing: string[];          // facts the coverage check reported missing, most reported first
  answered_elsewhere: GapHint[];
  requests: { id: string; title: string; status: DocRequestStatus; document_id: string | null;
              created_at: string; decided_at: string | null }[];
  addressed: boolean;
};
type GapHint = {
  kind: "answer" | "verified";
  org: { name: string; slug: string; hue: number };
  question: string; answer: string;        // an excerpt, citation markers removed
  similarity: number; answered_at: string;
  verified_answer_id: string | null;
  documents: { id: string; title: string; org_slug: string; collection_name: string | null;
               source_id: string }[];      // the cited documents outside this org
};
```

## Document requests

A member asks the org's admins for a document the knowledge base lacks — from an answer
that couldn't answer, or from a knowledge gap. Admins fulfil it with the document that
answers it, or dismiss it, as with access requests. Requesters follow their own.

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/orgs/{slug}/doc-requests` | `{title: string ≤ 300, note?: ≤ 2000, question?: ≤ 2000, message_id?}` | `201 DocRequest`; `200` with the open request when you already asked for the same title (case, spacing and end punctuation folded) |
| GET | `/orgs/{slug}/doc-requests?status=pending\|fulfilled\|dismissed\|all&limit=&offset=` `[admin]` | – | `Page<DocRequest>` (default `pending`; newest first) |
| GET | `/orgs/{slug}/doc-requests/mine?status=…&limit=&offset=` | – | `Page<DocRequest>`: the caller's own (default `all`) |
| POST | `/orgs/{slug}/doc-requests/{id}/fulfill` `[admin]` | `{document_id, note?: ≤ 2000}` | `DocRequest` (`404` document not in this org, `400 document_hidden`, `400 requester_cannot_read`, `409 not_a_member`, `409 already_decided`) |
| POST | `/orgs/{slug}/doc-requests/{id}/dismiss` `[admin]` | `{note?: ≤ 2000}` or no body | `DocRequest` (`409 already_decided`) |

`message_id` must be an assistant message in one of the caller's own conversations in
this org (else 404); the request's `question` is then the user message before it.
Otherwise `question` is free text, e.g. a gap's `label`. The document that fulfils a
request must belong to the org, not be hidden from search, and be in a collection the
requester can read — give them access to a restricted collection first. A request is
decided once; dismissing it lets the same thing be asked again. Deleting the document
later leaves the request fulfilled with `document: null`.

```ts
type DocRequestStatus = "pending" | "fulfilled" | "dismissed";
type DocRequest = {
  id: string; title: string; note: string | null; question: string | null;
  message_id: string | null;
  conversation_id: string | null;     // only on the caller's own requests
  status: DocRequestStatus;
  requester: Person;
  document: { id: string; title: string; collection_id: string; collection_name: string;
              source_id: string; url: string | null } | null;  // fulfilled, and the viewer can read it
  resolution: string | null;          // the admin's note on deciding
  decided_by: Person | null;
  created_at: string; decided_at: string | null;
};
```

To close the loop, an admin re-asks the question in chat (`/o/{slug}/chat?q=…` in the web
app prefills it) and verifies the answer, or writes a verified answer directly
(`POST /orgs/{slug}/verified-answers` with `question` and `answer`).

## Audit log `[admin]`

Who read the organization's data, and the sensitive changes made in it. An org's log
covers **every request that returned its documents, whichever org's workspace it came
from**: when someone in orgs A and B asks a question from B that reads A's documents,
A's admins see who asked, what, and which of A's documents it reached — and nothing of
what it read in B (B's documents, name and ids never appear in A's log). Entries are
snapshots (name, email, API key name, document titles as they were), so they outlive
the conversation, a revoked key or a member leaving. Admin API keys can read and export
the log (for a SIEM); members get 403, non-members 404.

What counts as a read (`kind`):

| kind | recorded when | `query` | `documents` |
|---|---|---|---|
| `chat` | an answer is saved (also `stopped` / `error`) | the question | everything retrieved and shown to the model; `cited` marks what the answer cites |
| `search` | `POST /search`, MCP `search` | the query | the results |
| `read` | `GET /documents/{id}`, MCP `read_document` | null | the document |
| `shared` | a viewer opens a shared answer with `access: "full"` | the question | the answer's sources |

`channel` is `app` (signed in), `api` (an API key on the REST API), `mcp` (an API key
through the MCP server) or `slack` (asked through the Slack bot, as the linked user).
Records queries that return records (`POST /records/query` in list or count mode, MCP
`query_records`, `/records/resolve`, MCP `resolve_role`) are logged as `search` with the
query `records {type}: {filters}` and the documents they returned; a group-by returns
counts, not documents, and isn't logged. `GET /records/{id}` is logged as `read`. Admin actions also
include `agent.*` (`agent.updated` with `changes` to its name, mode, instructions or
collections), `graph.rebuilt`, `record_schema.*` (suggested, created, updated,
activated, archived, extracted, deleted; edits carry `changes`), `doc_request.fulfilled` / `.dismissed` and
`slack.*` (connected, disconnected, and `channel_added` / `channel_removed` in each org a
channel starts or stops answering from, never naming the channel's other orgs).
Web results and attached files aren't org data and aren't
listed.

```ts
type AuditActor = {
  type: "user" | "api_key";            // an API key acts as its creator: user_* is the creator
  user_id: string | null;              // null once the user no longer exists
  name: string; email: string;         // as they were at the time
  api_key_id: string | null; api_key_name: string | null;
};
type AccessEntry = {
  id: string; created_at: string;
  kind: "chat" | "search" | "read" | "shared";
  channel: "app" | "api" | "mcp";
  actor: AuditActor;
  query: string | null;
  cross_org: boolean;                  // made from another org's workspace (never named)
  message_id: string | null;           // the answer (chat, shared); null when cross_org
  documents: { id: string; title: string; collection: string | null; cited: boolean }[];  // this org's only
  document_count: number; cited_count: number;
};
type AuditEvent = {
  id: string; created_at: string;
  action: string;                      // see below
  channel: "app" | "api";
  actor: AuditActor;
  target_type: string; target_id: string | null; target_label: string | null;
  details: Record<string, unknown>;    // never secrets: no tokens, keys, passwords or connector secrets
};
type CursorPage<T> = { items: T[]; next_cursor: string | null; limit: number };
```

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/orgs/{slug}/audit/access?since=&until=&actor_id=&q=&kind=&channel=&document_id=&cursor=&limit=50` | – | `CursorPage<AccessEntry>`, newest first |
| GET | `/orgs/{slug}/audit/events?since=&until=&actor_id=&q=&action=&cursor=&limit=50` | – | `CursorPage<AuditEvent>`, newest first |
| GET | `/orgs/{slug}/audit/export?log=access\|events&format=csv\|jsonl` + the same filters | – | the log, oldest first, streamed as a download (`text/csv` or `application/x-ndjson`) |

Filters: `since` (inclusive) / `until` (exclusive) are ISO-8601 times (UTC if no offset;
400 `invalid_range` unless `since < until`); `actor_id` is a user id (their own requests
and their API keys') or an API key id; `q` is a substring of the query (access) or of
the target label (events); `document_id` finds the entries that returned a document;
`action` is one action or a group (`member` matches every `member.*`). `limit` ≤ 200.
Pass `next_cursor` back as `?cursor=` for the next page (null at the end; 400
`invalid_cursor` for one that wasn't issued). The log is cursor-paginated, not
`Page<T>`: it grows while you read it, and has no useful total.

Exports carry every column of the list routes (CSV: `documents` as `title (cited); …`,
`details` as JSON; cells that a spreadsheet would run as a formula are prefixed with
`'`). Each export is itself recorded, as `audit.exported` with its filters, before it
starts.

Actions (`target_type` in brackets):

| action | details |
|---|---|
| `org.created`, `org.updated` (organization) | `changes`: `name`, `description`, `hue`, `settings.<key>` → `{from, to}` |
| `member.joined` (member) | `role`; the actor is the person who accepted the invite |
| `member.role_changed` (member) | `role: {from, to}` |
| `member.removed`, `member.left` (member) | `role`, `keys_revoked` (their API keys for the org) |
| `invite.created`, `invite.revoked` (invite) | `role`; the label is the email (never the token) |
| `collection.created` (collection) | `visibility`, `granted` (emails) |
| `collection.updated`, `collection.access_changed` (collection) | `changes`, `granted`, `revoked`; `access_changed` when the visibility or the access list changed |
| `collection.deleted` (collection) | `documents` |
| `source.created` (source) | `kind`, `collection`, `config` (secret fields removed), `write_only_fields` (names only), `sync_interval_minutes` |
| `source.updated` (source) | `changes` (`name`, `sync_interval_minutes`, `config.<key>`), `write_only_fields` |
| `source.synced`, `source.files_uploaded`, `source.deleted` (source) | `full`; `files`; `kind`, `documents` |
| `document.hidden`, `document.unhidden`, `document.boosted`, `document.deleted` (document) | `boost: {from, to}`; `source` |
| `api_key.created`, `api_key.revoked` (api_key) | `role`, `expires_at`, `created_by` (never the key, its hash or prefix) |
| `share.created`, `share.revoked` (shared_answer) | `message_id`; the label is the question (never the token) |
| `access_request.approved`, `access_request.dismissed` (access_request) | `collection`, `collection_id`, `note`; the label is the requester's email |
| `verified_answer.created`, `.updated`, `.reverified`, `.deleted` (verified_answer) | `collection`, `owner`, `expires_at`, `changes` (an edited answer shows as `{changed: true}`) |
| `audit.exported` (audit_log) | `log`, `format`, `filters` |

Events are written in the same transaction as the change: a change that fails leaves
no event. The worker deletes entries older than `AUDIT_RETENTION_DAYS` (default 365;
0 keeps them forever).

## OpenAPI

`/api/openapi.json` declares a `bearer` security scheme (API key or session JWT — the
"Authorize" button in `/api/docs`), a `default` error response (`ErrorOut`) on every
operation, and stable operation ids of the form `<tag>-<function>` (e.g.
`documents-list_documents`) for client generators.

## Health & models

`GET /api/health` (no auth) →
`{status: "ok", version, llm_configured, llm_providers: ("anthropic"|"nvidia")[], default_model, embedding_provider, rerank_provider: string | null, web_search_provider: string | null}`.

`GET /api/models` → `{id, label, provider: "anthropic" | "nvidia", hint, available}[]` —
the models an org can pick; `available` is false when that provider has no key.
