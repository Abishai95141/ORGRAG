"use client";

import Link from "next/link";
import { useState } from "react";
import { DocumentSheet } from "@/components/chat/document-sheet";
import { docSource } from "@/components/gaps/doc-source";
import { RequestDocumentDialog } from "@/components/gaps/request-dialog";
import { IconArrowRight, IconChat, IconFile, IconPlus, IconRequest } from "@/components/icons";
import { CompassArt } from "@/components/illustrations";
import { useCurrentOrg } from "@/components/layout/org-context";
import { Badge, Button, Card, EmptyState, PageHeader, Skeleton } from "@/components/ui";
import { useMyDocRequests } from "@/lib/queries";
import type { DocRequest, DocRequestStatus, SourceRef } from "@/lib/types";
import { atLeast, cn, relativeTime } from "@/lib/utils";

const STATUS: Record<DocRequestStatus, { label: string; tone: "ok" | "warn" | "outline" }> = {
  pending: { label: "Waiting for an admin", tone: "warn" },
  fulfilled: { label: "Fulfilled", tone: "ok" },
  dismissed: { label: "Dismissed", tone: "outline" },
};

export default function RequestsPage() {
  const { org } = useCurrentOrg();
  const requests = useMyDocRequests(org.slug);
  const [asking, setAsking] = useState(false);
  const [preview, setPreview] = useState<SourceRef | null>(null);
  const isAdmin = atLeast(org.role, "admin");

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl px-5 py-10 sm:px-8">
        <PageHeader
          eyebrow={org.name}
          title="Document requests"
          description="Documents you asked your admins for. When one is added, it shows here: ask your question again and the answer will come from it."
          actions={
            <Button variant="primary" onClick={() => setAsking(true)}>
              <IconPlus size={16} /> Request a document
            </Button>
          }
        />

        {isAdmin ? (
          <Link
            href={`/o/${org.slug}/gaps`}
            className="mb-4 flex items-center justify-between gap-3 rounded-[var(--radius-card)] border border-line bg-surface-2/50 px-4 py-3 text-[13px] text-ink-2 transition-colors hover:border-line-strong hover:text-ink"
          >
            <span>Everyone's requests, and the questions nobody could answer, are in Knowledge gaps.</span>
            <IconArrowRight size={16} className="shrink-0 text-ink-3" />
          </Link>
        ) : null}

        {requests.isLoading ? (
          <div className="flex flex-col gap-3">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-24 rounded-[14px]" />
            ))}
          </div>
        ) : !requests.data?.length ? (
          <Card>
            <EmptyState
              art={<CompassArt />}
              title="No requests yet"
              description="When an answer comes up short, use the request button under it to ask your admins for the missing document."
            />
          </Card>
        ) : (
          <ul className="flex flex-col gap-3">
            {requests.data.map((r) => (
              <RequestCard key={r.id} request={r} onOpenDocument={setPreview} />
            ))}
          </ul>
        )}
      </div>
      <RequestDocumentDialog open={asking} onOpenChange={setAsking} />
      <DocumentSheet source={preview} onClose={() => setPreview(null)} />
    </div>
  );
}

function RequestCard({ request: r, onOpenDocument }: { request: DocRequest; onOpenDocument: (s: SourceRef) => void }) {
  const { org } = useCurrentOrg();
  const status = STATUS[r.status];
  const decider = r.decided_by?.name || r.decided_by?.email;
  return (
    <li>
      <Card className={cn("p-5", r.status === "dismissed" && "opacity-80")}>
        <div className="flex items-start gap-3">
          <IconRequest size={18} className={cn("mt-0.5 shrink-0", r.status === "fulfilled" ? "text-ok" : "text-ink-3")} />
          <div className="min-w-0 flex-1">
            <h3 className="text-[14.5px] leading-snug font-semibold">{r.title}</h3>
            <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-ink-3">
              <Badge tone={status.tone}>{status.label}</Badge>
              <span>Requested {relativeTime(r.created_at)}</span>
              {r.decided_at ? (
                <span>
                  {r.status === "fulfilled" ? "Fulfilled" : "Dismissed"} {relativeTime(r.decided_at)}
                  {decider ? ` by ${decider}` : ""}
                </span>
              ) : null}
            </div>
            {r.question ? <p className="mt-2 text-[13px] text-ink-2">You asked: “{r.question}”</p> : null}
            {r.note ? <p className="mt-1 text-[13px] text-ink-3">Your note: “{r.note}”</p> : null}
            {r.resolution ? (
              <p className="mt-2 rounded-[10px] border border-line bg-surface-2/50 px-3 py-2 text-[13px] text-ink-2">
                {decider ?? "An admin"}: “{r.resolution}”
              </p>
            ) : null}

            {r.status === "fulfilled" ? (
              r.document ? (
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => onOpenDocument(docSource(r.document!, org))}
                    className="inline-flex max-w-full items-center gap-2 rounded-[10px] border border-line bg-surface px-3 py-2 text-left transition-colors hover:border-line-strong"
                  >
                    <IconFile size={15} className="shrink-0 text-ok" />
                    <span className="min-w-0">
                      <span className="block truncate text-[13px] font-medium">{r.document.title}</span>
                      <span className="block truncate text-[11.5px] text-ink-3">Answers it · {r.document.collection_name}</span>
                    </span>
                  </button>
                  {r.question ? (
                    <Link href={`/o/${org.slug}/chat?q=${encodeURIComponent(r.question)}`}>
                      <Button size="sm" variant="secondary">
                        <IconChat size={14} /> Ask again
                      </Button>
                    </Link>
                  ) : null}
                </div>
              ) : (
                <p className="mt-2 text-[13px] text-ink-3">The document that answered it is no longer available to you.</p>
              )
            ) : null}

            {r.conversation_id ? (
              <Link href={`/o/${org.slug}/chat/${r.conversation_id}`} className="mt-3 inline-flex items-center gap-1 text-[12.5px] text-ink-3 underline decoration-line-strong underline-offset-4 hover:text-ink">
                Open the conversation
              </Link>
            ) : null}
          </div>
        </div>
      </Card>
    </li>
  );
}
