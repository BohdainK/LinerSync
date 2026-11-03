import { ArrowRight } from "lucide-react";
import { useState } from "react";
import type { MatchPairOut } from "@/api/types";
import { TrackCard } from "@/components/TrackArt";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

const LIMIT = 50;

/** Each source song beside the song it became on the destination, with both covers. */
export function MatchList({
  matches,
  sourceName,
  targetName,
}: {
  matches: MatchPairOut[];
  sourceName: string;
  targetName: string;
}) {
  const [all, setAll] = useState(false);
  const shown = all ? matches : matches.slice(0, LIMIT);
  return (
    <div className="space-y-2">
      <div className="hidden grid-cols-[1fr_auto_1fr] gap-3 text-xs font-medium tracking-wide text-muted-foreground uppercase sm:grid">
        <span>{sourceName}</span>
        <span className="w-4" />
        <span>{targetName}</span>
      </div>
      <ul className="divide-y rounded-md border" aria-label={`Songs from ${sourceName} to ${targetName}`}>
        {shown.map((m, i) => (
          <li
            key={`${m.source.service}:${m.source.track_id}:${i}`}
            className="grid grid-cols-1 items-center gap-2 p-2 sm:grid-cols-[1fr_auto_1fr] sm:gap-3"
          >
            <TrackCard track={m.source} />
            <ArrowRight className="hidden size-4 text-muted-foreground sm:block" aria-hidden />
            <div className="flex min-w-0 items-center justify-between gap-2">
              <TrackCard track={m.target} />
              {m.added ? null : (
                <Badge variant="outline" className="shrink-0">
                  Already there
                </Badge>
              )}
            </div>
          </li>
        ))}
      </ul>
      {!all && matches.length > LIMIT ? (
        <Button type="button" variant="outline" size="sm" onClick={() => setAll(true)}>
          Show all {matches.length}
        </Button>
      ) : null}
    </div>
  );
}
