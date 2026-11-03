import { Music } from "lucide-react";
import { useState } from "react";
import type { TrackOut } from "@/api/types";
import { safeHref } from "@/lib/safeUrl";
import { cn } from "@/lib/utils";

/** An http(s) image, or an inline one (an image can't run script, so `data:image/` is inert). */
function imageSrc(url: string | null | undefined): string | null {
  if (url?.startsWith("data:image/")) return url;
  return safeHref(url);
}

/** A song's album cover, or a note glyph when there is none or it fails to load. */
export function TrackArt({ track, className }: { track: TrackOut; className?: string }) {
  const [failed, setFailed] = useState(false);
  const url = imageSrc(track.artwork_url);
  const box = cn("size-10 shrink-0 rounded-sm bg-muted", className);
  if (!url || failed) {
    return (
      <span className={cn(box, "flex items-center justify-center text-muted-foreground")} aria-hidden>
        <Music className="size-1/2" />
      </span>
    );
  }
  return (
    <img
      src={url}
      alt=""
      loading="lazy"
      referrerPolicy="no-referrer"
      className={cn(box, "object-cover")}
      onError={() => setFailed(true)}
    />
  );
}

/** Cover, title and artists on one line. */
export function TrackCard({ track, className }: { track: TrackOut; className?: string }) {
  const href = track.url ? safeHref(track.url) : null;
  return (
    <div className={cn("flex min-w-0 items-center gap-3", className)}>
      <TrackArt track={track} />
      <div className="min-w-0">
        <p className="truncate font-medium" title={track.title}>
          {href ? (
            <a className="hover:underline" href={href} target="_blank" rel="noreferrer">
              {track.title}
            </a>
          ) : (
            track.title
          )}
        </p>
        {track.artists.length > 0 ? (
          <p className="truncate text-sm text-muted-foreground">{track.artists.join(", ")}</p>
        ) : null}
      </div>
    </div>
  );
}
