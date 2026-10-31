import type {
  Agent,
  ApiKey,
  Attachment,
  Collection,
  ConnectorSpec,
  Conversation,
  DocumentDetail,
  DocumentRow,
  DocumentSort,
  GraphStats,
  Health,
  Insights,
  Invite,
  Member,
  Message,
  ModelInfo,
  Mode,
  Org,
  OrgSettings,
  OrgSummary,
  Page,
  Role,
  Source,
  SourceRef,
  User,
  Vote,
} from "./types";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public code?: string,
  ) {
    super(message);
  }
}

function describe(detail: unknown): string {
  if (typeof detail === "string") return detail;
  // FastAPI 422 validation errors arrive as a list
  if (Array.isArray(detail) && detail.length) {
    const first = detail[0] as { msg?: string; loc?: unknown[] };
    const field = Array.isArray(first.loc) ? first.loc.at(-1) : undefined;
    return field ? `${String(field)}: ${first.msg}` : (first.msg ?? "Invalid request");
  }
  return "Something went wrong";
}

export async function request<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, headers, ...rest } = init;
  const res = await fetch(`/api${path}`, {
    credentials: "same-origin",
    ...rest,
    headers: {
      ...(json !== undefined ? { "Content-Type": "application/json" } : {}),
      ...headers,
    },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  const data = text ? safeJson(text) : undefined;
  if (!res.ok) {
    const body = (data ?? {}) as { detail?: unknown; code?: string };
    throw new ApiError(res.status, describe(body.detail ?? res.statusText), body.code);
  }
  return data as T;
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text };
  }
}

const o = (slug: string) => `/orgs/${encodeURIComponent(slug)}`;

function query(params: Record<string, string | number | boolean | null | undefined>) {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  return qs.toString();
}

const UPLOAD_BATCH_FILES = 20;
const UPLOAD_BATCH_BYTES = 90 * 1024 * 1024; // under the API's 100 MB multipart cap

function uploadBatches(files: File[]): File[][] {
  const batches: File[][] = [];
  let current: File[] = [];
  let bytes = 0;
  for (const file of files) {
    if (current.length && (current.length >= UPLOAD_BATCH_FILES || bytes + file.size > UPLOAD_BATCH_BYTES)) {
      batches.push(current);
      current = [];
      bytes = 0;
    }
    current.push(file);
    bytes += file.size;
  }
  if (current.length) batches.push(current);
  return batches;
}

export const api = {
  health: () => request<Health>("/health"),
  models: () => request<ModelInfo[]>("/models"),

  // auth
  me: () => request<{ user: User; orgs: OrgSummary[] }>("/auth/me"),
  login: (email: string, password: string) =>
    request<{ user: User }>("/auth/login", { method: "POST", json: { email, password } }),
  signup: (name: string, email: string, password: string) =>
    request<{ user: User }>("/auth/signup", { method: "POST", json: { name, email, password } }),
  logout: () => request<void>("/auth/logout", { method: "POST" }),

  // orgs
  orgs: () => request<OrgSummary[]>("/orgs"),
  createOrg: (body: { name: string; slug?: string; description?: string }) =>
    request<Org>("/orgs", { method: "POST", json: body }),
  org: (slug: string) => request<Org>(o(slug)),
  updateOrg: (
    slug: string,
    body: { name?: string; description?: string | null; hue?: number; settings?: Partial<OrgSettings> },
  ) => request<Org>(o(slug), { method: "PATCH", json: body }),
  deleteOrg: (slug: string) => request<void>(o(slug), { method: "DELETE" }),

  // members & invites
  members: (slug: string) => request<Member[]>(`${o(slug)}/members`),
  updateMember: (slug: string, userId: string, role: Role) =>
    request<Member>(`${o(slug)}/members/${userId}`, { method: "PATCH", json: { role } }),
  removeMember: (slug: string, userId: string) =>
    request<void>(`${o(slug)}/members/${userId}`, { method: "DELETE" }),
  invites: (slug: string) => request<Invite[]>(`${o(slug)}/invites`),
  createInvite: (slug: string, email: string, role: Role) =>
    request<Invite>(`${o(slug)}/invites`, { method: "POST", json: { email, role } }),
  revokeInvite: (slug: string, id: string) => request<void>(`${o(slug)}/invites/${id}`, { method: "DELETE" }),
  invite: (token: string) =>
    request<{ org: { name: string; slug: string; hue: number }; email: string; role: Role; expired: boolean }>(
      `/invites/${token}`,
    ),
  acceptInvite: (token: string) => request<OrgSummary>(`/invites/${token}/accept`, { method: "POST" }),

  // collections
  collections: (slug: string) => request<Collection[]>(`${o(slug)}/collections`),
  createCollection: (slug: string, body: Partial<Collection>) =>
    request<Collection>(`${o(slug)}/collections`, { method: "POST", json: body }),
  updateCollection: (slug: string, id: string, body: Partial<Collection>) =>
    request<Collection>(`${o(slug)}/collections/${id}`, { method: "PATCH", json: body }),
  deleteCollection: (slug: string, id: string) =>
    request<void>(`${o(slug)}/collections/${id}`, { method: "DELETE" }),

  // sources
  connectors: () => request<ConnectorSpec[]>("/connectors"),
  sources: (slug: string) => request<Source[]>(`${o(slug)}/sources`),
  source: (slug: string, id: string) => request<Source>(`${o(slug)}/sources/${id}`),
  createSource: (
    slug: string,
    body: {
      kind: string;
      name: string;
      collection_id: string;
      config: Record<string, unknown>;
      sync_interval_minutes?: number | null;
    },
  ) => request<Source>(`${o(slug)}/sources`, { method: "POST", json: body }),
  updateSource: (slug: string, id: string, body: Partial<Pick<Source, "name" | "config" | "sync_interval_minutes">>) =>
    request<Source>(`${o(slug)}/sources/${id}`, { method: "PATCH", json: body }),
  deleteSource: (slug: string, id: string) => request<void>(`${o(slug)}/sources/${id}`, { method: "DELETE" }),
  syncSource: (slug: string, id: string) => request<Source>(`${o(slug)}/sources/${id}/sync`, { method: "POST" }),
  uploadFiles: async (slug: string, id: string, files: File[]) => {
    // The API takes at most 20 files / 100 MB per request, so send larger selections in batches.
    let accepted = 0;
    let source: Source | undefined;
    for (const batch of uploadBatches(files)) {
      const form = new FormData();
      batch.forEach((f) => form.append("files", f));
      const res = await request<{ accepted: number; source: Source }>(`${o(slug)}/sources/${id}/files`, {
        method: "POST",
        body: form,
      });
      accepted += res.accepted;
      source = res.source;
    }
    return { accepted, source: source as Source };
  },
  documents: (slug: string, sourceId: string, params: { limit?: number; offset?: number; q?: string } = {}) =>
    request<Page<DocumentRow>>(`${o(slug)}/sources/${sourceId}/documents?${query(params)}`),
  orgDocuments: (
    slug: string,
    params: {
      q?: string;
      collection_id?: string;
      source_id?: string;
      hidden?: boolean;
      sort?: DocumentSort;
      limit?: number;
      offset?: number;
    } = {},
  ) => request<Page<DocumentRow>>(`${o(slug)}/documents?${query(params)}`),
  document: (slug: string, id: string) => request<DocumentDetail>(`${o(slug)}/documents/${id}`),
  curateDocument: (slug: string, id: string, body: { boost?: number; hidden?: boolean }) =>
    request<DocumentDetail>(`${o(slug)}/documents/${id}`, { method: "PATCH", json: body }),
  voteDocument: (slug: string, id: string, vote: Vote | null, messageId?: string) =>
    request<{ document_id: string; vote: Vote | null; feedback_up: number; feedback_down: number }>(
      `${o(slug)}/documents/${id}/feedback`,
      { method: "POST", json: { vote, message_id: messageId } },
    ),
  deleteDocument: (slug: string, id: string) => request<void>(`${o(slug)}/documents/${id}`, { method: "DELETE" }),

  // chat attachments
  uploadAttachment: (slug: string, file: File, conversationId?: string) => {
    const form = new FormData();
    form.append("file", file);
    if (conversationId) form.append("conversation_id", conversationId);
    return request<Attachment>(`${o(slug)}/attachments`, { method: "POST", body: form });
  },
  attachment: (slug: string, id: string) => request<Attachment & { content: string }>(`${o(slug)}/attachments/${id}`),
  deleteAttachment: (slug: string, id: string) => request<void>(`${o(slug)}/attachments/${id}`, { method: "DELETE" }),

  // api keys
  apiKeys: (slug: string) => request<ApiKey[]>(`${o(slug)}/api-keys`),
  createApiKey: (slug: string, body: { name: string; role: "admin" | "member" | "viewer"; expires_in_days?: number }) =>
    request<ApiKey & { key: string }>(`${o(slug)}/api-keys`, { method: "POST", json: body }),
  revokeApiKey: (slug: string, id: string) => request<void>(`${o(slug)}/api-keys/${id}`, { method: "DELETE" }),

  // knowledge graph
  graphStats: (slug: string) => request<GraphStats>(`${o(slug)}/graph`),
  rebuildGraph: (slug: string) => request<GraphStats>(`${o(slug)}/graph/rebuild`, { method: "POST" }),

  // search
  search: (slug: string, body: { query: string; org_slugs?: string[]; collection_ids?: string[]; limit?: number }) =>
    request<{ results: SourceRef[]; latency_ms: number }>(`${o(slug)}/search`, { method: "POST", json: body }),

  // agents
  agents: (slug: string) => request<Agent[]>(`${o(slug)}/agents`),
  createAgent: (slug: string, body: Partial<Agent>) =>
    request<Agent>(`${o(slug)}/agents`, { method: "POST", json: body }),
  updateAgent: (slug: string, id: string, body: Partial<Agent>) =>
    request<Agent>(`${o(slug)}/agents/${id}`, { method: "PATCH", json: body }),
  deleteAgent: (slug: string, id: string) => request<void>(`${o(slug)}/agents/${id}`, { method: "DELETE" }),

  // chat
  conversations: (slug: string) =>
    request<Page<Conversation>>(`${o(slug)}/conversations?limit=50`).then((page) => page.items),
  createConversation: (slug: string, body: { agent_id?: string | null; mode?: Mode; scope_org_slugs?: string[] }) =>
    request<Conversation>(`${o(slug)}/conversations`, { method: "POST", json: body }),
  conversation: (slug: string, id: string) =>
    request<{ conversation: Conversation; messages: Message[] }>(`${o(slug)}/conversations/${id}`),
  updateConversation: (slug: string, id: string, body: { title?: string; mode?: Mode; scope_org_slugs?: string[] }) =>
    request<Conversation>(`${o(slug)}/conversations/${id}`, { method: "PATCH", json: body }),
  deleteConversation: (slug: string, id: string) =>
    request<void>(`${o(slug)}/conversations/${id}`, { method: "DELETE" }),
  /** Stop an answer that is still being written; returns it with what was written so far. */
  cancelMessage: (slug: string, messageId: string) =>
    request<Message>(`${o(slug)}/messages/${messageId}/cancel`, { method: "POST" }),
  feedback: (slug: string, messageId: string, feedback: "up" | "down" | null) =>
    request<void>(`${o(slug)}/messages/${messageId}/feedback`, { method: "POST", json: { feedback } }),

  insights: (slug: string, days = 30) => request<Insights>(`${o(slug)}/insights?days=${days}`),
};
