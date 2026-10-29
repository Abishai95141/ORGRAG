"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { IconCopy, IconLink, IconMail, IconTrash } from "@/components/icons";
import { Avatar } from "@/components/illustrations";
import { useCurrentOrg } from "@/components/layout/org-context";
import { Badge, Button, Card, Input, PageHeader, Select, Skeleton, Tooltip } from "@/components/ui";
import { confirmAction } from "@/components/ui/confirm";
import { api, ApiError } from "@/lib/api";
import { qk, useInvites, useMembers } from "@/lib/queries";
import type { Role } from "@/lib/types";
import { atLeast, relativeTime } from "@/lib/utils";

const ROLES: { value: Role; label: string; hint: string }[] = [
  { value: "owner", label: "Owner", hint: "Everything, including deleting the org" },
  { value: "admin", label: "Admin", hint: "Manage members, sources, agents, settings" },
  { value: "member", label: "Member", hint: "Ask, search and upload files" },
  { value: "viewer", label: "Viewer", hint: "Ask and search only" },
];

const inviteUrl = (token: string) => (typeof window === "undefined" ? `/invite/${token}` : `${window.location.origin}/invite/${token}`);

export default function MembersPage() {
  const { org, user } = useCurrentOrg();
  const qc = useQueryClient();
  const isAdmin = atLeast(org.role, "admin");
  const members = useMembers(org.slug);
  const invites = useInvites(org.slug, isAdmin);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("member");

  const invite = useMutation({
    mutationFn: () => api.createInvite(org.slug, email.trim(), role),
    onSuccess: async (inv) => {
      setEmail("");
      qc.invalidateQueries({ queryKey: qk.invites(org.slug) });
      await navigator.clipboard.writeText(inviteUrl(inv.token)).catch(() => {});
      toast.success("Invite link copied", { description: `Send it to ${inv.email}. It expires ${relativeTime(inv.expires_at)}.` });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Could not invite"),
  });

  const changeRole = useMutation({
    mutationFn: ({ id, role }: { id: string; role: Role }) => api.updateMember(org.slug, id, role),
    onSuccess: () => (qc.invalidateQueries({ queryKey: qk.members(org.slug) }), toast("Role updated")),
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Could not change role"),
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.removeMember(org.slug, id),
    onSuccess: (_, id) => {
      if (id === user.id) window.location.href = "/";
      qc.invalidateQueries({ queryKey: qk.members(org.slug) });
      toast("Member removed");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Could not remove"),
  });

  const revoke = useMutation({
    mutationFn: (id: string) => api.revokeInvite(org.slug, id),
    onSuccess: () => qc.invalidateQueries({ queryKey: qk.invites(org.slug) }),
  });

  const pending = (invites.data ?? []).filter((i) => !i.accepted_at);

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl px-5 py-10 sm:px-8">
        <PageHeader eyebrow={org.name} title="Members" description="People in this organization and what they can do. Membership is what lets someone include this org in a cross-organization question." />

        {isAdmin ? (
          <Card className="mb-6 p-4">
            <form
              onSubmit={(e) => {
                e.preventDefault();
                if (email.trim()) invite.mutate();
              }}
              className="flex flex-col gap-2 sm:flex-row"
            >
              <div className="relative flex-1">
                <IconMail size={15} className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-ink-3" />
                <Input type="email" required placeholder="colleague@company.com" value={email} onChange={(e) => setEmail(e.target.value)} className="pl-9" />
              </div>
              <Select value={role} onValueChange={setRole} options={ROLES.filter((r) => r.value !== "owner" || org.role === "owner")} className="sm:w-36" />
              <Button type="submit" variant="primary" loading={invite.isPending}>
                <IconLink size={15} /> Create invite link
              </Button>
            </form>
          </Card>
        ) : null}

        <Card className="overflow-hidden">
          {members.isLoading ? (
            <div className="flex flex-col gap-3 p-4">
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} className="h-9" />
              ))}
            </div>
          ) : (
            <ul className="divide-y divide-line">
              {(members.data ?? []).map((m) => {
                const self = m.user.id === user.id;
                return (
                  <li key={m.user.id} className="flex items-center gap-3 px-4 py-3">
                    <Avatar name={m.user.name} hue={m.user.avatar_hue} size={32} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 truncate text-[13.5px] font-medium">
                        {m.user.name}
                        {self ? <Badge>You</Badge> : null}
                      </div>
                      <div className="truncate text-[12px] text-ink-3">
                        {m.user.email} · joined {relativeTime(m.joined_at)}
                      </div>
                    </div>
                    {isAdmin && !(m.role === "owner" && org.role !== "owner") ? (
                      <Select
                        value={m.role}
                        onValueChange={(r) => changeRole.mutate({ id: m.user.id, role: r })}
                        options={ROLES.filter((r) => r.value !== "owner" || org.role === "owner")}
                        className="h-8 w-28 text-[13px]"
                      />
                    ) : (
                      <span className="w-28 text-right text-[13px] text-ink-2 capitalize">{m.role}</span>
                    )}
                    {isAdmin || self ? (
                      <Tooltip label={self ? "Leave organization" : "Remove"}>
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          aria-label={self ? "Leave organization" : `Remove ${m.user.name}`}
                          onClick={async () =>
                            (await confirmAction({
                              title: self ? `Leave ${org.name}?` : `Remove ${m.user.name}?`,
                              body: self ? "You'll lose access to its sources and conversations." : "They'll lose access immediately.",
                              confirmLabel: self ? "Leave" : "Remove",
                              danger: true,
                            })) && remove.mutate(m.user.id)
                          }
                        >
                          <IconTrash size={15} />
                        </Button>
                      </Tooltip>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          )}
        </Card>

        {isAdmin && pending.length ? (
          <>
            <h2 className="mt-10 mb-3 text-[15px] font-semibold">
              Pending invites <span className="font-mono text-[12px] font-normal text-ink-3">{pending.length}</span>
            </h2>
            <Card className="divide-y divide-line overflow-hidden">
              {pending.map((i) => (
                <div key={i.id} className="flex items-center gap-3 px-4 py-2.5">
                  <span className="grid h-8 w-8 place-items-center rounded-full border border-dashed border-line-strong text-ink-3">
                    <IconMail size={14} />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[13px] font-medium">{i.email}</div>
                    <div className="text-[11.5px] text-ink-3">
                      {i.role} · expires {relativeTime(i.expires_at)}
                    </div>
                  </div>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={async () => {
                      await navigator.clipboard.writeText(inviteUrl(i.token)).catch(() => {});
                      toast("Invite link copied");
                    }}
                  >
                    <IconCopy size={14} /> Copy link
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => revoke.mutate(i.id)}>
                    Revoke
                  </Button>
                </div>
              ))}
            </Card>
          </>
        ) : null}
      </div>
    </div>
  );
}
