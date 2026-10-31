"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { IconCheck, IconLock, IconMore, IconPencil, IconPeople, IconPlus, IconTrash, IconX } from "@/components/icons";
import { Avatar, FanArt, Glyph, GLYPHS, GlyphTile } from "@/components/illustrations";
import { useCurrentOrg } from "@/components/layout/org-context";
import { Badge, Button, Card, Dialog, EmptyState, Field, Input, Menu, MenuContent, MenuItem, MenuTrigger, PageHeader, Segmented, Skeleton, Textarea } from "@/components/ui";
import { confirmAction } from "@/components/ui/confirm";
import { api, ApiError } from "@/lib/api";
import { qk, useCollections, useMembers } from "@/lib/queries";
import type { AccessRequest, Collection } from "@/lib/types";
import { atLeast, cn, formatNumber, relativeTime } from "@/lib/utils";

export default function CollectionsPage() {
  const { org } = useCurrentOrg();
  const qc = useQueryClient();
  const collections = useCollections(org.slug);
  const [editing, setEditing] = useState<Collection | "new" | null>(null);
  const isAdmin = atLeast(org.role, "admin");

  const remove = useMutation({
    mutationFn: (id: string) => api.deleteCollection(org.slug, id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.collections(org.slug) });
      qc.invalidateQueries({ queryKey: qk.sources(org.slug) });
      toast("Collection deleted");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Could not delete"),
  });

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl px-5 py-10 sm:px-8">
        <PageHeader
          eyebrow={org.name}
          title="Collections"
          description="Group sources by topic and decide who can see them. Restricted collections are invisible to search for anyone not listed — including the agent acting on their behalf."
          actions={
            isAdmin ? (
              <Button variant="primary" onClick={() => setEditing("new")}>
                <IconPlus size={16} /> New collection
              </Button>
            ) : null
          }
        />

        {isAdmin ? <AccessRequests /> : null}

        {collections.isLoading ? (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-40 rounded-[14px]" />
            ))}
          </div>
        ) : !collections.data?.length ? (
          <Card>
            <EmptyState art={<FanArt />} title="No collections yet" description="Collections hold sources and control who can search them." />
          </Card>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {collections.data.map((c) => (
              <Card key={c.id} className="group flex flex-col p-4 transition-colors hover:border-line-strong">
                <div className="flex items-start justify-between gap-2">
                  <GlyphTile name={c.glyph} hue={org.hue} size={40} />
                  <div className="flex items-center gap-1">
                    {c.visibility === "restricted" ? (
                      <Badge tone="warn">
                        <IconLock size={12} /> Restricted
                      </Badge>
                    ) : (
                      <Badge>
                        <IconPeople size={12} /> Everyone
                      </Badge>
                    )}
                    {isAdmin ? (
                      <Menu>
                        <MenuTrigger asChild>
                          <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${c.name}`}>
                            <IconMore size={16} />
                          </Button>
                        </MenuTrigger>
                        <MenuContent align="end">
                          <MenuItem icon={<IconPencil size={15} />} onSelect={() => setEditing(c)}>
                            Edit
                          </MenuItem>
                          <MenuItem
                            danger
                            icon={<IconTrash size={15} />}
                            onSelect={async () =>
                              (await confirmAction({
                                title: `Delete ${c.name}?`,
                                body: `This removes its ${c.source_count} sources and ${c.document_count} documents.`,
                                confirmLabel: "Delete collection",
                                danger: true,
                              })) && remove.mutate(c.id)
                            }
                          >
                            Delete
                          </MenuItem>
                        </MenuContent>
                      </Menu>
                    ) : null}
                  </div>
                </div>
                <h3 className="mt-4 text-[15px] font-semibold">{c.name}</h3>
                <p className="mt-1 line-clamp-2 min-h-[2.6em] text-[13px] leading-relaxed text-ink-3">{c.description || "No description."}</p>
                <div className="mt-4 flex gap-4 border-t border-line pt-3 font-mono text-[11px] text-ink-3">
                  <span>{formatNumber(c.document_count)} docs</span>
                  <span>{c.source_count} sources</span>
                  {c.visibility === "restricted" ? <span>{c.allowed_user_ids.length} people</span> : null}
                </div>
              </Card>
            ))}
          </div>
        )}
      </div>
      <CollectionDialog key={editing === "new" ? "new" : (editing?.id ?? "none")} value={editing} onClose={() => setEditing(null)} />
    </div>
  );
}

function CollectionDialog({ value, onClose }: { value: Collection | "new" | null; onClose: () => void }) {
  const { org } = useCurrentOrg();
  const qc = useQueryClient();
  const members = useMembers(org.slug);
  const editing = value && value !== "new" ? value : null;
  const [form, setForm] = useState({
    name: editing?.name ?? "",
    description: editing?.description ?? "",
    glyph: (editing?.glyph as string) ?? "stack",
    visibility: editing?.visibility ?? ("org" as "org" | "restricted"),
    allowed: new Set(editing?.allowed_user_ids ?? []),
  });
  const [error, setError] = useState<string | null>(null);
  useEffect(() => setError(null), [value]);

  const save = useMutation({
    mutationFn: () => {
      const body = {
        name: form.name.trim(),
        description: form.description.trim() || null,
        glyph: form.glyph,
        visibility: form.visibility,
        allowed_user_ids: form.visibility === "restricted" ? [...form.allowed] : [],
      };
      return editing ? api.updateCollection(org.slug, editing.id, body) : api.createCollection(org.slug, body);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.collections(org.slug) });
      toast.success(editing ? "Collection updated" : "Collection created");
      onClose();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Could not save"),
  });

  const nonAdmins = (members.data ?? []).filter((m) => !atLeast(m.role, "admin"));

  return (
    <Dialog
      open={!!value}
      onOpenChange={(v) => !v && onClose()}
      title={editing ? `Edit ${editing.name}` : "New collection"}
      width={520}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" onClick={() => save.mutate()} loading={save.isPending} disabled={!form.name.trim()}>
            {editing ? "Save changes" : "Create collection"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <Field label="Name" htmlFor="col-name" error={error}>
          <Input id="col-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="Security policies" autoFocus />
        </Field>
        <Field label="Description" htmlFor="col-desc" hint="The agent reads this to decide where to look.">
          <Textarea id="col-desc" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} className="min-h-16" />
        </Field>
        <fieldset>
          <legend className="mb-2 text-[13px] font-medium text-ink-2">Glyph</legend>
          <div className="flex flex-wrap gap-1.5">
            {GLYPHS.map((g) => (
              <button
                key={g}
                type="button"
                aria-label={g}
                aria-pressed={form.glyph === g}
                onClick={() => setForm({ ...form, glyph: g })}
                className={cn(
                  "grid h-10 w-10 place-items-center rounded-[10px] border transition-colors",
                  form.glyph === g ? "border-ink bg-surface-2 text-ink" : "border-line text-ink-3 hover:border-line-strong hover:text-ink-2",
                )}
              >
                <Glyph name={g} size={20} />
              </button>
            ))}
          </div>
        </fieldset>
        <div>
          <div className="mb-2 text-[13px] font-medium text-ink-2">Who can search it</div>
          <Segmented
            value={form.visibility}
            onChange={(v) => setForm({ ...form, visibility: v })}
            options={[
              { value: "org", label: `Everyone in ${org.name}`, icon: <IconPeople size={14} /> },
              { value: "restricted", label: "Selected people", icon: <IconLock size={14} /> },
            ]}
          />
        </div>
        {form.visibility === "restricted" ? (
          <div className="rounded-[12px] border border-line">
            <div className="border-b border-line px-3 py-2 text-[12px] text-ink-3">Admins and owners always have access.</div>
            <ul className="max-h-52 overflow-y-auto p-1">
              {nonAdmins.length ? (
                nonAdmins.map((m) => {
                  const on = form.allowed.has(m.user.id);
                  return (
                    <li key={m.user.id}>
                      <button
                        type="button"
                        onClick={() => {
                          const next = new Set(form.allowed);
                          if (on) next.delete(m.user.id);
                          else next.add(m.user.id);
                          setForm({ ...form, allowed: next });
                        }}
                        className="flex w-full items-center gap-2.5 rounded-[8px] px-2 py-1.5 text-left hover:bg-surface-2"
                      >
                        <Avatar name={m.user.name} hue={m.user.avatar_hue} size={24} />
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-[13px] font-medium">{m.user.name}</span>
                          <span className="block truncate text-[11.5px] text-ink-3">{m.user.email}</span>
                        </span>
                        <span className={cn("grid h-4 w-4 place-items-center rounded-[5px] border", on ? "border-ink bg-ink text-bg" : "border-line-strong")}>
                          {on ? <IconCheck size={12} strokeWidth={2} /> : null}
                        </span>
                      </button>
                    </li>
                  );
                })
              ) : (
                <li className="px-2 py-3 text-[12.5px] text-ink-3">Everyone here is already an admin.</li>
              )}
            </ul>
          </div>
        ) : null}
      </div>
    </Dialog>
  );
}

/** Members asking to read restricted collections (from shared answers they couldn't open). */
function AccessRequests() {
  const { org } = useCurrentOrg();
  const qc = useQueryClient();
  const requests = useQuery({ queryKey: qk.accessRequests(org.slug), queryFn: () => api.accessRequests(org.slug) });
  const decide = useMutation({
    mutationFn: ({ r, approve }: { r: AccessRequest; approve: boolean }) =>
      approve ? api.approveAccess(org.slug, r.id) : api.dismissAccess(org.slug, r.id),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: qk.accessRequests(org.slug) });
      qc.invalidateQueries({ queryKey: qk.collections(org.slug) });
      toast(r.status === "approved" ? `${r.user.name || r.user.email} can now read ${r.collection_name}` : "Request dismissed");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Couldn't update the request"),
  });
  if (!requests.data?.length) return null;
  return (
    <Card className="mb-4 p-5">
      <h2 className="text-[14px] font-semibold">Access requests</h2>
      <p className="mt-0.5 text-[12.5px] text-ink-3">People asking to read a restricted collection. Approving adds them to it.</p>
      <ul className="mt-3 divide-y divide-line">
        {requests.data.map((r) => (
          <li key={r.id} className="flex flex-wrap items-center gap-3 py-2.5 text-[13px]">
            <Avatar name={r.user.name || r.user.email} hue={r.user.avatar_hue} size={24} />
            <div className="min-w-0 flex-1">
              <p className="truncate">
                <span className="font-medium">{r.user.name || r.user.email}</span> wants to read{" "}
                <span className="font-medium">{r.collection_name}</span>
              </p>
              <p className="truncate text-[12px] text-ink-3">
                {relativeTime(r.created_at)}
                {r.note ? ` · “${r.note}”` : ""}
              </p>
            </div>
            <div className="flex gap-1.5">
              <Button size="sm" variant="ghost" onClick={() => decide.mutate({ r, approve: false })} disabled={decide.isPending}>
                <IconX size={14} /> Dismiss
              </Button>
              <Button size="sm" variant="primary" onClick={() => decide.mutate({ r, approve: true })} disabled={decide.isPending}>
                <IconCheck size={14} /> Approve
              </Button>
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}
