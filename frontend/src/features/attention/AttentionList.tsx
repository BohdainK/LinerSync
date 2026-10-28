import type { AttentionOut } from "@/api/types";
import { ATTENTION_KIND_LABELS } from "@/copy";

export function AttentionList({
  items,
  onSelect,
}: {
  items: AttentionOut[];
  onSelect(item: AttentionOut): void;
}) {
  return (
    <ul className="space-y-2">
      {items.map((item) => (
        <li key={item.item_id}>
          <button
            type="button"
            className="w-full rounded-md border p-2 text-left hover:bg-accent"
            onClick={() => onSelect(item)}
          >
            <span className="font-medium">{ATTENTION_KIND_LABELS[item.kind]}</span>
            {item.sync_name ? <span> {item.sync_name}</span> : null}
            {item.detail ? (
              <span className="block text-sm text-muted-foreground">{item.detail}</span>
            ) : null}
          </button>
        </li>
      ))}
    </ul>
  );
}
