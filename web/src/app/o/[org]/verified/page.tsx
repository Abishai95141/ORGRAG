"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { toast } from "sonner";
import { safeComponents } from "@/components/chat/markdown";
import { IconChat, IconChevronDown, IconMore, IconPencil, IconRefresh, IconSearch, IconTrash, IconVerified } from "@/components/icons";
import { CompassArt } from "@/components/illustrations";
import { useCurrentOrg } from "@/components/layout/org-context";
import { Badge, Button, Card, EmptyState, Input, Menu, MenuContent, MenuItem, MenuTrigger, PageHeader, Segmented, Skeleton } from "@/components/ui";
import { confirmAction } from "@/components/ui/confirm";
import { VerifyDialog } from "@/components/verified/verify-dialog";
import { api } from "@/lib/api";
import { qk } from "@/lib/queries";
import type { VerifiedAnswer, VerifiedStatus } from "@/lib/types";
import { atLeast, cn, relativeTime } from "@/lib/utils";

type Filter = VerifiedStatus | "all";

const STATUS: Record<VerifiedStatus, { label: string; tone: "ok" | "warn" | "err" }> = {
  active: { label: "Verified", tone: "ok" },
  expiring: { label: "Expiring soon", tone: "warn" },
  expired: { label: "Expired", tone: "err" },
};

const dateFmt = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" });

export default function VerifiedAnswersPage() {
  const { org } = useCurrentOrg();
  const focus = useSearchParams().get("focus");
  const [filter, setFilter] = useState<Filter>("all");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<VerifiedAnswer | null>(null);
  const isAdmin = atLeast(org.role, "admin");

  useEffect(() => {
    const t = setTimeout(() => setQuery(q.trim()), 250);
    return () => clearTimeout(t);
  }, [q]);

  const list = useQuery({
    queryKey: [...qk.verified(org.slug), filter, query],
    queryFn: () => api.verifiedAnswers(org.slug, { status: filter, q: query }),
  });

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl px-5 py-10 sm:px-8">
        <PageHeader
          eyebrow={org.name}
          title="Verified answers"
          description="Answers your admins reviewed and approved. They're found and cited ahead of documents until they expire; then they leave search until someone re-verifies them."
        />

        <div className="mb-4 flex flex-wrap items-center gap-3">
          <Segmented
            size="sm"
            value={filter}
            onChange={setFilter}
            options={[
              { value: "all", label: "All" },
              { value: "active", label: "Verified" },
              { value: "expiring", label: "Expiring" },
              { value: "expired", label: "Expired" },
            ]}
          />
          <div className="relative min-w-48 flex-1">
            <IconSearch size={15} className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-ink-3" />
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search questions and answers" className="pl-9" aria-label="Search verified answers" />
          </div>
        </div>

        {list.isLoading ? (
          <div className="flex flex-col gap-3">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-24 rounded-[14px]" />
            ))}
          </div>
        ) : !list.data?.length ? (
          <Card>
            <EmptyState
              art={<CompassArt />}
              title={query || filter !== "all" ? "Nothing matches" : "No verified answers yet"}
              description={
                query || filter !== "all"
                  ? "Try another search or filter."
                  : isAdmin
                    ? "When an answer in chat is right and worth reusing, use the shield under it to verify it."
                    : "Admins can verify good answers from chat; they'll appear here."
              }
              action={
                isAdmin && !query && filter === "all" ? (
                  <Link href={`/o/${org.slug}/chat`}>
                    <Button variant="primary">
                      <IconChat size={16} /> Go to chat
                    </Button>
                  </Link>
                ) : null
              }
            />
          </Card>
        ) : (
          <ul className="flex flex-col gap-3">
            {list.data.map((v) => (
              <VerifiedCard key={v.id} value={v} isAdmin={isAdmin} initiallyOpen={v.id === focus} onEdit={() => setEditing(v)} />
            ))}
          </ul>
        )}
      </div>
      <VerifyDialog open={!!editing} onOpenChange={(open) => !open && setEditing(null)} existing={editing ?? undefined} />
    </div>
  );
}

function VerifiedCard({ value: v, isAdmin, initiallyOpen, onEdit }: { value: VerifiedAnswer; isAdmin: boolean; initiallyOpen: boolean; onEdit: () => void }) {
  const { org } = useCurrentOrg();
  const qc = useQueryClient();
  const [open, setOpen] = useState(initiallyOpen);
  const status = STATUS[v.status];
  const refresh = () => qc.invalidateQueries({ queryKey: qk.verified(org.slug) });

  const reverify = useMutation({
    mutationFn: () => api.reverify(org.slug, v.id, 90),
    onSuccess: () => (refresh(), toast("Re-verified for 90 days")),
    onError: (e) => toast.error(e instanceof Error ? e.message : "Couldn't re-verify"),
  });
  const remove = useMutation({
    mutationFn: () => api.deleteVerified(org.slug, v.id),
    onSuccess: () => (refresh(), toast("Verified answer deleted")),
  });

  return (
    <li>
      <Card className={cn("p-5", v.status === "expired" && "opacity-80")}>
        <div className="flex items-start gap-3">
          <IconVerified size={18} className={cn("mt-0.5 shrink-0", v.status === "expired" ? "text-ink-3" : "text-accent")} />
          <div className="min-w-0 flex-1">
            <button type="button" className="group flex w-full items-start gap-2 text-left" onClick={() => setOpen(!open)} aria-expanded={open}>
              <h3 className="min-w-0 flex-1 text-[14.5px] leading-snug font-semibold group-hover:underline">{v.question}</h3>
              <IconChevronDown size={16} className={cn("mt-0.5 shrink-0 text-ink-3 transition-transform", open && "rotate-180")} />
            </button>
            <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-ink-3">
              <Badge tone={status.tone}>{status.label}</Badge>
              <span>{v.collection_name}</span>
              {v.owner ? <span>Owner: {v.owner.name || v.owner.email}</span> : null}
              <span>
                Verified by {v.verified_by?.name || v.verified_by?.email || "a former member"} {relativeTime(v.verified_at)}
              </span>
              <span>{v.expires_at ? `${v.status === "expired" ? "Expired" : "Valid until"} ${dateFmt.format(new Date(v.expires_at))}` : "Doesn't expire"}</span>
            </div>
            {open ? (
              <div className="prose-answer mt-3 border-t border-line pt-3 text-[14px] leading-relaxed">
                <ReactMarkdown remarkPlugins={[remarkGfm]} components={safeComponents}>
                  {v.answer.replace(/\s*\[\d{1,3}\]/g, "")}
                </ReactMarkdown>
                {v.sources.length ? (
                  <p className="mt-3 text-[12px] text-ink-3">Based on: {v.sources.map((s) => s.title).join(" · ")}</p>
                ) : null}
              </div>
            ) : (
              <p className="mt-2 line-clamp-2 text-[13px] leading-relaxed text-ink-2">{v.answer.replace(/\s*\[\d{1,3}\]/g, "").replace(/[#*_`>|-]+/g, " ")}</p>
            )}
          </div>
          {isAdmin ? (
            <Menu>
              <MenuTrigger asChild>
                <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${v.question}`}>
                  <IconMore size={16} />
                </Button>
              </MenuTrigger>
              <MenuContent align="end">
                <MenuItem icon={<IconRefresh size={15} />} onSelect={() => reverify.mutate()}>
                  Re-verify for 90 days
                </MenuItem>
                <MenuItem icon={<IconPencil size={15} />} onSelect={onEdit}>
                  Edit
                </MenuItem>
                <MenuItem
                  danger
                  icon={<IconTrash size={15} />}
                  onSelect={async () =>
                    (await confirmAction({
                      title: "Delete this verified answer?",
                      body: "It leaves search and answers. The original conversation isn't affected.",
                      confirmLabel: "Delete",
                      danger: true,
                    })) && remove.mutate()
                  }
                >
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
