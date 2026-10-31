"use client";

import { Suspense, useEffect, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { IconChannel, IconCheck, IconCopy, IconLock, IconPencil, IconPlus, IconTrash, IconWarning } from "@/components/icons";
import { OrgMark } from "@/components/illustrations";
import { useCurrentOrg } from "@/components/layout/org-context";
import { SettingsNav } from "@/components/layout/settings-nav";
import { Badge, Button, Card, Dialog, Field, Input, PageHeader, Select, Skeleton, Tooltip } from "@/components/ui";
import { confirmAction } from "@/components/ui/confirm";
import { api, ApiError } from "@/lib/api";
import { qk, useSlack, useSlackChannels } from "@/lib/queries";
import type { SlackChannel, SlackOrg, SlackWorkspace } from "@/lib/types";
import { atLeast, cn, relativeTime } from "@/lib/utils";

// Why a connection attempt came back without connecting (the callback's `slack_error`).
const INSTALL_ERRORS: Record<string, string> = {
  wrong_user: "Finish connecting in the browser where you started, signed in as the same admin.",
  not_admin: "Only admins of this organization can connect Slack.",
  access_denied: "The Slack connection was cancelled.",
  enterprise_install: "Org-wide Enterprise Grid installs aren't supported. Install the app to one workspace.",
  no_bot_token: "Slack didn't return a bot token. Check the app's bot scopes and try again.",
  invalid_code: "That Slack sign-in expired. Try connecting again.",
};

export default function SlackSettingsPage() {
  return (
    <Suspense>
      <SlackSettings />
    </Suspense>
  );
}

function SlackSettings() {
  const { org } = useCurrentOrg();
  const slack = useSlack(org.slug);
  useInstallResult();

  if (!atLeast(org.role, "admin")) {
    return <div className="p-10 text-[14px] text-ink-3">Only admins can change organization settings.</div>;
  }

  const data = slack.data;
  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-3xl px-5 py-10 sm:px-8">
        <PageHeader eyebrow={org.name} title="Settings" />
        <SettingsNav slug={org.slug} />

        {!data ? (
          <Skeleton className="h-64 rounded-[14px]" />
        ) : (
          <div className="flex flex-col gap-6">
            <Card className="p-5">
              <div className="mb-5 flex items-start justify-between gap-4">
                <div>
                  <h2 className="text-[15px] font-semibold">Slack</h2>
                  <p className="mt-0.5 text-[13px] text-ink-3">
                    People mention <span className="font-medium text-ink-2">@Strata</span> in a channel or message it directly, and get a
                    cited answer in the thread, with exactly the access they have here.
                  </p>
                </div>
                {data.configured ? (
                  <Badge tone="ok">
                    <IconCheck size={12} /> Ready
                  </Badge>
                ) : (
                  <Badge tone="warn">
                    <IconWarning size={12} /> Not set up
                  </Badge>
                )}
              </div>
              <div className="flex flex-col gap-4">
                <Rules />
                {data.configured ? (
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <p className="text-[12.5px] text-ink-3">
                      Connecting sends you to Slack to approve the app, then back here.
                    </p>
                    <a href={api.slackInstallUrl(org.slug)}>
                      <Button variant="primary">
                        <IconPlus size={15} /> {data.workspaces.length ? "Connect another workspace" : "Connect a workspace"}
                      </Button>
                    </a>
                  </div>
                ) : (
                  <p className="rounded-[10px] bg-warn-soft px-3 py-2 text-[13px] text-warn">
                    The server needs a Slack app first: set <code className="font-mono">SLACK_CLIENT_ID</code>,{" "}
                    <code className="font-mono">SLACK_CLIENT_SECRET</code> and <code className="font-mono">SLACK_SIGNING_SECRET</code>{" "}
                    (see the README&apos;s Slack setup).
                  </p>
                )}
                <SetupUrls eventsUrl={data.events_url} redirectUrl={data.redirect_url} />
              </div>
            </Card>

            {data.workspaces.map((ws) => (
              <WorkspaceCard key={ws.id} ws={ws} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

/** Toast the outcome of the OAuth round trip (`?connected=` / `?slack_error=`), once. */
function useInstallResult() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const qc = useQueryClient();
  const { org } = useCurrentOrg();
  const connected = params.get("connected");
  const error = params.get("slack_error");
  useEffect(() => {
    if (!connected && !error) return;
    if (connected) {
      toast.success(`Connected ${connected}`, { description: "Invite @Strata to a channel, or send it a direct message." });
      qc.invalidateQueries({ queryKey: qk.slack(org.slug) });
    } else if (error) {
      toast.error(INSTALL_ERRORS[error] ?? `Slack couldn't connect (${error}).`);
    }
    router.replace(pathname);
  }, [connected, error, org.slug, pathname, qc, router]);
}

function Rules() {
  return (
    <ul className="grid gap-2 text-[13px] text-ink-2 sm:grid-cols-2">
      <li className="rounded-[10px] border border-line bg-surface-2/40 px-3 py-2.5">
        <div className="flex items-center gap-1.5 font-medium text-ink">
          <IconChannel size={15} className="text-ink-3" /> In channels
        </div>
        <p className="mt-1 text-[12.5px] leading-relaxed text-ink-3">
          Everyone in a channel reads the answer, so only collections visible to the whole organization are searched.
        </p>
      </li>
      <li className="rounded-[10px] border border-line bg-surface-2/40 px-3 py-2.5">
        <div className="flex items-center gap-1.5 font-medium text-ink">
          <IconLock size={15} className="text-ink-3" /> In direct messages
        </div>
        <p className="mt-1 text-[12.5px] leading-relaxed text-ink-3">
          Only the asker reads it, so everything they can read is searched, restricted collections included.
        </p>
      </li>
    </ul>
  );
}

function SetupUrls({ eventsUrl, redirectUrl }: { eventsUrl: string; redirectUrl: string }) {
  return (
    <details className="group rounded-[10px] border border-line">
      <summary className="cursor-pointer list-none px-3 py-2 text-[12.5px] text-ink-3 select-none hover:text-ink-2">
        Slack app URLs <span className="text-ink-3/70">· for whoever configures the Slack app</span>
      </summary>
      <div className="flex flex-col gap-2 border-t border-line px-3 py-3">
        <CopyRow label="Event Subscriptions → Request URL" value={eventsUrl} />
        <CopyRow label="OAuth & Permissions → Redirect URL" value={redirectUrl} />
      </div>
    </details>
  );
}

function CopyRow({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div>
      <div className="mb-1 text-[11.5px] text-ink-3">{label}</div>
      <div className="flex items-center gap-2">
        <code className="min-w-0 flex-1 truncate rounded-[8px] border border-line bg-surface px-2.5 py-1.5 font-mono text-[12px]">{value}</code>
        <Button
          size="sm"
          onClick={async () => {
            await navigator.clipboard.writeText(value).catch(() => {});
            setCopied(true);
            setTimeout(() => setCopied(false), 1400);
          }}
        >
          {copied ? <IconCheck size={14} /> : <IconCopy size={14} />} {copied ? "Copied" : "Copy"}
        </Button>
      </div>
    </div>
  );
}

function OrgChips({ orgs, others }: { orgs: SlackOrg[]; others: number }) {
  return (
    <span className="flex flex-wrap items-center gap-1.5">
      {orgs.map((o, i) => (
        <span key={o.slug} className="inline-flex items-center gap-1.5 rounded-[7px] border border-line bg-surface px-1.5 py-0.5 text-[12px]">
          {orgs.length > 1 ? <span className="font-mono text-[10.5px] text-ink-3">{i + 1}</span> : null}
          <OrgMark name={o.name} hue={o.hue} size={14} />
          {o.name}
        </span>
      ))}
      {others ? (
        <span className="text-[12px] text-ink-3">
          +{others} other{others === 1 ? "" : "s"}
        </span>
      ) : null}
      {!orgs.length && !others ? <span className="text-[12px] text-ink-3">No organization: the bot won&apos;t answer here</span> : null}
    </span>
  );
}

function WorkspaceCard({ ws }: { ws: SlackWorkspace }) {
  const { org } = useCurrentOrg();
  const qc = useQueryClient();
  const channels = useSlackChannels(org.slug, ws.id);
  const [editing, setEditing] = useState<SlackChannel | "new" | null>(null);
  const others = ws.orgs.filter((o) => o.slug !== org.slug);

  async function disconnect() {
    const last = ws.orgs.length + ws.other_org_count <= 1;
    const ok = await confirmAction({
      title: `Disconnect ${ws.team_name}?`,
      body: last
        ? `Strata stops answering in ${ws.team_name} and its bot token is revoked. Channel settings and linked accounts there are removed.`
        : `People in ${ws.team_name} can no longer ask ${org.name}, and ${org.name} is removed from every channel's scope there. Other organizations stay connected.`,
      confirmLabel: "Disconnect",
      danger: true,
    });
    if (!ok) return;
    try {
      await api.disconnectSlack(org.slug, ws.id);
      qc.invalidateQueries({ queryKey: qk.slack(org.slug) });
      toast(`Disconnected ${ws.team_name}`);
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Could not disconnect");
    }
  }

  async function reset(ch: SlackChannel) {
    const ok = await confirmAction({
      title: `Reset #${ch.channel_name ?? ch.channel_id}?`,
      body: ws.default_org
        ? `The channel goes back to searching ${ws.default_org.name}, like every channel without its own setting.`
        : ws.default_elsewhere
          ? "The channel goes back to searching this workspace's default organization."
          : "The channel stops answering until it gets its own setting: this workspace has no default organization.",
      confirmLabel: "Reset channel",
    });
    if (!ok) return;
    try {
      await api.resetSlackChannel(org.slug, ws.id, ch.channel_id);
      qc.invalidateQueries({ queryKey: qk.slackChannels(org.slug, ws.id) });
    } catch (e) {
      toast.error(e instanceof ApiError ? e.message : "Could not reset the channel");
    }
  }

  return (
    <Card className="p-5">
      <div className="flex items-start gap-3">
        <span className="grid h-9 w-9 shrink-0 place-items-center rounded-[10px] border border-line bg-surface-2/60 text-ink-2">
          <IconChannel size={18} />
        </span>
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-[15px] font-semibold">{ws.team_name}</h2>
          <p className="truncate font-mono text-[11px] text-ink-3">
            {ws.team_id} · connected {relativeTime(ws.connected_at)}
            {ws.connected_by ? ` by ${ws.connected_by.name}` : ""}
          </p>
        </div>
        <Tooltip label="Disconnect">
          <Button variant="ghost" size="icon-sm" aria-label={`Disconnect ${ws.team_name}`} onClick={disconnect}>
            <IconTrash size={15} />
          </Button>
        </Tooltip>
      </div>

      <dl className="mt-4 grid gap-x-4 gap-y-2 text-[13px] sm:grid-cols-[180px_1fr]">
        <dt className="text-ink-3">Channels without a setting</dt>
        <dd>
          {ws.default_org ? (
            <OrgChips orgs={[ws.default_org]} others={0} />
          ) : (
            <span className="text-ink-3">{ws.default_elsewhere ? "Another organization" : "None: the organization that connected first has disconnected"}</span>
          )}
        </dd>
        <dt className="text-ink-3">Direct messages</dt>
        <dd className="text-ink-2">Every connected organization the asker belongs to</dd>
        {others.length || ws.other_org_count ? (
          <>
            <dt className="text-ink-3">Also connected</dt>
            <dd>
              <OrgChips orgs={others} others={ws.other_org_count} />
            </dd>
          </>
        ) : null}
      </dl>

      <div className="mt-5 flex items-center justify-between gap-3">
        <div>
          <h3 className="text-[13.5px] font-semibold">Channel scopes</h3>
          <p className="text-[12.5px] text-ink-3">Choose which organizations a channel searches, e.g. a Slack Connect channel shared with a client.</p>
        </div>
        <Button size="sm" onClick={() => setEditing("new")}>
          <IconPlus size={14} /> Set up a channel
        </Button>
      </div>
      {channels.isLoading ? (
        <Skeleton className="mt-3 h-12 rounded-[12px]" />
      ) : channels.data?.length ? (
        <ul className="mt-3 divide-y divide-line rounded-[12px] border border-line">
          {channels.data.map((ch) => (
            <li key={ch.channel_id} className="flex flex-wrap items-center gap-3 px-3.5 py-2.5 text-[13px]">
              <div className="min-w-[140px]">
                <div className="font-medium">#{ch.channel_name ?? ch.channel_id}</div>
                <div className="font-mono text-[11px] text-ink-3">
                  {ch.channel_id}
                  {ch.updated_by ? ` · ${ch.updated_by.name}, ${relativeTime(ch.updated_at)}` : ""}
                </div>
              </div>
              <div className="min-w-0 flex-1">
                <OrgChips orgs={ch.orgs} others={ch.other_org_count} />
              </div>
              <div className="flex gap-1">
                <Tooltip label="Edit">
                  <Button variant="ghost" size="icon-sm" aria-label={`Edit #${ch.channel_name ?? ch.channel_id}`} onClick={() => setEditing(ch)}>
                    <IconPencil size={15} />
                  </Button>
                </Tooltip>
                <Tooltip label="Reset to default">
                  <Button variant="ghost" size="icon-sm" aria-label={`Reset #${ch.channel_name ?? ch.channel_id}`} onClick={() => reset(ch)}>
                    <IconTrash size={15} />
                  </Button>
                </Tooltip>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-3 text-[12.5px] text-ink-3">No channel has its own setting yet.</p>
      )}

      {editing ? <ChannelDialog ws={ws} channel={editing === "new" ? null : editing} onClose={() => setEditing(null)} /> : null}
    </Card>
  );
}

function ChannelDialog({ ws, channel, onClose }: { ws: SlackWorkspace; channel: SlackChannel | null; onClose: () => void }) {
  const { org } = useCurrentOrg();
  const qc = useQueryClient();
  const [channelId, setChannelId] = useState(channel?.channel_id ?? "");
  const [channelName, setChannelName] = useState(channel?.channel_name ?? "");
  const [manual, setManual] = useState(false);
  const [selected, setSelected] = useState<string[]>(channel ? channel.orgs.map((o) => o.slug) : [org.slug]);
  const bot = useQuery({
    queryKey: qk.slackBotChannels(org.slug, ws.id),
    queryFn: () => api.slackBotChannels(org.slug, ws.id),
    enabled: !channel,
    retry: false,
  });
  const pickable = !channel && !manual && !bot.isError && (bot.data?.length ?? 0) > 0;

  const save = useMutation({
    mutationFn: () =>
      api.setSlackChannel(org.slug, ws.id, channelId.trim(), {
        org_slugs: selected,
        channel_name: channelName.trim().replace(/^#/, "") || null,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.slackChannels(org.slug, ws.id) });
      qc.invalidateQueries({ queryKey: qk.slackBotChannels(org.slug, ws.id) });
      toast.success("Channel scope saved");
      onClose();
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Could not save"),
  });

  const toggle = (slug: string) => setSelected((s) => (s.includes(slug) ? s.filter((x) => x !== slug) : [...s, slug]));
  const kept = new Set(channel?.orgs.map((o) => o.slug) ?? []);

  return (
    <Dialog
      open
      onOpenChange={(v) => !v && onClose()}
      title={channel ? `#${channel.channel_name ?? channel.channel_id}` : "Set up a channel"}
      description="The bot answers in this channel from the organizations you pick, in this order: the first one the asker belongs to holds the conversation."
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" loading={save.isPending} disabled={!channelId.trim() || !selected.length} onClick={() => save.mutate()}>
            Save
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {channel ? null : pickable ? (
          <Field
            label="Channel"
            htmlFor="slack-channel"
            hint={
              <>
                Channels the bot has been added to.{" "}
                <button type="button" className="underline underline-offset-2 hover:text-ink" onClick={() => setManual(true)}>
                  Enter an ID instead
                </button>
              </>
            }
          >
            <Select
              id="slack-channel"
              value={channelId || undefined}
              placeholder="Pick a channel"
              onValueChange={(id) => {
                setChannelId(id);
                setChannelName(bot.data?.find((c) => c.id === id)?.name ?? "");
              }}
              options={(bot.data ?? []).map((c) => ({
                value: c.id,
                label: `#${c.name}`,
                hint: [c.is_private ? "Private" : null, c.mapped ? "already set up" : null].filter(Boolean).join(" · ") || undefined,
              }))}
            />
          </Field>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2">
            <Field
              label="Channel ID"
              htmlFor="slack-channel-id"
              hint={bot.isError ? "Couldn't list the bot's channels; enter the ID (channel details → About)." : "From the channel's details, e.g. C07ABC123."}
            >
              <Input id="slack-channel-id" placeholder="C07ABC123" value={channelId} onChange={(e) => setChannelId(e.target.value.toUpperCase())} />
            </Field>
            <Field label="Name" htmlFor="slack-channel-name" hint="Optional, for this list.">
              <Input id="slack-channel-name" placeholder="client-acme" value={channelName} onChange={(e) => setChannelName(e.target.value)} />
            </Field>
          </div>
        )}

        <div>
          <div className="mb-2 text-[13px] font-medium text-ink-2">Searches</div>
          <ul className="rounded-[12px] border border-line p-1">
            {ws.orgs.map((o) => {
              const position = selected.indexOf(o.slug);
              const on = position >= 0;
              // Adding an organization takes an admin of it; removing one you can't add back is allowed.
              const locked = !on && !kept.has(o.slug) && !atLeast(o.role, "admin");
              return (
                <li key={o.slug}>
                  <button
                    type="button"
                    role="checkbox"
                    aria-checked={on}
                    aria-label={o.name}
                    disabled={locked}
                    onClick={() => toggle(o.slug)}
                    className="flex w-full items-center gap-2.5 rounded-[8px] px-2 py-1.5 text-left hover:bg-surface-2 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:bg-transparent"
                  >
                    <OrgMark name={o.name} hue={o.hue} size={22} />
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-[13px] font-medium">{o.name}</span>
                      <span className="block truncate text-[11.5px] text-ink-3">
                        {locked ? `Only an admin of ${o.name} can add it` : `You're ${/^[aeiou]/.test(o.role) ? "an" : "a"} ${o.role} here`}
                      </span>
                    </span>
                    {on && selected.length > 1 ? <span className="font-mono text-[11px] text-ink-3">{position + 1}</span> : null}
                    <span className={cn("grid h-4 w-4 place-items-center rounded-[5px] border", on ? "border-ink bg-ink text-bg" : "border-line-strong")}>
                      {on ? <IconCheck size={12} strokeWidth={2} /> : null}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
          {channel?.other_org_count ? (
            <p className="mt-2 text-[12px] text-ink-3">
              Also searches {channel.other_org_count} organization{channel.other_org_count === 1 ? "" : "s"} you&apos;re not in. Those
              stay as they are, after yours.
            </p>
          ) : (
            <p className="mt-2 text-[12px] text-ink-3">Only organizations that connected {ws.team_name} are listed.</p>
          )}
        </div>
      </div>
    </Dialog>
  );
}
