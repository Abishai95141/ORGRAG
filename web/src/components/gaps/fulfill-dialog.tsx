"use client";

import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { IconCheck, IconFile, IconSearch } from "@/components/icons";
import { useCurrentOrg } from "@/components/layout/org-context";
import { Button, Dialog, Field, Input, Skeleton, Textarea } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { qk, useCollections } from "@/lib/queries";
import type { DocRequest } from "@/lib/types";
import { cn, relativeTime } from "@/lib/utils";

/**
 * Answer a document request with a document of this org. The API checks the requester
 * can read it (a restricted collection needs their access first) and that it isn't
 * hidden from search; newest documents are listed first, since the answer is usually
 * the one just added.
 */
export function FulfillDialog({ request, onOpenChange }: { request: DocRequest | null; onOpenChange: (open: boolean) => void }) {
  const { org } = useCurrentOrg();
  const qc = useQueryClient();
  const open = !!request;
  const collections = useCollections(org.slug);
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [picked, setPicked] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    setQ("");
    setQuery("");
    setPicked(null);
    setNote("");
    setError(null);
  }, [open, request?.id]);

  useEffect(() => {
    const t = setTimeout(() => setQuery(q.trim()), 250);
    return () => clearTimeout(t);
  }, [q]);

  const params = { q: query, hidden: false, sort: "updated" as const, limit: 8 };
  const docs = useQuery({
    queryKey: qk.orgDocuments(org.slug, params),
    queryFn: () => api.orgDocuments(org.slug, params),
    enabled: open,
  });
  const names = useMemo(() => new Map((collections.data ?? []).map((c) => [c.id, c])), [collections.data]);

  async function fulfil() {
    if (!request || !picked) return;
    setSaving(true);
    setError(null);
    try {
      await api.fulfillDocRequest(org.slug, request.id, { document_id: picked, note: note.trim() || undefined });
      await Promise.all([
        qc.invalidateQueries({ queryKey: qk.docRequests(org.slug) }),
        qc.invalidateQueries({ queryKey: qk.gaps(org.slug) }),
      ]);
      toast(`${request.requester.name || request.requester.email} can now see the document that answers it`);
      onOpenChange(false);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Couldn't fulfill the request.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      width={580}
      title="Fulfill request"
      description={request ? <>Which document answers “{request.title}”? Add it under Sources first if it isn't indexed yet.</> : undefined}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="primary" loading={saving} disabled={!picked} onClick={fulfil}>
            <IconCheck size={15} /> Fulfill
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <div className="relative">
          <IconSearch size={15} className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-ink-3" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search documents by title" className="pl-9" aria-label="Search documents" />
        </div>
        {docs.isLoading ? (
          <div className="flex flex-col gap-2">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-11 rounded-[10px]" />
            ))}
          </div>
        ) : !docs.data?.items.length ? (
          <p className="text-[13px] text-ink-3">{query ? "No document matches." : "This organization has no documents yet."}</p>
        ) : (
          <ul role="radiogroup" aria-label="Documents" className="flex flex-col gap-1.5">
            {docs.data.items.map((d) => {
              const col = names.get(d.collection_id);
              const selected = picked === d.id;
              return (
                <li key={d.id}>
                  <button
                    type="button"
                    role="radio"
                    aria-checked={selected}
                    onClick={() => setPicked(d.id)}
                    className={cn(
                      "flex w-full items-center gap-2.5 rounded-[10px] border px-3 py-2 text-left transition-colors",
                      selected ? "border-ink bg-surface-2" : "border-line hover:border-line-strong",
                    )}
                  >
                    <IconFile size={15} className="shrink-0 text-ink-3" />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13px] font-medium">{d.title}</span>
                      <span className="block truncate text-[11.5px] text-ink-3">
                        {col?.name ?? "Collection"}
                        {col?.visibility === "restricted" ? " · restricted" : ""} · updated {relativeTime(d.source_updated_at ?? d.updated_at)}
                      </span>
                    </span>
                    {selected ? <IconCheck size={15} className="shrink-0 text-ink" /> : null}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
        <Field label="Note to the requester" htmlFor="ff-note" hint="Optional.">
          <Textarea id="ff-note" rows={2} maxLength={2000} value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
        {error ? (
          <p role="alert" className="rounded-[10px] border border-err/30 bg-err-soft px-3 py-2 text-[13px] text-err">
            {error}
          </p>
        ) : null}
      </div>
    </Dialog>
  );
}
