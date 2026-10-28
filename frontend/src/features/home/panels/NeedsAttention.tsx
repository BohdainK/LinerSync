import { useState } from "react";
import { Link } from "react-router";
import type { AttentionOut, WarningOut } from "@/api/types";
import { ErrorNotice } from "@/components/ErrorNotice";
import { AttentionList } from "@/features/attention/AttentionList";
import { AttentionModal } from "@/features/attention/AttentionModal";
import { useAttention } from "@/features/attention/useAttention";

export function NeedsAttention({ warnings }: { warnings: WarningOut[] }) {
  const attention = useAttention();
  const [selected, setSelected] = useState<AttentionOut | null>(null);
  const items = attention.data ?? [];
  return (
    <section aria-labelledby="home-attention" className="space-y-3">
      <h2 id="home-attention" className="text-lg font-semibold">Needs attention</h2>
      {warnings.length > 0 ? (
        <ul className="space-y-1">
          {warnings.map((w) => (
            <li key={`${w.kind}:${w.service}`} className="text-sm">
              <span>{w.message}</span>{" "}
              <Link className="text-primary underline underline-offset-4" to={`/connections/${w.service}`}>
                Open {w.service}
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
      {attention.isError ? <ErrorNotice error={attention.error} onRetry={() => void attention.refetch()} /> : null}
      {attention.isSuccess && items.length === 0 && warnings.length === 0 ? (
        <p className="text-muted-foreground">All clear</p>
      ) : null}
      <AttentionList items={items} onSelect={setSelected} />
      <AttentionModal item={selected} onOpenChange={(open) => !open && setSelected(null)} />
    </section>
  );
}
