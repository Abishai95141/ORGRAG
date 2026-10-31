"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { IconPlus, IconTrash } from "@/components/icons";
import { useCurrentOrg } from "@/components/layout/org-context";
import { Badge, Button, Dialog, Field, Input, Select, Textarea } from "@/components/ui";
import { confirmAction } from "@/components/ui/confirm";
import { api, ApiError } from "@/lib/api";
import { qk } from "@/lib/queries";
import type { RecordField, RecordFieldType, RecordOrigin, RecordSchema, RecordSchemaStatus, RecordValue } from "@/lib/types";

export const ORIGIN_LABEL: Record<RecordOrigin, string> = {
  fields_table: "Field tables",
  rows_table: "Table rows",
  field_lines: "Labelled lines",
  llm: "Read from prose",
};

export const ORIGIN_HINT: Record<RecordOrigin, string> = {
  fields_table: "One record per document, from its field/value table.",
  rows_table: "One record per row of a table that recurs across documents.",
  field_lines: "One record per run of “Label: value” lines.",
  llm: "The model reads each document; every value is tied to the words it came from.",
};

export const STATUS: Record<RecordSchemaStatus, { label: string; tone: "accent" | "ok" | "neutral" }> = {
  suggested: { label: "Suggested", tone: "accent" },
  active: { label: "Active", tone: "ok" },
  archived: { label: "Archived", tone: "neutral" },
};

export const TYPE_LABEL: Record<RecordFieldType, string> = {
  text: "Text",
  number: "Number",
  date: "Date",
  boolean: "Yes / no",
  choice: "Choice",
};

const dateFmt = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });

export function formatValue(value: RecordValue | undefined, type: RecordFieldType | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return value.toLocaleString();
  if (type === "date" && /^\d{4}-\d{2}-\d{2}$/.test(value)) return dateFmt.format(new Date(`${value}T00:00:00Z`));
  return value;
}

/** A label as a field name, the way the API derives them ("Renewal date" → "renewal_date"). */
export function fieldName(label: string) {
  const name = label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 64);
  return /^[a-z]/.test(name) ? name : `f_${name}`.slice(0, 64);
}

export function StatusBadge({ schema }: { schema: RecordSchema }) {
  if (schema.extracting) return <Badge tone="warn">Extracting…</Badge>;
  const s = STATUS[schema.status];
  return <Badge tone={s.tone}>{s.label}</Badge>;
}

/** Status changes, extraction and deletion, with the toasts and cache refreshes they need. */
export function useSchemaActions(schema: RecordSchema, opts: { onDeleted?: () => void } = {}) {
  const { org } = useCurrentOrg();
  const qc = useQueryClient();
  const refresh = () => qc.invalidateQueries({ queryKey: qk.recordSchemas(org.slug) });
  const fail = (e: unknown) => toast.error(e instanceof Error ? e.message : "Something went wrong");

  const setStatus = useMutation({
    mutationFn: (status: RecordSchemaStatus) => api.updateRecordSchema(org.slug, schema.id, { status }),
    onSuccess: (s) => {
      refresh();
      toast(s.status === "active" ? `${s.label}: extracting records` : s.status === "archived" ? `${s.label} archived` : "Saved");
    },
    onError: fail,
  });
  const extract = useMutation({
    mutationFn: (force: boolean) => api.extractRecords(org.slug, schema.id, force),
    onSuccess: () => (refresh(), toast("Extraction queued")),
    onError: fail,
  });
  const remove = useMutation({
    mutationFn: () => api.deleteRecordSchema(org.slug, schema.id),
    onSuccess: () => {
      refresh();
      toast(`${schema.label} deleted`);
      opts.onDeleted?.();
    },
    onError: fail,
  });

  const archive = async () => {
    // A suggestion has no records yet, so dismissing it needs no confirmation.
    if (
      schema.status !== "active" ||
      (await confirmAction({
        title: `Archive ${schema.label}?`,
        body: "Its records are removed and answers stop using them. Activate it again to re-extract.",
        confirmLabel: "Archive",
      }))
    )
      setStatus.mutate("archived");
  };
  const del = async () => {
    if (
      await confirmAction({
        title: `Delete ${schema.label}?`,
        body: "The record type and its records are deleted. Documents aren't affected. “Find record types” can suggest it again.",
        confirmLabel: "Delete",
        danger: true,
      })
    )
      remove.mutate();
  };
  return {
    activate: () => setStatus.mutate("active"),
    archive,
    del,
    extract: (force = false) => extract.mutate(force),
    busy: setStatus.isPending || extract.isPending || remove.isPending,
  };
}

/* ------------------------------ Find record types ------------------------------ */

export function FindDialog({ open, onOpenChange, onFound }: { open: boolean; onOpenChange: (v: boolean) => void; onFound: () => void }) {
  const { org } = useCurrentOrg();
  const qc = useQueryClient();
  const collections = useQuery({ queryKey: qk.collections(org.slug), queryFn: () => api.collections(org.slug), enabled: open });
  const [collectionId, setCollectionId] = useState<string | undefined>();

  useEffect(() => {
    if (open && !collectionId && collections.data?.length) setCollectionId(collections.data[0].id);
  }, [open, collectionId, collections.data]);

  const find = useMutation({
    mutationFn: () => api.suggestRecordSchemas(org.slug, collectionId!),
    onSuccess: (found) => {
      qc.invalidateQueries({ queryKey: qk.recordSchemas(org.slug) });
      const fresh = found.filter((s) => s.status === "suggested").length;
      toast(
        found.length === 0
          ? "No recurring tables or field lists in this collection"
          : fresh
            ? `Found ${fresh} record type${fresh === 1 ? "" : "s"} to review`
            : "Nothing new: these record types are already set up",
      );
      onOpenChange(false);
      onFound();
    },
    onError: (e) => toast.error(e instanceof Error ? e.message : "Couldn't look for record types"),
  });

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title="Find record types"
      description="Strata looks for tables and “Label: value” lists that repeat across a collection's documents, and suggests a record type for each. Nothing is extracted until you activate one."
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" loading={find.isPending} disabled={!collectionId} onClick={() => find.mutate()}>
            Find record types
          </Button>
        </>
      }
    >
      <Field label="Collection">
        <Select
          value={collectionId}
          onValueChange={setCollectionId}
          placeholder="Choose a collection"
          options={(collections.data ?? []).map((c) => ({ value: c.id, label: c.name, hint: `${c.document_count} documents` }))}
        />
      </Field>
    </Dialog>
  );
}

/* ------------------------------ Field editor ------------------------------ */

type Draft = RecordField & { _new?: boolean; _options: string; _aliases: string };

const toDraft = (f: RecordField): Draft => ({ ...f, _options: (f.options ?? []).join(", "), _aliases: (f.aliases ?? []).join(", ") });
const split = (s: string) =>
  s
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean);

function FieldRows({
  fields,
  setFields,
  keyIndex,
  setKeyIndex,
  showAliases,
}: {
  fields: Draft[];
  setFields: (f: Draft[]) => void;
  keyIndex: number;
  setKeyIndex: (i: number) => void;
  showAliases: boolean;
}) {
  const update = (i: number, patch: Partial<Draft>) => setFields(fields.map((f, j) => (j === i ? { ...f, ...patch } : f)));
  const removeAt = (i: number) => {
    setFields(fields.filter((_, j) => j !== i));
    if (i < keyIndex) setKeyIndex(keyIndex - 1);
    else if (i === keyIndex) setKeyIndex(-1);
  };
  return (
    <div className="flex flex-col gap-2">
      <div className="grid grid-cols-[1fr_118px_52px_28px] items-center gap-2 px-1 text-[11.5px] font-medium text-ink-3">
        <span>Field</span>
        <span>Type</span>
        <span className="text-center">Key</span>
        <span />
      </div>
      {fields.map((f, i) => {
        const name = f._new ? fieldName(f.label) : f.name;
        return (
          <div key={i} className="rounded-[10px] border border-line p-2">
            <div className="grid grid-cols-[1fr_118px_52px_28px] items-center gap-2">
              <Input
                value={f.label}
                onChange={(e) => update(i, { label: e.target.value })}
                placeholder="Label, e.g. Renewal date"
                aria-label={`Field ${i + 1} label`}
              />
              <Select
                value={f.type}
                onValueChange={(type) => update(i, { type })}
                options={(Object.keys(TYPE_LABEL) as RecordFieldType[]).map((t) => ({ value: t, label: TYPE_LABEL[t] }))}
              />
              <label className="flex justify-center" title="The value that names a record (vendor, incident ID…)">
                <input
                  type="radio"
                  name="key-field"
                  checked={keyIndex === i}
                  onChange={() => setKeyIndex(i)}
                  aria-label={`Use ${f.label || "this field"} as the key`}
                  className="h-4 w-4 accent-[var(--ink)]"
                />
              </label>
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={`Remove ${f.label || "field"}`}
                disabled={fields.length === 1}
                onClick={() => removeAt(i)}
              >
                <IconTrash size={15} />
              </Button>
            </div>
            <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1.5 px-1">
              <code className="font-mono text-[11px] text-ink-3">{name || "name"}</code>
              {f.type === "choice" ? (
                <Input
                  value={f._options}
                  onChange={(e) => update(i, { _options: e.target.value })}
                  placeholder="Options, comma-separated"
                  aria-label={`${f.label} options`}
                  className="h-7 min-w-48 flex-1 text-[12.5px]"
                />
              ) : null}
              {showAliases ? (
                <Input
                  value={f._aliases}
                  onChange={(e) => update(i, { _aliases: e.target.value })}
                  placeholder="Other column labels, comma-separated"
                  aria-label={`${f.label} other column labels`}
                  className="h-7 min-w-48 flex-1 text-[12.5px]"
                />
              ) : null}
            </div>
          </div>
        );
      })}
      <Button
        variant="ghost"
        size="sm"
        className="self-start"
        onClick={() => setFields([...fields, { name: "", label: "", type: "text", _new: true, _options: "", _aliases: "" }])}
      >
        <IconPlus size={15} /> Add field
      </Button>
    </div>
  );
}

/** Turn drafts into API fields, or an error message. */
function finish(fields: Draft[], keyIndex: number): { fields: RecordField[]; key: string } | string {
  const out: RecordField[] = [];
  for (const f of fields) {
    const label = f.label.trim();
    if (!label) return "Every field needs a label.";
    const name = f._new ? fieldName(label) : f.name;
    const options = split(f._options);
    if (f.type === "choice" && !options.length) return `“${label}” is a choice: list its options.`;
    out.push({
      name,
      label,
      type: f.type,
      description: f.description ?? "",
      options: f.type === "choice" ? options : null,
      aliases: split(f._aliases),
    });
  }
  const names = out.map((f) => f.name);
  if (new Set(names).size !== names.length) return "Two fields have the same name; give them different labels.";
  if (!out[keyIndex]) return "Choose the key field: the value that names each record.";
  return { fields: out, key: out[keyIndex].name };
}

export function FieldsDialog({ schema, open, onOpenChange }: { schema: RecordSchema; open: boolean; onOpenChange: (v: boolean) => void }) {
  const { org } = useCurrentOrg();
  const qc = useQueryClient();
  const [label, setLabel] = useState(schema.label);
  const [description, setDescription] = useState(schema.description);
  const [fields, setFields] = useState<Draft[]>([]);
  const [keyIndex, setKeyIndex] = useState(-1);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setLabel(schema.label);
    setDescription(schema.description);
    setFields(schema.fields.map(toDraft));
    setKeyIndex(schema.fields.findIndex((f) => f.name === schema.key_field));
    setError(null);
  }, [open, schema]);

  const save = useMutation({
    mutationFn: (body: { fields: RecordField[]; key_field: string }) =>
      api.updateRecordSchema(org.slug, schema.id, { label: label.trim(), description: description.trim(), ...body }),
    onSuccess: (s) => {
      qc.invalidateQueries({ queryKey: qk.recordSchemas(org.slug) });
      toast(s.status === "active" && s.extracting ? "Saved: re-extracting records" : "Saved");
      onOpenChange(false);
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Couldn't save"),
  });

  const submit = () => {
    const done = finish(fields, keyIndex);
    if (typeof done === "string") return setError(done);
    if (!label.trim()) return setError("Give the record type a name.");
    save.mutate({ fields: done.fields, key_field: done.key });
  };

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      width={620}
      title={`Edit ${schema.label}`}
      description={
        schema.status === "active"
          ? "Changing fields or the key re-extracts every document's records."
          : "Review the fields before activating. Types decide how values are compared: dates and numbers can be filtered by range."
      }
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" loading={save.isPending} onClick={submit}>
            Save
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label="Name">
          <Input value={label} onChange={(e) => setLabel(e.target.value)} maxLength={120} />
        </Field>
        <Field label="Description" hint="Tells answers what this record type covers.">
          <Textarea value={description} onChange={(e) => setDescription(e.target.value)} maxLength={1000} className="min-h-16" />
        </Field>
        <FieldRows fields={fields} setFields={setFields} keyIndex={keyIndex} setKeyIndex={setKeyIndex} showAliases={schema.origin !== "llm"} />
        {schema.origin !== "llm" ? (
          <p className="text-[12px] text-ink-3">A field fills from the column or label with the same name, or one of its other labels.</p>
        ) : null}
        {error ? <p className="text-[13px] text-err">{error}</p> : null}
      </div>
    </Dialog>
  );
}

/* ------------------------------ New (prose) record type ------------------------------ */

export function NewSchemaDialog({ open, onOpenChange, onCreated }: { open: boolean; onOpenChange: (v: boolean) => void; onCreated: (s: RecordSchema) => void }) {
  const { org } = useCurrentOrg();
  const qc = useQueryClient();
  const collections = useQuery({ queryKey: qk.collections(org.slug), queryFn: () => api.collections(org.slug), enabled: open });
  const [collectionId, setCollectionId] = useState<string | undefined>();
  const [label, setLabel] = useState("");
  const [description, setDescription] = useState("");
  const [fields, setFields] = useState<Draft[]>([]);
  const [keyIndex, setKeyIndex] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setLabel("");
    setDescription("");
    setFields([{ name: "", label: "", type: "text", _new: true, _options: "", _aliases: "" }]);
    setKeyIndex(0);
    setError(null);
  }, [open]);
  useEffect(() => {
    if (open && !collectionId && collections.data?.length) setCollectionId(collections.data[0].id);
  }, [open, collectionId, collections.data]);

  const create = useMutation({
    mutationFn: (body: { fields: RecordField[]; key_field: string }) =>
      api.createRecordSchema(org.slug, {
        collection_id: collectionId!,
        name: fieldName(label),
        label: label.trim(),
        description: description.trim(),
        ...body,
      }),
    onSuccess: (s) => {
      qc.invalidateQueries({ queryKey: qk.recordSchemas(org.slug) });
      toast(`${s.label} created. Activate it to extract records.`);
      onOpenChange(false);
      onCreated(s);
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Couldn't create the record type"),
  });

  const submit = () => {
    if (!collectionId) return setError("Choose a collection.");
    if (!label.trim()) return setError("Give the record type a name.");
    const done = finish(fields, keyIndex);
    if (typeof done === "string") return setError(done);
    create.mutate({ fields: done.fields, key_field: done.key });
  };

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      width={620}
      title="New record type"
      description="For facts written in prose rather than tables (decisions in meeting notes, commitments in contracts). The model reads each document and keeps only values it can tie to the exact words they came from."
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" loading={create.isPending} onClick={submit}>
            Create
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Name">
            <Input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="e.g. Decision" maxLength={120} />
          </Field>
          <Field label="Collection">
            <Select
              value={collectionId}
              onValueChange={setCollectionId}
              placeholder="Choose a collection"
              options={(collections.data ?? []).map((c) => ({ value: c.id, label: c.name }))}
            />
          </Field>
        </div>
        <Field label="What counts as one record?" hint="The model reads this, so be specific.">
          <Textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="e.g. A decision recorded in meeting notes: what was decided, who owns it, and when."
            maxLength={1000}
            className="min-h-16"
          />
        </Field>
        <FieldRows fields={fields} setFields={setFields} keyIndex={keyIndex} setKeyIndex={setKeyIndex} showAliases={false} />
        {error ? <p className="text-[13px] text-err">{error}</p> : null}
      </div>
    </Dialog>
  );
}
