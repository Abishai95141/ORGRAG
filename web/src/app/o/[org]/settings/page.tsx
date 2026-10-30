"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { IconCheck, IconCopy, IconKey, IconTrash, IconWarning } from "@/components/icons";
import { OrgMark } from "@/components/illustrations";
import { useCurrentOrg } from "@/components/layout/org-context";
import { Badge, Button, Card, Field, Input, PageHeader, Segmented, Select, Skeleton, Switch, Textarea, Tooltip } from "@/components/ui";
import { confirmAction } from "@/components/ui/confirm";
import { api, ApiError } from "@/lib/api";
import { qk, useHealth, useModels, useOrg } from "@/lib/queries";
import type { ApiKey, OrgSettings, Role } from "@/lib/types";
import { atLeast, cn, HUES, orgColor, relativeTime } from "@/lib/utils";

const PROVIDER_LABEL = { anthropic: "Anthropic", nvidia: "NVIDIA" } as const;

const RECENCY: { value: OrgSettings["recency_bias"]; label: string }[] = [
  { value: "off", label: "Off" },
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
];

export default function SettingsPage() {
  const { org } = useCurrentOrg();
  const router = useRouter();
  const qc = useQueryClient();
  const full = useOrg(org.slug);
  const health = useHealth();
  const models = useModels();
  const [profile, setProfile] = useState({ name: "", description: "", hue: org.hue });
  const [settings, setSettings] = useState<OrgSettings | null>(null);

  useEffect(() => {
    if (!full.data) return;
    setProfile({ name: full.data.name, description: full.data.description ?? "", hue: full.data.hue });
    setSettings(full.data.settings);
  }, [full.data]);

  const save = useMutation({
    mutationFn: (body: Parameters<typeof api.updateOrg>[1]) => api.updateOrg(org.slug, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.org(org.slug) });
      qc.invalidateQueries({ queryKey: qk.me });
      toast.success("Settings saved");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Could not save"),
  });

  async function deleteOrg() {
    const ok = await confirmAction({
      title: `Delete ${org.name}?`,
      body: "All sources, documents, agents and conversations in this organization are permanently deleted.",
      confirmLabel: "Delete organization",
      danger: true,
    });
    if (!ok) return;
    await api.deleteOrg(org.slug);
    await qc.invalidateQueries({ queryKey: qk.me });
    router.replace("/");
  }

  if (!atLeast(org.role, "admin")) {
    return <div className="p-10 text-[14px] text-ink-3">Only admins can change organization settings.</div>;
  }

  const set = <K extends keyof OrgSettings>(k: K, v: OrgSettings[K]) => setSettings((s) => (s ? { ...s, [k]: v } : s));

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl px-5 py-10 sm:px-8">
        <PageHeader eyebrow={org.name} title="Settings" />

        {!full.data || !settings ? (
          <Skeleton className="h-96 rounded-[14px]" />
        ) : (
          <div className="flex flex-col gap-6">
            <Section title="Profile" description="How this organization appears in switchers, scopes and citations.">
              <div className="flex items-center gap-4">
                <OrgMark name={profile.name || org.name} hue={profile.hue} size={52} />
                <div className="flex flex-wrap gap-1.5">
                  {HUES.map((h) => (
                    <button
                      key={h}
                      type="button"
                      aria-label={`Hue ${h}`}
                      aria-pressed={profile.hue === h}
                      onClick={() => setProfile({ ...profile, hue: h })}
                      className={cn("h-7 w-7 rounded-[8px] transition-transform", profile.hue === h ? "ring-2 ring-ink ring-offset-2 ring-offset-surface" : "hover:scale-105")}
                      style={{ background: orgColor(h) }}
                    />
                  ))}
                </div>
              </div>
              <Field label="Name" htmlFor="set-name">
                <Input id="set-name" value={profile.name} onChange={(e) => setProfile({ ...profile, name: e.target.value })} />
              </Field>
              <Field label="Description" htmlFor="set-desc" hint="The agent uses this to judge when this organization is relevant in a multi-org question.">
                <Textarea id="set-desc" value={profile.description} onChange={(e) => setProfile({ ...profile, description: e.target.value })} className="min-h-20" />
              </Field>
              <div className="flex justify-end">
                <Button variant="primary" loading={save.isPending} onClick={() => save.mutate({ name: profile.name, description: profile.description || null, hue: profile.hue })}>
                  Save profile
                </Button>
              </div>
            </Section>

            <Section
              title="Agent"
              description="How Strata reasons for this organization."
              aside={
                health.data ? (
                  health.data.llm_configured ? (
                    <Badge tone="ok">
                      <IconCheck size={12} /> {health.data.llm_providers.map((p) => PROVIDER_LABEL[p]).join(" + ")} connected
                    </Badge>
                  ) : (
                    <Badge tone="warn">
                      <IconWarning size={12} /> No API key
                    </Badge>
                  )
                ) : null
              }
            >
              <Field
                label="Model"
                htmlFor="set-model"
                hint={
                  full.data.effective_model && full.data.effective_model !== settings.model
                    ? `No key for this model's provider — answers currently use ${
                        models.data?.find((m) => m.id === full.data!.effective_model)?.label ?? full.data.effective_model
                      }.`
                    : undefined
                }
              >
                <Select
                  id="set-model"
                  value={settings.model}
                  onValueChange={(v) => set("model", v)}
                  options={(models.data ?? []).map((m) => ({
                    value: m.id,
                    label: m.label,
                    hint: m.available ? `${PROVIDER_LABEL[m.provider]} · ${m.hint}` : `${PROVIDER_LABEL[m.provider]} · no API key`,
                  }))}
                />
              </Field>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Quick mode effort" htmlFor="set-qe" hint="Lower is faster.">
                  <Select
                    id="set-qe"
                    value={settings.quick_effort}
                    onValueChange={(v) => set("quick_effort", v)}
                    options={[
                      { value: "low", label: "Low" },
                      { value: "medium", label: "Medium" },
                      { value: "high", label: "High" },
                    ]}
                  />
                </Field>
                <Field label="Deep mode effort" htmlFor="set-de" hint="Higher plans and verifies more.">
                  <Select
                    id="set-de"
                    value={settings.deep_effort}
                    onValueChange={(v) => set("deep_effort", v)}
                    options={[
                      { value: "medium", label: "Medium" },
                      { value: "high", label: "High" },
                      { value: "xhigh", label: "Extra high" },
                    ]}
                  />
                </Field>
                <Field label="Default mode" htmlFor="set-mode">
                  <Select
                    id="set-mode"
                    value={settings.default_mode}
                    onValueChange={(v) => set("default_mode", v)}
                    options={[
                      { value: "quick", label: "Quick" },
                      { value: "deep", label: "Deep" },
                    ]}
                  />
                </Field>
                <Field label="Results per search" htmlFor="set-k" hint="Passages returned to the agent per query (3–20).">
                  <Input
                    id="set-k"
                    type="number"
                    min={3}
                    max={20}
                    value={settings.retrieval_top_k}
                    onChange={(e) => set("retrieval_top_k", Math.max(3, Math.min(20, Number(e.target.value) || 8)))}
                  />
                </Field>
              </div>
              <Switch
                checked={settings.allow_cross_org}
                onCheckedChange={(v) => set("allow_cross_org", v)}
                label="Allow cross-organization questions"
                description="Members who also belong to other organizations can include this one when asking across orgs. Turn off to keep its knowledge siloed."
              />
              <div className="flex justify-end">
                <Button variant="primary" loading={save.isPending} onClick={() => save.mutate({ settings })}>
                  Save agent settings
                </Button>
              </div>
            </Section>

            <Section
              title="Retrieval"
              description="How sources are ranked before the agent reads them."
              aside={
                health.data ? (
                  <span className="font-mono text-[11px] text-ink-3">
                    embeddings: {health.data.embedding_provider} · rerank: {health.data.rerank_provider ?? "off"}
                  </span>
                ) : null
              }
            >
              <Switch
                checked={settings.rerank}
                onCheckedChange={(v) => set("rerank", v)}
                label="Rerank results"
                description={
                  health.data?.rerank_provider
                    ? `A cross-encoder (${health.data.rerank_provider === "nvidia" ? "NVIDIA Nemotron" : "local model"}) re-scores the top candidates for each search. More precise, slightly slower.`
                    : "No reranker is available on this server (set NVIDIA_API_KEY, or install local embeddings)."
                }
              />
              <div className="flex items-start justify-between gap-6">
                <div className="min-w-0">
                  <div className="text-sm font-medium">Prefer recent documents</div>
                  <div className="mt-0.5 text-[13px] text-ink-3">
                    How strongly newer documents outrank older ones of similar relevance. Uses each source&apos;s own
                    last-modified time.
                  </div>
                </div>
                <Segmented size="sm" value={settings.recency_bias} onChange={(v) => set("recency_bias", v)} options={RECENCY} />
              </div>
              <Switch
                checked={settings.web_search && !!health.data?.web_search_provider}
                onCheckedChange={(v) => set("web_search", v)}
                label="Web search by default"
                description={
                  health.data?.web_search_provider
                    ? `The agent may search the public web (via ${health.data.web_search_provider}) when the knowledge base can't answer. People can still switch it per question.`
                    : "Unavailable: set TAVILY_API_KEY or BRAVE_API_KEY on the server to enable web search."
                }
              />
              <div className="flex justify-end">
                <Button variant="primary" loading={save.isPending} onClick={() => save.mutate({ settings })}>
                  Save retrieval settings
                </Button>
              </div>
            </Section>

            <ApiKeys slug={org.slug} role={org.role} />

            {org.role === "owner" ? (
              <Section title="Danger zone" description="Permanently delete this organization and everything in it." danger>
                <div>
                  <Button variant="danger" onClick={deleteOrg}>
                    Delete {org.name}
                  </Button>
                </div>
              </Section>
            ) : null}
          </div>
        )}
      </div>
    </div>
  );
}

function Section({
  title,
  description,
  children,
  aside,
  danger,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
  aside?: React.ReactNode;
  danger?: boolean;
}) {
  return (
    <Card className={cn("p-5", danger && "border-err/30")}>
      <div className="mb-5 flex items-start justify-between gap-4">
        <div>
          <h2 className={cn("text-[15px] font-semibold", danger && "text-err")}>{title}</h2>
          {description ? <p className="mt-0.5 text-[13px] text-ink-3">{description}</p> : null}
        </div>
        {aside}
      </div>
      <div className="flex flex-col gap-4">{children}</div>
    </Card>
  );
}

function ApiKeys({ slug, role }: { slug: string; role: Role }) {
  const qc = useQueryClient();
  const keys = useQuery({ queryKey: qk.apiKeys(slug), queryFn: () => api.apiKeys(slug) });
  const [name, setName] = useState("");
  const [keyRole, setKeyRole] = useState<"admin" | "member" | "viewer">("member");
  const [created, setCreated] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const create = useMutation({
    mutationFn: () => api.createApiKey(slug, { name: name.trim(), role: keyRole }),
    onSuccess: (k) => {
      setCreated(k.key);
      setName("");
      qc.invalidateQueries({ queryKey: qk.apiKeys(slug) });
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Could not create the key"),
  });

  async function revoke(k: ApiKey) {
    const ok = await confirmAction({
      title: `Revoke “${k.name}”?`,
      body: "Anything using this key stops working immediately.",
      confirmLabel: "Revoke key",
      danger: true,
    });
    if (!ok) return;
    await api.revokeApiKey(slug, k.id);
    qc.invalidateQueries({ queryKey: qk.apiKeys(slug) });
  }

  const roles = (["viewer", "member", "admin"] as const).filter((r) => atLeast(role, r));

  return (
    <Section
      title="API keys"
      description="Let scripts and other services search and ask questions in this organization. A key acts as you, with at most the role you give it."
      aside={
        <a href="/api/docs" target="_blank" rel="noreferrer" className="text-[12px] whitespace-nowrap text-ink-3 underline decoration-line-strong underline-offset-4 hover:text-ink">
          API reference
        </a>
      }
    >
      {created ? (
        <div className="rounded-[12px] border border-ok/30 bg-ok-soft/60 p-3.5">
          <div className="mb-2 text-[13px] font-medium text-ink">Copy your new key now — it won&apos;t be shown again.</div>
          <div className="flex items-center gap-2">
            <code className="min-w-0 flex-1 truncate rounded-[8px] border border-line bg-surface px-2.5 py-1.5 font-mono text-[12px]">{created}</code>
            <Button
              size="sm"
              onClick={async () => {
                await navigator.clipboard.writeText(created).catch(() => {});
                setCopied(true);
                setTimeout(() => setCopied(false), 1400);
              }}
            >
              {copied ? <IconCheck size={14} /> : <IconCopy size={14} />} {copied ? "Copied" : "Copy"}
            </Button>
          </div>
          <p className="mt-2 font-mono text-[11px] text-ink-3">Authorization: Bearer {created.slice(0, 16)}…</p>
        </div>
      ) : null}

      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-[200px] flex-1">
          <Field label="Name" htmlFor="key-name">
            <Input id="key-name" placeholder="CI pipeline" value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
        </div>
        <Field label="Role" htmlFor="key-role">
          <Select
            id="key-role"
            value={keyRole}
            onValueChange={(v) => setKeyRole(v as typeof keyRole)}
            options={roles.map((r) => ({ value: r, label: r[0].toUpperCase() + r.slice(1) }))}
            className="w-32"
          />
        </Field>
        <Button variant="primary" loading={create.isPending} disabled={!name.trim()} onClick={() => create.mutate()}>
          <IconKey size={15} /> Create key
        </Button>
      </div>

      {keys.data?.length ? (
        <ul className="divide-y divide-line rounded-[12px] border border-line">
          {keys.data.map((k) => (
            <li key={k.id} className="flex items-center gap-3 px-3.5 py-2.5 text-[13px]">
              <IconKey size={15} className="shrink-0 text-ink-3" />
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium">{k.name}</div>
                <div className="font-mono text-[11px] text-ink-3">
                  {k.prefix}… · {k.last_used_at ? `used ${relativeTime(k.last_used_at)}` : "never used"}
                </div>
              </div>
              <Badge tone="outline" className="capitalize">
                {k.role}
              </Badge>
              <Tooltip label="Revoke">
                <Button variant="ghost" size="icon-sm" aria-label={`Revoke ${k.name}`} onClick={() => revoke(k)}>
                  <IconTrash size={15} />
                </Button>
              </Tooltip>
            </li>
          ))}
        </ul>
      ) : keys.isLoading ? null : (
        <p className="text-[12.5px] text-ink-3">No keys yet.</p>
      )}
    </Section>
  );
}
