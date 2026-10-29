import { IconFile, IconGlobe, IconRepo } from "@/components/icons";
import type { SourceKind } from "@/lib/types";

export function SourceKindIcon({ kind, size = 15, className }: { kind: SourceKind; size?: number; className?: string }) {
  const Icon = kind === "web" ? IconGlobe : kind === "github" ? IconRepo : IconFile;
  return <Icon size={size} className={className} />;
}

export const SOURCE_KIND_LABEL: Record<SourceKind, string> = {
  upload: "Files",
  web: "Website",
  github: "GitHub",
};
