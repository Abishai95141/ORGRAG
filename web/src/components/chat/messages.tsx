"use client";

import { useState } from "react";
import { toast } from "sonner";
import Link from "next/link";
import { IconCheck, IconCopy, IconDeep, IconPaperclip, IconQuick, IconThumbDown, IconThumbUp, IconVerified, IconWarning } from "@/components/icons";
import { OrgMark, StrataMark } from "@/components/illustrations";
import { useCurrentOrg } from "@/components/layout/org-context";
import { Tooltip } from "@/components/ui";
import { VerifyDialog } from "@/components/verified/verify-dialog";
import { api } from "@/lib/api";
import type { Message, MessageAttachment, Mode, SourceRef, TraceStep } from "@/lib/types";
import { atLeast, cn, formatBytes } from "@/lib/utils";
import { Answer } from "./answer";
import { citationStyle, originLabel, SourceRefIcon } from "./source-kind";
import { Trace } from "./trace";
import type { LiveTurn } from "@/lib/stream";

export function UserMessage({ content, attachments }: { content: string; attachments?: MessageAttachment[] }) {
  return (
    <div className="flex animate-rise flex-col items-end gap-1.5">
      {attachments?.length ? (
        <div className="flex max-w-[85%] flex-wrap justify-end gap-1.5">
          {attachments.map((a) => (
            <span
              key={a.id}
              className="inline-flex max-w-[240px] items-center gap-1.5 rounded-[9px] border border-line bg-surface px-2 py-1 text-[12px] text-ink-2"
            >
              <IconPaperclip size={13} className="shrink-0 text-ink-3" />
              <span className="truncate">{a.filename}</span>
              <span className="shrink-0 font-mono text-[10.5px] text-ink-3">{formatBytes(a.size_bytes)}</span>
            </span>
          ))}
        </div>
      ) : null}
      <div className="max-w-[85%] rounded-[16px] rounded-br-[5px] bg-surface-2 px-4 py-2.5 text-[15px] leading-relaxed whitespace-pre-wrap text-ink">
        {content}
      </div>
    </div>
  );
}

type AssistantProps = {
  slug: string;
  id: string | null;
  content: string;
  sources: SourceRef[];
  steps: TraceStep[];
  mode: Mode;
  status: "connecting" | "streaming" | "complete" | "error" | "stopped";
  error?: string | null;
  feedback?: "up" | "down" | null;
  /** The question this answers (prefills "Verify this answer"). */
  question?: string;
  verifiedAnswerId?: string | null;
  agentName?: string | null;
  focused?: boolean;
  onFocus?: () => void;
  onOpenSource: (s: SourceRef) => void;
};

export function AssistantMessage(props: AssistantProps) {
  const { content, sources, steps, status, mode, onOpenSource } = props;
  const live = status === "connecting" || status === "streaming";
  const cited = citedRefs(content);
  const shownSources = sources.filter((s) => cited.has(s.ref));
  const orgs = uniqueOrgs(shownSources.length ? shownSources : sources);

  return (
    <article
      className={cn("group/msg relative animate-rise rounded-[14px] transition-colors", props.focused && "lg:before:absolute lg:before:inset-y-1 lg:before:-left-4 lg:before:w-[2px] lg:before:rounded-full lg:before:bg-accent/60")}
      onMouseDown={props.onFocus}
    >
      <header className="mb-2 flex items-center gap-2 text-[12.5px] text-ink-3">
        <StrataMark size={16} className="text-ink" />
        <span className="font-medium text-ink-2">{props.agentName ?? "Strata"}</span>
        <span className="inline-flex items-center gap-1 rounded-[6px] border border-line px-1.5 py-px font-mono text-[10.5px] tracking-wide uppercase">
          {mode === "deep" ? <IconDeep size={11} /> : <IconQuick size={11} />}
          {mode}
        </span>
        {orgs.length > 1 ? (
          <span className="inline-flex items-center gap-1.5">
            <span className="flex -space-x-1">
              {orgs.map((o) => (
                <OrgMark key={o.id} name={o.name} hue={o.hue} size={15} className="ring-2 ring-bg" />
              ))}
            </span>
            {orgs.length} organizations
          </span>
        ) : null}
      </header>

      <Trace steps={steps} sources={sources} live={live} onOpenSource={onOpenSource} />

      {status === "connecting" && !steps.length ? (
        <div className="flex items-center gap-2 text-[13px] text-ink-3">
          <span className="strata-pulse h-2 w-2 rounded-full bg-accent" />
          Starting…
        </div>
      ) : null}

      {content ? <Answer content={content} sources={sources} streaming={status === "streaming"} onOpenSource={onOpenSource} /> : null}

      {status === "error" ? (
        <div className="mt-3 flex items-start gap-2 rounded-[10px] border border-err/30 bg-err-soft px-3 py-2 text-[13px] text-err">
          <IconWarning size={16} className="mt-px shrink-0" />
          <span>{props.error ?? "The answer could not be completed."}</span>
        </div>
      ) : null}
      {status === "stopped" ? <p className="mt-2 text-[12.5px] text-ink-3">Stopped.</p> : null}

      {shownSources.length && !live ? <SourceStrip sources={shownSources} onOpenSource={onOpenSource} /> : null}

      {!live && props.id && content ? (
        <MessageToolbar
          slug={props.slug}
          id={props.id}
          content={content}
          feedback={props.feedback ?? null}
          verify={
            status === "complete"
              ? {
                  question: props.question,
                  verifiedAnswerId: props.verifiedAnswerId ?? null,
                  sourceCollections: shownSources.filter((s) => s.org_slug === props.slug && s.collection_name).map((s) => s.collection_name!),
                }
              : null
          }
        />
      ) : null}
    </article>
  );
}

function SourceStrip({ sources, onOpenSource }: { sources: SourceRef[]; onOpenSource: (s: SourceRef) => void }) {
  return (
    <div className="mt-5 grid gap-2 sm:grid-cols-2">
      {sources.slice(0, 6).map((s) => (
        <button
          key={s.ref}
          type="button"
          onClick={() => onOpenSource(s)}
          className="group/src flex min-w-0 items-start gap-2.5 rounded-[11px] border border-line bg-surface px-3 py-2.5 text-left transition-colors hover:border-line-strong"
        >
          <span
            className="mt-px grid h-5 min-w-5 place-items-center rounded-[5px] px-1 font-mono text-[10.5px] font-medium"
            style={citationStyle(s)}
          >
            {s.ref}
          </span>
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[13px] font-medium text-ink">{s.title}</span>
            <span className="mt-0.5 flex items-center gap-1.5 text-[11.5px] text-ink-3">
              <SourceRefIcon source={s} size={12} />
              <span className="truncate">
                {s.origin === "knowledge" ? `${s.org_name} · ${s.collection_name}` : originLabel(s)}
              </span>
            </span>
          </span>
        </button>
      ))}
    </div>
  );
}

function MessageToolbar({
  slug,
  id,
  content,
  feedback,
  verify,
}: {
  slug: string;
  id: string;
  content: string;
  feedback: "up" | "down" | null;
  verify: { question?: string; verifiedAnswerId: string | null; sourceCollections: string[] } | null;
}) {
  const { org } = useCurrentOrg();
  const [copied, setCopied] = useState(false);
  const [fb, setFb] = useState(feedback);
  const [verifying, setVerifying] = useState(false);
  const [verifiedId, setVerifiedId] = useState(verify?.verifiedAnswerId ?? null);
  const canVerify = !!verify && atLeast(org.role, "admin");

  async function copy() {
    await navigator.clipboard.writeText(content.replace(/\[(\d+(?:,\s*\d+)*)\]/g, "")).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 1400);
  }

  async function rate(next: "up" | "down") {
    const value = fb === next ? null : next;
    setFb(value);
    try {
      await api.feedback(slug, id, value);
      if (value) toast(value === "up" ? "Thanks — marked as helpful" : "Thanks — we'll learn from this");
    } catch {
      setFb(fb);
    }
  }

  const btn = "grid h-7 w-7 place-items-center rounded-md text-ink-3 transition-colors hover:bg-surface-2 hover:text-ink";
  return (
    <div className="mt-3 flex items-center gap-0.5 opacity-70 transition-opacity group-hover/msg:opacity-100">
      <Tooltip label={copied ? "Copied" : "Copy answer"}>
        <button type="button" className={btn} onClick={copy} aria-label="Copy answer">
          {copied ? <IconCheck size={15} /> : <IconCopy size={15} />}
        </button>
      </Tooltip>
      <Tooltip label="Helpful">
        <button type="button" className={cn(btn, fb === "up" && "text-ok")} onClick={() => rate("up")} aria-pressed={fb === "up"} aria-label="Helpful">
          <IconThumbUp size={15} />
        </button>
      </Tooltip>
      <Tooltip label="Not helpful">
        <button type="button" className={cn(btn, fb === "down" && "text-err")} onClick={() => rate("down")} aria-pressed={fb === "down"} aria-label="Not helpful">
          <IconThumbDown size={15} />
        </button>
      </Tooltip>
      {verifiedId ? (
        <Tooltip label="Verified answer — open in Verified answers">
          <Link href={`/o/${slug}/verified?focus=${verifiedId}`} className={cn(btn, "text-accent")} aria-label="Verified answer">
            <IconVerified size={15} />
          </Link>
        </Tooltip>
      ) : canVerify ? (
        <>
          <Tooltip label="Verify this answer">
            <button type="button" className={btn} onClick={() => setVerifying(true)} aria-label="Verify this answer">
              <IconVerified size={15} />
            </button>
          </Tooltip>
          <VerifyDialog
            open={verifying}
            onOpenChange={setVerifying}
            messageId={id}
            question={verify?.question}
            answer={content}
            sourceCollections={verify?.sourceCollections}
            onDone={(v) => setVerifiedId(v.id)}
          />
        </>
      ) : null}
    </div>
  );
}

export function citedRefs(content: string) {
  const out = new Set<number>();
  for (const m of content.matchAll(/\[(\d{1,3}(?:\s*,\s*\d{1,3})*)\]/g)) {
    m[1].split(",").forEach((n) => out.add(Number(n.trim())));
  }
  return out;
}

export function uniqueOrgs(sources: SourceRef[]) {
  const seen = new Map<string, { id: string; name: string; hue: number; slug: string }>();
  for (const s of sources) {
    if (s.origin !== "knowledge" || !s.org_id || seen.has(s.org_id)) continue;
    seen.set(s.org_id, { id: s.org_id, name: s.org_name ?? "", hue: s.org_hue ?? 0, slug: s.org_slug ?? "" });
  }
  return [...seen.values()];
}

export function messageStatus(m: Message): AssistantProps["status"] {
  return m.status === "streaming" || m.status === "error" || m.status === "stopped" ? m.status : "complete";
}

/**
 * One polite live region per chat view: announces the live turn's progress to screen
 * readers (not every token, which would be unusable) and errors immediately.
 */
export function TurnAnnouncer({ status, sources, error }: { status: LiveTurn["status"] | null; sources: number; error: string | null }) {
  const message =
    status === "connecting"
      ? "Researching your question."
      : status === "streaming"
        ? "Writing the answer."
        : status === "done"
          ? `Answer ready${sources ? `, with ${sources} source${sources === 1 ? "" : "s"}` : ""}.`
          : status === "stopped"
            ? "Answer stopped."
            : "";
  return (
    <>
      <div role="status" aria-live="polite" className="sr-only">
        {message}
      </div>
      <div role="alert" className="sr-only">
        {status === "error" ? `The answer failed: ${error ?? "unknown error"}` : ""}
      </div>
    </>
  );
}
