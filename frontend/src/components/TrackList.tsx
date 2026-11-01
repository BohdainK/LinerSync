import { useState } from "react";
import type { TrackOut } from "@/api/types";
import { ExternalLink } from "@/components/ExternalLink";
import { Button } from "@/components/ui/button";
import { formatDuration } from "@/lib/format";

const LIMIT = 100;

export function TrackRow({ track }: { track: TrackOut }) {
  const details = [track.artists.join(", "), track.album ?? "", formatDuration(track.duration_ms)]
    .filter(Boolean)
    .join(" · ");
  return (
    <>
      <span className="font-medium">
        {track.url ? <ExternalLink href={track.url}>{track.title}</ExternalLink> : track.title}
      </span>
      {details ? <span className="text-sm text-muted-foreground"> {details}</span> : null}
    </>
  );
}

export function TrackList({
  tracks,
  numbered = false,
}: {
  tracks: TrackOut[];
  numbered?: boolean;
}) {
  const [all, setAll] = useState(false);
  const shown = all ? tracks : tracks.slice(0, LIMIT);
  const Tag = numbered ? "ol" : "ul";
  return (
    <div className="space-y-2">
      <Tag className={numbered ? "list-decimal space-y-1 pl-6" : "list-none space-y-1"}>
        {shown.map((t, i) => (
          <li key={`${t.service}:${t.track_id}:${i}`}>
            <TrackRow track={t} />
          </li>
        ))}
      </Tag>
      {!all && tracks.length > LIMIT ? (
        <Button type="button" variant="outline" size="sm" onClick={() => setAll(true)}>
          Show all {tracks.length}
        </Button>
      ) : null}
    </div>
  );
}
