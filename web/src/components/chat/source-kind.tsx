import { IconChannel, IconDrive, IconFile, IconGlobe, IconPaperclip, IconRepo, IconWiki } from "@/components/icons";
import type { SourceKind, SourceRef } from "@/lib/types";

const KIND_ICON: Record<SourceKind, typeof IconFile> = {
  upload: IconFile,
  web: IconGlobe,
  github: IconRepo,
  gdrive: IconDrive,
  slack: IconChannel,
  notion: IconWiki,
};

export function SourceKindIcon({ kind, size = 15, className }: { kind: SourceKind | null; size?: number; className?: string }) {
  const Icon = (kind && KIND_ICON[kind]) || IconFile;
  return <Icon size={size} className={className} />;
}

/** Icon for a citation: web results get a globe, attachments a paperclip. */
export function SourceRefIcon({ source, size = 15, className }: { source: SourceRef; size?: number; className?: string }) {
  if (source.origin === "web") return <IconGlobe size={size} className={className} />;
  if (source.origin === "attachment") return <IconPaperclip size={size} className={className} />;
  return <SourceKindIcon kind={source.source_kind} size={size} className={className} />;
}

export const SOURCE_KIND_LABEL: Record<SourceKind, string> = {
  upload: "Files",
  web: "Website",
  github: "GitHub",
  gdrive: "Google Drive",
  slack: "Slack",
  notion: "Notion",
};

/** Where a citation came from, as a short label: org, "Web" or "Attached file". */
export function originLabel(source: SourceRef) {
  if (source.origin === "web") return "Web";
  if (source.origin === "attachment") return "Attached file";
  return source.org_name ?? "";
}

/** Chip colours: knowledge sources wear their org's hue; web and files stay neutral ink. */
export function citationStyle(source: SourceRef, strong = true): React.CSSProperties {
  if (source.origin !== "knowledge" || source.org_hue === null) {
    return strong
      ? { background: "var(--surface-3)", color: "var(--ink-2)", boxShadow: "inset 0 0 0 1px var(--line-strong)" }
      : { background: "var(--surface-2)", color: "var(--ink-3)" };
  }
  const hue = source.org_hue;
  return strong
    ? {
        background: `hsl(${hue} 40% var(--org-l) / .14)`,
        color: `hsl(${hue} 50% calc(var(--org-l) - 10%))`,
        boxShadow: `inset 0 0 0 1px hsl(${hue} 40% var(--org-l) / .25)`,
      }
    : { background: "var(--surface-2)", color: "var(--ink-3)" };
}
