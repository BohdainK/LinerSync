import type { LimitOut } from "@/api/types";
import { Section } from "@/components/Section";
import { Progress } from "@/components/ui/progress";
import { brandStyle } from "@/lib/brand";
import { formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";

const NEAR_LIMIT = 0.9;

export function ApiLimits({ limits }: { limits: LimitOut[] }) {
  if (limits.length === 0) return null;
  return (
    <Section title="API limits today">
      <ul className="grid grid-cols-[repeat(auto-fill,minmax(16rem,1fr))] gap-3">
        {limits.map((l) => (
          <li
            key={l.service}
            style={brandStyle(l.service)}
            className="brand-wash flex flex-col gap-2 rounded-lg border p-4 text-sm"
          >
            <div className="flex items-baseline justify-between gap-2">
              <span className="truncate font-medium">{l.display_name}</span>
              <span className="shrink-0 tabular-nums text-muted-foreground">
                {l.limit === null ? `${l.used} used` : `${l.used} of ${l.limit}`}
              </span>
            </div>
            {l.limit === null ? (
              <p className="text-muted-foreground">No daily limit</p>
            ) : (
              <LimitBar limit={l} max={l.limit} />
            )}
          </li>
        ))}
      </ul>
    </Section>
  );
}

function LimitBar({ limit: l, max }: { limit: LimitOut; max: number }) {
  const ratio = max > 0 ? l.used / max : 1;
  const resets = formatRelative(l.resets_at);
  const status =
    ratio >= 1
      ? `Limit reached, resets ${resets}`
      : ratio >= NEAR_LIMIT
        ? `Almost at the limit, resets ${resets}`
        : `Resets ${resets}`;
  return (
    <>
      <Progress
        value={Math.min(ratio, 1) * 100}
        aria-valuetext={`${l.used} of ${max}`}
        aria-label={`${l.display_name} requests used today`}
        className={cn(
          "bg-[color-mix(in_oklab,var(--brand)_18%,transparent)]",
          ratio >= 1
            ? "[&>[data-slot=progress-indicator]]:bg-destructive"
            : "[&>[data-slot=progress-indicator]]:bg-(--brand)",
        )}
      />
      <p className={cn("text-muted-foreground", ratio >= 1 && "text-destructive")}>{status}</p>
    </>
  );
}
