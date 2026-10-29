"use client";

import { useRef, useState } from "react";
import { IconUpload } from "@/components/icons";
import { Progress } from "@/components/ui";
import type { Source, SourceStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

const STATUS: Record<SourceStatus, { label: string; cls: string; dot: string }> = {
  idle: { label: "Idle", cls: "text-ink-3", dot: "bg-ink-3" },
  queued: { label: "Queued", cls: "text-ink-2", dot: "bg-ink-3 strata-pulse" },
  syncing: { label: "Syncing", cls: "text-accent", dot: "bg-accent strata-pulse" },
  ready: { label: "Ready", cls: "text-ok", dot: "bg-ok" },
  error: { label: "Error", cls: "text-err", dot: "bg-err" },
};

export function StatusPill({ status }: { status: SourceStatus }) {
  const s = STATUS[status];
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-[12px] font-medium", s.cls)}>
      <span className={cn("h-1.5 w-1.5 rounded-full", s.dot)} />
      {s.label}
    </span>
  );
}

export function SyncProgress({ source }: { source: Source }) {
  if (source.status !== "syncing" && source.status !== "queued") return null;
  const p = source.progress;
  const pct = p && p.total ? (p.done / p.total) * 100 : null;
  return (
    <div className="flex flex-col gap-1">
      <Progress value={source.status === "queued" ? null : pct} />
      <div className="flex justify-between font-mono text-[10.5px] text-ink-3">
        <span className="truncate">{p?.message ?? (source.status === "queued" ? "Waiting for a worker" : "Working")}</span>
        {p ? <span>{p.total ? `${p.done}/${p.total}` : p.done}</span> : null}
      </div>
    </div>
  );
}

export const ACCEPT = ".pdf,.docx,.md,.markdown,.txt,.html,.htm,.csv,.json";

export function Dropzone({ onFiles, compact, disabled }: { onFiles: (files: File[]) => void; compact?: boolean; disabled?: boolean }) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        if (!disabled) onFiles([...e.dataTransfer.files]);
      }}
      onClick={() => !disabled && input.current?.click()}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
      className={cn(
        "flex cursor-pointer flex-col items-center justify-center rounded-[14px] border border-dashed text-center transition-colors",
        compact ? "gap-1.5 px-4 py-5" : "gap-2 px-6 py-9",
        over ? "border-accent bg-accent-soft/50" : "border-line-strong bg-surface-2/40 hover:bg-surface-2",
        disabled && "pointer-events-none opacity-50",
      )}
    >
      <span className="grid h-9 w-9 place-items-center rounded-full border border-line bg-surface text-ink-2">
        <IconUpload size={17} />
      </span>
      <div className="text-[13.5px] font-medium">Drop files or click to browse</div>
      <div className="font-mono text-[11px] text-ink-3">PDF · DOCX · MD · TXT · HTML · CSV · JSON — up to 25 MB each</div>
      <input
        ref={input}
        type="file"
        multiple
        accept={ACCEPT}
        className="hidden"
        onChange={(e) => {
          onFiles([...(e.target.files ?? [])]);
          e.target.value = "";
        }}
      />
    </div>
  );
}

export const INTERVALS: { value: string; label: string }[] = [
  { value: "0", label: "Manual only" },
  { value: "60", label: "Every hour" },
  { value: "360", label: "Every 6 hours" },
  { value: "1440", label: "Daily" },
  { value: "10080", label: "Weekly" },
];
