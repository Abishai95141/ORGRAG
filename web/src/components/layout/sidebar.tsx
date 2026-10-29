"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { useMemo } from "react";
import {
  IconAgent,
  IconChat,
  IconCheck,
  IconChevronUpDown,
  IconInsights,
  IconLayers,
  IconLogout,
  IconMonitor,
  IconMoon,
  IconMore,
  IconPeople,
  IconPlus,
  IconSliders,
  IconSources,
  IconSun,
  IconTrash,
} from "@/components/icons";
import { Avatar, OrgMark, StrataMark } from "@/components/illustrations";
import { Menu, MenuContent, MenuItem, MenuLabel, MenuSeparator, MenuTrigger, Segmented } from "@/components/ui";
import { useTheme } from "@/components/theme";
import { api } from "@/lib/api";
import { qk, useConversations } from "@/lib/queries";
import { atLeast, cn, dayBucket } from "@/lib/utils";
import { useCurrentOrg } from "./org-context";

export function Sidebar({ onNavigate }: { onNavigate?: () => void }) {
  const { org, orgs, user } = useCurrentOrg();
  const pathname = usePathname();
  const base = `/o/${org.slug}`;
  const isAdmin = atLeast(org.role, "admin");

  const nav = [
    { href: `${base}/chat`, label: "Chat", icon: IconChat, match: `${base}/chat` },
    { href: `${base}/sources`, label: "Sources", icon: IconSources, match: `${base}/sources` },
    { href: `${base}/collections`, label: "Collections", icon: IconLayers, match: `${base}/collections` },
    { href: `${base}/agents`, label: "Agents", icon: IconAgent, match: `${base}/agents` },
    { href: `${base}/members`, label: "Members", icon: IconPeople, match: `${base}/members` },
    ...(isAdmin
      ? [
          { href: `${base}/insights`, label: "Insights", icon: IconInsights, match: `${base}/insights` },
          { href: `${base}/settings`, label: "Settings", icon: IconSliders, match: `${base}/settings` },
        ]
      : []),
  ];

  return (
    <nav className="flex h-full flex-col gap-1 bg-surface-2/40 px-2.5 pt-3 pb-2.5" aria-label="Primary">
      <OrgSwitcher />

      <Link
        href={`${base}/chat`}
        onClick={onNavigate}
        className="mt-2 mb-2 flex h-9 items-center gap-2 rounded-[var(--radius-ctl)] border border-line bg-surface px-3 text-[13.5px] font-medium shadow-[0_1px_0_rgba(0,0,0,.03)] transition-colors hover:border-line-strong"
      >
        <IconPlus size={16} className="text-ink-3" />
        New question
      </Link>

      <ul className="flex flex-col gap-px">
        {nav.map((item) => {
          const active = item.match === `${base}/chat` ? pathname === `${base}/chat` : pathname.startsWith(item.match);
          return (
            <li key={item.href}>
              <Link
                href={item.href}
                onClick={onNavigate}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "group flex h-8 items-center gap-2.5 rounded-[8px] px-2.5 text-[13.5px] transition-colors",
                  active ? "bg-surface font-medium text-ink shadow-[0_0_0_1px_var(--line)]" : "text-ink-2 hover:bg-surface-2 hover:text-ink",
                )}
              >
                <item.icon size={17} className={active ? "text-accent" : "text-ink-3 group-hover:text-ink-2"} />
                {item.label}
              </Link>
            </li>
          );
        })}
      </ul>

      <ConversationList onNavigate={onNavigate} />

      <UserMenu name={user.name} email={user.email} hue={user.avatar_hue} orgCount={orgs.length} />
    </nav>
  );
}

function OrgSwitcher() {
  const { org, orgs } = useCurrentOrg();
  const router = useRouter();
  return (
    <Menu>
      <MenuTrigger asChild>
        <button className="flex h-11 w-full items-center gap-2.5 rounded-[10px] px-1.5 text-left transition-colors hover:bg-surface-2 data-[state=open]:bg-surface-2">
          <OrgMark name={org.name} hue={org.hue} size={30} />
          <div className="min-w-0 flex-1">
            <div className="truncate text-[14px] leading-tight font-semibold">{org.name}</div>
            <div className="truncate font-mono text-[11px] text-ink-3">
              {org.role} · {orgs.length} org{orgs.length === 1 ? "" : "s"}
            </div>
          </div>
          <IconChevronUpDown size={16} className="text-ink-3" />
        </button>
      </MenuTrigger>
      <MenuContent className="w-64">
        <MenuLabel>Organizations</MenuLabel>
        {orgs.map((o) => (
          <MenuItem
            key={o.id}
            icon={<OrgMark name={o.name} hue={o.hue} size={18} />}
            onSelect={() => router.push(`/o/${o.slug}/chat`)}
            trailing={o.id === org.id ? <IconCheck size={14} className="text-ink-2" /> : <span className="font-mono text-[10.5px] text-ink-3">{o.role}</span>}
          >
            {o.name}
          </MenuItem>
        ))}
        <MenuSeparator />
        <MenuItem icon={<IconPlus size={15} />} onSelect={() => router.push("/onboarding")}>
          New organization
        </MenuItem>
      </MenuContent>
    </Menu>
  );
}

function ConversationList({ onNavigate }: { onNavigate?: () => void }) {
  const { org } = useCurrentOrg();
  const pathname = usePathname();
  const router = useRouter();
  const qc = useQueryClient();
  const { data, isLoading } = useConversations(org.slug);

  const groups = useMemo(() => {
    const out = new Map<string, NonNullable<typeof data>>();
    for (const c of data ?? []) {
      const b = dayBucket(c.updated_at);
      out.set(b, [...(out.get(b) ?? []), c]);
    }
    return [...out.entries()];
  }, [data]);

  const remove = useMutation({
    mutationFn: (id: string) => api.deleteConversation(org.slug, id),
    onSuccess: (_, id) => {
      qc.invalidateQueries({ queryKey: qk.conversations(org.slug) });
      if (pathname.endsWith(id)) router.push(`/o/${org.slug}/chat`);
      toast("Conversation deleted");
    },
  });

  return (
    <div className="mt-4 min-h-0 flex-1 overflow-y-auto pr-0.5">
      {isLoading ? (
        <div className="flex flex-col gap-2 px-2.5 pt-2">
          {[70, 85, 60, 75].map((w) => (
            <div key={w} className="strata-pulse h-3 rounded bg-surface-3" style={{ width: `${w}%` }} />
          ))}
        </div>
      ) : !data?.length ? (
        <p className="px-2.5 pt-2 text-[12.5px] leading-relaxed text-ink-3">Your questions will collect here.</p>
      ) : (
        groups.map(([label, items]) => (
          <div key={label} className="mb-3">
            <div className="px-2.5 pb-1 font-mono text-[10.5px] tracking-wider text-ink-3 uppercase">{label}</div>
            <ul className="flex flex-col gap-px">
              {items.map((c) => {
                const href = `/o/${org.slug}/chat/${c.id}`;
                const active = pathname === href;
                return (
                  <li key={c.id} className="group relative">
                    <Link
                      href={href}
                      onClick={onNavigate}
                      className={cn(
                        "flex h-8 items-center gap-2 rounded-[8px] pr-8 pl-2.5 text-[13px] transition-colors",
                        active ? "bg-surface text-ink shadow-[0_0_0_1px_var(--line)]" : "text-ink-2 hover:bg-surface-2 hover:text-ink",
                      )}
                    >
                      {c.scope_org_ids.length > 1 ? (
                        <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent" title="Multi-organization" />
                      ) : null}
                      <span className="truncate">{c.title || "Untitled"}</span>
                    </Link>
                    <Menu>
                      <MenuTrigger asChild>
                        <button
                          aria-label="Conversation actions"
                          className="absolute top-1 right-1 grid h-6 w-6 place-items-center rounded-md text-ink-3 opacity-0 transition-opacity group-hover:opacity-100 hover:bg-surface-3 hover:text-ink focus-visible:opacity-100 data-[state=open]:opacity-100"
                        >
                          <IconMore size={14} />
                        </button>
                      </MenuTrigger>
                      <MenuContent align="end">
                        <MenuItem danger icon={<IconTrash size={15} />} onSelect={() => remove.mutate(c.id)}>
                          Delete
                        </MenuItem>
                      </MenuContent>
                    </Menu>
                  </li>
                );
              })}
            </ul>
          </div>
        ))
      )}
    </div>
  );
}

function UserMenu({ name, email, hue, orgCount }: { name: string; email: string; hue: number; orgCount: number }) {
  const router = useRouter();
  const qc = useQueryClient();
  const { pref, set } = useTheme();

  async function logout() {
    await api.logout().catch(() => {});
    qc.clear();
    router.replace("/login");
  }

  return (
    <div className="border-t border-line pt-2">
      <Menu>
        <MenuTrigger asChild>
          <button className="flex h-11 w-full items-center gap-2.5 rounded-[10px] px-1.5 text-left transition-colors hover:bg-surface-2 data-[state=open]:bg-surface-2">
            <Avatar name={name} hue={hue} size={28} />
            <div className="min-w-0 flex-1">
              <div className="truncate text-[13px] font-medium">{name}</div>
              <div className="truncate text-[11.5px] text-ink-3">{email}</div>
            </div>
            <StrataMark size={14} className="text-ink-3" />
          </button>
        </MenuTrigger>
        <MenuContent side="top" className="w-64">
          <div className="px-2 pt-1.5 pb-2">
            <div className="mb-2 font-mono text-[10.5px] tracking-wider text-ink-3 uppercase">Appearance</div>
            <Segmented
              size="sm"
              value={pref}
              onChange={set}
              options={[
                { value: "light", label: "Light", icon: <IconSun size={14} /> },
                { value: "dark", label: "Dark", icon: <IconMoon size={14} /> },
                { value: "system", label: "Auto", icon: <IconMonitor size={14} /> },
              ]}
            />
          </div>
          <MenuSeparator />
          <div className="px-2 py-1.5 text-[12px] text-ink-3">
            Member of {orgCount} organization{orgCount === 1 ? "" : "s"}
          </div>
          <MenuItem icon={<IconLogout size={15} />} onSelect={logout}>
            Sign out
          </MenuItem>
        </MenuContent>
      </Menu>
    </div>
  );
}
