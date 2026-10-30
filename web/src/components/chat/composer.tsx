"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  IconAgent,
  IconCheck,
  IconDeep,
  IconGlobe,
  IconPaperclip,
  IconQuick,
  IconScope,
  IconSend,
  IconStop,
  IconWarning,
  IconX,
  Spinner,
} from "@/components/icons";
import { Glyph, OrgMark } from "@/components/illustrations";
import { Kbd, Menu, MenuCheckItem, MenuContent, MenuItem, MenuLabel, MenuSeparator, MenuTrigger, Tooltip } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import type { Agent, Attachment, Mode, OrgSummary } from "@/lib/types";
import { cn, formatBytes } from "@/lib/utils";

export type PendingAttachment = {
  key: string;
  name: string;
  size: number;
  status: "uploading" | "ready" | "error";
  attachment?: Attachment;
  error?: string;
};

export type ComposerState = {
  text: string;
  mode: Mode;
  scope: string[]; // org slugs
  agentId: string | null;
  attachments?: PendingAttachment[];
  /** null follows the org's default. */
  web?: boolean | null;
};

const ACCEPT = ".pdf,.docx,.md,.markdown,.txt,.html,.htm,.csv,.json";
const MAX_FILES = 10;

/** Ids of the attachments ready to send with the message. */
export const readyAttachmentIds = (s: ComposerState) =>
  (s.attachments ?? []).flatMap((a) => (a.status === "ready" && a.attachment ? [a.attachment.id] : []));

export function Composer({
  value,
  onChange,
  onSubmit,
  onStop,
  streaming,
  orgs,
  currentSlug,
  agents,
  agentLocked,
  placeholder = "Ask anything across your organizations…",
  autoFocus,
  size = "md",
  slug,
  webAvailable = false,
  webDefault = false,
}: {
  value: ComposerState;
  onChange: (v: ComposerState) => void;
  onSubmit: () => void;
  onStop?: () => void;
  streaming?: boolean;
  orgs: OrgSummary[];
  currentSlug: string;
  agents?: Agent[];
  agentLocked?: boolean;
  placeholder?: string;
  autoFocus?: boolean;
  size?: "md" | "lg";
  /** Org to upload attachments into; attachments are off without it. */
  slug?: string;
  /** A web search provider is configured on the server. */
  webAvailable?: boolean;
  webDefault?: boolean;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  // Uploads finish after the render that started them; read the latest state through a ref.
  const latest = useRef(value);
  latest.current = value;
  const attachments = value.attachments ?? [];
  const webOn = value.web ?? webDefault;

  function updateAttachment(key: string, patch: Partial<PendingAttachment> | null) {
    const cur = latest.current;
    const list = (cur.attachments ?? []).flatMap((a) => (a.key !== key ? [a] : patch ? [{ ...a, ...patch }] : []));
    onChange({ ...cur, attachments: list });
  }

  function addFiles(files: FileList | File[]) {
    if (!slug) return;
    const room = MAX_FILES - attachments.length;
    const picked = [...files].slice(0, Math.max(0, room));
    if (!picked.length) return;
    const added: PendingAttachment[] = picked.map((f) => ({
      key: `${f.name}-${f.size}-${Math.random().toString(36).slice(2, 8)}`,
      name: f.name,
      size: f.size,
      status: "uploading",
    }));
    const cur = latest.current;
    onChange({ ...cur, attachments: [...(cur.attachments ?? []), ...added] });
    picked.forEach((file, i) => {
      api
        .uploadAttachment(slug, file)
        .then((attachment) => updateAttachment(added[i].key, { status: "ready", attachment }))
        .catch((e) =>
          updateAttachment(added[i].key, { status: "error", error: e instanceof ApiError ? e.message : "Upload failed" }),
        );
    });
  }

  function removeAttachment(a: PendingAttachment) {
    updateAttachment(a.key, null);
    if (slug && a.attachment) void api.deleteAttachment(slug, a.attachment.id).catch(() => {});
  }

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "0px";
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`;
  }, [value.text]);

  useEffect(() => {
    if (autoFocus) ref.current?.focus();
  }, [autoFocus]);

  const uploading = attachments.some((a) => a.status === "uploading");
  const canSend = value.text.trim().length > 0 && !streaming && !uploading;

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (canSend) onSubmit();
      }}
      onDragOver={(e) => {
        if (!slug || !e.dataTransfer.types.includes("Files")) return;
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragging(false);
      }}
      onDrop={(e) => {
        if (!slug || !e.dataTransfer.files.length) return;
        e.preventDefault();
        setDragging(false);
        addFiles(e.dataTransfer.files);
      }}
      className={cn(
        "relative rounded-[18px] border border-line bg-surface shadow-[0_1px_0_rgba(0,0,0,.03),0_10px_30px_-18px_rgba(0,0,0,.25)] transition-[border-color,box-shadow] focus-within:border-line-strong",
        dragging && "border-accent shadow-[0_0_0_3px_var(--accent-soft)]",
      )}
    >
      {dragging ? (
        <div className="pointer-events-none absolute inset-0 z-10 grid place-items-center rounded-[18px] bg-surface/85 text-[13px] font-medium text-accent">
          <span className="inline-flex items-center gap-2">
            <IconPaperclip size={16} /> Drop to attach
          </span>
        </div>
      ) : null}
      {attachments.length ? (
        <div className="flex flex-wrap gap-1.5 px-3 pt-3">
          {attachments.map((a) => (
            <AttachmentChip key={a.key} item={a} onRemove={() => removeAttachment(a)} />
          ))}
        </div>
      ) : null}
      <textarea
        ref={ref}
        rows={1}
        value={value.text}
        onChange={(e) => onChange({ ...value, text: e.target.value })}
        onPaste={(e) => {
          if (slug && e.clipboardData.files.length) {
            e.preventDefault();
            addFiles(e.clipboardData.files);
          }
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            if (canSend) onSubmit();
          }
        }}
        placeholder={placeholder}
        aria-label="Question"
        className={cn(
          "block w-full resize-none bg-transparent px-4 text-ink outline-none placeholder:text-ink-3",
          size === "lg" ? "pt-4 pb-2 text-[16px] leading-relaxed" : "pt-3.5 pb-1.5 text-[15px] leading-relaxed",
        )}
      />
      <div className="flex flex-wrap items-center gap-1.5 px-2.5 pt-1 pb-2.5">
        {slug ? (
          <>
            <input
              ref={fileInput}
              type="file"
              multiple
              accept={ACCEPT}
              className="hidden"
              onChange={(e) => {
                if (e.target.files) addFiles(e.target.files);
                e.target.value = "";
              }}
            />
            <Tooltip label="Attach files (PDF, Word, Markdown, text, CSV)">
              <button
                type="button"
                onClick={() => fileInput.current?.click()}
                disabled={attachments.length >= MAX_FILES}
                aria-label="Attach files"
                className="grid h-8 w-8 place-items-center rounded-[9px] text-ink-3 transition-colors hover:bg-surface-2 hover:text-ink disabled:opacity-40"
              >
                <IconPaperclip size={16} />
              </button>
            </Tooltip>
          </>
        ) : null}
        <ModeToggle mode={value.mode} onChange={(mode) => onChange({ ...value, mode })} />
        <ScopePicker orgs={orgs} currentSlug={currentSlug} scope={value.scope} onChange={(scope) => onChange({ ...value, scope })} />
        {agents?.length ? (
          <AgentPicker
            agents={agents}
            agentId={value.agentId}
            locked={agentLocked}
            onChange={(agentId) => {
              const agent = agents.find((a) => a.id === agentId);
              onChange({ ...value, agentId, mode: agent?.mode ?? value.mode });
            }}
          />
        ) : null}
        {webAvailable ? (
          <Tooltip label={webOn ? "Web search on — the agent may search the public web" : "Let the agent search the public web"}>
            <button
              type="button"
              aria-pressed={webOn}
              onClick={() => onChange({ ...value, web: !webOn })}
              className={cn(
                "inline-flex h-8 items-center gap-1.5 rounded-[9px] px-2 text-[12.5px] font-medium transition-colors",
                webOn ? "bg-surface-2 text-ink shadow-[inset_0_0_0_1px_var(--line)]" : "text-ink-3 hover:bg-surface-2 hover:text-ink-2",
              )}
            >
              <IconGlobe size={15} />
              Web
            </button>
          </Tooltip>
        ) : null}
        <div className="ml-auto flex items-center gap-2">
          <span className="hidden items-center gap-1 text-[11.5px] text-ink-3 sm:flex">
            <Kbd>↵</Kbd> send
          </span>
          {streaming ? (
            <Tooltip label="Stop">
              <button
                type="button"
                onClick={onStop}
                aria-label="Stop generating"
                className="grid h-8 w-8 place-items-center rounded-full bg-ink text-bg transition-transform hover:scale-105"
              >
                <IconStop size={14} />
              </button>
            </Tooltip>
          ) : (
            <button
              type="submit"
              disabled={!canSend}
              aria-label="Send"
              className="grid h-8 w-8 place-items-center rounded-full bg-accent text-accent-ink transition-all hover:bg-accent-hover disabled:bg-surface-3 disabled:text-ink-3"
            >
              <IconSend size={16} strokeWidth={1.9} />
            </button>
          )}
        </div>
      </div>
    </form>
  );
}

function ModeToggle({ mode, onChange }: { mode: Mode; onChange: (m: Mode) => void }) {
  const opts: { value: Mode; label: string; icon: React.ReactNode; tip: string }[] = [
    { value: "quick", label: "Quick", icon: <IconQuick size={14} />, tip: "One or two searches, fast answer" },
    { value: "deep", label: "Deep", icon: <IconDeep size={14} />, tip: "Plans, searches iteratively, reads sources, then answers" },
  ];
  return (
    <div role="radiogroup" aria-label="Answer mode" className="inline-flex rounded-[9px] bg-surface-2 p-0.5">
      {opts.map((o) => (
        <Tooltip key={o.value} label={o.tip}>
          <button
            type="button"
            role="radio"
            aria-checked={mode === o.value}
            onClick={() => onChange(o.value)}
            className={cn(
              "inline-flex h-7 items-center gap-1.5 rounded-[7px] px-2.5 text-[12.5px] font-medium transition-all",
              mode === o.value
                ? o.value === "deep"
                  ? "bg-surface text-accent shadow-[0_0_0_1px_var(--line)]"
                  : "bg-surface text-ink shadow-[0_0_0_1px_var(--line)]"
                : "text-ink-3 hover:text-ink-2",
            )}
          >
            {o.icon}
            {o.label}
          </button>
        </Tooltip>
      ))}
    </div>
  );
}

function ScopePicker({
  orgs,
  currentSlug,
  scope,
  onChange,
}: {
  orgs: OrgSummary[];
  currentSlug: string;
  scope: string[];
  onChange: (s: string[]) => void;
}) {
  const selected = orgs.filter((o) => scope.includes(o.slug));
  const toggle = (slug: string, on: boolean) => {
    const next = on ? [...new Set([...scope, slug])] : scope.filter((s) => s !== slug);
    onChange(next.length ? next : [currentSlug]);
  };

  return (
    <Menu>
      <MenuTrigger asChild>
        <button
          type="button"
          className="inline-flex h-8 items-center gap-1.5 rounded-[9px] px-2 text-[12.5px] font-medium text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink data-[state=open]:bg-surface-2"
          aria-label="Search scope"
        >
          {selected.length > 1 ? (
            <>
              <span className="flex -space-x-1">
                {selected.slice(0, 3).map((o) => (
                  <OrgMark key={o.id} name={o.name} hue={o.hue} size={16} className="ring-2 ring-surface" />
                ))}
              </span>
              {selected.length} orgs
            </>
          ) : selected[0] ? (
            <>
              <OrgMark name={selected[0].name} hue={selected[0].hue} size={16} />
              <span className="max-w-[120px] truncate">{selected[0].name}</span>
            </>
          ) : (
            <>
              <IconScope size={15} /> Scope
            </>
          )}
        </button>
      </MenuTrigger>
      <MenuContent className="w-72">
        <MenuLabel>Search across</MenuLabel>
        {orgs.map((o) => (
          <MenuCheckItem
            key={o.id}
            checked={scope.includes(o.slug)}
            onCheckedChange={(v) => toggle(o.slug, v)}
            icon={<OrgMark name={o.name} hue={o.hue} size={18} />}
          >
            <span className="flex items-baseline gap-2">
              {o.name}
              <span className="font-mono text-[10.5px] text-ink-3">{o.document_count} docs</span>
            </span>
          </MenuCheckItem>
        ))}
        {orgs.length > 1 ? (
          <>
            <MenuSeparator />
            <MenuItem icon={<IconScope size={15} />} onSelect={() => onChange(orgs.map((o) => o.slug))}>
              All my organizations
            </MenuItem>
          </>
        ) : (
          <p className="px-2 pt-1 pb-2 text-[12px] leading-relaxed text-ink-3">
            Join or create another organization to ask across several at once.
          </p>
        )}
      </MenuContent>
    </Menu>
  );
}

function AgentPicker({
  agents,
  agentId,
  locked,
  onChange,
}: {
  agents: Agent[];
  agentId: string | null;
  locked?: boolean;
  onChange: (id: string | null) => void;
}) {
  const agent = agents.find((a) => a.id === agentId);
  const trigger = (
    <button
      type="button"
      disabled={locked}
      className="inline-flex h-8 items-center gap-1.5 rounded-[9px] px-2 text-[12.5px] font-medium text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink disabled:hover:bg-transparent data-[state=open]:bg-surface-2"
    >
      {agent ? <Glyph name={agent.glyph} size={15} /> : <IconAgent size={15} />}
      <span className="max-w-[120px] truncate">{agent?.name ?? "Agent"}</span>
    </button>
  );
  if (locked) return trigger;
  return (
    <Menu>
      <MenuTrigger asChild>{trigger}</MenuTrigger>
      <MenuContent className="w-72">
        <MenuLabel>Answer as</MenuLabel>
        <MenuItem icon={<IconAgent size={15} />} onSelect={() => onChange(null)} trailing={!agentId ? <IconCheck size={14} /> : null}>
          Strata <span className="text-ink-3">— general</span>
        </MenuItem>
        {agents.map((a) => (
          <MenuItem key={a.id} icon={<Glyph name={a.glyph} size={15} />} onSelect={() => onChange(a.id)} trailing={a.id === agentId ? <IconCheck size={14} /> : null}>
            {a.name}
          </MenuItem>
        ))}
      </MenuContent>
    </Menu>
  );
}

function AttachmentChip({ item, onRemove }: { item: PendingAttachment; onRemove: () => void }) {
  const failed = item.status === "error";
  return (
    <span
      title={failed ? item.error : item.name}
      className={cn(
        "group/chip inline-flex h-8 max-w-[260px] animate-fade items-center gap-2 rounded-[10px] border pr-1 pl-2 text-[12.5px]",
        failed ? "border-err/30 bg-err-soft text-err" : "border-line bg-surface-2/60 text-ink-2",
      )}
    >
      {item.status === "uploading" ? (
        <Spinner size={13} className="shrink-0 text-ink-3" />
      ) : failed ? (
        <IconWarning size={14} className="shrink-0" />
      ) : (
        <IconPaperclip size={14} className="shrink-0 text-ink-3" />
      )}
      <span className="min-w-0 truncate font-medium">{item.name}</span>
      <span className="shrink-0 font-mono text-[10.5px] opacity-70">
        {failed ? "failed" : item.attachment ? `${item.attachment.token_count.toLocaleString()} tok` : formatBytes(item.size)}
      </span>
      <button
        type="button"
        onClick={onRemove}
        aria-label={`Remove ${item.name}`}
        className="grid h-6 w-6 shrink-0 place-items-center rounded-[7px] opacity-60 transition-opacity hover:bg-surface-3 hover:opacity-100"
      >
        <IconX size={12} />
      </button>
    </span>
  );
}
