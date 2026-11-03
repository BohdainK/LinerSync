import type { AttentionOut } from "@/api/types";
import { TrackArt } from "@/components/TrackArt";
import { ATTENTION_KIND_LABELS } from "@/copy";
import { useServiceNames } from "@/features/pickers/useServiceNames";

function Summary({ item }: { item: AttentionOut }) {
  const nameOf = useServiceNames();
  const song = item.kind === "unmatched" ? item.source : null;
  if (song) {
    const where = item.target_service ? ` on ${nameOf(item.target_service)}` : "";
    return (
      <span className="flex min-w-0 items-center gap-3">
        <TrackArt track={song} className="size-8" />
        <span className="min-w-0">
          <span className="block truncate font-medium">
            {song.title}
            {song.artists.length > 0 ? <span className="font-normal"> · {song.artists.join(", ")}</span> : null}
          </span>
          <span className="block truncate text-sm text-muted-foreground">
            Didn't match{where}
            {item.sync_name ? ` · ${item.sync_name}` : ""}
            {item.candidates.length > 0 ? ` · ${item.candidates.length} to choose from` : ""}
          </span>
        </span>
      </span>
    );
  }
  return (
    <>
      <span className="font-medium">{ATTENTION_KIND_LABELS[item.kind]}</span>
      {item.sync_name ? <span> {item.sync_name}</span> : null}
      {item.detail ? <span className="block text-sm text-muted-foreground">{item.detail}</span> : null}
    </>
  );
}

export function AttentionList({ items, onSelect }: { items: AttentionOut[]; onSelect(item: AttentionOut): void }) {
  return (
    <ul className="space-y-2">
      {items.map((item) => (
        <li key={item.item_id}>
          <button
            type="button"
            className="w-full rounded-md border p-2 text-left hover:bg-accent"
            onClick={() => onSelect(item)}
          >
            <Summary item={item} />
          </button>
        </li>
      ))}
    </ul>
  );
}
