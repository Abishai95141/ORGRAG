"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { IconMore, IconPlus, IconRefresh, IconSearch, IconTable, IconTrash } from "@/components/icons";
import { CompassArt } from "@/components/illustrations";
import { useCurrentOrg } from "@/components/layout/org-context";
import { FindDialog, NewSchemaDialog, ORIGIN_LABEL, StatusBadge, TYPE_LABEL, formatValue, useSchemaActions } from "@/components/records/shared";
import { Button, Card, EmptyState, Menu, MenuContent, MenuItem, MenuTrigger, PageHeader, Segmented, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { qk } from "@/lib/queries";
import type { RecordSchema, RecordSchemaStatus } from "@/lib/types";
import { atLeast, cn, relativeTime } from "@/lib/utils";

export default function RecordsPage() {
  const { org } = useCurrentOrg();
  const router = useRouter();
  const isAdmin = atLeast(org.role, "admin");
  const [picked, setStatus] = useState<RecordSchemaStatus | null>(null);
  const [finding, setFinding] = useState(false);
  const [creating, setCreating] = useState(false);

  const schemas = useQuery({
    queryKey: qk.recordSchemas(org.slug),
    queryFn: () => api.recordSchemas(org.slug),
    refetchInterval: (q) => (q.state.data?.some((s) => s.extracting) ? 1500 : false),
  });

  const counts = useMemo(() => {
    const c: Record<RecordSchemaStatus, number> = { active: 0, suggested: 0, archived: 0 };
    for (const s of schemas.data ?? []) c[s.status] += 1;
    return c;
  }, [schemas.data]);

  // Until an admin picks a tab, open on suggestions when nothing is active yet.
  const status = picked ?? (!counts.active && counts.suggested ? "suggested" : "active");
  // Members only ever see active types; admins filter by status.
  const shown = (schemas.data ?? []).filter((s) => !isAdmin || s.status === status);
  const byCollection = useMemo(() => {
    const groups = new Map<string, RecordSchema[]>();
    for (const s of shown) groups.set(s.collection_name, [...(groups.get(s.collection_name) ?? []), s]);
    return [...groups.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [shown]);

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl px-5 py-10 sm:px-8">
        <PageHeader
          eyebrow={org.name}
          title="Records"
          description="Registers, inventories and logs in your documents, kept as rows you can filter and count exactly. Answers use them for “which”, “how many” and “list every” questions, so nothing is missed."
          actions={
            isAdmin ? (
              <>
                <Button variant="secondary" onClick={() => setCreating(true)}>
                  <IconPlus size={16} /> New
                </Button>
                <Button variant="primary" onClick={() => setFinding(true)}>
                  <IconSearch size={16} /> Find record types
                </Button>
              </>
            ) : null
          }
        />

        {isAdmin ? (
          <div className="mb-4">
            <Segmented
              size="sm"
              value={status}
              onChange={setStatus}
              options={[
                { value: "active", label: `Active${counts.active ? ` · ${counts.active}` : ""}` },
                { value: "suggested", label: `Suggested${counts.suggested ? ` · ${counts.suggested}` : ""}` },
                { value: "archived", label: `Archived${counts.archived ? ` · ${counts.archived}` : ""}` },
              ]}
            />
          </div>
        ) : null}

        {schemas.isLoading ? (
          <div className="flex flex-col gap-3">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-28 rounded-[14px]" />
            ))}
          </div>
        ) : !shown.length ? (
          <Card>
            <EmptyState
              art={<CompassArt />}
              title={
                !isAdmin
                  ? "No records yet"
                  : status === "suggested"
                    ? "Nothing to review"
                    : status === "archived"
                      ? "Nothing archived"
                      : "No active record types"
              }
              description={
                !isAdmin
                  ? "When an admin turns a register or table in your documents into records, you can browse and filter it here."
                  : status === "active" && counts.suggested
                    ? `${counts.suggested} suggested record type${counts.suggested === 1 ? " is" : "s are"} waiting for review.`
                    : status === "archived"
                      ? "Record types you archive keep their fields, so you can activate them again."
                      : "Find record types looks for tables and field lists that repeat across a collection's documents."
              }
              action={
                isAdmin && status === "active" && counts.suggested ? (
                  <Button variant="primary" onClick={() => setStatus("suggested")}>
                    Review suggestions
                  </Button>
                ) : isAdmin && status !== "archived" ? (
                  <Button variant="primary" onClick={() => setFinding(true)}>
                    <IconSearch size={16} /> Find record types
                  </Button>
                ) : null
              }
            />
          </Card>
        ) : (
          <div className="flex flex-col gap-7">
            {byCollection.map(([collection, list]) => (
              <section key={collection}>
                <h2 className="mb-2.5 font-mono text-[11px] tracking-wider text-ink-3 uppercase">{collection}</h2>
                <ul className="flex flex-col gap-3">
                  {list.map((s) => (
                    <SchemaCard key={s.id} schema={s} isAdmin={isAdmin} />
                  ))}
                </ul>
              </section>
            ))}
          </div>
        )}
      </div>
      <FindDialog open={finding} onOpenChange={setFinding} onFound={() => setStatus("suggested")} />
      <NewSchemaDialog open={creating} onOpenChange={setCreating} onCreated={(s) => router.push(`/o/${org.slug}/records/${s.id}`)} />
    </div>
  );
}

function SchemaCard({ schema: s, isAdmin }: { schema: RecordSchema; isAdmin: boolean }) {
  const { org } = useCurrentOrg();
  const actions = useSchemaActions(s);
  const href = `/o/${org.slug}/records/${s.id}`;
  const records = s.stats.records ?? 0;
  const documents = s.stats.documents ?? 0;
  const samples = s.status === "suggested" ? (s.stats.samples ?? []) : [];
  const preview = s.fields.slice(0, 4);

  return (
    <li>
      <Card className={cn("p-5", s.status === "archived" && "opacity-80")}>
        <div className="flex items-start gap-3">
          <IconTable size={18} className={cn("mt-0.5 shrink-0", s.status === "active" ? "text-accent" : "text-ink-3")} />
          <div className="min-w-0 flex-1">
            <Link href={href} className="text-[14.5px] leading-snug font-semibold hover:underline">
              {s.label}
            </Link>
            <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-ink-3">
              <StatusBadge schema={s} />
              <span>{ORIGIN_LABEL[s.origin]}</span>
              <span>
                {s.status === "suggested"
                  ? `About ${records.toLocaleString()} record${records === 1 ? "" : "s"} in ${documents} document${documents === 1 ? "" : "s"}`
                  : s.status === "active"
                    ? `${records.toLocaleString()} record${records === 1 ? "" : "s"} from ${documents} document${documents === 1 ? "" : "s"}`
                    : `${s.fields.length} fields`}
              </span>
              {s.status === "active" && s.extracted_at ? <span>Extracted {relativeTime(s.extracted_at)}</span> : null}
              {isAdmin && s.status === "active" && s.stats.failed ? (
                <span className="text-err">
                  {s.stats.failed} document{s.stats.failed === 1 ? "" : "s"} failed
                </span>
              ) : null}
            </div>
            <div className="mt-2.5 flex flex-wrap gap-1.5">
              {s.fields.map((f) => (
                <span
                  key={f.name}
                  title={f.type === "choice" ? `One of: ${(f.options ?? []).join(", ")}` : undefined}
                  className={cn(
                    "inline-flex h-6 items-center gap-1 rounded-[6px] border border-line px-1.5 text-[12px] text-ink-2",
                    f.name === s.key_field && "border-line-strong font-medium text-ink",
                  )}
                >
                  {f.label}
                  <span className="text-[11px] text-ink-3">{f.name === s.key_field ? "key" : TYPE_LABEL[f.type].toLowerCase()}</span>
                </span>
              ))}
            </div>
            {samples.length ? (
              <div className="mt-3 overflow-x-auto rounded-[10px] border border-line">
                <table className="w-full text-left text-[12.5px]">
                  <thead className="bg-surface-2/60 text-ink-3">
                    <tr>
                      {preview.map((f) => (
                        <th key={f.name} className="px-3 py-1.5 font-medium whitespace-nowrap">
                          {f.label}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {samples.map((row, i) => (
                      <tr key={i} className="border-t border-line">
                        {preview.map((f) => (
                          <td key={f.name} className="max-w-56 truncate px-3 py-1.5 text-ink-2">
                            {formatValue(row[f.name], "text")}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
            {isAdmin && s.status === "suggested" ? (
              <div className="mt-3.5 flex flex-wrap gap-2">
                <Button size="sm" variant="primary" loading={actions.busy} onClick={actions.activate}>
                  Activate
                </Button>
                <Link href={href}>
                  <Button size="sm" variant="secondary">
                    Review fields
                  </Button>
                </Link>
                <Button size="sm" variant="ghost" onClick={actions.archive}>
                  Dismiss
                </Button>
              </div>
            ) : null}
          </div>
          {isAdmin ? (
            <Menu>
              <MenuTrigger asChild>
                <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${s.label}`}>
                  <IconMore size={16} />
                </Button>
              </MenuTrigger>
              <MenuContent align="end">
                {s.status === "active" ? (
                  <>
                    <MenuItem icon={<IconRefresh size={15} />} onSelect={() => actions.extract(true)}>
                      Re-extract all documents
                    </MenuItem>
                    <MenuItem onSelect={actions.archive}>Archive</MenuItem>
                  </>
                ) : (
                  <MenuItem onSelect={actions.activate}>Activate</MenuItem>
                )}
                <MenuItem danger icon={<IconTrash size={15} />} onSelect={actions.del}>
                  Delete
                </MenuItem>
              </MenuContent>
            </Menu>
          ) : null}
        </div>
      </Card>
    </li>
  );
}
