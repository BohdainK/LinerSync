import { useCallback, useMemo, useState } from "react";
import type { PickChoiceIn, TrackOut } from "@/api/types";

/** A swap chosen in a preview: this source song should link to this destination song. */
export type Pick = { runId: string; memberId: string; source: TrackOut; target: TrackOut };

export function usePicks() {
  const [picks, setPicks] = useState<Pick[]>([]);

  const pick = useCallback((p: Pick) => {
    setPicks((prev) => [...prev, p]);
  }, []);

  // Remembered matches are global per (source song, target service), so a pick in any run applies to all.
  const picked = useCallback(
    (source: TrackOut, targetService: string): TrackOut | undefined =>
      picks.findLast(
        (q) =>
          q.source.service === source.service &&
          q.source.track_id === source.track_id &&
          q.target.service === targetService,
      )?.target,
    [picks],
  );

  const clear = useCallback(() => setPicks([]), []);

  const toApi = useCallback((): PickChoiceIn[] => {
    const byKey = new Map<string, PickChoiceIn>();
    for (const p of picks) {
      const key = JSON.stringify([p.source.service, p.source.track_id, p.target.service]);
      byKey.delete(key); // re-insert so the last pick also comes last
      byKey.set(key, {
        source_service: p.source.service,
        source_track_id: p.source.track_id,
        target_service: p.target.service,
        target_track_id: p.target.track_id,
      });
    }
    return [...byKey.values()];
  }, [picks]);

  return useMemo(() => ({ picks, pick, picked, clear, toApi }), [picks, pick, picked, clear, toApi]);
}

export type PicksStore = ReturnType<typeof usePicks>;
