// Mirrors docs/API.md — keep in sync with backend/app/schemas.

export type Role = "owner" | "admin" | "member" | "viewer";
export type Mode = "quick" | "deep";

export type User = { id: string; email: string; name: string; avatar_hue: number; created_at: string };

export type OrgSettings = {
  model: string;
  quick_effort: "low" | "medium" | "high";
  deep_effort: "medium" | "high" | "xhigh";
  default_mode: Mode;
  retrieval_top_k: number;
  allow_cross_org: boolean;
  rerank: boolean;
  recency_bias: "off" | "low" | "medium" | "high";
  web_search: boolean;
  knowledge_graph: boolean;
};

/** A paginated list. `limit` / `offset` echo the request. */
export type Page<T> = { items: T[]; total: number; limit: number; offset: number };

/** Every error response body. `code` is stable and machine-readable. */
export type ApiErrorBody = { detail: string; code: string; request_id: string | null };

export type OrgSummary = {
  id: string;
  slug: string;
  name: string;
  hue: number;
  role: Role;
  member_count: number;
  document_count: number;
};

export type Org = OrgSummary & {
  description: string | null;
  settings: OrgSettings;
  /** The model answers actually use (falls back when settings.model's provider has no key). */
  effective_model: string | null;
  created_at: string;
};

export type Member = { user: User; role: Role; joined_at: string };

export type Invite = {
  id: string;
  email: string;
  role: Role;
  token: string;
  expires_at: string;
  created_at: string;
  accepted_at: string | null;
};

/** Glyphs the API accepts for collections and agents (anything else is a 422). */
export type Glyph = "stack" | "book" | "gear" | "chart" | "shield" | "people" | "code" | "globe" | "compass" | "leaf";

export type Collection = {
  id: string;
  org_id: string;
  name: string;
  description: string | null;
  visibility: "org" | "restricted";
  glyph: Glyph | string;
  document_count: number;
  source_count: number;
  allowed_user_ids: string[];
  created_at: string;
};

// "verified": an admin-verified answer, indexed like a document.
export type SourceKind = "upload" | "web" | "github" | "gdrive" | "slack" | "notion" | "verified";
export type SourceStatus = "idle" | "queued" | "syncing" | "ready" | "error";

export type Source = {
  id: string;
  org_id: string;
  collection_id: string;
  collection_name: string;
  kind: SourceKind;
  name: string;
  config: Record<string, unknown>;
  status: SourceStatus;
  error: string | null;
  document_count: number;
  chunk_count: number;
  sync_interval_minutes: number | null;
  last_synced_at: string | null;
  created_at: string;
  progress: { done: number; total: number | null; message: string | null } | null;
};

export type ConnectorSpec = {
  kind: SourceKind;
  name: string;
  description: string;
  config_schema: Record<string, unknown>;
};

export type DocumentRow = {
  id: string;
  source_id: string;
  collection_id: string;
  title: string;
  url: string | null;
  mime: string;
  chunk_count: number;
  token_count: number;
  boost: number;
  hidden: boolean;
  feedback_up: number;
  feedback_down: number;
  source_updated_at: string | null;
  updated_at: string;
};

export type Vote = "up" | "down";

export type DocumentDetail = DocumentRow & { content: string; my_vote?: Vote | null };

export type DocumentSort = "title" | "updated" | "feedback" | "boost";

export type Agent = {
  id: string;
  org_id: string;
  name: string;
  description: string | null;
  instructions: string;
  mode: Mode;
  glyph: Glyph | string;
  collection_ids: string[];
  starter_prompts: string[];
  created_by: string | null;
  created_at: string;
};

export type Conversation = {
  id: string;
  org_id: string;
  agent_id: string | null;
  title: string;
  scope_org_ids: string[];
  mode: Mode;
  created_at: string;
  updated_at: string;
};

export type SourceOrigin = "knowledge" | "web" | "attachment";

/** A citation target. Knowledge sources carry org + document fields; web results
 * only a url; attachments an attachment_id. */
export type SourceRef = {
  ref: number;
  origin: SourceOrigin;
  document_id: string | null;
  chunk_id: string | null;
  attachment_id: string | null;
  title: string;
  url: string | null;
  snippet: string;
  org_id: string | null;
  org_slug: string | null;
  org_name: string | null;
  org_hue: number | null;
  collection_name: string | null;
  source_kind: SourceKind | null;
  score: number;
  updated_at?: string | null;
};

export type Attachment = {
  id: string;
  conversation_id: string | null;
  filename: string;
  mime: string;
  size_bytes: number;
  token_count: number;
  created_at: string;
};

export type MessageAttachment = Pick<Attachment, "id" | "filename" | "mime" | "size_bytes" | "token_count">;

export type StepKind = "plan" | "search" | "records" | "web" | "read" | "reflect" | "answer";

export type TraceStep = {
  id: string;
  kind: StepKind;
  label: string;
  status: "running" | "done" | "error";
  detail: {
    queries?: string[];
    refs?: number[];
    reasoning?: string;
    plan?: { question: string; rationale: string }[];
    document_title?: string;
    /** Coverage check: facts the sources didn't state. */
    missing?: string[];
    /** Listing collections or graph entities: not a search for the answer. */
    listing?: boolean;
    /** query_records: the record type looked up. */
    record_type?: string;
  };
  started_at: string;
  finished_at: string | null;
};

export type Message = {
  id: string;
  conversation_id: string;
  role: "user" | "assistant";
  content: string;
  sources: SourceRef[];
  attachments: MessageAttachment[];
  trace: TraceStep[];
  feedback: "up" | "down" | null;
  status: "complete" | "streaming" | "error" | "stopped";
  created_at: string;
  verified_answer_id?: string | null;
  share_token?: string | null;
};

export type Share = { token: string; url: string; created_at: string };

export type MissingAccess = {
  kind: "org" | "collection" | "attachment" | "hidden";
  org_name: string;
  org_slug: string | null;
  collection_id: string | null;
  collection_name: string | null;
  requested: boolean;
};

export type SharedAnswer = {
  token: string;
  access: "full" | "restricted" | "none";
  org: { name: string; slug: string; hue: number };
  author_name: string | null;
  question: string | null;
  answer: string | null;
  sources: SourceRef[];
  missing: MissingAccess[];
  answered_at: string | null;
  shared_at: string;
};

export type AccessRequest = {
  id: string;
  collection_id: string;
  collection_name: string;
  user: Person;
  note: string | null;
  status: "pending" | "approved" | "dismissed";
  created_at: string;
  decided_at: string | null;
};

export type Person = { id: string; name: string; email: string; avatar_hue: number };

export type VerifiedStatus = "active" | "expiring" | "expired";

export type VerifiedAnswer = {
  id: string;
  collection_id: string;
  collection_name: string;
  document_id: string;
  message_id: string | null;
  question: string;
  answer: string;
  sources: SourceRef[];
  owner: Person | null;
  verified_by: Person | null;
  verified_at: string;
  expires_at: string | null;
  status: VerifiedStatus;
  created_at: string;
  updated_at: string;
};

export type VerifiedAnswerInput = {
  question?: string;
  answer?: string;
  collection_id?: string;
  owner_id?: string;
};

/** A page of a log that's read newest first; pass `next_cursor` back for the next one. */
export type CursorPage<T> = { items: T[]; next_cursor: string | null; limit: number };

export type AccessKind = "chat" | "search" | "read" | "shared";
export type AuditChannel = "app" | "api" | "mcp" | "slack";

/** Who acted, as recorded at the time. With an API key, the user fields are its creator. */
export type AuditActor = {
  type: "user" | "api_key";
  user_id: string | null;
  name: string;
  email: string;
  api_key_id: string | null;
  api_key_name: string | null;
};

/** A request that returned this org's documents (only this org's are listed). */
export type AccessEntry = {
  id: string;
  created_at: string;
  kind: AccessKind;
  channel: AuditChannel;
  actor: AuditActor;
  query: string | null;
  /** Made from another organization's workspace (which stays unnamed). */
  cross_org: boolean;
  message_id: string | null;
  documents: { id: string; title: string; collection: string | null; cited: boolean }[];
  document_count: number;
  cited_count: number;
};

export type AuditEvent = {
  id: string;
  created_at: string;
  action: string;
  channel: AuditChannel;
  actor: AuditActor;
  target_type: string;
  target_id: string | null;
  target_label: string | null;
  details: Record<string, unknown>;
};

type AuditWindow = { since?: string; until?: string; actor_id?: string; q?: string };
export type AccessFilters = AuditWindow & { kind?: AccessKind; channel?: AuditChannel; document_id?: string };
export type EventFilters = AuditWindow & { action?: string };

export type Insights = {
  totals: { questions: number; conversations: number; documents: number; chunks: number; active_users: number };
  questions_per_day: { date: string; count: number }[];
  top_sources: { source_id: string; name: string; kind: SourceKind; citations: number }[];
  feedback: { up: number; down: number };
  recent_questions: { query: string; user_name: string; created_at: string; hits: number }[];
  usage: { model: string; answers: number; calls: number; input_tokens: number; output_tokens: number; cost_usd: number | null }[];
};

export type Health = {
  status: string;
  version: string;
  llm_configured: boolean;
  llm_providers: ("anthropic" | "nvidia" | "explabs")[];
  default_model: string;
  embedding_provider: string;
  rerank_provider: string | null;
  web_search_provider: string | null;
};

export type GraphStats = {
  enabled: boolean;
  entities: number;
  relations: number;
  documents_extracted: number;
  documents_pending: number;
};

export type ModelInfo = { id: string; label: string; provider: "anthropic" | "nvidia" | "explabs"; hint: string; available: boolean };

export type ApiKey = {
  id: string;
  name: string;
  prefix: string;
  role: Role;
  user_id: string;
  last_used_at: string | null;
  expires_at: string | null;
  created_at: string;
};

// Structured records: record types (schemas) found in a collection, and their rows.
export type RecordFieldType = "text" | "number" | "date" | "boolean" | "choice";
export type RecordSchemaStatus = "suggested" | "active" | "archived";
export type RecordOrigin = "fields_table" | "rows_table" | "field_lines" | "llm";
export type RecordOp = "eq" | "ne" | "contains" | "in" | "lt" | "lte" | "gt" | "gte" | "exists" | "missing";
export type RecordValue = string | number | boolean | null;

export type RecordField = {
  name: string;
  label: string;
  type: RecordFieldType;
  description?: string;
  options?: string[] | null;
  aliases?: string[];
};

export type RecordSchema = {
  id: string;
  collection_id: string;
  collection_name: string;
  name: string;
  label: string;
  description: string;
  fields: RecordField[];
  key_field: string;
  origin: RecordOrigin;
  status: RecordSchemaStatus;
  stats: {
    records?: number;
    documents?: number;
    extracted?: number;
    unchanged?: number;
    failed?: number;
    cost_usd?: number;
    samples?: Record<string, string>[];
  };
  extracting: boolean;
  extracted_at: string | null;
  created_at: string;
  updated_at: string;
};

export type RecordSchemaDetail = RecordSchema & {
  failures: { document_id: string; title: string; error: string; extracted_at: string | null }[];
};

export type RecordSchemaInput = {
  collection_id: string;
  name: string;
  label: string;
  description?: string;
  fields: RecordField[];
  key_field: string;
};

export type RecordFilter = { field: string; op: RecordOp; value?: RecordValue | RecordValue[] };

export type RecordQuery = {
  schema: string;
  filters?: RecordFilter[];
  mode?: "list" | "count" | "group";
  group_by?: string;
  sort?: string;
  descending?: boolean;
  limit?: number;
  latest_only?: boolean;
};

export type RecordRow = {
  id: string;
  schema_id: string;
  key: string;
  values: Record<string, RecordValue>;
  provenance: Record<string, { text: string; start: number; end: number }>;
  document: { id: string; title: string; updated_at: string | null };
};

export type RecordQueryResult = {
  total: number;
  schemas: string[];
  rows: RecordRow[];
  groups: { value: RecordValue; count: number }[];
};

export type GapReason = "unanswered" | "incomplete" | "downvoted" | "no_results";

export type DocRequestStatus = "pending" | "fulfilled" | "dismissed";

/** A similar question that got a good answer in another org the viewer belongs to. */
export type GapHint = {
  kind: "answer" | "verified";
  org: { name: string; slug: string; hue: number };
  question: string;
  answer: string;
  similarity: number;
  answered_at: string;
  verified_answer_id: string | null;
  documents: { id: string; title: string; org_slug: string; collection_name: string | null; source_id: string }[];
};

export type GapCluster = {
  id: string;
  label: string;
  count: number;
  askers: number;
  first_seen: string;
  last_seen: string;
  reasons: Record<GapReason, number>;
  examples: string[];
  missing: string[];
  answered_elsewhere: GapHint[];
  requests: { id: string; title: string; status: DocRequestStatus; document_id: string | null; created_at: string; decided_at: string | null }[];
  addressed: boolean;
};

export type Gaps = {
  days: number;
  since: string;
  questions: number;
  truncated: boolean;
  similarity: number;
  total_clusters: number;
  clusters: GapCluster[];
};

export type DocRequest = {
  id: string;
  title: string;
  note: string | null;
  question: string | null;
  message_id: string | null;
  /** Only on your own requests. */
  conversation_id: string | null;
  status: DocRequestStatus;
  requester: Person;
  document: { id: string; title: string; collection_id: string; collection_name: string; source_id: string; url: string | null } | null;
  resolution: string | null;
  decided_by: Person | null;
  created_at: string;
  decided_at: string | null;
};

// ── Slack bot ──────────────────────────────────────────────────────────────
/** An org connected to a Slack workspace, as the caller sees it (only orgs they're in). */
export type SlackOrg = { slug: string; name: string; hue: number; role: Role };

export type SlackWorkspace = {
  id: string;
  team_id: string;
  team_name: string;
  bot_user_id: string;
  connected_at: string;
  connected_by: Person | null;
  /** The org unmapped channels search (the first to connect); null if you're not in it. */
  default_org: SlackOrg | null;
  default_elsewhere: boolean;
  orgs: SlackOrg[];
  other_org_count: number;
};

export type SlackOverview = {
  configured: boolean;
  events_url: string;
  redirect_url: string;
  workspaces: SlackWorkspace[];
};

export type SlackChannel = {
  channel_id: string;
  channel_name: string | null;
  /** In search order; only orgs you belong to. */
  orgs: SlackOrg[];
  other_org_count: number;
  updated_by: Person | null;
  updated_at: string;
};

export type SlackBotChannel = { id: string; name: string; is_private: boolean; mapped: boolean };

export type SlackLinkPreview = {
  team_id: string;
  team_name: string;
  slack_user_id: string;
  slack_user_name: string | null;
  slack_email: string | null;
  expired: boolean;
  used: boolean;
  linked: "none" | "you" | "other";
  eligible: boolean;
};

export type SlackLink = {
  id: string;
  team_id: string;
  team_name: string;
  slack_user_id: string;
  slack_user_name: string | null;
  created_at: string;
};
