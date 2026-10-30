"use client";

import { useSyncExternalStore } from "react";
import type { Message, MessageAttachment, Mode, SourceRef, TraceStep } from "./types";

/**
 * Live answer turns live outside React so a stream survives route changes
 * (new chat → /chat/[id]) and component remounts. Components subscribe by
 * conversation id; persisted history comes from React Query and the live turn
 * is overlaid until the server's final message replaces it.
 */

export type LiveTurn = {
  conversationId: string;
  pendingText: string;
  pendingAttachments: MessageAttachment[];
  userMessage: Message | null;
  assistantId: string | null;
  mode: Mode;
  steps: TraceStep[];
  sources: SourceRef[];
  content: string;
  status: "connecting" | "streaming" | "done" | "error" | "stopped";
  error: string | null;
  final: Message | null;
};

type Listener = () => void;

const turns = new Map<string, LiveTurn>();
const controllers = new Map<string, AbortController>();
const listeners = new Set<Listener>();

function emit() {
  listeners.forEach((l) => l());
}

function patch(id: string, fn: (t: LiveTurn) => LiveTurn) {
  const cur = turns.get(id);
  if (!cur) return;
  turns.set(id, fn(cur));
  emit();
}

export function useLiveTurn(conversationId: string | undefined): LiveTurn | null {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => (conversationId ? (turns.get(conversationId) ?? null) : null),
    () => null,
  );
}

export function isStreaming(conversationId: string) {
  const t = turns.get(conversationId);
  return !!t && (t.status === "connecting" || t.status === "streaming");
}

export function clearTurn(conversationId: string) {
  if (turns.delete(conversationId)) emit();
}

export function stopTurn(conversationId: string) {
  controllers.get(conversationId)?.abort();
}

type StartArgs = {
  slug: string;
  conversationId: string;
  content: string;
  mode: Mode;
  scopeOrgSlugs?: string[];
  collectionIds?: string[];
  attachments?: MessageAttachment[];
  /** Per-message web search override; undefined follows the org setting. */
  webSearch?: boolean | null;
  onSettled?: () => void;
};

export async function startTurn(args: StartArgs) {
  const { slug, conversationId, content, mode } = args;
  controllers.get(conversationId)?.abort();
  const controller = new AbortController();
  controllers.set(conversationId, controller);

  turns.set(conversationId, {
    conversationId,
    pendingText: content,
    pendingAttachments: args.attachments ?? [],
    userMessage: null,
    assistantId: null,
    mode,
    steps: [],
    sources: [],
    content: "",
    status: "connecting",
    error: null,
    final: null,
  });
  emit();

  try {
    const res = await fetch(`/api/orgs/${encodeURIComponent(slug)}/conversations/${conversationId}/messages`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({
        content,
        mode,
        scope_org_slugs: args.scopeOrgSlugs,
        collection_ids: args.collectionIds?.length ? args.collectionIds : undefined,
        attachment_ids: args.attachments?.map((a) => a.id) ?? [],
        web_search: args.webSearch ?? undefined,
      }),
      signal: controller.signal,
    });
    if (!res.ok || !res.body) {
      let detail = res.statusText;
      try {
        detail = (await res.json()).detail ?? detail;
      } catch {}
      throw new Error(typeof detail === "string" ? detail : "Request failed");
    }

    for await (const evt of readSse(res.body)) {
      handleEvent(conversationId, evt.event, evt.data);
    }
    patch(conversationId, (t) => (t.status === "streaming" || t.status === "connecting" ? { ...t, status: "done" } : t));
  } catch (err) {
    if (controller.signal.aborted) {
      patch(conversationId, (t) => ({ ...t, status: "stopped" }));
    } else {
      const message = err instanceof Error ? err.message : "Connection lost";
      patch(conversationId, (t) => ({ ...t, status: "error", error: message }));
    }
  } finally {
    controllers.delete(conversationId);
    args.onSettled?.();
  }
}

function handleEvent(id: string, event: string, data: unknown) {
  switch (event) {
    case "start": {
      const d = data as { user_message: Message; assistant_message_id: string; mode: Mode };
      patch(id, (t) => ({ ...t, status: "streaming", userMessage: d.user_message, assistantId: d.assistant_message_id, mode: d.mode }));
      break;
    }
    case "step": {
      const step = data as TraceStep;
      patch(id, (t) => {
        const idx = t.steps.findIndex((s) => s.id === step.id);
        if (idx === -1) return { ...t, steps: [...t.steps, step] };
        const prev = t.steps[idx];
        // Keep reasoning that arrived via `reasoning` deltas if the upsert omits it.
        const merged: TraceStep = {
          ...prev,
          ...step,
          detail: { ...prev.detail, ...step.detail, reasoning: step.detail.reasoning ?? prev.detail.reasoning },
        };
        const steps = t.steps.slice();
        steps[idx] = merged;
        return { ...t, steps };
      });
      break;
    }
    case "reasoning": {
      const d = data as { step_id: string; text: string };
      patch(id, (t) => ({
        ...t,
        steps: t.steps.map((s) =>
          s.id === d.step_id ? { ...s, detail: { ...s.detail, reasoning: (s.detail.reasoning ?? "") + d.text } } : s,
        ),
      }));
      break;
    }
    case "sources": {
      const d = data as { sources: SourceRef[] };
      patch(id, (t) => {
        const known = new Set(t.sources.map((s) => s.ref));
        return { ...t, sources: [...t.sources, ...d.sources.filter((s) => !known.has(s.ref))] };
      });
      break;
    }
    case "delta": {
      const d = data as { text: string };
      patch(id, (t) => ({ ...t, status: "streaming", content: t.content + d.text }));
      break;
    }
    case "done": {
      const d = data as { message: Message };
      patch(id, (t) => ({ ...t, status: "done", final: d.message, content: d.message.content, sources: d.message.sources, steps: d.message.trace }));
      break;
    }
    case "error": {
      const d = data as { detail?: string };
      patch(id, (t) => ({ ...t, status: "error", error: d.detail ?? "The answer could not be completed." }));
      break;
    }
  }
}

/** Minimal spec-compliant SSE reader over a fetch body. */
async function* readSse(body: ReadableStream<Uint8Array>) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const raw = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      let event = "message";
      const dataLines: string[] = [];
      for (const line of raw.split("\n")) {
        if (!line || line.startsWith(":")) continue;
        const colon = line.indexOf(":");
        const field = colon === -1 ? line : line.slice(0, colon);
        const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
        if (field === "event") event = value;
        else if (field === "data") dataLines.push(value);
      }
      if (!dataLines.length) continue;
      try {
        yield { event, data: JSON.parse(dataLines.join("\n")) as unknown };
      } catch {
        // ignore malformed frames
      }
    }
  }
}
