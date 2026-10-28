import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { api, call } from "@/api/client";
import { keys } from "@/api/keys";
import type { BackupPlaylistOut, PlaylistOut } from "@/api/types";

export const ARCHIVE = "backup";

function fromBackup(e: BackupPlaylistOut): PlaylistOut {
  return {
    service: ARCHIVE,
    playlist_id: e.entry_id,
    name: e.name,
    description: "",
    cover_url: e.cover_url,
    owned: true,
    readable: true,
    unreadable_reason: null,
    track_count: e.track_count,
  };
}

/** Playlists of a service, or the archived backup entries for "backup". Disabled for null. */
export function usePlaylists(service: string | null): UseQueryResult<PlaylistOut[]> {
  const archive = service === ARCHIVE;
  // One cache entry per key, always the raw server shape: the backup table shares the archive key.
  const live = useQuery({
    queryKey: keys.playlists(service ?? ""),
    enabled: service !== null && !archive,
    queryFn: () => call(api.GET("/api/connections/{service}/playlists", { params: { path: { service: service ?? "" } } })),
  });
  const archived = useQuery({
    queryKey: keys.backupPlaylists({ archived: true }),
    enabled: archive,
    queryFn: () => call(api.GET("/api/backups/playlists", { params: { query: { archived: true } } })),
    select: (entries) => entries.map(fromBackup),
  });
  return archive ? archived : live;
}
