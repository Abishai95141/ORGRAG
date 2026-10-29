"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { IconExternal } from "@/components/icons";
import { OrgMark } from "@/components/illustrations";
import { Sheet, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { qk } from "@/lib/queries";
import type { SourceRef } from "@/lib/types";
import { hostname, relativeTime } from "@/lib/utils";

/** Full-document preview with the cited passage highlighted and scrolled into view. */
export function DocumentSheet({ source, onClose }: { source: SourceRef | null; onClose: () => void }) {
  const open = !!source;
  const doc = useQuery({
    queryKey: source ? qk.document(source.org_slug, source.document_id) : ["noop"],
    queryFn: () => api.document(source!.org_slug, source!.document_id),
    enabled: open,
  });
  const bodyRef = useRef<HTMLDivElement>(null);

  // Find the cited snippet in the rendered document and mark it.
  useEffect(() => {
    if (!doc.data || !source || !bodyRef.current) return;
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
  }, [doc.data, source]);

  return (
    <Sheet
      open={open}
      onOpenChange={(v) => !v && onClose()}
      title={source?.title ?? ""}
      subtitle={
        source ? (
          <span className="flex flex-wrap items-center gap-1.5">
            <OrgMark name={source.org_name} hue={source.org_hue} size={14} />
            {source.org_name} · {source.collection_name}
            {doc.data ? <> · updated {relativeTime(doc.data.updated_at)}</> : null}
            {source.url ? (
              <a href={source.url} target="_blank" rel="noreferrer" className="ml-1 inline-flex items-center gap-1 text-ink-2 hover:text-ink">
                <IconExternal size={12} /> {hostname(source.url) ?? "Open original"}
              </a>
            ) : null}
          </span>
        ) : null
      }
    >
      {source?.snippet ? (
        <div className="border-b border-line bg-surface-2/50 px-5 py-3">
          <div className="mb-1 font-mono text-[10.5px] tracking-wider text-ink-3 uppercase">Cited passage [{source.ref}]</div>
          <p className="text-[13px] leading-relaxed text-ink-2">{source.snippet}</p>
        </div>
      ) : null}
      <div ref={bodyRef} className="px-5 py-5">
        {doc.isLoading ? (
          <div className="flex flex-col gap-2.5">
            {[90, 75, 82, 60, 88, 70].map((w, i) => (
              <Skeleton key={i} className="h-3" />
            ))}
          </div>
        ) : doc.isError ? (
          <p className="text-[13px] text-ink-3">This document is no longer available.</p>
        ) : doc.data ? (
          <div className="prose-answer text-[14px]">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{doc.data.content}</ReactMarkdown>
          </div>
        ) : null}
      </div>
    </Sheet>
  );
}
