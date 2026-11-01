import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { api, call } from "@/api/client";
import { keys } from "@/api/keys";
import type { RunOut } from "@/api/types";

/** A run by id; disabled while `runId` is null. */
export function useRun(runId: string | null): UseQueryResult<RunOut> {
  return useQuery({
    queryKey: keys.run(runId ?? ""),
    enabled: runId !== null,
    queryFn: () => call(api.GET("/api/runs/{run_id}", { params: { path: { run_id: runId ?? "" } } })),
  });
}
