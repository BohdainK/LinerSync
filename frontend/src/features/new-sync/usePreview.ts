import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, call } from "@/api/client";
import { keys } from "@/api/keys";
import type { JobOut, RunOut, SyncCreateIn } from "@/api/types";
import { JOB_STATUS_LABELS } from "@/copy";
import { useRun } from "@/features/runs/useRun";
import { isActive, useJob } from "@/live/useJob";

const PREVIEW_DEBOUNCE_MS = 800;

export type PreviewState = {
  status: "idle" | "waiting" | "running" | "ready" | "failed";
  run: RunOut | null;
  /** The preview run's id once ready; "Start syncing" runs the sync from it. */
  runId: string | null;
  job: JobOut | null;
  error: unknown;
};

/** The submit that belongs to a selection key. */
type Submitted = { key: string; jobId: string | null; error: unknown };

/** Asks a superseded preview job to stop, unless it already ended. Errors don't matter (D10). */
function cancelIfActive(queryClient: QueryClient, jobId: string): void {
  const cached = queryClient.getQueryData<JobOut>(keys.job(jobId));
  if (cached && !isActive(cached)) return;
  call(api.POST("/api/jobs/{job_id}/cancel", { params: { path: { job_id: jobId } } })).then(
    (job) => queryClient.setQueryData(keys.job(job.job_id), job),
    () => {},
  );
}

/**
 * A live preview of the sync `input` would create (decision D10). Submits `POST /api/syncs/preview`
 * once `input` has been stable for 800 ms. Everything is keyed by `JSON.stringify(input)`: a changed
 * selection cancels the previous job if it is still active, and a job or run whose key isn't the
 * current one is never returned.
 */
export function usePreview(input: SyncCreateIn | null): PreviewState {
  const queryClient = useQueryClient();
  const key = input === null ? null : JSON.stringify(input);
  const [submitted, setSubmitted] = useState<Submitted | null>(null);
  // The newest submit. A response for any other token is stale and its job gets cancelled.
  const latest = useRef<{ key: string; jobId: string | null } | null>(null);

  useEffect(() => {
    const previous = latest.current;
    if (previous && previous.key !== key) {
      latest.current = null;
      setSubmitted(null); // A->B->A must not show A's cancelled job
      if (previous.jobId) cancelIfActive(queryClient, previous.jobId);
    }
    if (key === null) return;
    const timer = setTimeout(() => {
      if (latest.current?.key === key) return; // already submitted for this selection
      const token: { key: string; jobId: string | null } = { key, jobId: null };
      latest.current = token;
      const body = JSON.parse(key) as SyncCreateIn;
      call(api.POST("/api/syncs/preview", { body })).then(
        (job) => {
          queryClient.setQueryData(keys.job(job.job_id), job);
          if (latest.current !== token) {
            cancelIfActive(queryClient, job.job_id);
            return;
          }
          token.jobId = job.job_id;
          setSubmitted({ key, jobId: job.job_id, error: null });
        },
        (error: unknown) => {
          if (latest.current === token) setSubmitted({ key, jobId: null, error });
        },
      );
    }, PREVIEW_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [key, queryClient]);

  const current = submitted !== null && submitted.key === key ? submitted : null;
  const jobQuery = useJob(current?.jobId ?? null);
  const job = current !== null && jobQuery.data ? jobQuery.data : null;
  const runId = job?.status === "succeeded" ? (job.run_ids[0] ?? null) : null;
  const runQuery = useRun(runId);

  if (key === null) return { status: "idle", run: null, runId: null, job: null, error: null };
  if (current === null) return { status: "waiting", run: null, runId: null, job: null, error: null };
  if (current.error) return { status: "failed", run: null, runId: null, job: null, error: current.error };
  if (job === null || job.status === "queued") return { status: "waiting", run: null, runId: null, job, error: null };
  if (job.status === "running") return { status: "running", run: null, runId: null, job, error: null };
  if (job.status !== "succeeded") {
    const reason = job.error || JOB_STATUS_LABELS[job.status] || job.status;
    return { status: "failed", run: null, runId: null, job, error: new Error(reason) };
  }
  if (runId === null) {
    return { status: "failed", run: null, runId: null, job, error: new Error("The preview finished without a result") };
  }
  if (runQuery.isError) return { status: "failed", run: null, runId: null, job, error: runQuery.error };
  if (runQuery.data) return { status: "ready", run: runQuery.data, runId, job, error: null };
  return { status: "running", run: null, runId: null, job, error: null };
}
