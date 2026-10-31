"use client";

import { useEffect, useMemo, useState } from "react";
import { IconAnswer, IconChevronDown, IconGlobe, IconPlan, IconRead, IconReflect, IconScan, IconSearch, IconTable, IconWarning, Spinner } from "@/components/icons";
import type { SourceRef, StepKind, TraceStep } from "@/lib/types";
import { cn } from "@/lib/utils";

const STEP_ICON: Record<StepKind, typeof IconPlan> = {
  plan: IconPlan,
  search: IconSearch,
  records: IconTable,
  scan: IconScan,
  web: IconGlobe,
  read: IconRead,
  reflect: IconReflect,
  answer: IconAnswer,
};

function duration(steps: TraceStep[]) {
  if (!steps.length) return null;
  const start = Math.min(...steps.map((s) => new Date(s.started_at).getTime()));
  const end = Math.max(...steps.map((s) => new Date(s.finished_at ?? s.started_at).getTime()));
  const secs = Math.max(0, (end - start) / 1000);
  return secs < 1 ? "<1s" : secs < 60 ? `${secs.toFixed(secs < 10 ? 1 : 0)}s` : `${Math.floor(secs / 60)}m ${Math.round(secs % 60)}s`;
}

/**
 * The agent's working trace. Open while it runs; once the answer lands it
 * folds into a one-line summary the reader can expand.
 */
export function Trace({
  steps,
  sources,
  live,
  onOpenSource,
}: {
  steps: TraceStep[];
  sources: SourceRef[];
  live: boolean;
  onOpenSource: (s: SourceRef) => void;
}) {
  const [open, setOpen] = useState(live);
  useEffect(() => {
    if (live) setOpen(true);
  }, [live]);

  const visible = useMemo(() => steps.filter((s) => s.kind !== "answer" || s.status !== "done"), [steps]);
  const byRef = useMemo(() => new Map(sources.map((s) => [s.ref, s])), [sources]);
  if (!steps.length) return null;

  const searches = steps.filter((s) => s.kind === "search").reduce((n, s) => n + (s.detail.queries?.length ?? 1), 0);
  const lookups = steps.filter((s) => s.kind === "records").length;
  const scans = steps.filter((s) => s.kind === "scan");
  const scanned = scans.reduce((n, s) => n + (s.detail.scanned ?? 0), 0);
  const webSearches = steps.filter((s) => s.kind === "web").reduce((n, s) => n + (s.detail.queries?.length ?? 1), 0);
  const reads = steps.filter((s) => s.kind === "read").length;
  const failed = steps.some((s) => s.status === "error");
  const running = steps.find((s) => s.status === "running");

  return (
    <div className="mb-4">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="group flex w-full items-center gap-2.5 rounded-[10px] py-1 text-left text-[13px] text-ink-3 transition-colors hover:text-ink-2"
      >
        <span className="relative grid h-6 w-6 place-items-center rounded-full border border-line bg-surface">
          {running ? <Spinner size={13} className="text-accent" /> : failed ? <IconWarning size={13} className="text-warn" /> : <IconReflect size={13} />}
        </span>
        <span className="min-w-0 truncate">
          {running ? (
            <span className="text-ink-2">{running.label}…</span>
          ) : (
            <>
              <span className="font-medium text-ink-2">Researched</span>
              {searches ? ` · ${searches} ${searches === 1 ? "search" : "searches"}` : ""}
              {lookups ? ` · ${lookups} records ${lookups === 1 ? "lookup" : "lookups"}` : ""}
              {scans.length ? ` · ${scans.length} ${scans.length === 1 ? "scan" : "scans"} of ${scanned} ${scanned === 1 ? "document" : "documents"}` : ""}
              {webSearches ? ` · ${webSearches} web` : ""}
              {reads ? ` · ${reads} ${reads === 1 ? "read" : "reads"}` : ""}
              {sources.length ? ` · ${sources.length} sources` : ""}
              {duration(steps) ? ` · ${duration(steps)}` : ""}
            </>
          )}
        </span>
        <IconChevronDown size={14} className={cn("ml-auto shrink-0 transition-transform", open && "rotate-180")} />
      </button>

      {open ? (
        <ol className="relative mt-2 ml-3 animate-fade border-l border-line pl-5">
          {visible.map((step, i) => (
            <TraceRow key={step.id} step={step} last={i === visible.length - 1} byRef={byRef} onOpenSource={onOpenSource} />
          ))}
        </ol>
      ) : null}
    </div>
  );
}

function TraceRow({
  step,
  last,
  byRef,
  onOpenSource,
}: {
  step: TraceStep;
  last: boolean;
  byRef: Map<number, SourceRef>;
  onOpenSource: (s: SourceRef) => void;
}) {
  const Icon = STEP_ICON[step.kind] ?? IconReflect;
  const [showReasoning, setShowReasoning] = useState(false);
  const running = step.status === "running";
  const refs = (step.detail.refs ?? []).map((r) => byRef.get(r)).filter(Boolean) as SourceRef[];

  return (
    <li className={cn("relative", last ? "pb-1" : "pb-4")}>
      <span
        className={cn(
          "absolute top-0 -left-[33px] grid h-6 w-6 place-items-center rounded-full border bg-bg transition-colors",
          running ? "border-accent text-accent" : step.status === "error" ? "border-warn text-warn" : "border-line text-ink-3",
        )}
      >
        {running ? <Spinner size={12} /> : <Icon size={13} />}
      </span>

      <div className="flex min-h-6 items-center gap-2 text-[13px]">
        <span className={cn("font-medium", running ? "text-ink" : "text-ink-2")}>{step.label}</span>
        {step.detail.reasoning ? (
          <button
            type="button"
            onClick={() => setShowReasoning((v) => !v)}
            className="rounded px-1 font-mono text-[10.5px] tracking-wide text-ink-3 uppercase hover:bg-surface-2 hover:text-ink-2"
          >
            {showReasoning ? "hide" : "reasoning"}
          </button>
        ) : null}
      </div>

      {step.detail.plan?.length ? (
        <ol className="mt-2 flex flex-col gap-1.5">
          {step.detail.plan.map((p, i) => (
            <li key={i} className="flex gap-2.5 text-[13px] leading-snug">
              <span className="mt-[1px] font-mono text-[11px] text-ink-3 tabular-nums">{String(i + 1).padStart(2, "0")}</span>
              <span>
                <span className="text-ink">{p.question}</span>
                {p.rationale ? <span className="block text-[12px] text-ink-3">{p.rationale}</span> : null}
              </span>
            </li>
          ))}
        </ol>
      ) : null}

      {step.detail.queries?.length ? (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {step.detail.queries.map((q) => (
            <span key={q} className="inline-flex max-w-full items-center gap-1.5 rounded-[7px] border border-line bg-surface px-2 py-0.5 text-[12px] text-ink-2">
              {step.kind === "web" ? (
                <IconGlobe size={12} className="shrink-0 text-ink-3" />
              ) : step.kind === "scan" ? (
                <IconScan size={12} className="shrink-0 text-ink-3" />
              ) : (
                <IconSearch size={12} className="shrink-0 text-ink-3" />
              )}
              <span className="truncate">{q}</span>
            </span>
          ))}
        </div>
      ) : null}

      {step.detail.document_title ? (
        <div className="mt-1.5 truncate text-[12.5px] text-ink-3">{step.detail.document_title}</div>
      ) : null}

      {refs.length ? (
        <div className="mt-2 flex flex-wrap gap-1">
          {refs.slice(0, 10).map((s) => (
            <button
              key={s.ref}
              type="button"
              onClick={() => onOpenSource(s)}
              title={s.title}
              className="inline-flex max-w-[220px] items-center gap-1.5 rounded-[7px] bg-surface-2 px-1.5 py-0.5 text-[11.5px] text-ink-2 transition-colors hover:bg-surface-3 hover:text-ink"
            >
              <span
                className="h-1.5 w-1.5 shrink-0 rounded-full"
                style={{
                  background: s.origin === "knowledge" && s.org_hue !== null ? `hsl(${s.org_hue} var(--org-s) var(--org-l))` : "var(--ink-3)",
                }}
              />
              <span className="font-mono text-[10.5px] text-ink-3">{s.ref}</span>
              <span className="truncate">{s.title}</span>
            </button>
          ))}
          {refs.length > 10 ? <span className="px-1 text-[11.5px] text-ink-3">+{refs.length - 10}</span> : null}
        </div>
      ) : null}

      {showReasoning && step.detail.reasoning ? (
        <p className="mt-2 animate-fade border-l-2 border-line pl-3 text-[12.5px] leading-relaxed whitespace-pre-wrap text-ink-3">
          {step.detail.reasoning}
        </p>
      ) : running && step.detail.reasoning ? (
        <p className="mt-1.5 line-clamp-2 text-[12.5px] leading-relaxed text-ink-3">{step.detail.reasoning.slice(-240)}</p>
      ) : null}
    </li>
  );
}
