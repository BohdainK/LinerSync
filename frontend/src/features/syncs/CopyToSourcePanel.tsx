import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, call, type ApiError } from "@/api/client";
import { keys } from "@/api/keys";
import type { CopyToSourceIn, DestinationOut, JobOut, SyncOut } from "@/api/types";
import { ErrorNotice } from "@/components/ErrorNotice";
import { JobProgress } from "@/components/JobProgress";
import { Button } from "@/components/ui/button";
import { usePicks } from "@/features/matches/usePicks";
import { useServiceNames } from "@/features/pickers/useServiceNames";
import { TransferResult } from "@/features/transfers/TransferResult";
import { isActive, useCancelJob, useJob, useOnJobEnd } from "@/live/useJob";

/** A one-time copy of songs added on a destination into the source: preview first, then copy. */
export function CopyToSourcePanel({
  sync,
  destination,
  trackIds,
  onClose,
}: {
  sync: SyncOut;
  destination: DestinationOut;
  trackIds: string[];
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const nameOf = useServiceNames();
  const picks = usePicks();
  const cancel = useCancelJob();
  const [jobId, setJobId] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(true);
  const job = useJob(jobId);
  const path = { sync_id: sync.sync_id, member_id: destination.member_id };

  useOnJobEnd(job.data, () => {
    if (previewing) return;
    void queryClient.invalidateQueries({ queryKey: keys.sync(sync.sync_id) });
    void queryClient.invalidateQueries({ queryKey: keys.syncRuns(sync.sync_id) });
  });

  const copy = useMutation<JobOut, ApiError, CopyToSourceIn>({
    mutationFn: (body) => call(api.POST("/api/syncs/{sync_id}/destinations/{member_id}/copy-to-source", { params: { path }, body })),
    onSuccess: (out, vars) => {
      setPreviewing(vars.dry_run !== false);
      queryClient.setQueryData(keys.job(out.job_id), out);
      setJobId(out.job_id);
    },
  });

  const requested = useRef(false);
  useEffect(() => {
    if (requested.current) return;
    requested.current = true;
    copy.mutate({ track_ids: trackIds, dry_run: true });
  }, [copy, trackIds]);

  const current = job.data;
  const source = nameOf(sync.source.service);

  function startCopy() {
    if (!current) return;
    copy.reset();
    copy.mutate({ track_ids: trackIds, dry_run: false, preview_job_id: current.job_id, picks: picks.toApi() });
  }

  return (
    <div className="space-y-4 rounded-md border p-4" aria-label="Copy to source">
      {copy.error ? <ErrorNotice error={copy.error} /> : null}
      {job.error ? <ErrorNotice error={job.error} /> : null}
      {jobId === null && !copy.error ? <p className="text-sm text-muted-foreground">Working…</p> : null}
      {current ? <JobProgress job={current} onCancel={() => cancel.mutate(current.job_id)} /> : null}
      {cancel.error ? <ErrorNotice error={cancel.error} /> : null}
      {current && !isActive(current) ? (
        <div className="space-y-4">
          <div className="flex gap-2">
            {previewing && current.status === "succeeded" ? (
              <Button type="button" disabled={copy.isPending} onClick={startCopy}>
                Copy to {source}
              </Button>
            ) : null}
            <Button type="button" variant="outline" onClick={onClose}>
              Close
            </Button>
          </div>
          {current.run_ids.map((id) => (
            <TransferResult key={id} runId={id} picks={previewing ? picks : undefined} />
          ))}
        </div>
      ) : copy.error ? (
        <Button type="button" variant="outline" onClick={onClose}>
          Close
        </Button>
      ) : null}
    </div>
  );
}
