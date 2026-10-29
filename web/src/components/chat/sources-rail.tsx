"use client";

import { useMemo } from "react";
import { OrgMark } from "@/components/illustrations";
import type { SourceRef } from "@/lib/types";
import { hostname } from "@/lib/utils";
import { SourceKindIcon } from "./source-kind";

/** Right rail: every source the focused answer surfaced, grouped by organization. */
export function SourcesRail({
  sources,
  cited,
  onOpenSource,
}: {
  sources: SourceRef[];
  cited: Set<number>;
  onOpenSource: (s: SourceRef) => void;
}) {
  const groups = useMemo(() => {
    const m = new Map<string, { name: string; hue: number; items: SourceRef[] }>();
    for (const s of [...sources].sort((a, b) => a.ref - b.ref)) {
      const g = m.get(s.org_id) ?? { name: s.org_name, hue: s.org_hue, items: [] };
      g.items.push(s);
      m.set(s.org_id, g);
    }
    return [...m.entries()];
  }, [sources]);

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-12 shrink-0 items-center justify-between border-b border-line px-4">
        <span className="text-[13px] font-semibold">Sources</span>
        <span className="font-mono text-[11px] text-ink-3">
          {cited.size} cited · {sources.length} found
        </span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-3 py-3">
        {!sources.length ? (
          <p className="px-1 pt-2 text-[12.5px] leading-relaxed text-ink-3">
            Sources appear here as the agent finds them. Click any citation to read the passage in context.
          </p>
        ) : (
          groups.map(([id, g]) => (
            <section key={id} className="mb-4">
              <h3 className="mb-1.5 flex items-center gap-2 px-1 text-[12px] font-medium text-ink-2">
                <OrgMark name={g.name} hue={g.hue} size={16} />
                {g.name}
                <span className="font-mono text-[10.5px] text-ink-3">{g.items.length}</span>
              </h3>
              <ul className="flex flex-col gap-1">
                {g.items.map((s) => (
                  <li key={s.ref}>
                    <button
                      type="button"
                      onClick={() => onOpenSource(s)}
                      className={`w-full rounded-[10px] border px-2.5 py-2 text-left transition-colors ${
                        cited.has(s.ref) ? "border-line bg-surface hover:border-line-strong" : "border-transparent hover:bg-surface-2"
                      }`}
                    >
                      <div className="flex items-start gap-2">
                        <span
                          className="mt-px grid h-[18px] min-w-[18px] place-items-center rounded-[5px] px-1 font-mono text-[10px] font-medium"
                          style={
                            cited.has(s.ref)
                              ? { background: `hsl(${s.org_hue} 40% var(--org-l) / .14)`, color: `hsl(${s.org_hue} 50% calc(var(--org-l) - 10%))` }
                              : { background: "var(--surface-2)", color: "var(--ink-3)" }
                          }
                        >
                          {s.ref}
                        </span>
                        <div className="min-w-0 flex-1">
                          <div className={`truncate text-[12.5px] font-medium ${cited.has(s.ref) ? "text-ink" : "text-ink-2"}`}>{s.title}</div>
                          <div className="mt-0.5 flex items-center gap-1 text-[11px] text-ink-3">
                            <SourceKindIcon kind={s.source_kind} size={11} />
                            <span className="truncate">{hostname(s.url) ?? s.collection_name}</span>
                          </div>
                          {cited.has(s.ref) ? <p className="mt-1.5 line-clamp-2 text-[11.5px] leading-relaxed text-ink-3">{s.snippet}</p> : null}
                        </div>
                      </div>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          ))
        )}
      </div>
    </div>
  );
}
