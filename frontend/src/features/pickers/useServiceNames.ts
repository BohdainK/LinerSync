import { useConnections } from "@/features/connections/useConnections";
import { ARCHIVE } from "./usePlaylists";

/** Maps a service id to its display name (falls back to the id; "backup" is the Archive). */
export function useServiceNames(): (service: string) => string {
  const connections = useConnections();
  return (service) => {
    if (service === ARCHIVE) return "Archive";
    return connections.data?.services.find((s) => s.service === service)?.display_name ?? service;
  };
}
