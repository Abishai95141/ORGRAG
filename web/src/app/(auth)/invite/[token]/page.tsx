"use client";

import Link from "next/link";
import { use } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { OrgMark } from "@/components/illustrations";
import { Button, Skeleton } from "@/components/ui";
import { api, ApiError } from "@/lib/api";
import { qk, useMe } from "@/lib/queries";

export default function InvitePage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = use(params);
  const router = useRouter();
  const qc = useQueryClient();
  const me = useMe();
  const invite = useQuery({ queryKey: ["invite", token], queryFn: () => api.invite(token), retry: false });

  const accept = useMutation({
    mutationFn: () => api.acceptInvite(token),
    onSuccess: async (org) => {
      await qc.invalidateQueries({ queryKey: qk.me });
      toast.success(`You joined ${org.name}`);
      router.replace(`/o/${org.slug}/chat`);
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.message : "Could not accept invite"),
  });

  if (invite.isLoading) return <Skeleton className="h-48 w-full" />;
  if (invite.isError || !invite.data)
    return (
      <div>
        <h1 className="font-display text-[36px] leading-none tracking-tight">Invite not found</h1>
        <p className="mt-3 text-[14px] text-ink-3">This link may have been revoked. Ask an admin for a new one.</p>
      </div>
    );

  const { org, email, role, expired } = invite.data;
  const next = encodeURIComponent(`/invite/${token}`);

  return (
    <div className="flex flex-col gap-6">
      <OrgMark name={org.name} hue={org.hue} size={52} />
      <div>
        <p className="font-mono text-[11px] tracking-wider text-ink-3 uppercase">Invitation</p>
        <h1 className="mt-2 font-display text-[38px] leading-[1.05] tracking-tight">Join {org.name}</h1>
        <p className="mt-3 text-[14px] leading-relaxed text-ink-3">
          {email} was invited as <span className="font-medium text-ink">{role}</span>. You&apos;ll be able to ask questions across{" "}
          {org.name}&apos;s knowledge alongside your other organizations.
        </p>
      </div>
      {expired ? (
        <p className="rounded-[10px] bg-warn-soft px-3 py-2 text-[13px] text-warn">This invitation has expired.</p>
      ) : me.data ? (
        <Button variant="primary" size="lg" className="justify-center" loading={accept.isPending} onClick={() => accept.mutate()}>
          Accept and join
        </Button>
      ) : (
        <div className="flex gap-2">
          <Link href={`/signup?next=${next}`} className="flex-1">
            <Button variant="primary" size="lg" className="w-full justify-center">Create account</Button>
          </Link>
          <Link href={`/login?next=${next}`} className="flex-1">
            <Button size="lg" className="w-full justify-center">Sign in</Button>
          </Link>
        </div>
      )}
    </div>
  );
}
