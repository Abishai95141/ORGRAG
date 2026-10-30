"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Composer, type ComposerState } from "@/components/chat/composer";
import { IconArrowRight, IconInfo } from "@/components/icons";
import { ExcavationArt, GlyphTile } from "@/components/illustrations";
import { useCurrentOrg } from "@/components/layout/org-context";
import { api, ApiError } from "@/lib/api";
import { qk, useAgents, useHealth, useOrg } from "@/lib/queries";
import { startTurn } from "@/lib/stream";
import { cn } from "@/lib/utils";

function greeting(name: string) {
  const h = new Date().getHours();
  const first = name.split(" ")[0];
  return h < 5 ? `Up late, ${first}?` : h < 12 ? `Morning, ${first}.` : h < 18 ? `Afternoon, ${first}.` : `Evening, ${first}.`;
}

/** The attachments to send with a message, in the shape a user message shows them. */
function sendable(state: ComposerState) {
  return (state.attachments ?? []).flatMap((a) =>
    a.status === "ready" && a.attachment
      ? [{ id: a.attachment.id, filename: a.attachment.filename, mime: a.attachment.mime, size_bytes: a.attachment.size_bytes, token_count: a.attachment.token_count }]
      : [],
  );
}

function NewChat() {
  const { org, orgs, user } = useCurrentOrg();
  const router = useRouter();
  const qc = useQueryClient();
  const agents = useAgents(org.slug);
  const health = useHealth();
  const fullOrg = useOrg(org.slug);
  const [state, setState] = useState<ComposerState>({ text: "", mode: "quick", scope: [org.slug], agentId: null });
  const [sending, setSending] = useState(false);
  const params = useSearchParams();

  // Deep link from the Agents page: /chat?agent=<id>
  const agentParam = params.get("agent");
  useEffect(() => {
    const a = agents.data?.find((x) => x.id === agentParam);
    if (a) setState((s) => ({ ...s, agentId: a.id, mode: a.mode }));
  }, [agentParam, agents.data]);

  const agent = agents.data?.find((a) => a.id === state.agentId) ?? null;
  const starters = useMemo(() => {
    if (agent) return agent.starter_prompts.map((p) => ({ prompt: p, agent }));
    return (agents.data ?? []).flatMap((a) => a.starter_prompts.slice(0, 2).map((p) => ({ prompt: p, agent: a }))).slice(0, 4);
  }, [agent, agents.data]);

  async function send(text = state.text, agentId = state.agentId, mode = state.mode) {
    if (!text.trim() || sending) return;
    setSending(true);
    try {
      const convo = await api.createConversation(org.slug, { agent_id: agentId, mode, scope_org_slugs: state.scope });
      qc.setQueryData(qk.conversation(org.slug, convo.id), { conversation: convo, messages: [] });
      void startTurn({
        slug: org.slug,
        conversationId: convo.id,
        content: text.trim(),
        mode,
        scopeOrgSlugs: state.scope,
        attachments: sendable(state),
        webSearch: state.web,
        onSettled: () => {
          qc.invalidateQueries({ queryKey: qk.conversation(org.slug, convo.id) });
          qc.invalidateQueries({ queryKey: qk.conversations(org.slug) });
          // Titles are refined in the background; pick them up shortly after.
          setTimeout(() => qc.invalidateQueries({ queryKey: qk.conversations(org.slug) }), 4000);
        },
      });
      qc.invalidateQueries({ queryKey: qk.conversations(org.slug) });
      router.push(`/o/${org.slug}/chat/${convo.id}`);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Could not start the conversation");
      setSending(false);
    }
  }

  const multi = state.scope.length > 1;

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto flex min-h-full max-w-[760px] flex-col px-5 pt-[12vh] pb-12">
        <div className="animate-rise">
          <ExcavationArt className="mb-6 !max-w-[150px] opacity-90" />
          <h1 className="font-display text-[44px] leading-[1.02] tracking-tight sm:text-[52px]">{greeting(user.name)}</h1>
          <p className="mt-3 text-[15px] leading-relaxed text-ink-3">
            {multi ? (
              <>Ask across {state.scope.length} organizations — every answer shows which one each source came from.</>
            ) : (
              <>
                Ask about anything in <span className="font-medium text-ink-2">{org.name}</span>
                {orgs.length > 1 ? <>, or widen the scope to every organization you belong to.</> : "."}
              </>
            )}
          </p>
        </div>

        <div className="mt-8 animate-rise [animation-delay:60ms]">
          <Composer
            value={state}
            onChange={setState}
            onSubmit={() => send()}
            streaming={sending}
            orgs={orgs}
            currentSlug={org.slug}
            agents={agents.data}
            autoFocus
            size="lg"
            placeholder={agent ? `Ask ${agent.name}…` : undefined}
            slug={org.slug}
            webAvailable={!!health.data?.web_search_provider}
            webDefault={fullOrg.data?.settings.web_search ?? false}
          />
          {health.data && !health.data.llm_configured ? (
            <p className="mt-3 flex items-start gap-2 text-[12.5px] leading-relaxed text-ink-3">
              <IconInfo size={15} className="mt-px shrink-0 text-warn" />
              No model API key is configured, so Strata will search and show sources without writing an answer. Set
              NVIDIA_API_KEY or ANTHROPIC_API_KEY on the server to enable the agent.
            </p>
          ) : null}
        </div>

        {starters.length ? (
          <div className="mt-10 animate-rise [animation-delay:120ms]">
            <div className="mb-3 font-mono text-[10.5px] tracking-wider text-ink-3 uppercase">{agent ? `Try ${agent.name}` : "Start from an agent"}</div>
            <div className="grid gap-2 sm:grid-cols-2">
              {starters.map(({ prompt, agent: a }) => (
                <button
                  key={`${a.id}-${prompt}`}
                  type="button"
                  onClick={() => {
                    setState((s) => ({ ...s, agentId: a.id, mode: a.mode }));
                    void send(prompt, a.id, a.mode);
                  }}
                  className={cn(
                    "group flex items-start gap-3 rounded-[14px] border border-line bg-surface p-3.5 text-left transition-all",
                    "hover:-translate-y-px hover:border-line-strong hover:shadow-[0_8px_24px_-16px_rgba(0,0,0,.3)]",
                  )}
                >
                  <GlyphTile name={a.glyph} hue={org.hue} size={32} />
                  <span className="min-w-0 flex-1">
                    <span className="block text-[11.5px] font-medium text-ink-3">{a.name}</span>
                    <span className="mt-0.5 block text-[13.5px] leading-snug text-ink">{prompt}</span>
                  </span>
                  <IconArrowRight size={15} className="mt-1 text-ink-3 opacity-0 transition-opacity group-hover:opacity-100" />
                </button>
              ))}
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

export default function NewChatPage() {
  return (
    <Suspense>
      <NewChat />
    </Suspense>
  );
}
