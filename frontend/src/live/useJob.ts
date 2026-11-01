import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { api, call, type ApiError } from "@/api/client";
import { keys } from "@/api/keys";
import type { JobOut } from "@/api/types";
import { useEventsConnected } from "./events";

export function isActive(job: JobOut): boolean {
  return job.status === "queued" || job.status === "running";
}

/** A job by id. Polls every 3 s while the live stream is down, and stops once the job ends. */
export function useJob(jobId: string | null): UseQueryResult<JobOut> {
  const connected = useEventsConnected();
  return useQuery({
    queryKey: keys.job(jobId ?? ""),
    enabled: jobId !== null,
    queryFn: () =>
      call(api.GET("/api/jobs/{job_id}", { params: { path: { job_id: jobId ?? "" } } })),
    refetchInterval: (query) => {
      const job = query.state.data;
      if (connected || (job && !isActive(job))) return false;
      return 3000;
    },
  });
}

export function useCancelJob(): UseMutationResult<JobOut, ApiError, string> {
  const queryClient = useQueryClient();
  return useMutation<JobOut, ApiError, string>({
    mutationFn: (jobId) =>
      call(api.POST("/api/jobs/{job_id}/cancel", { params: { path: { job_id: jobId } } })),
    onSuccess: (job) => {
      queryClient.setQueryData(keys.job(job.job_id), job);
      void queryClient.invalidateQueries({ queryKey: keys.home });
    },
  });
}

/** Calls `onEnd` once each time the job leaves queued/running. */
export function useOnJobEnd(job: JobOut | undefined, onEnd: () => void): void {
  const latest = useRef(onEnd);
  useEffect(() => {
    latest.current = onEnd;
  }, [onEnd]);
  const ended = job && !isActive(job) ? `${job.job_id}:${job.status}` : null;
  useEffect(() => {
    if (ended !== null) latest.current();
  }, [ended]);
}
