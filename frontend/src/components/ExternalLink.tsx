import type { ReactNode } from "react";
import { safeHref } from "@/lib/safeUrl";

/** A link from the server: rendered only for http(s) URLs, otherwise as plain text. */
export function ExternalLink({ href, children }: { href: string; children: ReactNode }) {
  const safe = safeHref(href);
  if (!safe) return <span>{children}</span>;
  return (
    <a href={safe} target="_blank" rel="noreferrer" className="text-primary underline underline-offset-4">
      {children}
    </a>
  );
}
