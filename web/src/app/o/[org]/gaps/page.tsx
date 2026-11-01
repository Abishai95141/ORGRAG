"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { DocumentSheet } from "@/components/chat/document-sheet";
import { FulfillDialog } from "@/components/gaps/fulfill-dialog";
import { docSource } from "@/components/gaps/doc-source";
import { RequestDocumentDialog } from "@/components/gaps/request-dialog";
import { IconChat, IconCheck, IconChevronDown, IconFile, IconGap, IconLock, IconPlus, IconRequest, IconSources, IconVerified, IconX } from "@/components/icons";
import { Avatar, NoResultsArt, OrgMark } from "@/components/illustrations";
import { useCurrentOrg } from "@/components/layout/org-context";
import { Badge, Button, Card, Dialog, EmptyState, Field, PageHeader, Segmented, Skeleton, Stat, Textarea } from "@/components/ui";
import { VerifyDialog } from "@/components/verified/verify-dialog";
import { api, ApiError } from "@/lib/api";
import { qk, useDocRequests, useGaps } from "@/lib/queries";
import type { DocRequest, DocRequestStatus, GapCluster, GapHint, GapReason, SourceRef } from "@/lib/types";
import { atLeast, cn, formatNumber, relativeTime } from "@/lib/utils";

const REASONS: { key: GapReason; label: string; tone: "err" | "warn" | "neutral" | "outline"; hint: string }[] = [
  { key: "unanswered", label: "Unanswered", tone: "err", hint: "The agent searched but found nothing to cite" },
  { key: "incomplete", label: "Incomplete", tone: "warn", hint: "The coverage check found facts missing from the sources" },
  { key: "downvoted", label: "Thumbs-down", tone: "neutral", hint: "The asker rated the answer not helpful" },
  { key: "no_results", label: "No search results", tone: "outline", hint: "A search that found nothing" },
  {
    key: "restricted_in_channel",
    label: "Restricted, asked in a channel",
    tone: "outline",
    hint: "Asked in a Slack channel, which doesn't search the restricted collections the asker can read",
  },
];

export default function GapsPage() {
  const { org } = useCurrentOrg();
  const [days, setDays] = useState<"7" | "30" | "90">("30");
  const [preview, setPreview] = useState<SourceRef | null>(null);
  const isAdmin = atLeast(org.role, "admin");
  const gaps = useGaps(org.slug, Number(days), isAdmin);
  const d = gaps.data;

  if (!isAdmin) {
    return (
      <div className="h-full overflow-y-auto">
        <div className="mx-auto max-w-4xl px-5 py-10 sm:px-8">
          <Card>
            <EmptyState
              art={<NoResultsArt />}
              title="Admins only"
              description="Knowledge gaps show admins what nobody could find. You can ask for a missing document under Requests."
              action={
                <Link href={`/o/${org.slug}/requests`}>
                  <Button variant="primary">
                    <IconRequest size={16} /> Your requests
                  </Button>
                </Link>
              }
            />
          </Card>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl px-5 py-10 sm:px-8">
        <PageHeader
          eyebrow={org.name}
          title="Knowledge gaps"
          description="Questions your sources couldn't answer, grouped by meaning: what the agent found missing, where else it was answered, and who asked for a document."
          actions={
            <Segmented
              size="sm"
              value={days}
              onChange={setDays}
              options={[
                { value: "7", label: "7 days" },
                { value: "30", label: "30 days" },
                { value: "90", label: "90 days" },
              ]}
            />
          }
        />

        <DocRequestsPanel onOpenDocument={setPreview} />

        {!d ? (
          gaps.error ? (
            <Card className="p-5 text-[13px] text-err">{gaps.error instanceof ApiError ? gaps.error.message : "Couldn't load knowledge gaps."}</Card>
          ) : (
            <div className="flex flex-col gap-3">
              <Skeleton className="h-24 rounded-[14px]" />
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-36 rounded-[14px]" />
              ))}
            </div>
          )
        ) : (
          <div className="flex flex-col gap-4">
            <Card className="grid grid-cols-2 gap-6 p-5 sm:grid-cols-4">
              <Stat label="Unanswered questions" value={formatNumber(d.questions)} sub={d.truncated ? "the most recent only" : `last ${days} days`} />
              <Stat label="Gaps" value={formatNumber(d.total_clusters)} sub="grouped by meaning" />
              <Stat label="Asked often" value={formatNumber(d.clusters.filter((c) => c.count > 1).length)} sub="gaps asked more than once" />
              <Stat label="Answered elsewhere" value={formatNumber(d.clusters.filter((c) => c.answered_elsewhere.length).length)} sub="in your other organizations" />
            </Card>

            {!d.clusters.length ? (
              <Card>
                <EmptyState
                  art={<NoResultsArt />}
                  title="No gaps found"
                  description={`Every question in the last ${days} days found an answer in your sources. Unanswered questions, thumbs-downs and empty searches will collect here.`}
                />
              </Card>
            ) : (
              <ul className="flex flex-col gap-3">
                {d.clusters.map((c) => (
                  <GapCard key={c.id} gap={c} onOpenDocument={setPreview} />
                ))}
                {d.total_clusters > d.clusters.length ? (
                  <li className="px-1 text-[12.5px] text-ink-3">
                    Showing the {d.clusters.length} biggest of {d.total_clusters} gaps.
                  </li>
                ) : null}
              </ul>
            )}
          </div>
        )}
      </div>
      <DocumentSheet source={preview} onClose={() => setPreview(null)} />
    </div>
  );
}

function GapCard({ gap, onOpenDocument }: { gap: GapCluster; onOpenDocument: (s: SourceRef) => void }) {
  const { org } = useCurrentOrg();
  const [open, setOpen] = useState(false);
  const [requesting, setRequesting] = useState(false);
  const others = gap.examples.filter((e) => e !== gap.label);
  const reasons = REASONS.filter((r) => gap.reasons[r.key] > 0);
  // Every question came from a Slack channel that left out restricted collections the
  // asker can read: the answer may already be in one of them.
  const channelOnly = gap.reasons.restricted_in_channel === gap.count;

  return (
    <li>
      <Card className={cn("p-5", gap.addressed && "opacity-80")}>
        <div className="flex items-start gap-3">
          <IconGap size={18} className={cn("mt-0.5 shrink-0", gap.addressed ? "text-ok" : channelOnly ? "text-ink-3" : "text-accent")} />
          <div className="min-w-0 flex-1">
            <h3 className="text-[14.5px] leading-snug font-semibold">{gap.label}</h3>
            <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-ink-3">
              {gap.addressed ? (
                <Badge tone="ok">
                  <IconCheck size={12} /> Addressed
                </Badge>
              ) : null}
              <span className="font-medium text-ink-2">
                Asked {gap.count} time{gap.count === 1 ? "" : "s"}
              </span>
              <span>
                by {gap.askers || "no"} {gap.askers === 1 ? "person" : "people"}
              </span>
              <span>last {relativeTime(gap.last_seen)}</span>
              {gap.first_seen !== gap.last_seen ? <span>first {relativeTime(gap.first_seen)}</span> : null}
            </div>
            {reasons.length ? (
              <div className="mt-2.5 flex flex-wrap gap-1.5">
                {reasons.map((r) => (
                  <span key={r.key} title={r.hint}>
                    <Badge tone={r.tone}>
                      {r.label} · {gap.reasons[r.key]}
                    </Badge>
                  </span>
                ))}
              </div>
            ) : null}

            {gap.missing.length ? (
              <div className="mt-3.5">
                <div className="font-mono text-[10.5px] tracking-wider text-ink-3 uppercase">Missing from the sources</div>
                <ul className="mt-1.5 flex flex-col gap-1 text-[13px] text-ink-2">
                  {gap.missing.map((m) => (
                    <li key={m} className="flex gap-2">
                      <span className="mt-[7px] h-1 w-1 shrink-0 rounded-full bg-warn" />
                      <span>{m}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            {others.length ? (
              <div className="mt-3">
                <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="inline-flex items-center gap-1 text-[12.5px] text-ink-3 hover:text-ink">
                  Also asked as {others.length} other way{others.length === 1 ? "" : "s"}
                  <IconChevronDown size={14} className={cn("transition-transform", open && "rotate-180")} />
                </button>
                {open ? (
                  <ul className="mt-1.5 flex flex-col gap-1 border-l border-line pl-3 text-[13px] text-ink-2">
                    {others.map((e) => (
                      <li key={e}>“{e}”</li>
                    ))}
                  </ul>
                ) : null}
              </div>
            ) : null}

            {gap.answered_elsewhere.length ? (
              <div className="mt-4 flex flex-col gap-2">
                {gap.answered_elsewhere.map((h, i) => (
                  <ElsewhereHint key={`${h.org.slug}-${h.kind}-${i}`} hint={h} onOpenDocument={onOpenDocument} />
                ))}
              </div>
            ) : null}

            {gap.requests.length ? (
              <div className="mt-3.5 flex flex-wrap items-center gap-1.5 text-[12px] text-ink-3">
                <IconRequest size={14} className="text-ink-3" />
                {gap.requests.map((r) => (
                  <Badge key={r.id} tone={r.status === "fulfilled" ? "ok" : "outline"}>
                    {r.title} · {r.status === "fulfilled" ? "fulfilled" : "requested"}
                  </Badge>
                ))}
              </div>
            ) : null}

            {channelOnly ? (
              <p className="mt-4 flex gap-2 border-t border-line pt-3 text-[12.5px] leading-relaxed text-ink-3">
                <IconLock size={14} className="mt-0.5 shrink-0" />
                <span>
                  Asked only in Slack channels, which search org-wide collections. The askers can read restricted collections that weren&apos;t
                  searched, so the answer may already be there: in a DM, the bot searches them too.
                </span>
              </p>
            ) : (
              <div className="mt-4 flex flex-wrap items-center gap-1.5 border-t border-line pt-3">
                <Link href={`/o/${org.slug}/sources`}>
                  <Button size="sm" variant="secondary">
                    <IconSources size={14} /> Add a source
                  </Button>
                </Link>
                <Button size="sm" variant="ghost" onClick={() => setRequesting(true)}>
                  <IconPlus size={14} /> Request a document
                </Button>
              </div>
            )}
          </div>
        </div>
      </Card>
      <RequestDocumentDialog open={requesting} onOpenChange={setRequesting} question={gap.label} />
    </li>
  );
}

/** "Answered in org B": shown only when you're in B and can read what it cites. */
function ElsewhereHint({ hint, onOpenDocument }: { hint: GapHint; onOpenDocument: (s: SourceRef) => void }) {
  return (
    <div className="rounded-[11px] border border-line bg-surface-2/50 px-3.5 py-3">
      <div className="flex flex-wrap items-center gap-2 text-[12px] text-ink-3">
        <OrgMark name={hint.org.name} hue={hint.org.hue} size={16} />
        <span>
          {hint.kind === "verified" ? "Verified answer" : "Answered"} in <span className="font-medium text-ink-2">{hint.org.name}</span>
        </span>
        <span>· {relativeTime(hint.answered_at)}</span>
        <span className="font-mono text-[11px]" title="Similarity to this gap's questions">
          · {Math.round(hint.similarity * 100)}% similar
        </span>
      </div>
      <p className="mt-1.5 text-[13px] font-medium text-ink">“{hint.question}”</p>
      <p className="mt-1 line-clamp-3 text-[13px] leading-relaxed text-ink-2">{hint.answer}</p>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {hint.documents.map((doc) => (
          <button
            key={doc.id}
            type="button"
            onClick={() => onOpenDocument(docSource(doc, { slug: doc.org_slug, name: hint.org.name, hue: hint.org.hue }))}
            className="inline-flex max-w-full items-center gap-1.5 rounded-[8px] border border-line bg-surface px-2 py-1 text-[12px] text-ink-2 transition-colors hover:border-line-strong hover:text-ink"
          >
            <IconFile size={13} className="shrink-0 text-ink-3" />
            <span className="truncate">{doc.title}</span>
            {doc.collection_name ? <span className="shrink-0 text-ink-3">· {doc.collection_name}</span> : null}
          </button>
        ))}
        {hint.verified_answer_id ? (
          <Link
            href={`/o/${hint.org.slug}/verified?focus=${hint.verified_answer_id}`}
            className="inline-flex items-center gap-1.5 rounded-[8px] border border-line bg-surface px-2 py-1 text-[12px] text-ink-2 transition-colors hover:border-line-strong hover:text-ink"
          >
            <IconVerified size={13} className="text-accent" /> Open the verified answer
          </Link>
        ) : null}
      </div>
    </div>
  );
}

/** The admins' queue: members asking for documents the knowledge base lacks. */
function DocRequestsPanel({ onOpenDocument }: { onOpenDocument: (s: SourceRef) => void }) {
  const { org } = useCurrentOrg();
  const [status, setStatus] = useState<DocRequestStatus>("pending");
  const requests = useDocRequests(org.slug, status);
  const [fulfilling, setFulfilling] = useState<DocRequest | null>(null);
  const [dismissing, setDismissing] = useState<DocRequest | null>(null);
  const [verifying, setVerifying] = useState<DocRequest | null>(null);

  return (
    <Card className="mb-4 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-[14px] font-semibold">Document requests</h2>
          <p className="mt-0.5 text-[12.5px] text-ink-3">People asking for a document the knowledge base doesn't have. Fulfill with the document that answers it.</p>
        </div>
        <Segmented
          size="sm"
          value={status}
          onChange={setStatus}
          options={[
            { value: "pending", label: "Pending" },
            { value: "fulfilled", label: "Fulfilled" },
            { value: "dismissed", label: "Dismissed" },
          ]}
        />
      </div>
      {requests.isLoading ? (
        <div className="mt-3 flex flex-col gap-2">
          <Skeleton className="h-10" />
          <Skeleton className="h-10" />
        </div>
      ) : !requests.data?.length ? (
        <p className="mt-3 text-[13px] text-ink-3">
          {status === "pending" ? "No open requests." : status === "fulfilled" ? "Nothing fulfilled yet." : "Nothing dismissed."}
        </p>
      ) : (
        <ul className="mt-3 divide-y divide-line">
          {requests.data.map((r) => (
            <li key={r.id} className="flex flex-wrap items-center gap-3 py-2.5 text-[13px]">
              <Avatar name={r.requester.name || r.requester.email} hue={r.requester.avatar_hue} size={24} />
              <div className="min-w-0 flex-1">
                <p className="break-words">
                  <span className="font-medium">{r.requester.name || r.requester.email}</span> asked for <span className="font-medium">{r.title}</span>
                </p>
                <p className="truncate text-[12px] text-ink-3">
                  {relativeTime(r.created_at)}
                  {r.question ? ` · asked “${r.question}”` : ""}
                  {r.note ? ` · “${r.note}”` : ""}
                </p>
                {r.status === "fulfilled" ? (
                  <p className="mt-0.5 truncate text-[12px] text-ink-3">
                    {r.document ? (
                      <>
                        Answered by{" "}
                        <button
                          type="button"
                          className="font-medium text-ink-2 underline decoration-line-strong underline-offset-2 hover:text-ink"
                          onClick={() => onOpenDocument(docSource(r.document!, org))}
                        >
                          {r.document.title}
                        </button>
                      </>
                    ) : (
                      "The document that answered it was deleted"
                    )}
                    {r.decided_by ? ` · by ${r.decided_by.name || r.decided_by.email}` : ""} {relativeTime(r.decided_at)}
                  </p>
                ) : r.status === "dismissed" ? (
                  <p className="mt-0.5 truncate text-[12px] text-ink-3">
                    Dismissed {relativeTime(r.decided_at)}
                    {r.resolution ? ` · “${r.resolution}”` : ""}
                  </p>
                ) : null}
              </div>
              {r.status === "pending" ? (
                <div className="flex gap-1.5">
                  <Button size="sm" variant="ghost" onClick={() => setDismissing(r)}>
                    <IconX size={14} /> Dismiss
                  </Button>
                  <Button size="sm" variant="primary" onClick={() => setFulfilling(r)}>
                    <IconCheck size={14} /> Fulfill
                  </Button>
                </div>
              ) : r.status === "fulfilled" ? (
                <div className="flex gap-1.5">
                  <Link href={`/o/${org.slug}/chat?q=${encodeURIComponent(r.question ?? r.title)}`}>
                    <Button size="sm" variant="ghost">
                      <IconChat size={14} /> Ask in chat
                    </Button>
                  </Link>
                  <Button size="sm" variant="secondary" onClick={() => setVerifying(r)}>
                    <IconVerified size={14} /> Verify an answer
                  </Button>
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      <FulfillDialog request={fulfilling} onOpenChange={(open) => !open && setFulfilling(null)} />
      <DismissDialog request={dismissing} onClose={() => setDismissing(null)} />
      <VerifyDialog
        open={!!verifying}
        onOpenChange={(open) => !open && setVerifying(null)}
        question={verifying?.question ?? verifying?.title}
        sourceCollections={verifying?.document ? [verifying.document.collection_name] : []}
      />
    </Card>
  );
}

function DismissDialog({ request, onClose }: { request: DocRequest | null; onClose: () => void }) {
  const { org } = useCurrentOrg();
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const dismiss = useMutation({
    mutationFn: () => api.dismissDocRequest(org.slug, request!.id, note.trim() || undefined),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.docRequests(org.slug) });
      qc.invalidateQueries({ queryKey: qk.gaps(org.slug) });
      toast("Request dismissed");
      setNote("");
      onClose();
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Couldn't dismiss the request"),
  });
  return (
    <Dialog
      open={!!request}
      onOpenChange={(open) => !open && onClose()}
      title="Dismiss request"
      description={request ? <>Close “{request.title}” without a document. The requester sees it was dismissed, with your note.</> : undefined}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" loading={dismiss.isPending} onClick={() => dismiss.mutate()}>
            Dismiss
          </Button>
        </>
      }
    >
      <Field label="Note to the requester" htmlFor="dm-note" hint="Optional: why, or where to look instead.">
        <Textarea id="dm-note" rows={3} maxLength={2000} value={note} onChange={(e) => setNote(e.target.value)} />
      </Field>
    </Dialog>
  );
}
