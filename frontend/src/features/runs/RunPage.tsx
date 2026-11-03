import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router";
import { api, call, type ApiError } from "@/api/client";
import { keys } from "@/api/keys";
import type { JobOut } from "@/api/types";
import { ErrorNotice, QueryError } from "@/components/ErrorNotice";
import { JobProgress } from "@/components/JobProgress";
import { StatusBadge } from "@/components/StatusBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatChangeCounts, formatDateTime } from "@/lib/format";
import { linkClass } from "@/lib/styles";
import { isActive, useCancelJob, useJob, useOnJobEnd } from "@/live/useJob";
import { RunResults } from "./RunResults";
import { useRun } from "./useRun";

export function RunPage() {
  const { runId = "" } = useParams();
  const queryClient = useQueryClient();
  const run = useRun(runId);
  const [undoJobId, setUndoJobId] = useState<string | null>(null);
  const undo = useMutation<JobOut, ApiError, void>({
    mutationFn: () => call(api.POST("/api/runs/{run_id}/undo", { params: { path: { run_id: runId } } })),
    onSuccess: (job) => {
      queryClient.setQueryData(keys.job(job.job_id), job);
      setUndoJobId(job.job_id);
      void queryClient.invalidateQueries({ queryKey: keys.run(runId) });
    },
  });
  const undoJob = useJob(undoJobId);
  const cancel = useCancelJob();
  const syncId = run.data?.sync_id;
  useOnJobEnd(undoJob.data, () => {
    void queryClient.invalidateQueries({ queryKey: keys.run(runId) });
    if (syncId) void queryClient.invalidateQueries({ queryKey: keys.syncRuns(syncId) });
  });

  if (run.isError) return <QueryError query={run} />;
  if (!run.data) return <p>Loading…</p>;
  const r = run.data;

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold">Run</h1>
        <p>
          <Link className={linkClass} to={`/syncs/${r.sync_id}`}>
            Back to the sync
          </Link>
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge kind="run" status={r.status} />
          {r.dry_run ? <Badge variant="outline">Dry run</Badge> : null}
          <span className="text-sm text-muted-foreground">
            {r.kind} · {r.trigger}
          </span>
        </div>
        <p className="text-sm">
          Started {formatDateTime(r.started_at)}
          {r.finished_at ? ` · Finished ${formatDateTime(r.finished_at)}` : ""}
        </p>
        <p className="text-sm">
          <span>{formatChangeCounts(r)}</span>
          {r.not_made > 0 ? ` · ${r.not_made} not made` : ""}
          {r.unmatched > 0 ? ` · ${r.unmatched} didn't match` : ""}
        </p>
        {r.error ? <p className="text-sm text-destructive">{r.error}</p> : null}
        {r.undoable ? (
          <Button
            type="button"
            variant="outline"
            disabled={undo.isPending || (undoJob.data !== undefined && isActive(undoJob.data))}
            onClick={() => undo.mutate()}
          >
            Undo this run
          </Button>
        ) : null}
        {undo.error ? <ErrorNotice error={undo.error} /> : null}
        {undoJob.data ? <JobProgress job={undoJob.data} onCancel={() => cancel.mutate(undoJob.data.job_id)} /> : null}
        {undoJob.data?.run_ids[0] ? (
          <p className="text-sm">
            <Link className={linkClass} to={`/runs/${undoJob.data.run_ids[0]}`}>
              View the undo run
            </Link>
          </p>
        ) : null}
      </header>

      <RunResults run={r} />
    </div>
  );
}
