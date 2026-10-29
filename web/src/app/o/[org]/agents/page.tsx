"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { IconArrowRight, IconCheck, IconDeep, IconMore, IconPencil, IconPlus, IconQuick, IconTrash, IconX } from "@/components/icons";
import { CompassArt, Glyph, GLYPHS, GlyphTile } from "@/components/illustrations";
import { useCurrentOrg } from "@/components/layout/org-context";
import { Badge, Button, Card, Dialog, EmptyState, Field, Input, Menu, MenuContent, MenuItem, MenuTrigger, PageHeader, Segmented, Skeleton, Textarea } from "@/components/ui";
import { confirmAction } from "@/components/ui/confirm";
import { api, ApiError } from "@/lib/api";
import { qk, useAgents, useCollections } from "@/lib/queries";
import type { Agent, Mode } from "@/lib/types";
import { atLeast, cn } from "@/lib/utils";

export default function AgentsPage() {
  const { org } = useCurrentOrg();
  const qc = useQueryClient();
  const agents = useAgents(org.slug);
  const collections = useCollections(org.slug);
  const [editing, setEditing] = useState<Agent | "new" | null>(null);
  const isAdmin = atLeast(org.role, "admin");
  const colName = new Map((collections.data ?? []).map((c) => [c.id, c.name]));

  const remove = useMutation({
    mutationFn: (id: string) => api.deleteAgent(org.slug, id),
    onSuccess: () => (qc.invalidateQueries({ queryKey: qk.agents(org.slug) }), toast("Agent deleted")),
  });

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl px-5 py-10 sm:px-8">
        <PageHeader
          eyebrow={org.name}
          title="Agents"
          description="Agents are focused researchers: their own instructions, a default set of collections to search, and a working style — quick lookups or deep investigations."
          actions={
            isAdmin ? (
              <Button variant="primary" onClick={() => setEditing("new")}>
                <IconPlus size={16} /> New agent
              </Button>
            ) : null
          }
        />
        {agents.isLoading ? (
          <div className="grid gap-3 md:grid-cols-2">
            {[0, 1].map((i) => (
              <Skeleton key={i} className="h-48 rounded-[14px]" />
            ))}
          </div>
        ) : !agents.data?.length ? (
          <Card>
            <EmptyState
              art={<CompassArt />}
              title="No agents yet"
              description="Create one for a team or a recurring job — an on-call assistant, a policy checker, an onboarding guide."
              action={isAdmin ? <Button variant="primary" onClick={() => setEditing("new")}><IconPlus size={16} /> Create an agent</Button> : null}
            />
          </Card>
        ) : (
          <div className="grid gap-3 md:grid-cols-2">
            {agents.data.map((a) => (
              <Card key={a.id} className="flex flex-col p-5">
                <div className="flex items-start gap-3.5">
                  <GlyphTile name={a.glyph} hue={org.hue} size={44} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <h3 className="truncate text-[15px] font-semibold">{a.name}</h3>
                      <Badge tone={a.mode === "deep" ? "accent" : "neutral"}>
                        {a.mode === "deep" ? <IconDeep size={11} /> : <IconQuick size={11} />}
                        {a.mode}
                      </Badge>
                    </div>
                    <p className="mt-1 line-clamp-2 text-[13px] leading-relaxed text-ink-3">{a.description || a.instructions}</p>
                  </div>
                  {isAdmin ? (
                    <Menu>
                      <MenuTrigger asChild>
                        <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${a.name}`}>
                          <IconMore size={16} />
                        </Button>
                      </MenuTrigger>
                      <MenuContent align="end">
                        <MenuItem icon={<IconPencil size={15} />} onSelect={() => setEditing(a)}>
                          Edit
                        </MenuItem>
                        <MenuItem
                          danger
                          icon={<IconTrash size={15} />}
                          onSelect={async () => (await confirmAction({ title: `Delete ${a.name}?`, confirmLabel: "Delete agent", danger: true })) && remove.mutate(a.id)}
                        >
                          Delete
                        </MenuItem>
                      </MenuContent>
                    </Menu>
                  ) : null}
                </div>
                <div className="mt-4 flex flex-wrap gap-1.5">
                  {a.collection_ids.length ? (
                    a.collection_ids.map((id) => (
                      <span key={id} className="rounded-[6px] border border-line px-1.5 py-0.5 text-[11.5px] text-ink-2">
                        {colName.get(id) ?? "Hidden collection"}
                      </span>
                    ))
                  ) : (
                    <span className="text-[12px] text-ink-3">Searches every collection the asker can see</span>
                  )}
                </div>
                {a.starter_prompts.length ? (
                  <ul className="mt-4 flex flex-col gap-1 border-t border-line pt-3">
                    {a.starter_prompts.slice(0, 3).map((p) => (
                      <li key={p} className="truncate text-[12.5px] text-ink-2">
                        <span className="mr-1.5 text-ink-3">→</span>
                        {p}
                      </li>
                    ))}
                  </ul>
                ) : null}
                <div className="mt-auto pt-4">
                  <Link href={`/o/${org.slug}/chat?agent=${a.id}`}>
                    <Button size="sm">
                      Ask {a.name} <IconArrowRight size={14} />
                    </Button>
                  </Link>
                </div>
              </Card>
            ))}
          </div>
        )}
      </div>
      <AgentDialog key={editing === "new" ? "new" : (editing?.id ?? "none")} value={editing} onClose={() => setEditing(null)} />
    </div>
  );
}

function AgentDialog({ value, onClose }: { value: Agent | "new" | null; onClose: () => void }) {
  const { org } = useCurrentOrg();
  const qc = useQueryClient();
  const collections = useCollections(org.slug);
  const a = value && value !== "new" ? value : null;
  const [form, setForm] = useState({
    name: a?.name ?? "",
    description: a?.description ?? "",
    instructions: a?.instructions ?? "",
    mode: (a?.mode ?? "quick") as Mode,
    glyph: (a?.glyph as string) ?? "book",
    collection_ids: a?.collection_ids ?? [],
    starter_prompts: a?.starter_prompts ?? [],
  });
  const [prompt, setPrompt] = useState("");
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: () => {
      const body = { ...form, name: form.name.trim(), description: form.description.trim() || null };
      return a ? api.updateAgent(org.slug, a.id, body) : api.createAgent(org.slug, body);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.agents(org.slug) });
      toast.success(a ? "Agent updated" : "Agent created");
      onClose();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Could not save"),
  });

  const toggleCol = (id: string) =>
    setForm((f) => ({ ...f, collection_ids: f.collection_ids.includes(id) ? f.collection_ids.filter((c) => c !== id) : [...f.collection_ids, id] }));

  const addPrompt = () => {
    const p = prompt.trim();
    if (p && !form.starter_prompts.includes(p)) setForm({ ...form, starter_prompts: [...form.starter_prompts, p].slice(0, 6) });
    setPrompt("");
  };

  return (
    <Dialog
      open={!!value}
      onOpenChange={(v) => !v && onClose()}
      title={a ? `Edit ${a.name}` : "New agent"}
      width={600}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" onClick={() => save.mutate()} loading={save.isPending} disabled={!form.name.trim() || !form.instructions.trim()}>
            {a ? "Save agent" : "Create agent"}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        <div className="flex gap-3">
          <Menu>
            <MenuTrigger asChild>
              <button type="button" aria-label="Choose glyph" className="mt-[22px] shrink-0 rounded-[11px] transition-transform hover:scale-105">
                <GlyphTile name={form.glyph} hue={org.hue} size={40} />
              </button>
            </MenuTrigger>
            <MenuContent className="!min-w-0 p-1.5">
              <div className="grid grid-cols-4 gap-1">
                {GLYPHS.map((g) => (
                  <button
                    key={g}
                    type="button"
                    onClick={() => setForm({ ...form, glyph: g })}
                    className={cn("grid h-9 w-9 place-items-center rounded-[8px] hover:bg-surface-2", form.glyph === g && "bg-surface-2 text-ink")}
                    aria-label={g}
                  >
                    <Glyph name={g} size={18} />
                  </button>
                ))}
              </div>
            </MenuContent>
          </Menu>
          <div className="flex-1">
            <Field label="Name" htmlFor="ag-name" error={error}>
              <Input id="ag-name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="On-call guide" autoFocus />
            </Field>
          </div>
        </div>
        <Field label="Short description" htmlFor="ag-desc">
          <Input id="ag-desc" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} placeholder="Walks engineers through incidents using our runbooks." />
        </Field>
        <Field label="Instructions" htmlFor="ag-ins" hint="How it should think, what to prioritise, how to format answers.">
          <Textarea
            id="ag-ins"
            value={form.instructions}
            onChange={(e) => setForm({ ...form, instructions: e.target.value })}
            className="min-h-32 font-mono text-[12.5px]"
            placeholder={"You help on-call engineers resolve incidents.\nAlways check the latest runbook first, quote exact commands, and flag anything that needs a second approver."}
          />
        </Field>
        <div>
          <div className="mb-2 text-[13px] font-medium text-ink-2">Working style</div>
          <Segmented
            value={form.mode}
            onChange={(mode) => setForm({ ...form, mode })}
            options={[
              { value: "quick", label: "Quick answers", icon: <IconQuick size={14} /> },
              { value: "deep", label: "Deep research", icon: <IconDeep size={14} /> },
            ]}
          />
        </div>
        <div>
          <div className="mb-2 text-[13px] font-medium text-ink-2">
            Collections <span className="font-normal text-ink-3">— none selected searches everything the asker can see</span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {(collections.data ?? []).map((c) => {
              const on = form.collection_ids.includes(c.id);
              return (
                <button
                  key={c.id}
                  type="button"
                  onClick={() => toggleCol(c.id)}
                  aria-pressed={on}
                  className={cn(
                    "inline-flex h-7 items-center gap-1.5 rounded-[8px] border px-2.5 text-[12.5px] transition-colors",
                    on ? "border-ink bg-ink text-bg" : "border-line text-ink-2 hover:border-line-strong",
                  )}
                >
                  {on ? <IconCheck size={12} strokeWidth={2} /> : <Glyph name={c.glyph} size={13} />}
                  {c.name}
                </button>
              );
            })}
          </div>
        </div>
        <div>
          <div className="mb-2 text-[13px] font-medium text-ink-2">Starter prompts</div>
          <div className="flex gap-2">
            <Input value={prompt} onChange={(e) => setPrompt(e.target.value)} onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), addPrompt())} placeholder="What's the rollback procedure for the API?" />
            <Button onClick={addPrompt} disabled={!prompt.trim()}>
              Add
            </Button>
          </div>
          {form.starter_prompts.length ? (
            <ul className="mt-2 flex flex-col gap-1">
              {form.starter_prompts.map((p) => (
                <li key={p} className="flex items-center gap-2 rounded-[8px] bg-surface-2/60 px-2.5 py-1.5 text-[12.5px]">
                  <span className="min-w-0 flex-1 truncate">{p}</span>
                  <button type="button" aria-label="Remove prompt" onClick={() => setForm({ ...form, starter_prompts: form.starter_prompts.filter((x) => x !== p) })} className="text-ink-3 hover:text-ink">
                    <IconX size={13} />
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      </div>
    </Dialog>
  );
}
