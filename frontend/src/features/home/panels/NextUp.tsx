import { Link } from "react-router";
import type { NextUpOut } from "@/api/types";
import { formatRelative } from "@/lib/format";

export function NextUp({ items }: { items: NextUpOut[] }) {
  return (
    <section aria-labelledby="home-next" className="space-y-3">
      <h2 id="home-next" className="text-lg font-semibold">Next up</h2>
      {items.length === 0 ? <p className="text-muted-foreground">Nothing scheduled</p> : null}
      <ul className="space-y-1">
        {items.map((n, i) => {
          const to = n.kind === "backup" ? "/backup" : n.sync_id ? `/syncs/${n.sync_id}` : null;
          return (
            <li key={i} className="flex justify-between gap-2 text-sm">
              {to ? (
                <Link className="text-primary underline underline-offset-4" to={to}>
                  {n.name}
                </Link>
              ) : (
                <span>{n.name}</span>
              )}
              <span className="text-muted-foreground">{formatRelative(n.at)}</span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
