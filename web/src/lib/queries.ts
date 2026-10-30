"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "./api";

export const qk = {
  me: ["me"] as const,
  health: ["health"] as const,
  org: (slug: string) => ["org", slug] as const,
  members: (slug: string) => ["org", slug, "members"] as const,
  invites: (slug: string) => ["org", slug, "invites"] as const,
  collections: (slug: string) => ["org", slug, "collections"] as const,
  sources: (slug: string) => ["org", slug, "sources"] as const,
  source: (slug: string, id: string) => ["org", slug, "sources", id] as const,
  documents: (slug: string, sourceId: string, q: string, offset: number) =>
    ["org", slug, "sources", sourceId, "documents", q, offset] as const,
  document: (slug: string, id: string) => ["org", slug, "document", id] as const,
  agents: (slug: string) => ["org", slug, "agents"] as const,
  conversations: (slug: string) => ["org", slug, "conversations"] as const,
  conversation: (slug: string, id: string) => ["org", slug, "conversation", id] as const,
  insights: (slug: string, days: number) => ["org", slug, "insights", days] as const,
  orgDocuments: (slug: string, params: Record<string, unknown>) => ["org", slug, "documents", params] as const,
  apiKeys: (slug: string) => ["org", slug, "api-keys"] as const,
  connectors: ["connectors"] as const,
  models: ["models"] as const,
};

export const useMe = () => useQuery({ queryKey: qk.me, queryFn: api.me, retry: false, staleTime: 60_000 });
export const useHealth = () => useQuery({ queryKey: qk.health, queryFn: api.health, staleTime: 5 * 60_000 });
export const useOrg = (slug: string) => useQuery({ queryKey: qk.org(slug), queryFn: () => api.org(slug) });
export const useMembers = (slug: string) => useQuery({ queryKey: qk.members(slug), queryFn: () => api.members(slug) });
export const useInvites = (slug: string, enabled = true) =>
  useQuery({ queryKey: qk.invites(slug), queryFn: () => api.invites(slug), enabled });
export const useCollections = (slug: string) =>
  useQuery({ queryKey: qk.collections(slug), queryFn: () => api.collections(slug) });
export const useAgents = (slug: string) => useQuery({ queryKey: qk.agents(slug), queryFn: () => api.agents(slug) });
export const useConversations = (slug: string) =>
  useQuery({ queryKey: qk.conversations(slug), queryFn: () => api.conversations(slug) });
export const useModels = () => useQuery({ queryKey: qk.models, queryFn: api.models, staleTime: 5 * 60_000 });
export const useConnectors = () =>
  useQuery({ queryKey: qk.connectors, queryFn: api.connectors, staleTime: Infinity });

const ACTIVE = new Set(["queued", "syncing"]);

export const useSources = (slug: string) =>
  useQuery({
    queryKey: qk.sources(slug),
    queryFn: () => api.sources(slug),
    // Poll while anything is syncing so progress feels live.
    refetchInterval: (q) => (q.state.data?.some((s) => ACTIVE.has(s.status)) ? 1500 : false),
  });

export const useSource = (slug: string, id: string) =>
  useQuery({
    queryKey: qk.source(slug, id),
    queryFn: () => api.source(slug, id),
    refetchInterval: (q) => (q.state.data && ACTIVE.has(q.state.data.status) ? 1200 : false),
  });
