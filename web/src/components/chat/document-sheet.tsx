"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { toast } from "sonner";
import { IconBoost, IconExternal, IconEye, IconEyeOff, IconGlobe, IconPaperclip, IconThumbDown, IconThumbUp } from "@/components/icons";
import { OrgMark } from "@/components/illustrations";
import { useCurrentOrg } from "@/components/layout/org-context";
import { Badge, Segmented, Sheet, Skeleton, Tooltip } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { qk, useMe } from "@/lib/queries";
import type { DocumentDetail, SourceRef, Vote } from "@/lib/types";
import { atLeast, cn, formatBytes, hostname, relativeTime } from "@/lib/utils";

/**
 * Preview for any citation. Knowledge documents open in full with the cited
 * passage highlighted, plus reader votes and (for admins) boost / hide controls.
 * Attached files show their extracted text; web results show the snippet and a link.
 */
export function DocumentSheet({ source, onClose }: { source: SourceRef | null; onClose: () => void }) {
  const open = !!source;
  const isDoc = source?.origin === "knowledge" && !!source.document_id && !!source.org_slug;
  const isFile = source?.origin === "attachment" && !!source.attachment_id;
  const docSlug = source?.org_slug ?? "";
  const me = useMe();
  const role = me.data?.orgs.find((o) => o.slug === docSlug)?.role;

  const doc = useQuery({
    queryKey: isDoc ? qk.document(docSlug, source!.document_id!) : ["noop"],
    queryFn: () => api.document(docSlug, source!.document_id!),
    enabled: open && isDoc,
  });
  // Attachments belong to the conversation, which lives in the current org.
  const fileSlug = useCurrentOrg().org.slug;
  const file = useQuery({
    queryKey: isFile ? ["attachment", source!.attachment_id] : ["noop-file"],
    queryFn: () => api.attachment(fileSlug, source!.attachment_id!),
    enabled: open && isFile,
  });
  const body = isDoc ? doc.data?.content : isFile ? file.data?.content : undefined;
  const loading = isDoc ? doc.isLoading : isFile ? file.isLoading : false;
  const failed = isDoc ? doc.isError : isFile ? file.isError : false;
  const bodyRef = useRef<HTMLDivElement>(null);

  // Find the cited snippet in the rendered document and mark it.
  useEffect(() => {
    if (!body || !source || !bodyRef.current || source.origin !== "knowledge") return;
    const needle = source.snippet.replace(/\s+/g, " ").trim().slice(0, 60).toLowerCase();
    if (needle.length < 12) return;
    const blocks = bodyRef.current.querySelectorAll<HTMLElement>("p, li, td, h1, h2, h3, h4, pre");
    for (const el of blocks) {
      const text = el.textContent?.replace(/\s+/g, " ").toLowerCase() ?? "";
      if (text.includes(needle.slice(0, 40))) {
        el.style.background = "var(--accent-soft)";
        el.style.boxShadow = "0 0 0 6px var(--accent-soft)";
        el.style.borderRadius = "4px";
        el.scrollIntoView({ block: "center", behavior: "smooth" });
        break;
      }
    }
  }, [body, source]);

  return (
    <Sheet
      open={open}
      onOpenChange={(v) => !v && onClose()}
      title={source?.title ?? ""}
      subtitle={source ? <Subtitle source={source} doc={doc.data} size={file.data?.size_bytes} /> : null}
    >
      {isDoc && doc.data ? <DocumentActions slug={docSlug} doc={doc.data} canCurate={atLeast(role, "admin")} /> : null}

      {source?.snippet && source.origin !== "attachment" ? (
        <div className="border-b border-line bg-surface-2/50 px-5 py-3">
          <div className="mb-1 font-mono text-[10.5px] tracking-wider text-ink-3 uppercase">
            {source.origin === "web" ? "Search result" : "Cited passage"} {source.ref ? `[${source.ref}]` : ""}
          </div>
          <p className="text-[13px] leading-relaxed text-ink-2">{source.snippet}</p>
        </div>
      ) : null}

      <div ref={bodyRef} className="px-5 py-5">
        {source?.origin === "web" ? (
          <div className="flex flex-col items-start gap-3 text-[13px] text-ink-2">
            <p className="leading-relaxed text-ink-3">
              This source came from a web search. Strata reads the page when it needs more than the snippet; open
              the original to see it in full.
            </p>
            {source.url ? (
              <a
                href={source.url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex h-8 items-center gap-1.5 rounded-[9px] border border-line px-3 font-medium text-ink hover:border-line-strong"
              >
                <IconExternal size={14} /> Open {hostname(source.url) ?? "page"}
              </a>
            ) : null}
          </div>
        ) : loading ? (
          <div className="flex flex-col gap-2.5">
            {[90, 75, 82, 60, 88, 70].map((w, i) => (
              <Skeleton key={i} className="h-3" />
            ))}
          </div>
        ) : failed ? (
          <p className="text-[13px] text-ink-3">This {isFile ? "file" : "document"} is no longer available.</p>
        ) : body ? (
          <div className="prose-answer text-[14px]">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{body}</ReactMarkdown>
          </div>
        ) : null}
      </div>
    </Sheet>
  );
}

function Subtitle({ source, doc, size }: { source: SourceRef; doc?: DocumentDetail; size?: number }) {
  if (source.origin === "web") {
    return (
      <span className="flex items-center gap-1.5">
        <IconGlobe size={13} /> Web · {hostname(source.url) ?? "search result"}
      </span>
    );
  }
  if (source.origin === "attachment") {
    return (
      <span className="flex items-center gap-1.5">
        <IconPaperclip size={13} /> Attached to this conversation{size ? ` · ${formatBytes(size)}` : ""}
      </span>
    );
  }
  const changed = doc?.source_updated_at ?? doc?.updated_at;
  return (
    <span className="flex flex-wrap items-center gap-1.5">
      {source.org_name && source.org_hue !== null ? <OrgMark name={source.org_name} hue={source.org_hue} size={14} /> : null}
      {source.org_name} · {source.collection_name}
      {changed ? <> · updated {relativeTime(changed)}</> : null}
      {source.url ? (
        <a href={source.url} target="_blank" rel="noreferrer" className="ml-1 inline-flex items-center gap-1 text-ink-2 hover:text-ink">
          <IconExternal size={12} /> {hostname(source.url) ?? "Open original"}
        </a>
      ) : null}
    </span>
  );
}

const BOOSTS = [
  { value: "-2", label: "Lower" },
  { value: "0", label: "Normal" },
  { value: "2", label: "Higher" },
] as const;

/** Reader votes for everyone; boost and hide for admins. Both shape future ranking. */
function DocumentActions({ slug, doc, canCurate }: { slug: string; doc: DocumentDetail; canCurate: boolean }) {
  const qc = useQueryClient();
  const key = qk.document(slug, doc.id);
  const update = (patch: Partial<DocumentDetail>) => qc.setQueryData<DocumentDetail>(key, (d) => (d ? { ...d, ...patch } : d));

  const vote = useMutation({
    mutationFn: (v: Vote | null) => api.voteDocument(slug, doc.id, v),
    onSuccess: (r) => {
      update({ my_vote: r.vote, feedback_up: r.feedback_up, feedback_down: r.feedback_down });
      qc.invalidateQueries({ queryKey: ["org", slug, "sources"] });
    },
    onError: () => toast.error("Couldn't save your vote"),
  });
  const curate = useMutation({
    mutationFn: (body: { boost?: number; hidden?: boolean }) => api.curateDocument(slug, doc.id, body),
    onSuccess: (d) => {
      update({ boost: d.boost, hidden: d.hidden });
      qc.invalidateQueries({ queryKey: ["org", slug, "documents"] });
      qc.invalidateQueries({ queryKey: ["org", slug, "sources"] });
      toast(d.hidden ? "Hidden from search and answers" : "Ranking updated");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Couldn't update the document"),
  });

  const btn = "inline-flex h-7 items-center gap-1.5 rounded-[8px] px-2 text-[12px] font-medium transition-colors";
  const boostValue = String(doc.boost <= -1 ? -2 : doc.boost >= 1 ? 2 : 0) as (typeof BOOSTS)[number]["value"];

  return (
    <div className="sticky top-0 z-10 flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line bg-surface/95 px-5 py-2.5 backdrop-blur">
      <div className="flex items-center gap-1">
        <span className="mr-1 text-[12px] text-ink-3">Useful source?</span>
        <button
          type="button"
          aria-pressed={doc.my_vote === "up"}
          onClick={() => vote.mutate(doc.my_vote === "up" ? null : "up")}
          className={cn(btn, doc.my_vote === "up" ? "bg-ok-soft text-ok" : "text-ink-3 hover:bg-surface-2 hover:text-ink")}
        >
          <IconThumbUp size={14} />
          <span className="font-mono text-[11px] tabular-nums">{doc.feedback_up}</span>
        </button>
        <button
          type="button"
          aria-pressed={doc.my_vote === "down"}
          onClick={() => vote.mutate(doc.my_vote === "down" ? null : "down")}
          className={cn(btn, doc.my_vote === "down" ? "bg-err-soft text-err" : "text-ink-3 hover:bg-surface-2 hover:text-ink")}
        >
          <IconThumbDown size={14} />
          <span className="font-mono text-[11px] tabular-nums">{doc.feedback_down}</span>
        </button>
      </div>

      {canCurate ? (
        <div className="ml-auto flex items-center gap-2">
          {doc.hidden ? <Badge tone="warn">Hidden</Badge> : null}
          <Tooltip label="Ranking boost for this document">
            <span className="text-ink-3">
              <IconBoost size={15} />
            </span>
          </Tooltip>
          <Segmented
            size="sm"
            value={boostValue}
            onChange={(v) => curate.mutate({ boost: Number(v) })}
            options={BOOSTS.map((b) => ({ value: b.value, label: b.label }))}
          />
          <Tooltip label={doc.hidden ? "Show in search again" : "Hide from search and answers"}>
            <button
              type="button"
              onClick={() => curate.mutate({ hidden: !doc.hidden })}
              aria-label={doc.hidden ? "Unhide document" : "Hide document"}
              className={cn(btn, "text-ink-3 hover:bg-surface-2 hover:text-ink")}
            >
              {doc.hidden ? <IconEye size={15} /> : <IconEyeOff size={15} />}
            </button>
          </Tooltip>
        </div>
      ) : null}
    </div>
  );
}
