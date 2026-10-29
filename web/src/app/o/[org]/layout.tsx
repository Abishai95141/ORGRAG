"use client";

import { use, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { IconSidebar } from "@/components/icons";
import { NoResultsArt, OrgMark, StrataMark } from "@/components/illustrations";
import { OrgContext } from "@/components/layout/org-context";
import { Sidebar } from "@/components/layout/sidebar";
import { Button, EmptyState } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { useMe } from "@/lib/queries";
import { cn } from "@/lib/utils";

export default function OrgLayout({ children, params }: { children: React.ReactNode; params: Promise<{ org: string }> }) {
  const { org: slug } = use(params);
  const me = useMe();
  const router = useRouter();
  const pathname = usePathname();
  const [drawer, setDrawer] = useState(false);

  useEffect(() => {
    if (me.error instanceof ApiError && me.error.status === 401) {
      router.replace(`/login?next=${encodeURIComponent(pathname)}`);
    }
  }, [me.error, pathname, router]);

  useEffect(() => {
    try {
      localStorage.setItem("strata-last-org", slug);
    } catch {}
  }, [slug]);

  useEffect(() => setDrawer(false), [pathname]);

  if (!me.data) {
    return (
      <main className="grid min-h-screen place-items-center">
        <StrataMark size={32} className="strata-pulse text-ink" />
      </main>
    );
  }

  const org = me.data.orgs.find((o) => o.slug === slug);
  if (!org) {
    return (
      <main className="grid min-h-screen place-items-center">
        <EmptyState
          art={<NoResultsArt />}
          title="No access to this organization"
          description="It may not exist, or you haven't been invited yet. Ask one of its admins for an invite link."
          action={
            <Link href="/">
              <Button variant="primary">Go to my organizations</Button>
            </Link>
          }
        />
      </main>
    );
  }

  return (
    <OrgContext.Provider value={{ org, orgs: me.data.orgs, user: me.data.user }}>
      <div className="flex h-dvh overflow-hidden">
        <aside className="hidden w-[260px] shrink-0 border-r border-line md:block">
          <Sidebar />
        </aside>

        {/* Mobile drawer */}
        <div
          className={cn("fixed inset-0 z-40 bg-[rgb(20_19_16/0.3)] transition-opacity md:hidden", drawer ? "opacity-100" : "pointer-events-none opacity-0")}
          onClick={() => setDrawer(false)}
          aria-hidden="true"
        />
        <aside
          className={cn(
            "fixed inset-y-0 left-0 z-50 w-[280px] border-r border-line bg-bg transition-transform duration-200 md:hidden",
            drawer ? "translate-x-0" : "-translate-x-full",
          )}
        >
          <Sidebar onNavigate={() => setDrawer(false)} />
        </aside>

        <div className="flex min-w-0 flex-1 flex-col">
          <header className="flex h-12 items-center gap-2 border-b border-line px-3 md:hidden">
            <Button variant="ghost" size="icon-sm" aria-label="Open navigation" onClick={() => setDrawer(true)}>
              <IconSidebar size={18} />
            </Button>
            <OrgMark name={org.name} hue={org.hue} size={22} />
            <span className="truncate text-[14px] font-semibold">{org.name}</span>
          </header>
          <div className="min-h-0 flex-1">{children}</div>
        </div>
      </div>
    </OrgContext.Provider>
  );
}
