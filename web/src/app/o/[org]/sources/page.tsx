"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { SourceKindIcon, SOURCE_KIND_LABEL } from "@/components/chat/source-kind";
import { IconMore, IconPlus, IconRefresh, IconScan, IconTrash } from "@/components/icons";
import { ConduitArt } from "@/components/illustrations";
import { useCurrentOrg } from "@/components/layout/org-context";
import { AddSourceDialog } from "@/components/sources/add-source-dialog";
import { StatusPill, SyncProgress } from "@/components/sources/shared";
import { Button, Card, EmptyState, Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger, PageHeader, Skeleton } from "@/components/ui";
import { confirmAction } from "@/components/ui/confirm";
import { api } from "@/lib/api";
import { qk, useSources } from "@/lib/queries";
import { atLeast, formatNumber, relativeTime } from "@/lib/utils";

function SourcesInner() {
  const { org } = useCurrentOrg();
  const params = useSearchParams();
  const qc = useQueryClient();
  const sources = useSources(org.slug);
  const [adding, setAdding] = useState(false);
  const isAdmin = atLeast(org.role, "admin");

  useEffect(() => {
    if (params.get("welcome") && isAdmin) setAdding(true);
  }, [params, isAdmin]);

  const sync = useMutation({
    mutationFn: (id: string) => api.syncSource(org.slug, id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.sources(org.slug) });
      toast("Sync queued");
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.deleteSource(org.slug, id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.sources(org.slug) });
      qc.invalidateQueries({ queryKey: qk.collections(org.slug) });
      toast("Source removed");
    },
  });

  const totals = (sources.data ?? []).reduce((a, s) => ({ docs: a.docs + s.document_count, chunks: a.chunks + s.chunk_count }), { docs: 0, chunks: 0 });

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl px-5 py-10 sm:px-8">
        <PageHeader
          eyebrow={org.name}
          title="Sources"
          description="Everything Strata can read for this organization. Each source feeds a collection; the agent only searches collections the asker can see."
          actions={
            isAdmin ? (
              <Button variant="primary" onClick={() => setAdding(true)}>
                <IconPlus size={16} /> Add source
              </Button>
            ) : null
          }
        />

        {sources.isLoading ? (
          <div className="flex flex-col gap-2">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-[76px] rounded-[14px]" />
            ))}
          </div>
        ) : !sources.data?.length ? (
          <Card>
            <EmptyState
              art={<ConduitArt />}
              title="Bring knowledge in"
              description="Upload files, crawl a documentation site, or connect a GitHub repository. Strata keeps them in sync."
              action={
                isAdmin ? (
                  <Button variant="primary" size="lg" onClick={() => setAdding(true)}>
                    <IconPlus size={16} /> Add your first source
                  </Button>
                ) : (
                  <p className="text-[13px] text-ink-3">Ask an admin to connect a source.</p>
                )
              }
            />
          </Card>
        ) : (
          <>
            <div className="mb-3 flex items-center gap-4 font-mono text-[11px] text-ink-3">
              <span>{sources.data.length} sources</span>
              <span>{formatNumber(totals.docs)} documents</span>
              <span>{formatNumber(totals.chunks)} chunks</span>
            </div>
            <Card className="divide-y divide-line overflow-hidden">
              {sources.data.map((s) => (
                <div key={s.id} className="group relative flex items-center gap-4 px-4 py-3.5 transition-colors hover:bg-surface-2/40">
                  <span className="grid h-10 w-10 shrink-0 place-items-center rounded-[11px] border border-line bg-surface-2 text-ink-2">
                    <SourceKindIcon kind={s.kind} size={19} />
                  </span>
                  <div className="min-w-0 flex-1">
                    <Link href={`/o/${org.slug}/sources/${s.id}`} className="block truncate text-[14px] font-semibold after:absolute after:inset-0">
                      {s.name}
                    </Link>
                    <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[12px] text-ink-3">
                      <span>{SOURCE_KIND_LABEL[s.kind]}</span>
                      <span>·</span>
                      <span>{s.collection_name}</span>
                      {s.sync_interval_minutes ? (
                        <>
                          <span>·</span>
                          <span>auto-sync</span>
                        </>
                      ) : null}
                    </div>
                    {s.status === "error" && s.error ? <div className="mt-1 truncate text-[12px] text-err">{s.error}</div> : null}
                    {isAdmin && s.weak_document_count ? (
                      <div className="mt-1 flex items-center gap-1 text-[12px] text-warn">
                        <IconScan size={12} /> {s.weak_document_count} {s.weak_document_count === 1 ? "document" : "documents"} may be missing text from poorly read pages
                      </div>
                    ) : null}
                    <div className="mt-2 max-w-sm empty:hidden">
                      <SyncProgress source={s} />
                    </div>
                  </div>
                  <div className="hidden w-24 text-right sm:block">
                    <div className="text-[14px] font-semibold tabular-nums">{formatNumber(s.document_count)}</div>
                    <div className="text-[11.5px] text-ink-3">documents</div>
                  </div>
                  <div className="hidden w-28 text-right md:block">
                    <StatusPill status={s.status} />
                    <div className="mt-0.5 text-[11.5px] text-ink-3">{relativeTime(s.last_synced_at)}</div>
                  </div>
                  {isAdmin ? (
                    <Menu>
                      <MenuTrigger asChild>
                        <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${s.name}`} className="relative z-10">
                          <IconMore size={16} />
                        </Button>
                      </MenuTrigger>
                      <MenuContent align="end">
                        {s.kind !== "upload" ? (
                          <MenuItem icon={<IconRefresh size={15} />} onSelect={() => sync.mutate(s.id)} disabled={s.status === "syncing" || s.status === "queued"}>
                            Sync now
                          </MenuItem>
                        ) : null}
                        {s.kind !== "upload" ? <MenuSeparator /> : null}
                        <MenuItem danger icon={<IconTrash size={15} />} onSelect={async () => (await confirmAction({ title: `Remove ${s.name}?`, body: `Its ${s.document_count} documents will be removed from search. This cannot be undone.`, confirmLabel: "Remove source", danger: true })) && remove.mutate(s.id)}>
                          Remove source
                        </MenuItem>
                      </MenuContent>
                    </Menu>
                  ) : null}
                </div>
              ))}
            </Card>
          </>
        )}
      </div>
      <AddSourceDialog open={adding} onOpenChange={setAdding} slug={org.slug} />
    </div>
  );
}

export default function SourcesPage() {
  return (
    <Suspense>
      <SourcesInner />
    </Suspense>
  );
}
