import { IconScan } from "@/components/icons";
import type { DocumentRow } from "@/lib/types";

/** Pages the parser may have read poorly: weak (scanned, or mostly pictures) and not re-read by the vision pass. */
export function weakPages(doc: Pick<DocumentRow, "parse">): number[] {
  const reread = new Set(doc.parse?.vision_pages ?? []);
  return (doc.parse?.weak_pages ?? []).filter((n) => !reread.has(n));
}

/** "Page 3", "Pages 2 and 3", "Pages 1, 4, 5, 8, 9 and 12 more". */
function pageList(pages: number[]): string {
  const shown = pages.slice(0, 5);
  const more = pages.length - shown.length;
  if (shown.length === 1) return `Page ${shown[0]}`;
  if (more) return `Pages ${shown.join(", ")} and ${more} more`;
  return `Pages ${shown.slice(0, -1).join(", ")} and ${shown.at(-1)}`;
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** For admins: pages the parser flagged, so a scan that lost text isn't silent. */
export function WeakPagesNotice({ doc }: { doc: DocumentRow }) {
  const pages = weakPages(doc);
  if (!pages.length) return null;
  const one = pages.length === 1;
  const them = one ? "it" : "them";
  return (
    <div className="flex items-start gap-2.5 border-b border-line bg-surface-2/50 px-5 py-3 text-[13px] leading-relaxed">
      <IconScan size={15} className="mt-0.5 shrink-0 text-warn" />
      <p className="text-ink-3">
        <span className="font-medium text-ink-2">{plural(pages.length, "page")} may have been read poorly. </span>
        {pageList(pages)} {one ? "looks scanned or is" : "look scanned or are"} mostly pictures, so some of{" "}
        {one ? "its" : "their"} text may be missing from search and answers.{" "}
        {doc.parse?.vision_pages
          ? `Vision parsing couldn't re-read ${them}.`
          : `To re-read ${them}, turn on vision parsing (the server's VISION_PARSING setting), then remove this document and upload or sync it again.`}
      </p>
    </div>
  );
}

/** For admins, in document lists: a quiet line under the title. */
export function WeakPagesLine({ doc }: { doc: DocumentRow }) {
  const pages = weakPages(doc);
  if (!pages.length) return null;
  return (
    <div className="flex min-w-0 items-center gap-1 text-[11.5px] text-warn">
      <IconScan size={11} className="shrink-0" />
      <span className="truncate">{pageList(pages)} may have been read poorly</span>
    </div>
  );
}
