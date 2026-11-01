import type { ReactNode } from "react";
import { safeHref } from "@/lib/safeUrl";
import { linkClass } from "@/lib/styles";

/** A link from the server: rendered only for http(s) URLs, otherwise as plain text. */
export function ExternalLink({ href, children }: { href: string; children: ReactNode }) {
  const safe = safeHref(href);
  if (!safe) return <span>{children}</span>;
  return (
    <a href={safe} target="_blank" rel="noreferrer" className={linkClass}>
      {children}
    </a>
  );
}
