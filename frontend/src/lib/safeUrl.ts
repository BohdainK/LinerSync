/** Returns the link only when it is a plain http(s) URL; anything else renders as text. */
export function safeHref(href: string | null | undefined): string | null {
  if (!href) return null;
  try {
    const { protocol } = new URL(href);
    return protocol === "https:" || protocol === "http:" ? href : null;
  } catch {
    return null;
  }
}
