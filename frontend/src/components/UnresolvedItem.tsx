import type { UnresolvedOut } from "@/api/types";
import { TrackList } from "@/components/TrackList";

/** A song that didn't match: the source track, why, and the closest candidates. Renders an `<li>`. */
export function UnresolvedItem({ item, candidatesHeading }: { item: UnresolvedOut; candidatesHeading?: string }) {
  const candidates = item.candidates.map((c) => c.track);
  return (
    <li className="space-y-1 rounded-md border p-3">
      <TrackList tracks={[item.source]} />
      {item.reasons.length > 0 ? (
        <ul className="list-disc pl-6 text-sm text-muted-foreground">
          {item.reasons.map((reason, j) => (
            <li key={j}>{reason}</li>
          ))}
        </ul>
      ) : null}
      {candidates.length > 0 ? (
        candidatesHeading ? (
          <div className="text-sm">
            <p className="text-muted-foreground">{candidatesHeading}</p>
            <TrackList tracks={candidates} />
          </div>
        ) : (
          <TrackList tracks={candidates} />
        )
      ) : null}
    </li>
  );
}
