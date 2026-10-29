"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ConduitArt, OrgMark, Wordmark } from "@/components/illustrations";
import { IconArrowRight } from "@/components/icons";
import { Button, Field, Input, Textarea } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { qk, useMe } from "@/lib/queries";
import { cn, HUES, orgColor, slugify } from "@/lib/utils";

export default function OnboardingPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const me = useMe();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [description, setDescription] = useState("");
  const [hue, setHue] = useState(HUES[0]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const effectiveSlug = slugTouched ? slug : slugify(name);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const org = await api.createOrg({ name, slug: effectiveSlug || undefined, description: description || undefined });
      if (hue !== org.hue) await api.updateOrg(org.slug, { hue });
      await qc.invalidateQueries({ queryKey: qk.me });
      toast.success(`${org.name} is ready`);
      router.replace(`/o/${org.slug}/sources?welcome=1`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create organization");
      setLoading(false);
    }
  }

  const hasOrgs = !!me.data?.orgs.length;

  return (
    <main className="min-h-screen">
      <header className="flex items-center justify-between px-6 py-6 sm:px-10">
        <Wordmark />
        {hasOrgs ? (
          <Button variant="ghost" onClick={() => router.back()}>
            Cancel
          </Button>
        ) : null}
      </header>
      <div className="mx-auto grid max-w-5xl gap-12 px-6 pt-6 pb-20 sm:px-10 lg:grid-cols-[1fr_380px]">
        <form onSubmit={submit} className="flex animate-rise flex-col gap-6">
          <div>
            <p className="font-mono text-[11px] tracking-wider text-ink-3 uppercase">{hasOrgs ? "New organization" : "Step 1 of 2"}</p>
            <h1 className="mt-3 font-display text-[44px] leading-[1.02] tracking-tight">
              {hasOrgs ? "Add another organization" : `Set up your first organization${me.data ? `, ${me.data.user.name.split(" ")[0]}` : ""}`}
            </h1>
            <p className="mt-3 max-w-lg text-[15px] leading-relaxed text-ink-3">
              An organization holds its own sources, collections and agents. You can belong to many, and ask questions across
              all of them at once.
            </p>
          </div>

          <Field label="Organization name" htmlFor="org-name">
            <Input id="org-name" required value={name} onChange={(e) => setName(e.target.value)} placeholder="Northwind Labs" autoFocus className="h-11 text-[15px]" />
          </Field>

          <Field label="URL" htmlFor="org-slug" hint="Lowercase letters, numbers and dashes." error={error}>
            <div className="flex h-9 items-center rounded-[var(--radius-ctl)] border border-line bg-surface-2 text-sm focus-within:border-line-strong focus-within:shadow-[0_0_0_3px_var(--ring)]">
              <span className="pl-3 font-mono text-[12.5px] text-ink-3">strata/o/</span>
              <input
                id="org-slug"
                value={effectiveSlug}
                onChange={(e) => {
                  setSlugTouched(true);
                  setSlug(slugify(e.target.value));
                }}
                className="h-full min-w-0 flex-1 bg-transparent pr-3 font-mono text-[12.5px] outline-none"
                placeholder="northwind"
              />
            </div>
          </Field>

          <Field label="What does this organization know about?" htmlFor="org-desc" hint="Optional. Helps the agent decide when to search here.">
            <Textarea id="org-desc" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Engineering handbook, product specs and support runbooks for the Northwind platform." />
          </Field>

          <fieldset>
            <legend className="mb-2 text-[13px] font-medium text-ink-2">Identity colour</legend>
            <div className="flex flex-wrap gap-2">
              {HUES.map((h) => (
                <button
                  key={h}
                  type="button"
                  aria-label={`Hue ${h}`}
                  aria-pressed={hue === h}
                  onClick={() => setHue(h)}
                  className={cn(
                    "h-8 w-8 rounded-[9px] transition-transform",
                    hue === h ? "scale-110 ring-2 ring-ink ring-offset-2 ring-offset-bg" : "hover:scale-105",
                  )}
                  style={{ background: orgColor(h) }}
                />
              ))}
            </div>
          </fieldset>

          <div className="pt-2">
            <Button type="submit" variant="primary" size="lg" loading={loading} disabled={!name.trim()}>
              Create organization <IconArrowRight size={16} />
            </Button>
          </div>
        </form>

        <aside className="hidden lg:block">
          <div className="sticky top-10 rounded-[18px] border border-line bg-surface p-6">
            <div className="flex items-center gap-3">
              <OrgMark name={name || "Organization"} hue={hue} size={44} />
              <div className="min-w-0">
                <div className="truncate text-[15px] font-semibold">{name || "Your organization"}</div>
                <div className="font-mono text-[12px] text-ink-3">/o/{effectiveSlug || "slug"}</div>
              </div>
            </div>
            <div className="my-6 border-t border-dashed border-line" />
            <ConduitArt className="mx-auto max-w-[260px]" />
            <p className="mt-6 text-[13px] leading-relaxed text-ink-3">
              Next you&apos;ll connect a source — upload files, crawl a site, or point at a GitHub repository. Strata parses,
              chunks and indexes it so the agent can cite it.
            </p>
          </div>
        </aside>
      </div>
    </main>
  );
}
