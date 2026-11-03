import { ArrowRight } from "lucide-react";
import { useState } from "react";
import type { MatchPairOut } from "@/api/types";
import { TrackCard } from "@/components/TrackArt";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ChangeMatch } from "@/features/matches/ChangeMatch";
import { useMatchEditing } from "@/features/matches/MatchEditing";

const LIMIT = 50;

/** Each source song beside the song it became on the destination, with both covers. */
export function MatchList({
  matches,
  memberId,
  sourceName,
  targetName,
}: {
  matches: MatchPairOut[];
  memberId?: string;
  sourceName: string;
  targetName: string;
}) {
  const editing = useMatchEditing();
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
        {shown.map((m, i) => {
          const pick = editing ? editing.picks.picked(m.source, m.target.service) : undefined;
          return (
            <li
              key={`${m.source.service}:${m.source.track_id}:${i}`}
              className="grid grid-cols-1 items-center gap-2 p-2 sm:grid-cols-[1fr_auto_1fr] sm:gap-3"
            >
              <TrackCard track={m.source} />
              <ArrowRight className="hidden size-4 text-muted-foreground sm:block" aria-hidden />
              <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
                <TrackCard track={pick ?? m.target} />
                {pick ? (
                  <Badge className="shrink-0">Your pick</Badge>
                ) : m.added ? null : (
                  <Badge variant="outline" className="shrink-0">
                    Already there
                  </Badge>
                )}
                {editing && memberId ? (
                  <ChangeMatch
                    runId={editing.runId}
                    memberId={memberId}
                    source={m.source}
                    onPick={(target) =>
                      editing.picks.pick({ runId: editing.runId, memberId, source: m.source, target })
                    }
                  />
                ) : null}
              </div>
            </li>
          );
        })}
      </ul>
      {!all && matches.length > LIMIT ? (
        <Button type="button" variant="outline" size="sm" onClick={() => setAll(true)}>
          Show all {matches.length}
        </Button>
      ) : null}
    </div>
  );
}
