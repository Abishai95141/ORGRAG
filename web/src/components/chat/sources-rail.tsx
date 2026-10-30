"use client";

import { useMemo } from "react";
import { IconGlobe, IconPaperclip } from "@/components/icons";
import { OrgMark } from "@/components/illustrations";
import type { SourceRef } from "@/lib/types";
import { hostname } from "@/lib/utils";
import { citationStyle, SourceRefIcon } from "./source-kind";

type Group = { key: string; name: string; hue: number | null; origin: SourceRef["origin"]; items: SourceRef[] };
const ORIGIN_ORDER = { attachment: 0, knowledge: 1, web: 2 } as const;

/** Right rail: every source the focused answer surfaced — attached files first, then
 * each organization, then the web. */
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
    const m = new Map<string, Group>();
    for (const s of [...sources].sort((a, b) => a.ref - b.ref)) {
      const key = s.origin === "knowledge" ? (s.org_id ?? "org") : s.origin;
      const name = s.origin === "web" ? "Web" : s.origin === "attachment" ? "Attached files" : (s.org_name ?? "");
      const g = m.get(key) ?? { key, name, hue: s.org_hue, origin: s.origin, items: [] };
      g.items.push(s);
      m.set(key, g);
    }
    return [...m.values()].sort((a, b) => ORIGIN_ORDER[a.origin] - ORIGIN_ORDER[b.origin]);
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
          groups.map((g) => (
            <section key={g.key} className="mb-4">
              <h3 className="mb-1.5 flex items-center gap-2 px-1 text-[12px] font-medium text-ink-2">
                {g.origin === "knowledge" && g.hue !== null ? (
                  <OrgMark name={g.name} hue={g.hue} size={16} />
                ) : (
                  <span className="grid h-4 w-4 place-items-center rounded-[4px] bg-surface-3 text-ink-2">
                    {g.origin === "web" ? <IconGlobe size={11} /> : <IconPaperclip size={11} />}
                  </span>
                )}
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
                          style={citationStyle(s, cited.has(s.ref))}
                        >
                          {s.ref}
                        </span>
                        <div className="min-w-0 flex-1">
                          <div className={`truncate text-[12.5px] font-medium ${cited.has(s.ref) ? "text-ink" : "text-ink-2"}`}>{s.title}</div>
                          <div className="mt-0.5 flex items-center gap-1 text-[11px] text-ink-3">
                            <SourceRefIcon source={s} size={11} />
                            <span className="truncate">
                              {hostname(s.url) ?? s.collection_name ?? (s.origin === "attachment" ? "Attached to this conversation" : "")}
                            </span>
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
