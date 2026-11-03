import { useState } from "react";
import type { AttentionOut } from "@/api/types";
import { Section } from "@/components/Section";
import { AttentionList } from "@/features/attention/AttentionList";
import { AttentionModal } from "@/features/attention/AttentionModal";
import { useAttention } from "@/features/attention/useAttention";

/** This sync's open "Needs attention" items; nothing when there are none (or
 * they can't be loaded: Home shows that error). */
export function SyncAttention({ syncId }: { syncId: string }) {
  const attention = useAttention();
  const [selected, setSelected] = useState<AttentionOut | null>(null);
  const items = (attention.data ?? []).filter((i) => i.sync_id === syncId);
  if (items.length === 0) return null;
  return (
    <Section title={`Needs attention (${items.length})`}>
      <AttentionList items={items} onSelect={setSelected} />
      <AttentionModal item={selected} onOpenChange={(open) => !open && setSelected(null)} />
    </Section>
  );
}
