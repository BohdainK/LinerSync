import type { EstimateOut } from "@/api/types";

/** Ids of the playlists the estimate says will be skipped. Every bulk item shares one source, so the id identifies it. */
export function unreadableIds(estimate: EstimateOut | null): Set<string> {
  return new Set(estimate?.unreadable.map((p) => p.playlist_id) ?? []);
}
