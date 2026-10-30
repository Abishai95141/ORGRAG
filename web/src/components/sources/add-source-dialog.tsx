"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { IconArrowLeft, IconChannel, IconDrive, IconFile, IconGlobe, IconRepo, IconWiki, IconX } from "@/components/icons";
import { Button, Dialog, Field, Input, Select } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { qk, useCollections, useConnectors } from "@/lib/queries";
import type { SourceKind } from "@/lib/types";
import { cn } from "@/lib/utils";
import { type ConfigSchema, type FormValues, initialValues, missingSecrets, SchemaForm, toConfig } from "./schema-form";
import { Dropzone, INTERVALS } from "./shared";

const KINDS: { kind: SourceKind; title: string; body: string; icon: typeof IconFile }[] = [
  { kind: "upload", title: "Upload files", body: "PDFs, Word documents, Markdown, HTML and more. Drop them in, they're indexed in seconds.", icon: IconFile },
  { kind: "web", title: "Crawl a website", body: "Docs sites, wikis, help centres. Follows links on the same domain and respects robots.txt.", icon: IconGlobe },
  { kind: "github", title: "GitHub repository", body: "READMEs, docs folders and Markdown across a repo. Public, or private with a token.", icon: IconRepo },
  { kind: "gdrive", title: "Google Drive", body: "Docs, Sheets, Slides and files from shared drives or folders, through a service account.", icon: IconDrive },
  { kind: "slack", title: "Slack", body: "Threads and channel history from the channels your Strata bot has been invited to.", icon: IconChannel },
  { kind: "notion", title: "Notion", body: "Pages and database entries shared with a Notion integration, kept in sync as they change.", icon: IconWiki },
];

/** Connectors whose form is generated from their config schema. */
const SCHEMA_KINDS = new Set<SourceKind>(["gdrive", "slack", "notion"]);

/** Where to get the credential, shown above the generated form. */
const SETUP_HINT: Partial<Record<SourceKind, string>> = {
  gdrive: "Create a service account in Google Cloud, download its JSON key, then share the folders (or shared drives) with its email.",
  slack: "Create a Slack app with channels:history, channels:read, groups:history, groups:read and users:read, install it, and invite the bot to the channels to index.",
  notion: "Create an internal integration at notion.so/my-integrations, then share the pages to index with it (… → Connections).",
};

export function AddSourceDialog({ open, onOpenChange, slug }: { open: boolean; onOpenChange: (v: boolean) => void; slug: string }) {
  const router = useRouter();
  const qc = useQueryClient();
  const collections = useCollections(slug);
  const [kind, setKind] = useState<SourceKind | null>(null);
  const [name, setName] = useState("");
  const [collectionId, setCollectionId] = useState<string | undefined>();
  const [interval, setInterval_] = useState("0");
  const [web, setWeb] = useState({ url: "", max_pages: "50", max_depth: "2" });
  const [gh, setGh] = useState({ repo: "", branch: "", paths: "", token: "" });
  const connectors = useConnectors();
  const schema = (connectors.data?.find((c) => c.kind === kind)?.config_schema ?? {}) as ConfigSchema;
  const [form, setForm] = useState<FormValues>({});
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setKind(null);
      setName("");
      setFiles([]);
      setError(null);
      setWeb({ url: "", max_pages: "50", max_depth: "2" });
      setGh({ repo: "", branch: "", paths: "", token: "" });
      setForm({});
      setInterval_("0");
    }
  }, [open]);

  // Fresh defaults whenever a schema-driven connector is picked.
  useEffect(() => {
    if (kind && SCHEMA_KINDS.has(kind)) setForm(initialValues(schema));
  }, [kind, connectors.data]);

  useEffect(() => {
    if (!collectionId && collections.data?.length) setCollectionId(collections.data[0].id);
  }, [collections.data, collectionId]);

  const defaultName =
    kind === "web"
      ? safeHost(web.url)
      : kind === "github"
        ? gh.repo.split("/").pop() || ""
        : kind && SCHEMA_KINDS.has(kind)
          ? KINDS.find((k) => k.kind === kind)!.title
          : files.length
            ? "Uploaded files"
            : "";

  async function submit() {
    if (!kind || !collectionId) return;
    setBusy(true);
    setError(null);
    try {
      const config: Record<string, unknown> =
        kind === "web"
          ? { url: web.url.trim(), max_pages: Number(web.max_pages) || 50, max_depth: Number(web.max_depth) || 2 }
          : kind === "github"
            ? {
                repo: gh.repo.trim().replace(/^https?:\/\/github\.com\//, "").replace(/\.git$/, ""),
                ...(gh.branch.trim() ? { branch: gh.branch.trim() } : {}),
                ...(gh.paths.trim() ? { paths: gh.paths.split(",").map((p) => p.trim()).filter(Boolean) } : {}),
                ...(gh.token.trim() ? { token: gh.token.trim() } : {}),
              }
            : SCHEMA_KINDS.has(kind)
              ? toConfig(schema, form)
              : {};
      const source = await api.createSource(slug, {
        kind,
        name: (name || defaultName || "Untitled source").trim(),
        collection_id: collectionId,
        config,
        sync_interval_minutes: kind === "upload" || interval === "0" ? null : Number(interval),
      });
      if (kind === "upload" && files.length) await api.uploadFiles(slug, source.id, files);
      qc.invalidateQueries({ queryKey: qk.sources(slug) });
      qc.invalidateQueries({ queryKey: qk.collections(slug) });
      toast.success(kind === "upload" ? `Indexing ${files.length} file${files.length === 1 ? "" : "s"}` : "Source added — first sync started");
      onOpenChange(false);
      router.push(`/o/${slug}/sources/${source.id}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Could not add source");
    } finally {
      setBusy(false);
    }
  }

  const valid =
    !!collectionId &&
    (kind === "upload"
      ? files.length > 0
      : kind === "web"
        ? /^https?:\/\/.+\..+/.test(web.url.trim())
        : kind === "github"
          ? /[\w.-]+\/[\w.-]+/.test(gh.repo)
          : kind && SCHEMA_KINDS.has(kind)
            ? !!schema.properties && missingSecrets(schema, form).length === 0
            : false);

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      width={kind ? 520 : 640}
      title={kind ? KINDS.find((k) => k.kind === kind)!.title : "Add a source"}
      description={kind ? "Content is parsed, chunked and embedded into the collection you choose." : "Where should this organization's knowledge come from?"}
      footer={
        kind ? (
          <>
            <Button variant="ghost" onClick={() => setKind(null)} className="mr-auto">
              <IconArrowLeft size={15} /> Back
            </Button>
            <Button variant="primary" onClick={submit} loading={busy} disabled={!valid}>
              {kind === "upload" ? "Upload and index" : "Add and sync"}
            </Button>
          </>
        ) : null
      }
    >
      {!kind ? (
        <div className="grid gap-2.5 sm:grid-cols-3">
          {KINDS.map((k) => (
            <button
              key={k.kind}
              type="button"
              onClick={() => setKind(k.kind)}
              className="group flex flex-col items-start gap-3 rounded-[14px] border border-line bg-surface p-4 text-left transition-all hover:-translate-y-px hover:border-line-strong hover:shadow-[0_10px_24px_-18px_rgba(0,0,0,.35)]"
            >
              <span className="grid h-10 w-10 place-items-center rounded-[11px] border border-line bg-surface-2 text-ink-2 transition-colors group-hover:border-accent/40 group-hover:text-accent">
                <k.icon size={20} />
              </span>
              <span className="text-[14px] font-semibold">{k.title}</span>
              <span className="text-[12.5px] leading-relaxed text-ink-3">{k.body}</span>
            </button>
          ))}
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          {kind === "web" ? (
            <>
              <Field label="Start URL" htmlFor="src-url" hint="Strata follows links within this domain.">
                <Input id="src-url" placeholder="https://docs.example.com" value={web.url} onChange={(e) => setWeb({ ...web, url: e.target.value })} autoFocus />
              </Field>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Max pages" htmlFor="src-pages">
                  <Input id="src-pages" type="number" min={1} max={2000} value={web.max_pages} onChange={(e) => setWeb({ ...web, max_pages: e.target.value })} />
                </Field>
                <Field label="Link depth" htmlFor="src-depth">
                  <Input id="src-depth" type="number" min={0} max={6} value={web.max_depth} onChange={(e) => setWeb({ ...web, max_depth: e.target.value })} />
                </Field>
              </div>
            </>
          ) : null}

          {kind === "github" ? (
            <>
              <Field label="Repository" htmlFor="src-repo" hint="owner/name or a github.com URL.">
                <Input id="src-repo" placeholder="onyx-dot-app/onyx" value={gh.repo} onChange={(e) => setGh({ ...gh, repo: e.target.value })} autoFocus />
              </Field>
              <div className="grid grid-cols-2 gap-3">
                <Field label="Branch" htmlFor="src-branch" hint="Defaults to the repo's default.">
                  <Input id="src-branch" placeholder="main" value={gh.branch} onChange={(e) => setGh({ ...gh, branch: e.target.value })} />
                </Field>
                <Field label="Paths" htmlFor="src-paths" hint="Comma separated, optional.">
                  <Input id="src-paths" placeholder="docs, README.md" value={gh.paths} onChange={(e) => setGh({ ...gh, paths: e.target.value })} />
                </Field>
              </div>
              <Field label="Access token" htmlFor="src-token" hint="Only for private repos. Stored server-side, never shown again.">
                <Input id="src-token" type="password" autoComplete="off" placeholder="github_pat_…" value={gh.token} onChange={(e) => setGh({ ...gh, token: e.target.value })} />
              </Field>
            </>
          ) : null}

          {kind && SCHEMA_KINDS.has(kind) ? (
            <>
              {SETUP_HINT[kind] ? (
                <p className="rounded-[10px] border border-line bg-surface-2/50 px-3 py-2.5 text-[12.5px] leading-relaxed text-ink-2">{SETUP_HINT[kind]}</p>
              ) : null}
              {schema.properties ? <SchemaForm schema={schema} values={form} onChange={setForm} idPrefix={`src-${kind}`} /> : null}
            </>
          ) : null}

          {kind === "upload" ? (
            <>
              <Dropzone onFiles={(f) => setFiles((cur) => [...cur, ...f])} compact={files.length > 0} />
              {files.length ? (
                <ul className="flex max-h-40 flex-col gap-1 overflow-y-auto">
                  {files.map((f, i) => (
                    <li key={`${f.name}-${i}`} className="flex items-center gap-2 rounded-[8px] bg-surface-2/60 px-2.5 py-1.5 text-[12.5px]">
                      <IconFile size={14} className="text-ink-3" />
                      <span className="min-w-0 flex-1 truncate">{f.name}</span>
                      <span className="font-mono text-[11px] text-ink-3">{(f.size / 1024).toFixed(0)} KB</span>
                      <button type="button" aria-label={`Remove ${f.name}`} onClick={() => setFiles(files.filter((_, j) => j !== i))} className="text-ink-3 hover:text-ink">
                        <IconX size={13} />
                      </button>
                    </li>
                  ))}
                </ul>
              ) : null}
            </>
          ) : null}

          <div className={cn("grid gap-3", kind !== "upload" && "grid-cols-2")}>
            <Field label="Collection" htmlFor="src-collection">
              <Select
                id="src-collection"
                value={collectionId}
                onValueChange={setCollectionId}
                placeholder="Choose a collection"
                options={(collections.data ?? []).map((c) => ({ value: c.id, label: c.name, hint: c.visibility === "restricted" ? "Restricted" : undefined }))}
              />
            </Field>
            {kind !== "upload" ? (
              <Field label="Re-sync" htmlFor="src-interval">
                <Select id="src-interval" value={interval} onValueChange={setInterval_} options={INTERVALS} />
              </Field>
            ) : null}
          </div>

          <Field label="Name" htmlFor="src-name" error={error}>
            <Input id="src-name" placeholder={defaultName || "Engineering handbook"} value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
        </div>
      )}
    </Dialog>
  );
}

function safeHost(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}
