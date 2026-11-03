import { Link } from "react-router";
import type { NextUpOut } from "@/api/types";
import { Section } from "@/components/Section";
import { formatRelative } from "@/lib/format";
import { linkClass } from "@/lib/styles";
import { cn } from "@/lib/utils";

/** One stop on the line: the rail runs through every dot, and the first (next) stop is filled in. */
function Stop({ first, last }: { first: boolean; last: boolean }) {
  return (
    <span aria-hidden className="relative flex w-3 shrink-0 items-center justify-center self-stretch">
      <span
        className={cn(
          "absolute left-1/2 w-0.5 -translate-x-1/2 bg-primary/30",
          first ? "top-1/2" : "top-0",
          last ? "bottom-1/2" : "bottom-0",
        )}
      />
      <span className={cn("relative size-3 rounded-full border-2 border-primary", first ? "bg-primary" : "bg-background")} />
    </span>
  );
}

export function NextUp({ items }: { items: NextUpOut[] }) {
  return (
    <Section title="Next up">
      {items.length === 0 ? <p className="text-muted-foreground">Nothing scheduled</p> : null}
      <ol>
        {items.map((n, i) => {
          const to = n.kind === "backup" ? "/backup" : n.sync_id ? `/syncs/${n.sync_id}` : null;
          return (
            <li key={i} className="flex items-center gap-3 text-sm">
              <Stop first={i === 0} last={i === items.length - 1} />
              <span className="min-w-0 flex-1 truncate py-1.5">
                {to ? (
                  <Link className={linkClass} to={to}>
                    {n.name}
                  </Link>
                ) : (
                  n.name
                )}
              </span>
              <span className="shrink-0 text-muted-foreground tabular-nums">{formatRelative(n.at)}</span>
            </li>
          );
        })}
      </ol>
    </Section>
  );
}
