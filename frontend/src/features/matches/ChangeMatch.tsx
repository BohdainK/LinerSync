import { useMutation } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { api, call, type ApiError } from "@/api/client";
import type { CandidateOut, TrackOut } from "@/api/types";
import { ErrorNotice } from "@/components/ErrorNotice";
import { TrackRow } from "@/components/TrackList";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/** "Change": opens a panel of other destination songs for one source song, with a search box. */
export function ChangeMatch({
  runId,
  memberId,
  source,
  onPick,
}: {
  runId: string;
  memberId: string;
  source: TrackOut;
  onPick(target: TrackOut): void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const find = useMutation<CandidateOut[], ApiError, string | null>({
    mutationFn: (q) =>
      call(
        api.POST("/api/runs/{run_id}/alternatives", {
          params: { path: { run_id: runId } },
          body: {
            member_id: memberId,
            source_service: source.service,
            source_track_id: source.track_id,
            ...(q ? { query: q } : {}),
          },
        }),
      ),
  });

  function close() {
    setOpen(false);
    setQuery("");
    find.reset();
  }

  function openPanel() {
    setQuery("");
    find.reset();
    setOpen(true);
    find.mutate(null);
  }

  function search(e: FormEvent) {
    e.preventDefault();
    find.mutate(query.trim() || null);
  }

  if (!open) {
    return (
      <Button
        type="button"
        size="sm"
        variant="outline"
        aria-label={`Change match for ${source.title}`}
        onClick={openPanel}
      >
        Change
      </Button>
    );
  }
  return (
    <div className="w-full space-y-2 rounded-md border p-2">
      <form className="flex gap-2" onSubmit={search}>
        <Input aria-label="Search for a song" value={query} onChange={(e) => setQuery(e.target.value)} />
        <Button type="submit" size="sm" variant="outline" disabled={find.isPending}>
          Search
        </Button>
      </form>
      {find.error ? <ErrorNotice error={find.error} /> : null}
      {find.isPending ? <p className="text-sm text-muted-foreground">Searching…</p> : null}
      {find.isSuccess && find.data.length === 0 ? (
        <p className="text-sm text-muted-foreground">No other songs found.</p>
      ) : null}
      <ul className="space-y-1">
        {(find.data ?? []).map((c) => (
          <li key={`${c.track.service}:${c.track.track_id}`} className="flex items-center justify-between gap-2">
            <span className="min-w-0">
              <TrackRow track={c.track} />
            </span>
            <Button
              type="button"
              size="sm"
              aria-label={`Use this: ${c.track.title} by ${c.track.artists.join(", ")}`}
              onClick={() => {
                onPick(c.track);
                close();
              }}
            >
              Use this
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}
