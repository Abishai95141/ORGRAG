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
};

export type OrgSummary = {
  id: string;
  slug: string;
  name: string;
  hue: number;
  role: Role;
  member_count: number;
  document_count: number;
};

export type Org = OrgSummary & { description: string | null; settings: OrgSettings; created_at: string };

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

export type Glyph = "stack" | "book" | "gear" | "chart" | "shield" | "people" | "code" | "globe";

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

export type SourceKind = "upload" | "web" | "github";
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
  updated_at: string;
};

export type DocumentDetail = DocumentRow & { content: string };

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

export type SourceRef = {
  ref: number;
  document_id: string;
  chunk_id: string;
  title: string;
  url: string | null;
  snippet: string;
  org_id: string;
  org_slug: string;
  org_name: string;
  org_hue: number;
  collection_name: string;
  source_kind: SourceKind;
  score: number;
};

export type StepKind = "plan" | "search" | "read" | "reflect" | "answer";

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
  trace: TraceStep[];
  feedback: "up" | "down" | null;
  status: "complete" | "streaming" | "error";
  created_at: string;
};

export type Insights = {
  totals: { questions: number; conversations: number; documents: number; chunks: number; active_users: number };
  questions_per_day: { date: string; count: number }[];
  top_sources: { source_id: string; name: string; kind: SourceKind; citations: number }[];
  feedback: { up: number; down: number };
  recent_questions: { query: string; user_name: string; created_at: string; hits: number }[];
};

export type Health = { status: string; llm_configured: boolean; embedding_provider: string };
