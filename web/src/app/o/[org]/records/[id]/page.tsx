"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useMemo, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { DocumentSheet } from "@/components/chat/document-sheet";
import { IconArrowLeft, IconChevronDown, IconFile, IconPencil, IconPlus, IconRefresh, IconWarning, IconX } from "@/components/icons";
import { NoResultsArt } from "@/components/illustrations";
import { useCurrentOrg } from "@/components/layout/org-context";
import { FieldsDialog, ORIGIN_HINT, ORIGIN_LABEL, StatusBadge, TYPE_LABEL, formatValue, useSchemaActions } from "@/components/records/shared";
import { Badge, Button, Card, EmptyState, Input, PageHeader, Segmented, Select, Skeleton, Stat, Switch } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { qk } from "@/lib/queries";
import type { RecordField, RecordFilter, RecordOp, RecordRow, RecordSchemaDetail, RecordValue, SourceRef } from "@/lib/types";
import { atLeast, cn, relativeTime } from "@/lib/utils";

const LIMIT = 300; // the API's cap per query

export default function RecordTypePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { org } = useCurrentOrg();
  const router = useRouter();
  const isAdmin = atLeast(org.role, "admin");
  const [editing, setEditing] = useState(false);

  const schema = useQuery({
    queryKey: qk.recordSchema(org.slug, id),
    queryFn: () => api.recordSchema(org.slug, id),
    refetchInterval: (q) => (q.state.data?.extracting ? 1500 : false),
  });

  if (schema.isError) {
    return (
      <div className="grid h-full place-items-center">
        <EmptyState
          art={<NoResultsArt />}
          title="Record type not found"
          description={schema.error instanceof ApiError && schema.error.status === 404 ? "It was deleted, or it's in a collection you can't read." : "Couldn't load it. Try again."}
          action={
            <Link href={`/o/${org.slug}/records`}>
              <Button variant="secondary">Back to records</Button>
            </Link>
          }
        />
      </div>
    );
  }

  const s = schema.data;
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-6xl px-5 py-10 sm:px-8">
        <Link href={`/o/${org.slug}/records`} className="mb-5 inline-flex items-center gap-1.5 text-[13px] text-ink-3 hover:text-ink">
          <IconArrowLeft size={15} /> Records
        </Link>
        {!s ? (
          <div className="flex flex-col gap-3">
            <Skeleton className="h-16 w-2/3" />
            <Skeleton className="h-24" />
            <Skeleton className="h-64" />
          </div>
        ) : (
          <>
            <Header schema={s} isAdmin={isAdmin} onEdit={() => setEditing(true)} onDeleted={() => router.push(`/o/${org.slug}/records`)} />
            {s.status === "active" ? (
              <RecordsBrowser schema={s} />
            ) : (
              <Pending schema={s} isAdmin={isAdmin} onEdit={() => setEditing(true)} />
            )}
            <FieldsDialog schema={s} open={editing} onOpenChange={setEditing} />
          </>
        )}
      </div>
    </div>
  );
}

function Header({ schema: s, isAdmin, onEdit, onDeleted }: { schema: RecordSchemaDetail; isAdmin: boolean; onEdit: () => void; onDeleted: () => void }) {
  const actions = useSchemaActions(s, { onDeleted });
  const [showFailures, setShowFailures] = useState(false);
  return (
    <>
      <PageHeader
        eyebrow={s.collection_name}
        title={s.label}
        description={s.description || ORIGIN_HINT[s.origin]}
        actions={
          isAdmin ? (
            <>
              <Button variant="secondary" onClick={onEdit}>
                <IconPencil size={15} /> Edit fields
              </Button>
              {s.status === "active" ? (
                <Button variant="secondary" loading={actions.busy || s.extracting} onClick={() => actions.extract(true)}>
                  <IconRefresh size={15} /> Re-extract
                </Button>
              ) : null}
            </>
          ) : null
        }
      />
      <Card className="mb-6 p-5">
        <div className="grid grid-cols-2 gap-5 sm:grid-cols-4">
          <Stat label="Records" value={(s.stats.records ?? 0).toLocaleString()} sub={s.status === "suggested" ? "estimated" : undefined} />
          <Stat label="Documents" value={(s.stats.documents ?? 0).toLocaleString()} />
          <Stat label="Method" value={<span className="text-[17px]">{ORIGIN_LABEL[s.origin]}</span>} />
          <Stat
            label="Status"
            value={
              <span className="flex h-7 items-center">
                <StatusBadge schema={s} />
              </span>
            }
            sub={
              s.extracting
                ? "Extracting now"
                : s.extracted_at
                  ? `Extracted ${relativeTime(s.extracted_at)}${s.origin === "llm" && s.stats.cost_usd ? ` · $${s.stats.cost_usd.toFixed(4)}` : ""}`
                  : undefined
            }
          />
        </div>
        <div className="mt-4 flex flex-wrap gap-1.5 border-t border-line pt-4">
          {s.fields.map((f) => (
            <span
              key={f.name}
              title={f.type === "choice" ? `One of: ${(f.options ?? []).join(", ")}` : undefined}
              className={cn("inline-flex h-6 items-center gap-1 rounded-[6px] border border-line px-1.5 text-[12px] text-ink-2", f.name === s.key_field && "border-line-strong font-medium text-ink")}
            >
              {f.label}
              <span className="text-[11px] text-ink-3">
                {f.name === s.key_field ? "key" : f.type === "choice" ? `${f.options?.length ?? 0} choices` : TYPE_LABEL[f.type].toLowerCase()}
              </span>
            </span>
          ))}
        </div>
        {isAdmin && s.failures.length ? (
          <div className="mt-4 rounded-[10px] border border-err/30 bg-err-soft/40 p-3 text-[13px]">
            <button type="button" className="flex w-full items-center gap-2 text-left font-medium text-err" onClick={() => setShowFailures(!showFailures)} aria-expanded={showFailures}>
              <IconWarning size={15} />
              {s.failures.length} document{s.failures.length === 1 ? "" : "s"} couldn&apos;t be extracted. The next sync retries them.
              <IconChevronDown size={15} className={cn("ml-auto transition-transform", showFailures && "rotate-180")} />
            </button>
            {showFailures ? (
              <ul className="mt-2 flex flex-col gap-1.5 text-ink-2">
                {s.failures.map((f) => (
                  <li key={f.document_id}>
                    <span className="font-medium">{f.title}</span>: <span className="text-ink-3">{f.error}</span>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}
      </Card>
    </>
  );
}

/** Suggested or archived: what activating will do, and the sample rows found. */
function Pending({ schema: s, isAdmin, onEdit }: { schema: RecordSchemaDetail; isAdmin: boolean; onEdit: () => void }) {
  const actions = useSchemaActions(s);
  const samples = s.stats.samples ?? [];
  return (
    <Card className="p-5">
      <h2 className="text-[15px] font-semibold">{s.status === "suggested" ? "Review, then activate" : "Archived"}</h2>
      <p className="mt-1 max-w-2xl text-[13.5px] leading-relaxed text-ink-3">
        {s.status === "suggested"
          ? s.origin === "llm"
            ? `Activating reads every document in ${s.collection_name} with the model and keeps only values tied to the exact words they came from. The cost shows here once it's done.`
            : `Activating extracts these records from every document in ${s.collection_name}, and every later sync keeps them current. Check the field types first: dates and numbers can be filtered by range.`
          : "Archived record types have no records and answers don't use them. Activate it to extract its records again."}
      </p>
      {samples.length ? (
        <div className="mt-4 overflow-x-auto rounded-[10px] border border-line">
          <table className="w-full text-left text-[12.5px]">
            <thead className="bg-surface-2/60 text-ink-3">
              <tr>
                {s.fields.map((f) => (
                  <th key={f.name} className="px-3 py-1.5 font-medium whitespace-nowrap">
                    {f.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {samples.map((row, i) => (
                <tr key={i} className="border-t border-line">
                  {s.fields.map((f) => (
                    <td key={f.name} className="max-w-64 truncate px-3 py-1.5 text-ink-2">
                      {formatValue(row[f.name], "text")}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {isAdmin ? (
        <div className="mt-4 flex flex-wrap gap-2">
          <Button variant="primary" loading={actions.busy} onClick={actions.activate}>
            Activate
          </Button>
          <Button variant="secondary" onClick={onEdit}>
            <IconPencil size={15} /> Edit fields
          </Button>
          <Button variant="ghost" onClick={actions.del}>
            Delete
          </Button>
        </div>
      ) : null}
    </Card>
  );
}

/* ------------------------------ Browsing records ------------------------------ */

const OP_LABEL: Record<RecordOp, string> = {
  eq: "is",
  ne: "is not",
  contains: "contains",
  in: "is one of",
  lt: "is less than",
  lte: "is at most",
  gt: "is more than",
  gte: "is at least",
  exists: "is filled in",
  missing: "is empty",
};
const DATE_OP: Partial<Record<RecordOp, string>> = { lt: "is before", lte: "is on or before", gt: "is after", gte: "is on or after" };
const OPS: Record<RecordField["type"], RecordOp[]> = {
  text: ["contains", "eq", "ne", "in", "exists", "missing"],
  choice: ["eq", "ne", "in", "exists", "missing"],
  number: ["eq", "ne", "gt", "gte", "lt", "lte", "exists", "missing"],
  date: ["eq", "ne", "gt", "gte", "lt", "lte", "exists", "missing"],
  boolean: ["eq", "exists", "missing"],
};
const opLabel = (op: RecordOp, type: RecordField["type"]) => (type === "date" && DATE_OP[op]) || OP_LABEL[op];
const noValue = (op: RecordOp) => op === "exists" || op === "missing";

function describeFilter(f: RecordFilter, field: RecordField | undefined) {
  const type = field?.type ?? "text";
  const label = field?.label ?? f.field;
  if (noValue(f.op)) return `${label} ${opLabel(f.op, type)}`;
  const value = Array.isArray(f.value) ? f.value.map((v) => formatValue(v, type)).join(", ") : formatValue(f.value as RecordValue, type);
  return `${label} ${opLabel(f.op, type)} ${value}`;
}

function RecordsBrowser({ schema: s }: { schema: RecordSchemaDetail }) {
  const { org } = useCurrentOrg();
  const [filters, setFilters] = useState<RecordFilter[]>([]);
  const [view, setView] = useState<"rows" | "counts">("rows");
  const [groupBy, setGroupBy] = useState<string>(() => s.fields.find((f) => f.type === "choice" || f.type === "boolean")?.name ?? s.key_field);
  const [sort, setSort] = useState<{ field: string; desc: boolean } | null>(null);
  const [latestOnly, setLatestOnly] = useState(false);
  const [preview, setPreview] = useState<SourceRef | null>(null);
  const byName = useMemo(() => Object.fromEntries(s.fields.map((f) => [f.name, f])), [s.fields]);
  const columns = useMemo(() => [byName[s.key_field], ...s.fields.filter((f) => f.name !== s.key_field)].filter(Boolean), [byName, s]);

  const body = {
    schema: s.name,
    filters,
    mode: view === "counts" ? ("group" as const) : ("list" as const),
    group_by: view === "counts" ? groupBy : undefined,
    sort: view === "rows" ? sort?.field : undefined,
    descending: !!sort?.desc,
    latest_only: latestOnly,
    limit: LIMIT,
  };
  const result = useQuery({
    queryKey: qk.records(org.slug, s.id, { ...body, extracted: s.extracted_at }),
    queryFn: () => api.queryRecords(org.slug, body),
    placeholderData: keepPreviousData,
  });

  const addFilter = (f: RecordFilter) => setFilters((all) => [...all.filter((x) => !(x.field === f.field && x.op === f.op)), f]);

  function open(row: RecordRow) {
    // Highlight the record's key where it's written; fall back to its longest value.
    const keyText = row.provenance[s.key_field]?.text ?? "";
    const longest = Object.values(row.provenance).reduce((best, p) => (p.text.length > best.length ? p.text : best), "");
    const snippet = keyText.length >= 12 ? keyText : longest;
    setPreview({
      ref: 0,
      origin: "knowledge",
      document_id: row.document.id,
      chunk_id: null,
      attachment_id: null,
      title: row.document.title,
      url: null,
      snippet,
      org_id: org.id,
      org_slug: org.slug,
      org_name: org.name,
      org_hue: org.hue,
      collection_name: s.collection_name,
      source_kind: null,
      score: 0,
      updated_at: row.document.updated_at,
    });
  }

  const data = result.data;
  return (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <Segmented
          size="sm"
          value={view}
          onChange={setView}
          options={[
            { value: "rows", label: "Records" },
            { value: "counts", label: "Counts" },
          ]}
        />
        {view === "counts" ? (
          <div className="flex items-center gap-2 text-[13px] text-ink-3">
            by
            <Select value={groupBy} onValueChange={setGroupBy} className="h-8 w-48" options={s.fields.map((f) => ({ value: f.name, label: f.label }))} />
          </div>
        ) : null}
        <div className="ml-auto">
          <Switch
            checked={latestOnly}
            onCheckedChange={setLatestOnly}
            label="Latest version only"
            description="When an item appears in several versions of a document, keep the newest."
          />
        </div>
      </div>

      <FilterBar fields={s.fields} filters={filters} onAdd={addFilter} onRemove={(i) => setFilters(filters.filter((_, j) => j !== i))} byName={byName} />

      <div className="mt-3 mb-2 flex items-baseline gap-2 text-[13px] text-ink-3" aria-live="polite">
        {data ? (
          <>
            <span className="font-medium text-ink">
              {data.total.toLocaleString()} record{data.total === 1 ? "" : "s"}
            </span>
            {filters.length ? "match" : null}
            {view === "rows" && data.total > data.rows.length ? <span>· showing the first {data.rows.length}</span> : null}
            {data.schemas.length > 1 ? <span>· across {data.schemas.length} collections</span> : null}
            {result.isFetching ? <span>· updating…</span> : null}
          </>
        ) : null}
      </div>

      {result.isError ? (
        <Card className="p-5 text-[13.5px] text-err">{result.error instanceof Error ? result.error.message : "Couldn't load records"}</Card>
      ) : !data ? (
        <Skeleton className="h-72" />
      ) : view === "counts" ? (
        <Counts
          groups={data.groups}
          total={data.total}
          field={byName[groupBy]}
          onPick={(value) => {
            addFilter(value === null ? { field: groupBy, op: "missing" } : { field: groupBy, op: "eq", value });
            setView("rows");
          }}
        />
      ) : !data.rows.length ? (
        <Card>
          <EmptyState
            art={<NoResultsArt />}
            title={filters.length ? "No records match" : "No records yet"}
            description={filters.length ? "Remove a filter to widen the search." : s.extracting ? "Extraction is running; records appear as each document finishes." : "None of this collection's documents had records of this type."}
          />
        </Card>
      ) : (
        <Card className="overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-[13px]">
              <thead className="border-b border-line bg-surface-2/50 text-[12px] text-ink-3">
                <tr>
                  {columns.map((f) => (
                    <th key={f.name} className="px-3 py-2 font-medium whitespace-nowrap">
                      <button
                        type="button"
                        className="inline-flex items-center gap-1 hover:text-ink"
                        onClick={() => setSort(sort?.field === f.name ? (sort.desc ? null : { field: f.name, desc: true }) : { field: f.name, desc: false })}
                        aria-label={`Sort by ${f.label}`}
                      >
                        {f.label}
                        {sort?.field === f.name ? <IconChevronDown size={13} className={cn(!sort.desc && "rotate-180")} /> : null}
                      </button>
                    </th>
                  ))}
                  <th className="px-3 py-2 font-medium">Source</th>
                </tr>
              </thead>
              <tbody>
                {data.rows.map((row) => (
                  <tr key={row.id} className="border-b border-line last:border-0 hover:bg-surface-2/40">
                    {columns.map((f) => (
                      <td
                        key={f.name}
                        className={cn("max-w-72 px-3 py-2 align-top", f.name === s.key_field ? "font-medium" : "text-ink-2", f.type === "number" && "text-right tabular-nums")}
                        title={row.provenance[f.name]?.text}
                      >
                        <span className="line-clamp-2">{formatValue(row.values[f.name], f.type)}</span>
                      </td>
                    ))}
                    <td className="px-3 py-2 align-top">
                      <button type="button" onClick={() => open(row)} className="inline-flex max-w-56 items-center gap-1.5 text-left text-ink-2 hover:text-ink hover:underline">
                        <IconFile size={14} className="shrink-0 text-ink-3" />
                        <span className="truncate">{row.document.title}</span>
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
      <DocumentSheet source={preview} onClose={() => setPreview(null)} />
    </>
  );
}

function FilterBar({
  fields,
  filters,
  byName,
  onAdd,
  onRemove,
}: {
  fields: RecordField[];
  filters: RecordFilter[];
  byName: Record<string, RecordField>;
  onAdd: (f: RecordFilter) => void;
  onRemove: (i: number) => void;
}) {
  const [fieldName, setFieldName] = useState(fields[0]?.name ?? "");
  const field = byName[fieldName];
  const type = field?.type ?? "text";
  const [op, setOp] = useState<RecordOp>(OPS[type][0]);
  const [value, setValue] = useState("");

  const pickField = (name: string) => {
    setFieldName(name);
    setOp(OPS[byName[name]?.type ?? "text"][0]);
    setValue("");
  };

  const parsed = (): RecordValue | RecordValue[] | undefined => {
    if (noValue(op)) return undefined;
    if (op === "in")
      return value
        .split(",")
        .map((v) => v.trim())
        .filter(Boolean);
    if (type === "number") return value.trim() === "" ? undefined : Number(value);
    if (type === "boolean") return value === "true";
    return value.trim() || undefined;
  };
  const v = parsed();
  const ready = noValue(op) || (v !== undefined && !(Array.isArray(v) && !v.length) && !(typeof v === "number" && Number.isNaN(v)));
  const submit = () => {
    if (!ready || !field) return;
    onAdd(noValue(op) ? { field: field.name, op } : { field: field.name, op, value: v });
    setValue("");
  };

  return (
    <div className="flex flex-col gap-2.5">
      <form
        className="flex flex-wrap items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <Select value={fieldName} onValueChange={pickField} className="h-8 w-44" options={fields.map((f) => ({ value: f.name, label: f.label }))} />
        <Select value={op} onValueChange={setOp} className="h-8 w-40" options={OPS[type].map((o) => ({ value: o, label: opLabel(o, type) }))} />
        {noValue(op) ? null : type === "boolean" ? (
          <Select
            value={value || undefined}
            onValueChange={setValue}
            placeholder="Yes or no"
            className="h-8 w-32"
            options={[
              { value: "true", label: "Yes" },
              { value: "false", label: "No" },
            ]}
          />
        ) : type === "choice" && op !== "in" ? (
          <Select value={value || undefined} onValueChange={setValue} placeholder="Choose" className="h-8 w-44" options={(field?.options ?? []).map((o) => ({ value: o, label: o }))} />
        ) : (
          <Input
            type={type === "date" && op !== "in" ? "date" : type === "number" ? "number" : "text"}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder={op === "in" ? "Values, comma-separated" : "Value"}
            className="h-8 w-52"
            aria-label="Filter value"
          />
        )}
        <Button type="submit" size="sm" variant="secondary" disabled={!ready}>
          <IconPlus size={15} /> Add filter
        </Button>
      </form>
      {filters.length ? (
        <div className="flex flex-wrap gap-1.5">
          {filters.map((f, i) => (
            <Badge key={`${f.field}-${f.op}-${i}`} tone="outline" className="h-6 gap-1.5 pr-0.5 text-[12px]">
              {describeFilter(f, byName[f.field])}
              <button type="button" onClick={() => onRemove(i)} aria-label={`Remove filter ${describeFilter(f, byName[f.field])}`} className="grid h-5 w-5 place-items-center rounded-[5px] text-ink-3 hover:bg-surface-2 hover:text-ink">
                <IconX size={12} />
              </button>
            </Badge>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function Counts({ groups, total, field, onPick }: { groups: { value: RecordValue; count: number }[]; total: number; field: RecordField | undefined; onPick: (v: RecordValue) => void }) {
  if (!groups.length) return <Card className="p-5 text-[13.5px] text-ink-3">No records to count.</Card>;
  return (
    <Card className="overflow-hidden">
      <table className="w-full text-left text-[13px]">
        <thead className="border-b border-line bg-surface-2/50 text-[12px] text-ink-3">
          <tr>
            <th className="px-3 py-2 font-medium">{field?.label ?? "Value"}</th>
            <th className="w-28 px-3 py-2 text-right font-medium">Records</th>
            <th className="w-24 px-3 py-2 text-right font-medium">Share</th>
          </tr>
        </thead>
        <tbody>
          {groups.map((g) => (
            <tr key={String(g.value)} className="border-b border-line last:border-0 hover:bg-surface-2/40">
              <td className="px-3 py-2">
                <button type="button" className="text-left hover:underline" onClick={() => onPick(g.value)} title="Show these records">
                  {g.value === null ? <span className="text-ink-3">(blank)</span> : formatValue(g.value, field?.type)}
                </button>
              </td>
              <td className="px-3 py-2 text-right tabular-nums">{g.count.toLocaleString()}</td>
              <td className="px-3 py-2 text-right text-ink-3 tabular-nums">{total ? `${Math.round((100 * g.count) / total)}%` : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}
