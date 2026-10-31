"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { IconChannel, IconSliders } from "@/components/icons";
import { cn } from "@/lib/utils";

/** Tabs across the settings pages: the org's own settings, then integrations. */
export function SettingsNav({ slug }: { slug: string }) {
  const pathname = usePathname();
  const base = `/o/${slug}/settings`;
  const tabs = [
    { href: base, label: "General", icon: IconSliders },
    { href: `${base}/slack`, label: "Slack", icon: IconChannel },
  ];
  return (
    <nav aria-label="Settings" className="mb-6 flex gap-1 border-b border-line">
      {tabs.map((tab) => {
        const active = pathname === tab.href;
        return (
          <Link
            key={tab.href}
            href={tab.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "-mb-px inline-flex h-9 items-center gap-1.5 border-b-2 px-2.5 text-[13.5px] transition-colors",
              active ? "border-ink font-medium text-ink" : "border-transparent text-ink-3 hover:text-ink-2",
            )}
          >
            <tab.icon size={15} className={active ? "text-accent" : undefined} />
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
