"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Answer } from "@/components/chat/answer";
import { DocumentSheet } from "@/components/chat/document-sheet";
import { SourceStrip } from "@/components/chat/messages";
import { IconArrowRight, IconCheck, IconLock } from "@/components/icons";
import { OrgMark, StrataMark, Wordmark } from "@/components/illustrations";
import { OrgContext } from "@/components/layout/org-context";
import { Button, Card, Skeleton } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { useMe } from "@/lib/queries";
import type { MissingAccess, SharedAnswer, SourceRef } from "@/lib/types";
import { relativeTime } from "@/lib/utils";

/**
 * An answer someone shared by link. What it shows is decided per viewer by the API:
 * the answer only if you can read every source it cites; otherwise the question and
 * the collections you'd need, which you can ask an admin for.
 */
export default function SharedAnswerPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = use(params);
  const router = useRouter();
  const me = useMe();
  const shared = useQuery({ queryKey: ["shared", token], queryFn: () => api.shared(token), retry: false });
  const [open, setOpen] = useState<SourceRef | null>(null);

  const unauthenticated = shared.error instanceof ApiError && shared.error.status === 401;
  useEffect(() => {
    if (unauthenticated) router.replace(`/login?next=${encodeURIComponent(`/a/${token}`)}`);
  }, [unauthenticated, router, token]);

  const data = shared.data;
  const org = data && me.data?.orgs.find((o) => o.slug === data.org.slug);

  return (
    <main className="min-h-screen">
      <header className="flex h-14 items-center justify-between border-b border-line px-5 sm:px-8">
        <Link href="/" aria-label="Strata home">
          <Wordmark />
        </Link>
        {org ? (
          <Link href={`/o/${org.slug}/chat`} className="inline-flex items-center gap-1.5 text-[13px] text-ink-2 hover:text-ink">
            Ask {org.name} <IconArrowRight size={14} />
          </Link>
        ) : null}
      </header>
      <div className="mx-auto max-w-[760px] px-5 py-10">
        {shared.isLoading || unauthenticated ? (
          <div className="flex flex-col gap-4">
            <Skeleton className="h-6 w-2/3" />
            <Skeleton className="h-40 w-full" />
          </div>
        ) : !data ? (
          <NotFound message={shared.error instanceof ApiError ? shared.error.message : "This link couldn't be opened."} />
        ) : (
          <>
            <p className="flex items-center gap-2 text-[12.5px] text-ink-3">
              <OrgMark name={data.org.name} hue={data.org.hue} size={18} />
              <span>
                {data.org.name}
                {data.author_name ? ` · shared by ${data.author_name}` : ""} · {relativeTime(data.shared_at)}
              </span>
            </p>
            {data.question ? <h1 className="mt-3 font-display text-[30px] leading-[1.15] tracking-tight">{data.question}</h1> : null}

            {data.access === "full" && data.answer && org && me.data ? (
              <OrgContext.Provider value={{ org, orgs: me.data.orgs, user: me.data.user }}>
                <article className="mt-6">
                  <header className="mb-2 flex items-center gap-2 text-[12.5px] text-ink-3">
                    <StrataMark size={16} className="text-ink" />
                    <span className="font-medium text-ink-2">Strata</span>
                    {data.answered_at ? <span>· answered {relativeTime(data.answered_at)}</span> : null}
                  </header>
                  <Answer content={data.answer} sources={data.sources} streaming={false} onOpenSource={setOpen} />
                  <SourceStrip sources={cited(data)} onOpenSource={setOpen} />
                </article>
                <DocumentSheet source={open} onClose={() => setOpen(null)} />
              </OrgContext.Provider>
            ) : (
              <Restricted data={data} />
            )}
          </>
        )}
      </div>
    </main>
  );
}

function cited(data: SharedAnswer) {
  const refs = new Set<number>();
  for (const m of (data.answer ?? "").matchAll(/\[(\d{1,3})\]/g)) refs.add(Number(m[1]));
  return data.sources.filter((s) => refs.has(s.ref));
}

function NotFound({ message }: { message: string }) {
  return (
    <div>
      <h1 className="font-display text-[34px] leading-none tracking-tight">Link not available</h1>
      <p className="mt-3 text-[14px] text-ink-3">{message}. Ask whoever shared it for a new link.</p>
    </div>
  );
}

function Restricted({ data }: { data: SharedAnswer }) {
  if (data.access === "none") {
    return (
      <Card className="mt-6 p-6">
        <div className="flex items-start gap-3">
          <IconLock size={18} className="mt-0.5 shrink-0 text-ink-3" />
          <div>
            <h2 className="text-[15px] font-semibold">This answer belongs to {data.org.name}</h2>
            <p className="mt-1 text-[13.5px] leading-relaxed text-ink-3">
              You need to be a member of {data.org.name} to see it. Ask whoever shared the link to invite you.
            </p>
          </div>
        </div>
      </Card>
    );
  }
  return (
    <Card className="mt-6 p-6">
      <div className="flex items-start gap-3">
        <IconLock size={18} className="mt-0.5 shrink-0 text-ink-3" />
        <div className="min-w-0 flex-1">
          <h2 className="text-[15px] font-semibold">This answer uses sources you can&apos;t read</h2>
          <p className="mt-1 text-[13.5px] leading-relaxed text-ink-3">
            The answer is only shown to people who can read everything it cites. You&apos;d need:
          </p>
          <ul className="mt-4 flex flex-col divide-y divide-line rounded-[11px] border border-line">
            {data.missing.map((m, i) => (
              <MissingRow key={i} m={m} />
            ))}
          </ul>
        </div>
      </div>
    </Card>
  );
}

function MissingRow({ m }: { m: MissingAccess }) {
  const qc = useQueryClient();
  const request = useMutation({
    mutationFn: () => api.requestAccess(m.org_slug!, m.collection_id!),
    onSuccess: () => (qc.invalidateQueries({ queryKey: ["shared"] }), toast("Request sent to the admins")),
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Couldn't send the request"),
  });
  const label =
    m.kind === "collection"
      ? `${m.collection_name ?? "A collection"} · ${m.org_name}`
      : m.kind === "org"
        ? `Membership in ${m.org_name}`
        : m.kind === "attachment"
          ? "A file attached to the original conversation"
          : `A document an admin hid in ${m.collection_name ?? m.org_name}`;
  return (
    <li className="flex items-center gap-3 px-3.5 py-2.5 text-[13px]">
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {m.kind === "collection" && m.org_slug && m.collection_id ? (
        m.requested || request.isSuccess ? (
          <span className="inline-flex items-center gap-1 text-[12px] text-ink-3">
            <IconCheck size={13} /> Requested
          </span>
        ) : (
          <Button size="sm" loading={request.isPending} onClick={() => request.mutate()}>
            Request access
          </Button>
        )
      ) : null}
    </li>
  );
}
