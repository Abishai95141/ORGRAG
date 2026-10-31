"use client";

import { use, useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Composer, type ComposerState } from "@/components/chat/composer";
import { DocumentSheet } from "@/components/chat/document-sheet";
import { AssistantMessage, citedRefs, messageStatus, TurnAnnouncer, UserMessage } from "@/components/chat/messages";
import { SourcesRail } from "@/components/chat/sources-rail";
import { IconSidebar } from "@/components/icons";
import { NoResultsArt, OrgMark } from "@/components/illustrations";
import { useCurrentOrg } from "@/components/layout/org-context";
import { Button, EmptyState, Skeleton, Tooltip } from "@/components/ui";
import { api } from "@/lib/api";
import { qk, useAgents, useHealth, useOrg } from "@/lib/queries";
import { clearTurn, startTurn, stopTurn, useLiveTurn } from "@/lib/stream";
import type { Message, SourceRef } from "@/lib/types";
import { cn } from "@/lib/utils";

/** The attachments to send with a message, in the shape a user message shows them. */
function sendable(state: ComposerState) {
  return (state.attachments ?? []).flatMap((a) =>
    a.status === "ready" && a.attachment
      ? [{ id: a.attachment.id, filename: a.attachment.filename, mime: a.attachment.mime, size_bytes: a.attachment.size_bytes, token_count: a.attachment.token_count }]
      : [],
  );
}

export default function ConversationPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { org, orgs } = useCurrentOrg();
  const qc = useQueryClient();
  const convo = useQuery({ queryKey: qk.conversation(org.slug, id), queryFn: () => api.conversation(org.slug, id) });
  const agents = useAgents(org.slug);
  const health = useHealth();
  const fullOrg = useOrg(org.slug);
  const live = useLiveTurn(id);

  const [preview, setPreview] = useState<SourceRef | null>(null);
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const [rail, setRail] = useState(true);
  const [state, setState] = useState<ComposerState>({ text: "", mode: "quick", scope: [org.slug], agentId: null });
  const hydrated = useRef(false);

  // Seed composer settings from the conversation once.
  useEffect(() => {
    const c = convo.data?.conversation;
    if (!c || hydrated.current) return;
    hydrated.current = true;
    const slugs = orgs.filter((o) => c.scope_org_ids.includes(o.id)).map((o) => o.slug);
    setState((s) => ({ ...s, mode: c.mode, scope: slugs.length ? slugs : [org.slug], agentId: c.agent_id }));
  }, [convo.data, orgs, org.slug]);

  const persisted = useMemo(() => convo.data?.messages ?? [], [convo.data]);

  // Once the server has the live answer persisted, drop the overlay.
  useEffect(() => {
    if (!live || live.status === "connecting" || live.status === "streaming") return;
    const landed = persisted.some((m) => m.id === live.assistantId && m.status !== "streaming");
    if (landed) clearTurn(id);
  }, [live, persisted, id]);

  const liveActive = !!live && !persisted.some((m) => m.id === live.assistantId && m.status !== "streaming");
  const streaming = !!live && (live.status === "connecting" || live.status === "streaming");

  const rows = useMemo(() => {
    const list: Message[] = persisted.filter((m) => !(liveActive && (m.id === live?.assistantId || m.id === live?.userMessage?.id)));
    return list;
  }, [persisted, liveActive, live]);

  // Which answer the sources rail describes: the focused one, else the latest.
  const lastAssistant = [...rows].reverse().find((m) => m.role === "assistant");
  const railSource = liveActive && live
    ? { sources: live.sources, content: live.content }
    : (() => {
        const m = rows.find((r) => r.id === focusedId) ?? lastAssistant;
        return m ? { sources: m.sources, content: m.content } : { sources: [], content: "" };
      })();

  /* ---------- scrolling ---------- */
  const scroller = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);
  const onScroll = () => {
    const el = scroller.current;
    if (el) pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
  };
  const contentKey = `${rows.length}:${live?.content.length ?? 0}:${live?.steps.length ?? 0}:${live?.sources.length ?? 0}`;
  useEffect(() => {
    const el = scroller.current;
    if (el && pinned.current) el.scrollTo({ top: el.scrollHeight, behavior: streaming ? "auto" : "smooth" });
  }, [contentKey, streaming]);

  const send = useCallback(() => {
    const text = state.text.trim();
    if (!text) return;
    const attachments = sendable(state);
    setState((s) => ({ ...s, text: "", attachments: [] }));
    pinned.current = true;
    void startTurn({
      slug: org.slug,
      conversationId: id,
      content: text,
      mode: state.mode,
      scopeOrgSlugs: state.scope,
      attachments,
      webSearch: state.web,
      onSettled: () => {
        qc.invalidateQueries({ queryKey: qk.conversation(org.slug, id) });
        qc.invalidateQueries({ queryKey: qk.conversations(org.slug) });
      },
    });
  }, [state, org.slug, id, qc]);

  const openSource = useCallback((s: SourceRef) => setPreview(s), []);

  if (convo.isError) {
    return (
      <div className="grid h-full place-items-center">
        <EmptyState
          art={<NoResultsArt />}
          title="Conversation not found"
          description="It may have been deleted."
          action={
            <Link href={`/o/${org.slug}/chat`}>
              <Button variant="primary">New question</Button>
            </Link>
          }
        />
      </div>
    );
  }

  const conversation = convo.data?.conversation;
  const agent = agents.data?.find((a) => a.id === conversation?.agent_id);
  const scopeOrgs = orgs.filter((o) => conversation?.scope_org_ids.includes(o.id));

  return (
    <div className="flex h-full">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-12 shrink-0 items-center gap-3 border-b border-line px-5">
          <h1 className="min-w-0 truncate text-[14px] font-medium">{conversation?.title || (live ? live.pendingText : "…")}</h1>
          {scopeOrgs.length > 1 ? (
            <span className="flex shrink-0 items-center gap-1.5 text-[12px] text-ink-3">
              <span className="flex -space-x-1">
                {scopeOrgs.map((o) => (
                  <OrgMark key={o.id} name={o.name} hue={o.hue} size={16} className="ring-2 ring-bg" />
                ))}
              </span>
              <span className="hidden sm:inline">{scopeOrgs.length} organizations</span>
            </span>
          ) : null}
          <div className="ml-auto hidden xl:block">
            <Tooltip label={rail ? "Hide sources" : "Show sources"}>
              <Button variant="ghost" size="icon-sm" onClick={() => setRail((v) => !v)} aria-label="Toggle sources">
                <IconSidebar size={17} className="-scale-x-100" />
              </Button>
            </Tooltip>
          </div>
        </div>

        <div ref={scroller} onScroll={onScroll} className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto flex max-w-[760px] flex-col gap-8 px-5 pt-8 pb-6">
            {convo.isLoading && !live ? (
              <div className="flex flex-col gap-4">
                <Skeleton className="ml-auto h-10 w-2/3 rounded-2xl" />
                <Skeleton className="h-4 w-1/3" />
                <Skeleton className="h-24 w-full" />
              </div>
            ) : null}

            {rows.map((m, i) =>
              m.role === "user" ? (
                <UserMessage key={m.id} content={m.content} attachments={m.attachments} />
              ) : (
                <AssistantMessage
                  key={m.id}
                  slug={org.slug}
                  id={m.id}
                  content={m.content}
                  sources={m.sources}
                  steps={m.trace}
                  mode={conversation?.mode ?? "quick"}
                  status={messageStatus(m)}
                  feedback={m.feedback}
                  question={rows.slice(0, i).findLast((r) => r.role === "user")?.content}
                  verifiedAnswerId={m.verified_answer_id}
                  shareToken={m.share_token ?? null}
                  agentName={agent?.name}
                  focused={rail && !liveActive && (focusedId ? focusedId === m.id : m.id === lastAssistant?.id)}
                  onFocus={() => setFocusedId(m.id)}
                  onOpenSource={openSource}
                />
              ),
            )}

            <TurnAnnouncer status={live?.status ?? null} sources={live?.final?.sources.length ?? live?.sources.length ?? 0} error={live?.error ?? null} />
            {liveActive && live ? (
              <>
                <UserMessage
                  content={live.userMessage?.content ?? live.pendingText}
                  attachments={live.userMessage?.attachments ?? live.pendingAttachments}
                />
                <AssistantMessage
                  slug={org.slug}
                  id={live.final?.id ?? null}
                  content={live.content}
                  sources={live.sources}
                  steps={live.steps}
                  mode={live.mode}
                  status={live.status === "done" ? "complete" : live.status}
                  error={live.error}
                  agentName={agent?.name}
                  onOpenSource={openSource}
                />
              </>
            ) : null}
          </div>
        </div>

        <div className="relative shrink-0 px-5 pb-5">
          <div className="pointer-events-none absolute inset-x-0 -top-8 h-8 bg-gradient-to-t from-bg to-transparent" />
          <div className="mx-auto max-w-[760px]">
            <Composer
              value={state}
              onChange={setState}
              onSubmit={send}
              onStop={() => stopTurn(id)}
              streaming={streaming}
              orgs={orgs}
              currentSlug={org.slug}
              agents={agent ? [agent] : undefined}
              agentLocked
              placeholder="Ask a follow-up…"
              slug={org.slug}
              webAvailable={!!health.data?.web_search_provider}
              webDefault={fullOrg.data?.settings.web_search ?? false}
            />
          </div>
        </div>
      </div>

      <aside className={cn("hidden w-[320px] shrink-0 border-l border-line bg-surface-2/30", rail && "xl:block")}>
        <SourcesRail sources={railSource.sources} cited={citedRefs(railSource.content)} onOpenSource={openSource} />
      </aside>

      <DocumentSheet source={preview} onClose={() => setPreview(null)} />
    </div>
  );
}
