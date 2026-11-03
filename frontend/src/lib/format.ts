export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "";
  const total = Math.floor(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

export function formatDateTime(iso: string | null | undefined, locale?: string): string {
  if (!iso) return "";
  return new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(new Date(iso));
}

const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["day", 86_400_000],
  ["hour", 3_600_000],
  ["minute", 60_000],
];

export function formatRelative(iso: string, now: Date = new Date(), locale?: string): string {
  const diff = new Date(iso).getTime() - now.getTime();
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "always" });
  for (const [unit, size] of UNITS) {
    if (Math.abs(diff) >= size) return rtf.format(Math.round(diff / size), unit);
  }
  return rtf.format(Math.round(diff / 1000), "second");
}

/** A calendar date ("2026-03-01") as "Mar 1", in the browser's zone-free sense. */
export function formatDay(day: string, locale?: string): string {
  const [y, m, d] = day.split("-").map(Number);
  return new Intl.DateTimeFormat(locale, { month: "short", day: "numeric" }).format(new Date(y ?? 0, (m ?? 1) - 1, d ?? 1));
}

export function formatSongs(n: number): string {
  return `${n} ${n === 1 ? "song" : "songs"}`;
}

/** A run's changes as "+2 −1". */
export function formatChangeCounts({ added, removed }: { added: number; removed: number }): string {
  return `+${added} −${removed}`;
}

export function formatBackupResult(r: { saved: number; unchanged: number; archived: number; unreadable: number }): string {
  return `${r.saved} saved, ${r.unchanged} unchanged, ${r.archived} archived, ${r.unreadable} unreadable`;
}

/** The run history label for a run kind. */
export function runKindLabel(kind: string): string {
  return kind === "copy_to_source" ? "Copied to source" : kind;
}
