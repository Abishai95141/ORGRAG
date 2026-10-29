"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { DocumentSheet } from "@/components/chat/document-sheet";
import { SourceKindIcon, SOURCE_KIND_LABEL } from "@/components/chat/source-kind";
import { IconArrowLeft, IconExternal, IconRefresh, IconSearch, IconTrash } from "@/components/icons";
import { NoResultsArt } from "@/components/illustrations";
import { useCurrentOrg } from "@/components/layout/org-context";
import { Dropzone, INTERVALS, StatusPill, SyncProgress } from "@/components/sources/shared";
import { Button, Card, EmptyState, Input, Select, Skeleton, Stat } from "@/components/ui";
import { confirmAction } from "@/components/ui/confirm";
import { api, ApiError } from "@/lib/api";
import { qk, useSource } from "@/lib/queries";
import type { DocumentRow, SourceRef } from "@/lib/types";
import { atLeast, formatNumber, hostname, relativeTime } from "@/lib/utils";

const PAGE = 25;

export default function SourceDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { org } = useCurrentOrg();
  const router = useRouter();
  const qc = useQueryClient();
  const source = useSource(org.slug, id);
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const [preview, setPreview] = useState<SourceRef | null>(null);
  const isAdmin = atLeast(org.role, "admin");
  const canUpload = atLeast(org.role, "member");

  const docs = useQuery({
    queryKey: qk.documents(org.slug, id, q, offset),
    queryFn: () => api.documents(org.slug, id, { limit: PAGE, offset, q: q || undefined }),
    placeholderData: (prev) => prev,
    refetchInterval: source.data && ["queued", "syncing"].includes(source.data.status) ? 2500 : false,
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: qk.source(org.slug, id) });
    qc.invalidateQueries({ queryKey: ["org", org.slug, "sources", id, "documents"] });
    qc.invalidateQueries({ queryKey: qk.sources(org.slug) });
  };

  const sync = useMutation({ mutationFn: () => api.syncSource(org.slug, id), onSuccess: () => (refresh(), toast("Sync queued")) });
  const upload = useMutation({
    mutationFn: (files: File[]) => api.uploadFiles(org.slug, id, files),
    onSuccess: (r) => (refresh(), toast.success(`Indexing ${r.accepted} file${r.accepted === 1 ? "" : "s"}`)),
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Upload failed"),
  });
  const update = useMutation({
    mutationFn: (minutes: number | null) => api.updateSource(org.slug, id, { sync_interval_minutes: minutes }),
    onSuccess: () => (refresh(), toast("Schedule updated")),
  });
  const removeDoc = useMutation({
    mutationFn: (docId: string) => api.deleteDocument(org.slug, docId),
    onSuccess: () => (refresh(), toast("Document removed")),
  });

  async function removeSource() {
    const s = source.data!;
    const ok = await confirmAction({
      title: `Remove ${s.name}?`,
      body: `Its ${s.document_count} documents will be removed from search. This cannot be undone.`,
      confirmLabel: "Remove source",
      danger: true,
    });
    if (!ok) return;
    await api.deleteSource(org.slug, id);
    qc.invalidateQueries({ queryKey: qk.sources(org.slug) });
    router.push(`/o/${org.slug}/sources`);
  }

  function openDoc(d: DocumentRow) {
    const s = source.data!;
    setPreview({
      ref: 0,
      document_id: d.id,
      chunk_id: "",
      title: d.title,
      url: d.url,
      snippet: "",
      org_id: org.id,
      org_slug: org.slug,
      org_name: org.name,
      org_hue: org.hue,
      collection_name: s.collection_name,
      source_kind: s.kind,
      score: 0,
    });
  }

  if (source.isError) {
    return (
      <div className="grid h-full place-items-center">
        <EmptyState art={<NoResultsArt />} title="Source not found" action={<Link href={`/o/${org.slug}/sources`}><Button>Back to sources</Button></Link>} />
      </div>
    );
  }

  const s = source.data;
  const cfg = (s?.config ?? {}) as Record<string, unknown>;
  const total = docs.data?.total ?? 0;

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl px-5 py-8 sm:px-8">
        <Link href={`/o/${org.slug}/sources`} className="mb-6 inline-flex items-center gap-1.5 text-[13px] text-ink-3 hover:text-ink">
          <IconArrowLeft size={15} /> Sources
        </Link>

        {!s ? (
          <Skeleton className="h-40 rounded-[14px]" />
        ) : (
          <>
            <div className="flex flex-wrap items-start gap-4 pb-6">
              <span className="grid h-12 w-12 place-items-center rounded-[13px] border border-line bg-surface text-ink-2">
                <SourceKindIcon kind={s.kind} size={22} />
              </span>
              <div className="min-w-0 flex-1">
                <h1 className="font-display text-[34px] leading-[1.05] tracking-tight">{s.name}</h1>
                <div className="mt-1.5 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[13px] text-ink-3">
                  <StatusPill status={s.status} />
                  <span>·</span>
                  <span>{SOURCE_KIND_LABEL[s.kind]}</span>
                  <span>·</span>
                  <span>{s.collection_name}</span>
                  {typeof cfg.url === "string" ? (
                    <a href={cfg.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 hover:text-ink">
                      · {hostname(cfg.url)} <IconExternal size={12} />
                    </a>
                  ) : null}
                  {typeof cfg.repo === "string" ? (
                    <a href={`https://github.com/${cfg.repo}`} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-mono text-[12px] hover:text-ink">
                      · {cfg.repo} <IconExternal size={12} />
                    </a>
                  ) : null}
                </div>
              </div>
              {isAdmin ? (
                <div className="flex gap-2">
                  {s.kind !== "upload" ? (
                    <Button onClick={() => sync.mutate()} loading={sync.isPending} disabled={s.status === "syncing" || s.status === "queued"}>
                      <IconRefresh size={15} /> Sync now
                    </Button>
                  ) : null}
                  <Button variant="ghost" size="icon" aria-label="Remove source" onClick={removeSource}>
                    <IconTrash size={16} />
                  </Button>
                </div>
              ) : null}
            </div>

            {s.status === "error" && s.error ? (
              <div className="mb-5 rounded-[12px] border border-err/30 bg-err-soft px-4 py-3 text-[13px] text-err">
                <span className="font-semibold">Last sync failed.</span> {s.error}
              </div>
            ) : null}

            <Card className="mb-6 grid grid-cols-2 gap-6 p-5 sm:grid-cols-4">
              <Stat label="Documents" value={formatNumber(s.document_count)} />
              <Stat label="Chunks" value={formatNumber(s.chunk_count)} />
              <Stat label="Last synced" value={<span className="text-[18px]">{relativeTime(s.last_synced_at)}</span>} />
              <div className="flex flex-col gap-1.5">
                <div className="font-mono text-[10.5px] tracking-wider text-ink-3 uppercase">Schedule</div>
                {s.kind === "upload" ? (
                  <span className="text-[13px] text-ink-2">On upload</span>
                ) : isAdmin ? (
                  <Select
                    value={String(s.sync_interval_minutes ?? 0)}
                    onValueChange={(v) => update.mutate(v === "0" ? null : Number(v))}
                    options={INTERVALS}
                    className="h-8"
                  />
                ) : (
                  <span className="text-[13px] text-ink-2">{INTERVALS.find((i) => i.value === String(s.sync_interval_minutes ?? 0))?.label}</span>
                )}
              </div>
              {s.status === "syncing" || s.status === "queued" ? (
                <div className="col-span-full">
                  <SyncProgress source={s} />
                </div>
              ) : null}
            </Card>

            {s.kind === "upload" && canUpload ? (
              <div className="mb-6">
                <Dropzone compact onFiles={(f) => f.length && upload.mutate(f)} disabled={upload.isPending} />
              </div>
            ) : null}

            <div className="mb-3 flex items-center justify-between gap-3">
              <h2 className="text-[15px] font-semibold">
                Documents <span className="font-mono text-[12px] font-normal text-ink-3">{total}</span>
              </h2>
              <div className="relative w-64">
                <IconSearch size={15} className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-ink-3" />
                <Input
                  placeholder="Filter by title"
                  value={q}
                  onChange={(e) => {
                    setQ(e.target.value);
                    setOffset(0);
                  }}
                  className="h-8 pl-8 text-[13px]"
                />
              </div>
            </div>

            <Card className="overflow-hidden">
              {docs.isLoading ? (
                <div className="flex flex-col gap-3 p-4">
                  {[0, 1, 2, 3].map((i) => (
                    <Skeleton key={i} className="h-4" />
                  ))}
                </div>
              ) : !docs.data?.items.length ? (
                <p className="px-4 py-10 text-center text-[13px] text-ink-3">
                  {q ? "No documents match that filter." : s.status === "syncing" || s.status === "queued" ? "Documents will appear as they're indexed." : "No documents yet."}
                </p>
              ) : (
                <table className="w-full text-[13px]">
                  <thead>
                    <tr className="border-b border-line text-left font-mono text-[10.5px] tracking-wider text-ink-3 uppercase">
                      <th className="px-4 py-2.5 font-normal">Title</th>
                      <th className="hidden px-4 py-2.5 text-right font-normal sm:table-cell">Chunks</th>
                      <th className="hidden px-4 py-2.5 text-right font-normal md:table-cell">Tokens</th>
                      <th className="px-4 py-2.5 text-right font-normal">Updated</th>
                      {isAdmin ? <th className="w-10" /> : null}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {docs.data.items.map((d) => (
                      <tr key={d.id} className="group cursor-pointer transition-colors hover:bg-surface-2/50" onClick={() => openDoc(d)}>
                        <td className="max-w-0 px-4 py-2.5">
                          <div className="truncate font-medium">{d.title}</div>
                          {d.url ? <div className="truncate font-mono text-[11px] text-ink-3">{d.url.replace(/^https?:\/\//, "")}</div> : null}
                        </td>
                        <td className="hidden px-4 py-2.5 text-right text-ink-2 tabular-nums sm:table-cell">{d.chunk_count}</td>
                        <td className="hidden px-4 py-2.5 text-right text-ink-2 tabular-nums md:table-cell">{formatNumber(d.token_count)}</td>
                        <td className="px-4 py-2.5 text-right whitespace-nowrap text-ink-3">{relativeTime(d.updated_at)}</td>
                        {isAdmin ? (
                          <td className="px-2">
                            <button
                              aria-label={`Remove ${d.title}`}
                              onClick={async (e) => {
                                e.stopPropagation();
                                if (await confirmAction({ title: "Remove document?", body: d.title, confirmLabel: "Remove", danger: true })) removeDoc.mutate(d.id);
                              }}
                              className="grid h-7 w-7 place-items-center rounded-md text-ink-3 opacity-0 group-hover:opacity-100 hover:bg-surface-3 hover:text-err"
                            >
                              <IconTrash size={14} />
                            </button>
                          </td>
                        ) : null}
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {total > PAGE ? (
                <div className="flex items-center justify-between border-t border-line px-4 py-2.5 text-[12px] text-ink-3">
                  <span>
                    {offset + 1}–{Math.min(offset + PAGE, total)} of {total}
                  </span>
                  <div className="flex gap-1.5">
                    <Button size="sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
                      Previous
                    </Button>
                    <Button size="sm" disabled={offset + PAGE >= total} onClick={() => setOffset(offset + PAGE)}>
                      Next
                    </Button>
                  </div>
                </div>
              ) : null}
            </Card>
          </>
        )}
      </div>
      <DocumentSheet source={preview} onClose={() => setPreview(null)} />
    </div>
  );
}
