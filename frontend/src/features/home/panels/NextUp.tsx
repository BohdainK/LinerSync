import { Link } from "react-router";
import type { NextUpOut } from "@/api/types";
import { Section } from "@/components/Section";
import { formatRelative } from "@/lib/format";
import { linkClass } from "@/lib/styles";

export function NextUp({ items }: { items: NextUpOut[] }) {
  return (
    <Section title="Next up">
      {items.length === 0 ? <p className="text-muted-foreground">Nothing scheduled</p> : null}
      <ul className="space-y-1">
        {items.map((n, i) => {
          const to = n.kind === "backup" ? "/backup" : n.sync_id ? `/syncs/${n.sync_id}` : null;
          return (
            <li key={i} className="flex justify-between gap-2 text-sm">
              {to ? (
                <Link className={linkClass} to={to}>
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
    </Section>
  );
}
