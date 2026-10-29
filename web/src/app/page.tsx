"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { StrataMark } from "@/components/illustrations";
import { useMe } from "@/lib/queries";

export default function Home() {
  const router = useRouter();
  const me = useMe();

  useEffect(() => {
    if (me.isError) router.replace("/login");
    if (!me.data) return;
    if (!me.data.orgs.length) return router.replace("/onboarding");
    let last: string | null = null;
    try {
      last = localStorage.getItem("strata-last-org");
    } catch {}
    const target = me.data.orgs.find((o) => o.slug === last) ?? me.data.orgs[0];
    router.replace(`/o/${target.slug}/chat`);
  }, [me.data, me.isError, router]);

  return (
    <main className="grid min-h-screen place-items-center">
      <StrataMark size={32} className="strata-pulse text-ink" />
    </main>
  );
}
