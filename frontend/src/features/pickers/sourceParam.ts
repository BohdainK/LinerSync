/**
 * Reads `?source=` on the New sync and New transfer pages: `backup:<entryId>` for an archived
 * playlist, or `<service>:<playlistId>`. Only the first ":" separates, since ids may contain one.
 */
export function parseSourceParam(raw: string | null): { service: string; playlistId: string } | null {
  if (!raw) return null;
  const i = raw.indexOf(":");
  if (i <= 0 || i === raw.length - 1) return null;
  return { service: raw.slice(0, i), playlistId: raw.slice(i + 1) };
}
