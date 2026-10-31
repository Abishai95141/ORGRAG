"use client";

import Link from "next/link";
import { use, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { IconChannel, IconCheck, IconTrash, IconWarning } from "@/components/icons";
import { Button, Skeleton, Tooltip } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { qk, useMe, useSlackLinks } from "@/lib/queries";
import type { SlackLink } from "@/lib/types";
import { relativeTime } from "@/lib/utils";

/**
 * Where the Slack bot sends someone it doesn't recognise. Signed in, they confirm
 * that the Slack account is theirs; the bot then answers it as them, with their
 * permissions. The link is signed, expires after an hour, and works once.
 */
export default function SlackLinkPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = use(params);
  const router = useRouter();
  const qc = useQueryClient();
  const me = useMe();
  const preview = useQuery({ queryKey: ["slack-link", token], queryFn: () => api.slackLink(token), retry: false });
  const links = useSlackLinks(!!me.data);

  const unauthenticated = preview.error instanceof ApiError && preview.error.status === 401;
  useEffect(() => {
    if (unauthenticated) router.replace(`/login?next=${encodeURIComponent(`/slack/link/${token}`)}`);
  }, [unauthenticated, router, token]);

  const confirm = useMutation({
    mutationFn: () => api.confirmSlackLink(token),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["slack-link", token] });
      qc.invalidateQueries({ queryKey: qk.slackLinks });
      toast.success("Slack account connected");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Could not connect the account"),
  });

  if (preview.isLoading || unauthenticated) return <Skeleton className="h-48 w-full" />;
  if (!preview.data)
    return (
      <div>
        <h1 className="font-display text-[36px] leading-none tracking-tight">Link not available</h1>
        <p className="mt-3 text-[14px] text-ink-3">
          {preview.error instanceof ApiError ? preview.error.message : "This link couldn't be opened"}. Ask the Strata bot in Slack
          again for a new one.
        </p>
      </div>
    );

  const p = preview.data;
  // The email is Slack's own record of the account; the display name is whatever its owner typed.
  const who = p.slack_email ?? (p.slack_user_name ? `@${p.slack_user_name}` : "This Slack account");
  return (
    <div className="flex flex-col gap-6">
      <span className="grid h-[52px] w-[52px] place-items-center rounded-[14px] border border-line bg-surface-2/60 text-ink-2">
        <IconChannel size={26} />
      </span>
      <div>
        <p className="font-mono text-[11px] tracking-wider text-ink-3 uppercase">Slack · {p.team_name}</p>
        <h1 className="mt-2 font-display text-[38px] leading-[1.05] tracking-tight">Connect your Slack account</h1>
        <p className="mt-3 text-[14px] leading-relaxed text-ink-3">
          <span className="font-medium text-ink">{who}</span> in {p.team_name} will be able to ask Strata as{" "}
          <span className="font-medium text-ink">{me.data?.user.email}</span>, and see what you can see. Only confirm if that Slack
          account is yours and you asked the bot yourself: anyone who sends you this link could be asking to act as you.
        </p>
      </div>

      {p.linked === "you" ? (
        <p className="flex items-center gap-2 rounded-[10px] bg-ok-soft px-3 py-2 text-[13px] text-ok">
          <IconCheck size={15} /> Connected. Ask the bot your question again in Slack.
        </p>
      ) : p.linked === "other" ? (
        <Notice>This Slack account is already connected to another Strata account. It has to be disconnected there first.</Notice>
      ) : p.expired ? (
        <Notice>This link has expired. Ask the Strata bot in Slack again for a new one.</Notice>
      ) : p.used ? (
        <Notice>This link was already used. Ask the Strata bot in Slack again for a new one.</Notice>
      ) : !p.eligible ? (
        <Notice>
          None of your organizations has connected {p.team_name}. Ask an admin of your organization to connect it under Settings →
          Slack.
        </Notice>
      ) : (
        <Button variant="primary" size="lg" className="justify-center" loading={confirm.isPending} onClick={() => confirm.mutate()}>
          It&apos;s me — connect
        </Button>
      )}

      {links.data?.length ? <LinkedAccounts links={links.data} /> : null}
      {me.data?.orgs.length ? (
        <Link href={`/o/${me.data.orgs[0].slug}/chat`} className="text-[13px] text-ink-3 underline decoration-line-strong underline-offset-4 hover:text-ink">
          Go to Strata
        </Link>
      ) : null}
    </div>
  );
}

function Notice({ children }: { children: React.ReactNode }) {
  return (
    <p className="flex items-start gap-2 rounded-[10px] bg-warn-soft px-3 py-2 text-[13px] text-warn">
      <IconWarning size={15} className="mt-0.5 shrink-0" />
      <span>{children}</span>
    </p>
  );
}

function LinkedAccounts({ links }: { links: SlackLink[] }) {
  const qc = useQueryClient();
  const unlink = useMutation({
    mutationFn: (id: string) => api.unlinkSlack(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: qk.slackLinks });
      qc.invalidateQueries({ queryKey: ["slack-link"] });
      toast("Slack account disconnected");
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Could not disconnect"),
  });
  return (
    <div>
      <div className="mb-2 font-mono text-[10.5px] tracking-wider text-ink-3 uppercase">Slack accounts connected to you</div>
      <ul className="divide-y divide-line rounded-[12px] border border-line">
        {links.map((link) => (
          <li key={link.id} className="flex items-center gap-3 px-3 py-2 text-[13px]">
            <div className="min-w-0 flex-1">
              <div className="truncate font-medium">{link.slack_user_name ? `@${link.slack_user_name}` : link.slack_user_id}</div>
              <div className="truncate text-[11.5px] text-ink-3">
                {link.team_name} · {relativeTime(link.created_at)}
              </div>
            </div>
            <Tooltip label="Disconnect">
              <Button
                variant="ghost"
                size="icon-sm"
                aria-label={`Disconnect ${link.slack_user_name ?? link.slack_user_id}`}
                loading={unlink.isPending && unlink.variables === link.id}
                onClick={() => unlink.mutate(link.id)}
              >
                {unlink.isPending && unlink.variables === link.id ? null : <IconTrash size={15} />}
              </Button>
            </Tooltip>
          </li>
        ))}
      </ul>
    </div>
  );
}
