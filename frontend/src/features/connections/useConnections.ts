import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { api, call } from "@/api/client";
import { keys } from "@/api/keys";
import type { ConnectionListOut, ConnectionOut } from "@/api/types";

export function useConnections(): UseQueryResult<ConnectionListOut> {
  return useQuery({
    queryKey: keys.connections,
    queryFn: () => call(api.GET("/api/connections")),
  });
}

export function useConnection(service: string): UseQueryResult<ConnectionOut> {
  return useQuery({
    queryKey: keys.connection(service),
    queryFn: () => call(api.GET("/api/connections/{service}", { params: { path: { service } } })),
  });
}

export function isSetUp(c: ConnectionOut): boolean {
  return c.status !== "not_set_up";
}

/** The service can add songs, so it can be a destination. */
export function canAdd(c: ConnectionOut): boolean {
  return c.capabilities.includes("add");
}
