import type { LimitOut } from "@/api/types";
import { Section } from "@/components/Section";
import { formatRelative } from "@/lib/format";

export function ApiLimits({ limits }: { limits: LimitOut[] }) {
  if (limits.length === 0) return null;
  return (
    <Section title="API limits today">
      <ul className="space-y-1 text-sm">
        {limits.map((l) => (
          <li key={l.service}>
            {l.limit === null
              ? `${l.display_name}: ${l.used} (no daily limit)`
              : `${l.display_name}: ${l.used} of ${l.limit}`}
            <span className="text-muted-foreground">, resets {formatRelative(l.resets_at)}</span>
          </li>
        ))}
      </ul>
    </Section>
  );
}
