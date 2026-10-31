"use client";

import { Fragment, useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { IconBuilding, IconChevronDown, IconDownload, IconFile, IconKey, IconSearch, IconX } from "@/components/icons";
import { Avatar, CompassArt, NoResultsArt } from "@/components/illustrations";
import { useCurrentOrg } from "@/components/layout/org-context";
import { Badge, Button, Card, EmptyState, Input, Menu, MenuContent, MenuItem, MenuLabel, MenuTrigger, PageHeader, Segmented, Select, Skeleton, Tooltip } from "@/components/ui";
import { api } from "@/lib/api";
import { qk, useAuditAccess, useAuditEvents, useMembers } from "@/lib/queries";
import type { AccessEntry, AccessFilters, AccessKind, AuditActor, AuditChannel, AuditEvent, EventFilters } from "@/lib/types";
import { atLeast, HUES, relativeTime } from "@/lib/utils";

type Tab = "access" | "events";
type Period = "1" | "7" | "30" | "90" | "all";

const PERIODS: { value: Period; label: string }[] = [
  { value: "1", label: "24 hours" },
  { value: "7", label: "7 days" },
  { value: "30", label: "30 days" },
  { value: "90", label: "90 days" },
  { value: "all", label: "All" },
];

const KINDS: { value: AccessKind | "all"; label: string }[] = [
  { value: "all", label: "All reads" },
  { value: "chat", label: "Answers" },
  { value: "search", label: "Searches" },
  { value: "read", label: "Documents opened" },
  { value: "shared", label: "Shared answers" },
];

const CHANNELS: { value: AuditChannel | "all"; label: string }[] = [
  { value: "all", label: "Any channel" },
  { value: "app", label: "Strata app" },
  { value: "api", label: "API key" },
  { value: "mcp", label: "MCP server" },
];

const GROUPS = [
  { value: "all", label: "All actions" },
  { value: "member", label: "Members" },
  { value: "invite", label: "Invites" },
  { value: "collection", label: "Collections & access" },
  { value: "source", label: "Sources" },
  { value: "document", label: "Documents" },
  { value: "org", label: "Organization settings" },
  { value: "api_key", label: "API keys" },
  { value: "share", label: "Share links" },
  { value: "access_request", label: "Access requests" },
  { value: "verified_answer", label: "Verified answers" },
  { value: "audit", label: "Audit exports" },
];

const KIND_VERB: Record<AccessKind, string> = {
  chat: "asked",
  search: "searched",
  read: "opened a document",
  shared: "opened a shared answer",
};

/** How each admin action reads in a sentence: "<actor> <verb> <target>". */
const ACTIONS: Record<string, { verb: string; target?: false }> = {
  "org.created": { verb: "created the organization" },
  "org.updated": { verb: "changed the organization's settings", target: false },
  "member.joined": { verb: "joined the organization", target: false },
  "member.role_changed": { verb: "changed the role of" },
  "member.removed": { verb: "removed" },
  "member.left": { verb: "left the organization", target: false },
  "invite.created": { verb: "invited" },
  "invite.revoked": { verb: "revoked the invite for" },
  "collection.created": { verb: "created the collection" },
  "collection.updated": { verb: "edited the collection" },
  "collection.access_changed": { verb: "changed who can read" },
  "collection.deleted": { verb: "deleted the collection" },
  "source.created": { verb: "added the source" },
  "source.updated": { verb: "edited the source" },
  "source.synced": { verb: "started a full sync of" },
  "source.files_uploaded": { verb: "uploaded files to" },
  "source.deleted": { verb: "deleted the source" },
  "document.hidden": { verb: "hid" },
  "document.unhidden": { verb: "unhid" },
  "document.boosted": { verb: "changed the boost of" },
  "document.deleted": { verb: "deleted" },
  "api_key.created": { verb: "created the API key" },
  "api_key.revoked": { verb: "revoked the API key" },
  "share.created": { verb: "shared the answer to" },
  "share.revoked": { verb: "turned off the link to" },
  "access_request.approved": { verb: "approved read access for" },
  "access_request.dismissed": { verb: "dismissed the access request from" },
  "verified_answer.created": { verb: "verified the answer to" },
  "verified_answer.updated": { verb: "edited the verified answer to" },
  "verified_answer.reverified": { verb: "re-verified the answer to" },
  "verified_answer.deleted": { verb: "deleted the verified answer to" },
  "audit.exported": { verb: "exported the audit log:" },
};

const DETAIL_LABEL: Record<string, string> = {
  write_only_fields: "secret fields set",
  keys_revoked: "API keys revoked",
  sync_interval_minutes: "sync every (min)",
};

const timeFmt = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "medium" });

function sinceFor(period: Period): string | undefined {
  if (period === "all") return undefined;
  // Rounded to the minute so the query key stays stable while the page is open.
  const ms = Math.floor((Date.now() - Number(period) * 86_400_000) / 60_000) * 60_000;
  return new Date(ms).toISOString();
}

function hueFor(id: string | null) {
  let n = 0;
  for (const ch of id ?? "") n = (n * 31 + ch.charCodeAt(0)) % 9973;
  return HUES[n % HUES.length];
}

export default function AuditPage() {
  const { org } = useCurrentOrg();
  const qc = useQueryClient();
  const isAdmin = atLeast(org.role, "admin");
  const members = useMembers(org.slug);
  const [tab, setTab] = useState<Tab>("access");
  const [period, setPeriod] = useState<Period>("30");
  const [kind, setKind] = useState<AccessKind | "all">("all");
  const [channel, setChannel] = useState<AuditChannel | "all">("all");
  const [group, setGroup] = useState("all");
  const [actor, setActor] = useState<{ id: string; label: string } | null>(null);
  const [doc, setDoc] = useState<{ id: string; title: string } | null>(null);
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");

  useEffect(() => {
    const t = setTimeout(() => setQuery(q.trim()), 250);
    return () => clearTimeout(t);
  }, [q]);

  const since = useMemo(() => sinceFor(period), [period]);
  const accessFilters: AccessFilters = {
    since,
    actor_id: actor?.id,
    q: query || undefined,
    kind: kind === "all" ? undefined : kind,
    channel: channel === "all" ? undefined : channel,
    document_id: doc?.id,
  };
  const eventFilters: EventFilters = { since, actor_id: actor?.id, q: query || undefined, action: group === "all" ? undefined : group };
  const access = useAuditAccess(org.slug, accessFilters, isAdmin && tab === "access");
  const events = useAuditEvents(org.slug, eventFilters, isAdmin && tab === "events");

  const hues = useMemo(() => new Map((members.data ?? []).map((m) => [m.user.id, m.user.avatar_hue])), [members.data]);
  const actorOptions = useMemo(() => {
    const options = [{ value: "any", label: "Anyone" }, ...(members.data ?? []).map((m) => ({ value: m.user.id, label: m.user.name || m.user.email, hint: m.user.email }))];
    if (actor && !options.some((o) => o.value === actor.id)) options.push({ value: actor.id, label: actor.label, hint: "Not a current member" });
    return options;
  }, [members.data, actor]);

  if (!isAdmin) {
    return <div className="p-10 text-[14px] text-ink-3">Only admins can see the audit log.</div>;
  }

  const pickActor = (a: AuditActor, byKey = false) => {
    if (byKey && a.api_key_id) setActor({ id: a.api_key_id, label: `API key “${a.api_key_name ?? "deleted"}”` });
    else if (a.user_id) setActor({ id: a.user_id, label: a.name || a.email });
  };

  function exportLog(format: "csv" | "jsonl") {
    window.location.assign(api.auditExportUrl(org.slug, tab, format, tab === "access" ? accessFilters : eventFilters));
    toast("Export started", { description: "The download is recorded in the audit log." });
    // The export is recorded as an admin action before the file starts streaming.
    setTimeout(() => qc.invalidateQueries({ queryKey: qk.audit(org.slug) }), 1500);
  }

  const active = tab === "access" ? access : events;
  const filtered = !!(actor || doc || query || period !== "all" || (tab === "access" ? kind !== "all" || channel !== "all" : group !== "all"));
  const narrowed = !!(actor || doc || query || (tab === "access" ? kind !== "all" || channel !== "all" : group !== "all"));

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl px-5 py-10 sm:px-8">
        <PageHeader
          eyebrow={org.name}
          title="Audit log"
          description="Every read of this organization's data — including questions asked from another organization's workspace by people in both — and every admin change. Only admins see this page."
          actions={
            <Menu>
              <MenuTrigger asChild>
                <Button variant="secondary">
                  <IconDownload size={15} /> Export
                  <IconChevronDown size={14} className="text-ink-3" />
                </Button>
              </MenuTrigger>
              <MenuContent align="end">
                <MenuLabel>{tab === "access" ? "Data access" : "Admin actions"}, current filters</MenuLabel>
                <MenuItem onSelect={() => exportLog("csv")}>CSV (spreadsheets)</MenuItem>
                <MenuItem onSelect={() => exportLog("jsonl")}>JSON Lines</MenuItem>
              </MenuContent>
            </Menu>
          }
        />

        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <Segmented
            value={tab}
            onChange={setTab}
            options={[
              { value: "access", label: "Data access" },
              { value: "events", label: "Admin actions" },
            ]}
          />
          <Segmented size="sm" value={period} onChange={setPeriod} options={PERIODS} />
        </div>

        <div className="mb-4 flex flex-wrap items-center gap-2">
          {tab === "access" ? (
            <>
              <Select value={kind} onValueChange={setKind} options={KINDS} className="w-44" />
              <Select value={channel} onValueChange={setChannel} options={CHANNELS} className="w-40" />
            </>
          ) : (
            <Select value={group} onValueChange={setGroup} options={GROUPS} className="w-52" />
          )}
          <Select
            value={actor?.id ?? "any"}
            onValueChange={(v) => setActor(v === "any" ? null : { id: v, label: actorOptions.find((o) => o.value === v)?.label ?? v })}
            options={actorOptions}
            className="w-48"
          />
          <div className="relative min-w-48 flex-1">
            <IconSearch size={15} className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-ink-3" />
            <Input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={tab === "access" ? "Search questions and queries" : "Search targets"}
              className="pl-9"
              aria-label="Search the audit log"
            />
          </div>
        </div>

        {doc && tab === "access" ? (
          <div className="mb-4 flex items-center gap-2 text-[13px] text-ink-2">
            <span>Reads of</span>
            <span className="inline-flex items-center gap-1.5 rounded-[7px] border border-line bg-surface px-2 py-0.5">
              <IconFile size={13} className="text-ink-3" />
              {doc.title}
              <button type="button" aria-label="Clear the document filter" onClick={() => setDoc(null)} className="text-ink-3 hover:text-ink">
                <IconX size={13} />
              </button>
            </span>
          </div>
        ) : null}

        {active.isLoading ? (
          <Card className="flex flex-col gap-3 p-4">
            {[0, 1, 2, 3].map((i) => (
              <Skeleton key={i} className="h-14" />
            ))}
          </Card>
        ) : active.isError ? (
          <Card className="flex flex-col items-center gap-3 px-6 py-12 text-center">
            <p className="text-[14px] text-ink-2">{active.error instanceof Error ? active.error.message : "Couldn't load the audit log."}</p>
            <Button size="sm" onClick={() => active.refetch()}>
              Try again
            </Button>
          </Card>
        ) : tab === "access" ? (
          <AccessList
            pages={access.data?.pages.flatMap((p) => p.items) ?? []}
            hues={hues}
            filtered={filtered}
            narrowed={narrowed}
            onActor={pickActor}
            onDocument={setDoc}
          />
        ) : (
          <EventList pages={events.data?.pages.flatMap((p) => p.items) ?? []} hues={hues} filtered={filtered} narrowed={narrowed} onActor={pickActor} />
        )}

        {active.hasNextPage ? (
          <div className="mt-4 flex justify-center">
            <Button variant="secondary" loading={active.isFetchingNextPage} onClick={() => active.fetchNextPage()}>
              Load older entries
            </Button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

/* ---------------------------------- Data access ---------------------------------- */

function AccessList({
  pages,
  hues,
  filtered,
  narrowed,
  onActor,
  onDocument,
}: {
  pages: AccessEntry[];
  hues: Map<string, number>;
  filtered: boolean;
  narrowed: boolean;
  onActor: (a: AuditActor, byKey?: boolean) => void;
  onDocument: (d: { id: string; title: string }) => void;
}) {
  if (!pages.length) {
    return (
      <Card>
        <EmptyState
          art={narrowed ? <NoResultsArt /> : <CompassArt />}
          title={narrowed ? "Nothing matches" : filtered ? "No reads in this period" : "No reads recorded yet"}
          description={
            narrowed
              ? "Try other filters, or a longer period."
              : "Answers, searches, opened documents and shared answers that reach this organization's documents appear here — also when they're asked from another organization."
          }
        />
      </Card>
    );
  }
  return (
    <Card className="overflow-hidden">
      <ul className="divide-y divide-line">
        {pages.map((e) => (
          <AccessRow key={e.id} entry={e} hue={hues.get(e.actor.user_id ?? "") ?? hueFor(e.actor.user_id)} onActor={onActor} onDocument={onDocument} />
        ))}
      </ul>
    </Card>
  );
}

function AccessRow({
  entry: e,
  hue,
  onActor,
  onDocument,
}: {
  entry: AccessEntry;
  hue: number;
  onActor: (a: AuditActor, byKey?: boolean) => void;
  onDocument: (d: { id: string; title: string }) => void;
}) {
  const [all, setAll] = useState(false);
  const docs = all ? e.documents : e.documents.slice(0, 4);
  return (
    <li className="flex gap-3 px-4 py-3.5">
      <Avatar name={e.actor.name || e.actor.email} hue={hue} size={30} className="mt-0.5" />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px]">
          <ActorName actor={e.actor} onActor={onActor} />
          <span className="text-ink-3">{KIND_VERB[e.kind]}</span>
          <ChannelBadge actor={e.actor} channel={e.channel} onActor={onActor} />
          {e.cross_org ? (
            <Tooltip label="Asked from another organization's workspace. Only this organization's documents are listed.">
              <span>
                <Badge tone="warn">
                  <IconBuilding size={12} /> From another organization
                </Badge>
              </span>
            </Tooltip>
          ) : null}
        </div>
        {e.query ? <p className="mt-1 text-[13.5px] leading-snug break-words text-ink">“{e.query}”</p> : null}
        <ul className="mt-2 flex flex-wrap gap-1.5" aria-label="Documents returned">
          {docs.map((d) => (
            <li key={d.id} className="max-w-full">
              <Tooltip label="Show every read of this document">
                <button
                  type="button"
                  onClick={() => onDocument({ id: d.id, title: d.title })}
                  className="inline-flex max-w-full items-center gap-1.5 rounded-[7px] border border-line bg-surface-2/50 px-2 py-0.5 text-[12px] transition-colors hover:border-line-strong"
                >
                  <IconFile size={12} className="shrink-0 text-ink-3" />
                  <span className="truncate">{d.title || "Untitled"}</span>
                  {d.collection ? <span className="shrink-0 text-ink-3">· {d.collection}</span> : null}
                  {d.cited ? <span className="shrink-0 font-mono text-[10.5px] text-accent">cited</span> : null}
                </button>
              </Tooltip>
            </li>
          ))}
          {e.documents.length > 4 ? (
            <li>
              <button type="button" onClick={() => setAll(!all)} className="h-full px-1 text-[12px] text-ink-3 underline decoration-line-strong underline-offset-4 hover:text-ink">
                {all ? "Show fewer" : `+${e.documents.length - 4} more`}
              </button>
            </li>
          ) : null}
        </ul>
        {e.kind === "chat" || e.kind === "shared" ? (
          <p className="mt-1.5 text-[11.5px] text-ink-3">
            {e.document_count} retrieved · {e.cited_count} cited
          </p>
        ) : null}
      </div>
      <Time iso={e.created_at} />
    </li>
  );
}

/* ---------------------------------- Admin actions ---------------------------------- */

function EventList({
  pages,
  hues,
  filtered,
  narrowed,
  onActor,
}: {
  pages: AuditEvent[];
  hues: Map<string, number>;
  filtered: boolean;
  narrowed: boolean;
  onActor: (a: AuditActor, byKey?: boolean) => void;
}) {
  if (!pages.length) {
    return (
      <Card>
        <EmptyState
          art={narrowed ? <NoResultsArt /> : <CompassArt />}
          title={narrowed ? "Nothing matches" : filtered ? "No changes in this period" : "No admin actions yet"}
          description={narrowed ? "Try other filters, or a longer period." : "Changes to members, collections, sources, documents, settings and keys are recorded here."}
        />
      </Card>
    );
  }
  return (
    <Card className="overflow-hidden">
      <ul className="divide-y divide-line">
        {pages.map((e) => {
          const meta = ACTIONS[e.action] ?? { verb: e.action };
          return (
            <li key={e.id} className="flex gap-3 px-4 py-3.5">
              <Avatar name={e.actor.name || e.actor.email} hue={hues.get(e.actor.user_id ?? "") ?? hueFor(e.actor.user_id)} size={30} className="mt-0.5" />
              <div className="min-w-0 flex-1">
                <div className="text-[13px] leading-snug break-words">
                  <ActorName actor={e.actor} onActor={onActor} /> <span className="text-ink-2">{meta.verb}</span>{" "}
                  {meta.target !== false && e.target_label ? <span className="font-medium">{e.target_label}</span> : null}
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-2 text-[11.5px] text-ink-3">
                  <span className="font-mono">{e.action}</span>
                  <ChannelBadge actor={e.actor} channel={e.channel} onActor={onActor} />
                </div>
                <Details details={e.details} />
              </div>
              <Time iso={e.created_at} />
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

function Details({ details }: { details: Record<string, unknown> }) {
  const items = detailItems(details);
  if (!items.length) return null;
  return (
    <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[12px]">
      {items.map(([label, value]) => (
        <Fragment key={label}>
          <dt className="text-ink-3">{label}</dt>
          <dd className="min-w-0 break-words text-ink-2">{value}</dd>
        </Fragment>
      ))}
    </dl>
  );
}

function detailItems(details: Record<string, unknown>): [string, string][] {
  const out: [string, string][] = [];
  for (const [key, value] of Object.entries(details)) {
    if (key.endsWith("_id")) continue; // ids are in the export; names read better here
    if (key === "changes" && value && typeof value === "object") {
      for (const [field, change] of Object.entries(value as Record<string, unknown>)) out.push([humanize(field), formatChange(change)]);
      continue;
    }
    if (value === null || value === undefined || (typeof value === "object" && !Object.keys(value).length)) continue;
    if (key === "filters" && value && typeof value === "object") {
      const parts = Object.entries(value as Record<string, unknown>).map(([k, v]) => `${humanize(k)} ${formatValue(v)}`);
      out.push(["filters", parts.length ? parts.join(", ") : "none"]);
      continue;
    }
    out.push([humanize(key), formatChange(value)]);
  }
  return out;
}

function humanize(key: string) {
  return DETAIL_LABEL[key] ?? key.replace(/^settings\./, "").replace(/^config\./, "config ").replace(/[._]/g, " ");
}

function formatChange(change: unknown) {
  if (change && typeof change === "object" && "from" in change && "to" in change) {
    const c = change as { from: unknown; to: unknown };
    return `${formatValue(c.from)} → ${formatValue(c.to)}`;
  }
  if (change && typeof change === "object" && "changed" in change) return "changed";
  return formatValue(change);
}

const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/;

function formatValue(v: unknown): string {
  if (v === null || v === undefined || v === "") return "none";
  if (typeof v === "boolean") return v ? "on" : "off";
  if (typeof v === "string") return ISO.test(v) ? timeFmt.format(new Date(v)) : v.length > 160 ? `${v.slice(0, 157)}…` : v;
  if (Array.isArray(v)) return v.map(formatValue).join(", ");
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

/* ---------------------------------- Shared bits ---------------------------------- */

function ActorName({ actor, onActor }: { actor: AuditActor; onActor: (a: AuditActor, byKey?: boolean) => void }) {
  return (
    <Tooltip label={`${actor.email} · show only their entries`}>
      <button type="button" onClick={() => onActor(actor)} disabled={!actor.user_id} className="font-medium text-ink hover:underline disabled:no-underline">
        {actor.name || actor.email}
      </button>
    </Tooltip>
  );
}

function ChannelBadge({ actor, channel, onActor }: { actor: AuditActor; channel: AuditChannel; onActor: (a: AuditActor, byKey?: boolean) => void }) {
  if (channel === "app") return null;
  return (
    <Tooltip label={`Through ${channel === "mcp" ? "the MCP server" : "the API"} with an API key created by ${actor.email}. Click to show only this key.`}>
      <button type="button" onClick={() => onActor(actor, true)} className="inline-flex">
        <Badge tone="outline" className="cursor-pointer hover:border-line-strong">
          <IconKey size={12} /> {channel === "mcp" ? "MCP" : "API"} · {actor.api_key_name ?? "deleted key"}
        </Badge>
      </button>
    </Tooltip>
  );
}

function Time({ iso }: { iso: string }) {
  return (
    <time dateTime={iso} title={timeFmt.format(new Date(iso))} className="w-20 shrink-0 pt-0.5 text-right text-[12px] text-ink-3 tabular-nums">
      {relativeTime(iso)}
    </time>
  );
}
