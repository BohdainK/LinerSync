import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { api, call } from "@/api/client";
import { keys } from "@/api/keys";
import type { SettingsOut } from "@/api/types";

export function useSettings(): UseQueryResult<SettingsOut> {
  return useQuery({ queryKey: keys.settings, queryFn: () => call(api.GET("/api/settings")) });
}
