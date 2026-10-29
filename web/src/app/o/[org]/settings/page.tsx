"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { IconCheck, IconWarning } from "@/components/icons";
import { OrgMark } from "@/components/illustrations";
import { useCurrentOrg } from "@/components/layout/org-context";
import { Badge, Button, Card, Field, Input, PageHeader, Select, Skeleton, Switch, Textarea } from "@/components/ui";
import { confirmAction } from "@/components/ui/confirm";
import { api, ApiError } from "@/lib/api";
import { qk, useHealth, useOrg } from "@/lib/queries";
import type { OrgSettings } from "@/lib/types";
import { atLeast, cn, HUES, orgColor } from "@/lib/utils";

const MODELS = [
  { value: "claude-opus-5-5", label: "Claude Opus 5.5", hint: "Default — strongest reasoning for research" },
  { value: "claude-sonnet-5-5", label: "Claude Sonnet 5.5", hint: "Faster and cheaper for everyday questions" },
  { value: "claude-haiku-5-5", label: "Claude Haiku 5.5", hint: "Lowest latency and cost" },
];

export default function SettingsPage() {
  const { org } = useCurrentOrg();
  const router = useRouter();
  const qc = useQueryClient();
  const full = useOrg(org.slug);
  const health = useHealth();
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
                      <IconCheck size={12} /> API key configured
                    </Badge>
                  ) : (
                    <Badge tone="warn">
                      <IconWarning size={12} /> No API key
                    </Badge>
                  )
                ) : null
              }
            >
              <Field label="Model" htmlFor="set-model">
                <Select id="set-model" value={settings.model} onValueChange={(v) => set("model", v)} options={MODELS} />
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
              {health.data ? (
                <p className="font-mono text-[11px] text-ink-3">embeddings: {health.data.embedding_provider}</p>
              ) : null}
              <div className="flex justify-end">
                <Button variant="primary" loading={save.isPending} onClick={() => save.mutate({ settings })}>
                  Save agent settings
                </Button>
              </div>
            </Section>

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
