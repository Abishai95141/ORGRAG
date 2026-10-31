"use client";

import * as Popover from "@radix-ui/react-popover";
import { memo, useMemo, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { IconExternal, IconEye } from "@/components/icons";
import { OrgMark } from "@/components/illustrations";
import type { SourceRef } from "@/lib/types";
import { cn, hostname, orgColor } from "@/lib/utils";
import { BlockedImage, ExternalLink } from "./markdown";
import { citationStyle, originLabel, SourceRefIcon } from "./source-kind";

/**
 * Turn bare citation markers like [3] or [2, 5] into links the renderer can
 * recognise, without touching real markdown links ([text](url)) or code.
 */
function linkCitations(md: string) {
  const parts = md.split(/(```[\s\S]*?```|`[^`\n]*`)/g);
  return parts
    .map((part, i) =>
      i % 2 === 1
        ? part
        : part.replace(/\[(\d{1,3}(?:\s*,\s*\d{1,3})*)\](?![(:])/g, (_, nums: string) =>
            nums
              .split(",")
              .map((n) => `[${n.trim()}](#cite-${n.trim()})`)
              .join(""),
          ),
    )
    .join("");
}

export const Answer = memo(function Answer({
  content,
  sources,
  streaming,
  onOpenSource,
}: {
  content: string;
  sources: SourceRef[];
  streaming?: boolean;
  onOpenSource: (s: SourceRef) => void;
}) {
  const byRef = useMemo(() => new Map(sources.map((s) => [s.ref, s])), [sources]);

  const components = useMemo<Components>(
    () => ({
      a({ href, children }) {
        if (href?.startsWith("#cite-")) {
          const ref = Number(href.slice(6));
          const source = byRef.get(ref);
          // A number that matches no source is shown as such, not hidden: hiding it would
          // make an unsupported claim look like an uncited one.
          return source ? <CitationChip source={source} onOpen={onOpenSource} /> : <UnknownCitation n={ref} />;
        }
        return <ExternalLink href={href}>{children}</ExternalLink>;
      },
      img: ({ src, alt }) => <BlockedImage src={src} alt={alt} />,
    }),
    [byRef, onOpenSource],
  );

  return (
    <div className={cn("prose-answer", streaming && "is-streaming")}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {linkCitations(content)}
      </ReactMarkdown>
    </div>
  );
});

function UnknownCitation({ n }: { n: number }) {
  return (
    <span
      title={`[${n}] doesn't match any source of this answer`}
      aria-label={`Citation ${n} matches no source`}
      className="mx-[1px] inline-flex h-[18px] min-w-[18px] -translate-y-[1px] items-center justify-center rounded-[5px] border border-dashed border-line px-1 align-middle font-mono text-[10.5px] tabular-nums text-ink-3 line-through"
    >
      {n}
    </span>
  );
}

export function CitationChip({ source, onOpen }: { source: SourceRef; onOpen: (s: SourceRef) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <button
          type="button"
          onMouseEnter={() => setOpen(true)}
          onMouseLeave={() => setOpen(false)}
          onClick={() => onOpen(source)}
          className="mx-[1px] inline-flex h-[18px] min-w-[18px] -translate-y-[1px] items-center justify-center rounded-[5px] px-1 align-middle font-mono text-[10.5px] font-medium tabular-nums transition-colors"
          style={citationStyle(source)}
          aria-label={`Source ${source.ref}: ${source.title}`}
        >
          {source.ref}
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          side="top"
          sideOffset={8}
          onMouseEnter={() => setOpen(true)}
          onMouseLeave={() => setOpen(false)}
          onOpenAutoFocus={(e) => e.preventDefault()}
          className="z-50 w-80 animate-fade rounded-[12px] border border-line bg-surface p-3.5 shadow-pop"
        >
          <SourcePreview source={source} onOpen={onOpen} />
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

function SourcePreview({ source, onOpen }: { source: SourceRef; onOpen: (s: SourceRef) => void }) {
  const host = hostname(source.url);
  return (
    <div>
      <div className="flex items-center gap-2 text-[11.5px] text-ink-3">
        {source.origin === "knowledge" && source.org_name && source.org_hue !== null ? (
          <>
            <OrgMark name={source.org_name} hue={source.org_hue} size={16} />
            <span className="font-medium" style={{ color: orgColor(source.org_hue) }}>
              {source.org_name}
            </span>
            <span>·</span>
            <span className="truncate">{source.collection_name}</span>
          </>
        ) : (
          <span className="font-mono text-[10.5px] tracking-wider uppercase">
            {originLabel(source)}
            {source.origin === "web" && host ? ` · ${host}` : ""}
          </span>
        )}
      </div>
      <div className="mt-2 flex items-start gap-2">
        <SourceRefIcon source={source} className="mt-0.5 text-ink-3" />
        <div className="text-[13.5px] leading-snug font-semibold">{source.title}</div>
      </div>
      <p className="mt-2 line-clamp-4 text-[12.5px] leading-relaxed text-ink-2">{source.snippet}</p>
      <div className="mt-3 flex items-center gap-1.5 border-t border-line pt-2.5">
        <button
          onClick={() => onOpen(source)}
          className="inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-[12px] font-medium text-ink-2 hover:bg-surface-2 hover:text-ink"
        >
          <IconEye size={14} /> Preview
        </button>
        {source.url ? (
          <a
            href={source.url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex h-7 items-center gap-1.5 rounded-md px-2 text-[12px] font-medium text-ink-2 hover:bg-surface-2 hover:text-ink"
          >
            <IconExternal size={14} /> {host ?? "Open"}
          </a>
        ) : null}
        <span className="ml-auto font-mono text-[10.5px] text-ink-3">[{source.ref}]</span>
      </div>
    </div>
  );
}
