import type { SourceRef } from "@/lib/types";

/** A document as a citation target, so the shared document sheet can open it. */
export function docSource(
  doc: { id: string; title: string; collection_name: string | null; url?: string | null },
  org: { slug: string; name: string; hue: number },
): SourceRef {
  return {
    ref: 0,
    origin: "knowledge",
    document_id: doc.id,
    chunk_id: null,
    attachment_id: null,
    title: doc.title,
    url: doc.url ?? null,
    snippet: "",
    org_id: null,
    org_slug: org.slug,
    org_name: org.name,
    org_hue: org.hue,
    collection_name: doc.collection_name,
    source_kind: null,
    score: 0,
  };
}
