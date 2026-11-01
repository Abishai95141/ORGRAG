"use client";

import { type ReactNode, useState } from "react";
import { toast } from "sonner";
import { IconCheck, IconCopy, IconDrive, IconExternal, IconHash, IconLock, IconRefresh, IconWarning } from "@/components/icons";
import { Button } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import type { ConnectorCheck, ConnectorCheckItem, SourceKind } from "@/lib/types";
import { cn } from "@/lib/utils";

/** What a Slack app needs to read channel history (channels:join lets it join public channels itself). */
const SLACK_MANIFEST = {
  display_information: {
    name: "Strata",
    description: "Indexes channel history into Strata",
    background_color: "#1f1f1f",
  },
  features: { bot_user: { display_name: "Strata", always_online: false } },
  oauth_config: {
    scopes: {
      bot: ["channels:history", "channels:read", "channels:join", "groups:history", "groups:read", "users:read"],
    },
  },
  settings: {
    org_deploy_enabled: false,
    socket_mode_enabled: false,
    token_rotation_enabled: false,
  },
};
const SLACK_NEW_APP = `https://api.slack.com/apps?new_app=1&manifest_json=${encodeURIComponent(JSON.stringify(SLACK_MANIFEST))}`;

function copy(text: string, what: string) {
  navigator.clipboard?.writeText(text).then(
    () => toast.success(`${what} copied`),
    () => toast.error("Couldn't copy"),
  );
}

function Step({ n, title, children }: { n: number; title: string; children?: ReactNode }) {
  return (
    <li className="flex gap-3">
      <span className="mt-px grid h-5 w-5 shrink-0 place-items-center rounded-full border border-line bg-surface-2 font-mono text-[11px] text-ink-2">
        {n}
      </span>
      <div className="min-w-0 flex-1 text-[12.5px] leading-relaxed text-ink-2">
        <div className="font-medium text-ink">{title}</div>
        {children}
      </div>
    </li>
  );
}

function Link({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-accent hover:underline">
      {children}
      <IconExternal size={11} />
    </a>
  );
}

/** The service account's email, read from the pasted key so the user knows whom to share with. */
export function serviceAccountEmail(key: string): string | null {
  try {
    const email = JSON.parse(key)?.client_email;
    return typeof email === "string" && email.includes("@") ? email : null;
  } catch {
    // Not strict JSON (e.g. the key's newlines were unescaped when copied): the server
    // still reads it, so find the email anyway.
    return key.match(/"client_email"\s*:\s*"([^"\s]+@[^"\s]+)"/)?.[1] ?? null;
  }
}

/** Numbered setup for a connector whose credential comes from another service. */
export function SetupSteps({ kind, secret }: { kind: SourceKind; secret: string }) {
  if (kind === "gdrive") {
    const email = serviceAccountEmail(secret);
    return (
      <ol className="flex flex-col gap-3 rounded-[12px] border border-line bg-surface-2/40 p-3.5">
        <Step n={1} title="Create a service account">
          In <Link href="https://console.cloud.google.com/iam-admin/serviceaccounts">Google Cloud → Service accounts</Link>, pick or create a project,
          then <b>Create service account</b>. It needs no roles.
        </Step>
        <Step n={2} title="Turn on the Google Drive API">
          <Link href="https://console.cloud.google.com/apis/library/drive.googleapis.com">Enable it</Link> for the same project.
        </Step>
        <Step n={3} title="Create a JSON key and paste it below">
          Open the service account → <b>Keys</b> → <b>Add key</b> → <b>Create new key</b> → JSON. Paste the downloaded file&apos;s contents.
        </Step>
        <Step n={4} title="Share what Strata should read with the account">
          {email ? (
            <span className="mt-1 flex flex-wrap items-center gap-1.5">
              In Drive, <b>Share</b> each folder with
              <button
                type="button"
                onClick={() => copy(email, "Email")}
                className="inline-flex max-w-full items-center gap-1 rounded-[6px] border border-line bg-surface px-1.5 py-0.5 font-mono text-[11.5px] text-ink hover:border-line-strong"
              >
                <span className="truncate">{email}</span>
                <IconCopy size={11} className="shrink-0" />
              </button>
              as a Viewer. For a shared drive, add it under <b>Manage members</b>.
            </span>
          ) : (
            <> Share folders (Viewer) or add it to shared drives. Its email appears here once the key is pasted.</>
          )}
        </Step>
        <Step n={5} title="Check access, then pick folders">
          Or pick none to index everything the account can see.
        </Step>
      </ol>
    );
  }
  if (kind === "slack") {
    return (
      <ol className="flex flex-col gap-3 rounded-[12px] border border-line bg-surface-2/40 p-3.5">
        <Step n={1} title="Create the Slack app">
          <Link href={SLACK_NEW_APP}>Create it with the scopes Strata needs</Link>, choose your workspace, then <b>Create</b>. If the form opens
          empty, pick <b>From a manifest</b> and paste{" "}
          <button type="button" onClick={() => copy(JSON.stringify(SLACK_MANIFEST, null, 2), "Manifest")} className="text-accent hover:underline">
            this manifest
          </button>
          .
        </Step>
        <Step n={2} title="Install it and paste the bot token below">
          <b>Install App</b> → <b>Install to Workspace</b> → Allow, then copy the <b>Bot User OAuth Token</b> (xoxb-…).
        </Step>
        <Step n={3} title="Check access, then pick channels">
          Public channels are joined automatically. For a private channel, type <code className="font-mono text-[11.5px]">/invite @Strata</code> in it
          first. This app only reads history; the Slack bot that answers questions is set up under Settings → Slack.
        </Step>
      </ol>
    );
  }
  return null;
}

const ITEM_ICON: Record<string, typeof IconHash> = {
  shared_drive: IconDrive,
  folder: IconDrive,
  channel: IconHash,
  private_channel: IconLock,
};

const NOTHING_PICKED: Partial<Record<SourceKind, string>> = {
  gdrive: "Nothing picked: everything the account can see is indexed.",
  slack: "Nothing picked: every channel the bot is in is indexed.",
};

/** "Check access": asks the server what the credentials reach, then lets the user pick
 * from that list. Picked ids are written to the form field the check names. */
export function AccessCheck({
  slug,
  kind,
  config,
  ready,
  picked,
  onPick,
  onResult,
}: {
  slug: string;
  kind: SourceKind;
  /** The form as connector config (secrets included); only sent to this org's server. */
  config: () => Record<string, unknown>;
  /** The credential is filled in. */
  ready: boolean;
  picked: string[];
  onPick: (field: string, ids: string[]) => void;
  onResult: (result: ConnectorCheck | null) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ConnectorCheck | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const found = await api.checkConnector(slug, kind, { config: config() });
      setResult(found);
      onResult(found);
    } catch (e) {
      setResult(null);
      onResult(null);
      setError(e instanceof ApiError ? e.message : "Couldn't check access");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <Button size="sm" variant="secondary" onClick={run} loading={busy} disabled={!ready}>
          <IconRefresh size={14} /> {result ? "Check again" : "Check access"}
        </Button>
        {result ? (
          <span className="flex min-w-0 items-center gap-1 text-[12px] text-ok">
            <IconCheck size={13} className="shrink-0" />
            <span className="truncate">Connected as {result.identity}</span>
          </span>
        ) : !ready ? (
          <span className="text-[12px] text-ink-3">Paste the credential first.</span>
        ) : null}
      </div>
      {error ? (
        <p className="flex gap-1.5 rounded-[10px] border border-err/30 bg-err-soft px-3 py-2 text-[12.5px] leading-relaxed text-err">
          <IconWarning size={14} className="mt-0.5 shrink-0" />
          {error}
        </p>
      ) : null}
      {result?.warnings.map((w) => (
        <p key={w} className="flex gap-1.5 rounded-[10px] border border-warn/30 bg-warn-soft px-3 py-2 text-[12.5px] leading-relaxed text-warn">
          <IconWarning size={14} className="mt-0.5 shrink-0" />
          {w}
        </p>
      ))}
      {result?.items.length ? (
        <Picker items={result.items} picked={picked} onChange={(ids) => onPick(result.field, ids)} emptyHint={NOTHING_PICKED[kind]} />
      ) : null}
    </div>
  );
}

function Picker({
  items,
  picked,
  onChange,
  emptyHint,
}: {
  items: ConnectorCheckItem[];
  picked: string[];
  onChange: (ids: string[]) => void;
  emptyHint?: string;
}) {
  const chosen = new Set(picked);
  const selectable = items.filter((i) => i.ready);
  const toggle = (id: string) => onChange(chosen.has(id) ? picked.filter((p) => p !== id) : [...picked, id]);
  return (
    <div className="rounded-[12px] border border-line">
      <div className="flex items-center justify-between gap-2 border-b border-line px-3 py-2 text-[12px] text-ink-3">
        <span>{picked.length ? `${picked.length} of ${items.length} picked` : (emptyHint ?? `${items.length} found`)}</span>
        <span className="flex gap-2">
          <button type="button" className="hover:text-ink" onClick={() => onChange(selectable.map((i) => i.id))}>
            All
          </button>
          <button type="button" className="hover:text-ink" onClick={() => onChange([])}>
            None
          </button>
        </span>
      </div>
      <ul className="max-h-56 overflow-y-auto py-1">
        {items.map((item) => {
          const Icon = ITEM_ICON[item.kind] ?? IconHash;
          const on = chosen.has(item.id);
          return (
            <li key={item.id}>
              <button
                type="button"
                role="checkbox"
                aria-checked={on}
                disabled={!item.ready}
                onClick={() => toggle(item.id)}
                className={cn(
                  "flex w-full items-center gap-2.5 px-3 py-1.5 text-left text-[13px]",
                  item.ready ? "hover:bg-surface-2" : "cursor-not-allowed opacity-60",
                )}
              >
                <span
                  className={cn(
                    "grid h-4 w-4 shrink-0 place-items-center rounded-[4px] border",
                    on ? "border-accent bg-accent text-white" : "border-line-strong",
                  )}
                >
                  {on ? <IconCheck size={11} /> : null}
                </span>
                <Icon size={14} className="shrink-0 text-ink-3" />
                <span className="min-w-0 flex-1 truncate">{item.name}</span>
                <span className="max-w-[45%] shrink-0 truncate text-[11.5px] text-ink-3">{item.note ?? item.detail}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
