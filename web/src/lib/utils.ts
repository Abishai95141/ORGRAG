import clsx, { type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";
import type { Role } from "./types";

// Teach tailwind-merge our design tokens so e.g. `text-ink-3` and `text-[13px]` don't collide.
const twMerge = extendTailwindMerge({
  extend: {
    theme: {
      color: ["bg", "surface", "surface-2", "surface-3", "line", "line-strong", "ink", "ink-2", "ink-3", "accent", "accent-hover", "accent-ink", "accent-soft", "ok", "ok-soft", "warn", "warn-soft", "err", "err-soft", "chart"],
    },
  },
});

export const cn = (...v: ClassValue[]) => twMerge(clsx(v));

/** Org identity colour. Lightness/saturation come from the theme tokens. */
export const orgColor = (hue: number, alpha = 1) =>
  alpha === 1 ? `hsl(${hue} var(--org-s) var(--org-l))` : `hsl(${hue} var(--org-s) var(--org-l) / ${alpha})`;

/** Curated identity hues — chosen to stay distinct in both themes. */
export const HUES = [24, 352, 42, 96, 168, 196, 214, 262];

export const ROLE_RANK: Record<Role, number> = { viewer: 0, member: 1, admin: 2, owner: 3 };
export const atLeast = (role: Role | undefined, min: Role) => !!role && ROLE_RANK[role] >= ROLE_RANK[min];

const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
export function relativeTime(iso: string | null | undefined) {
  if (!iso) return "never";
  const diff = (new Date(iso).getTime() - Date.now()) / 1000;
  const abs = Math.abs(diff);
  if (abs < 45) return "just now";
  if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour");
  if (abs < 86400 * 7) return rtf.format(Math.round(diff / 86400), "day");
  return new Date(iso).toLocaleDateString("en", { month: "short", day: "numeric" });
}

export function formatNumber(n: number) {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `${Math.round(n / 1000)}k`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

export function initials(name: string) {
  const parts = name.trim().split(/\s+/);
  return ((parts[0]?.[0] ?? "") + (parts.length > 1 ? (parts.at(-1)?.[0] ?? "") : "")).toUpperCase() || "?";
}

export function slugify(s: string) {
  return s
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40);
}

/** Group conversations into Today / Yesterday / This week / Earlier. */
export function dayBucket(iso: string) {
  const d = new Date(iso);
  const now = new Date();
  const startOfToday = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const t = d.getTime();
  if (t >= startOfToday) return "Today";
  if (t >= startOfToday - 86400000) return "Yesterday";
  if (t >= startOfToday - 86400000 * 6) return "This week";
  return "Earlier";
}

export function hostname(url: string | null) {
  if (!url) return null;
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return null;
  }
}

export function formatBytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
