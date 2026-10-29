import type {
  Agent,
  Collection,
  ConnectorSpec,
  Conversation,
  DocumentDetail,
  DocumentRow,
  Health,
  Insights,
  Invite,
  Member,
  Message,
  Mode,
  Org,
  OrgSettings,
  OrgSummary,
  Role,
  Source,
  SourceRef,
  User,
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

export const api = {
  health: () => request<Health>("/health"),

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
  uploadFiles: (slug: string, id: string, files: File[]) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    return request<{ accepted: number; source: Source }>(`${o(slug)}/sources/${id}/files`, {
      method: "POST",
      body: form,
    });
  },
  documents: (slug: string, sourceId: string, params: { limit?: number; offset?: number; q?: string } = {}) => {
    const qs = new URLSearchParams();
    if (params.limit) qs.set("limit", String(params.limit));
    if (params.offset) qs.set("offset", String(params.offset));
    if (params.q) qs.set("q", params.q);
    return request<{ items: DocumentRow[]; total: number }>(`${o(slug)}/sources/${sourceId}/documents?${qs}`);
  },
  document: (slug: string, id: string) => request<DocumentDetail>(`${o(slug)}/documents/${id}`),
  deleteDocument: (slug: string, id: string) => request<void>(`${o(slug)}/documents/${id}`, { method: "DELETE" }),

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
  conversations: (slug: string) => request<Conversation[]>(`${o(slug)}/conversations?limit=50`),
  createConversation: (slug: string, body: { agent_id?: string | null; mode?: Mode; scope_org_slugs?: string[] }) =>
    request<Conversation>(`${o(slug)}/conversations`, { method: "POST", json: body }),
  conversation: (slug: string, id: string) =>
    request<{ conversation: Conversation; messages: Message[] }>(`${o(slug)}/conversations/${id}`),
  updateConversation: (slug: string, id: string, body: { title?: string; mode?: Mode; scope_org_slugs?: string[] }) =>
    request<Conversation>(`${o(slug)}/conversations/${id}`, { method: "PATCH", json: body }),
  deleteConversation: (slug: string, id: string) =>
    request<void>(`${o(slug)}/conversations/${id}`, { method: "DELETE" }),
  feedback: (slug: string, messageId: string, feedback: "up" | "down" | null) =>
    request<void>(`${o(slug)}/messages/${messageId}/feedback`, { method: "POST", json: { feedback } }),

  insights: (slug: string, days = 30) => request<Insights>(`${o(slug)}/insights?days=${days}`),
};
