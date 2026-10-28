import type { ChangeOut } from "@/api/types";
import { TrackRow } from "@/components/TrackList";

export function ChangesList({ changes }: { changes: ChangeOut[] }) {
  return (
    <ul className="space-y-2">
      {changes.map((c, i) => (
        <li key={i}>
          <p>{c.detail}</p>
          {c.track ? (
            <p>
              <TrackRow track={c.track} />
            </p>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
