import { useState } from "react";
import { Link } from "react-router";
import type { AttentionOut, WarningOut } from "@/api/types";
import { QueryError } from "@/components/ErrorNotice";
import { Section } from "@/components/Section";
import { AttentionList } from "@/features/attention/AttentionList";
import { AttentionModal } from "@/features/attention/AttentionModal";
import { useAttention } from "@/features/attention/useAttention";
import { linkClass } from "@/lib/styles";

export function NeedsAttention({ warnings }: { warnings: WarningOut[] }) {
  const attention = useAttention();
  const [selected, setSelected] = useState<AttentionOut | null>(null);
  const items = attention.data ?? [];
  return (
    <Section title="Needs attention">
      {warnings.length > 0 ? (
        <ul className="space-y-1">
          {warnings.map((w) => (
            <li key={`${w.kind}:${w.service}`} className="text-sm">
              <span>{w.message}</span>{" "}
              <Link className={linkClass} to={`/connections/${w.service}`}>
                Open {w.service}
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
      <QueryError query={attention} />
      {attention.isSuccess && items.length === 0 && warnings.length === 0 ? (
        <p className="text-muted-foreground">All clear</p>
      ) : null}
      <AttentionList items={items} onSelect={setSelected} />
      <AttentionModal item={selected} onOpenChange={(open) => !open && setSelected(null)} />
    </Section>
  );
}
