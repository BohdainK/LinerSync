import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { api, call, type ApiError } from "@/api/client";
import { keys } from "@/api/keys";
import type { JobOut } from "@/api/types";
import { ErrorNotice } from "@/components/ErrorNotice";
import { JobProgress } from "@/components/JobProgress";
import { StatusBadge } from "@/components/StatusBadge";
import { TrackList } from "@/components/TrackList";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useServiceNames } from "@/features/pickers/useServiceNames";
import { formatDateTime } from "@/lib/format";
import { isActive, useCancelJob, useJob } from "@/live/useJob";
import { MemberSection } from "./MemberSection";

export function RunPage() {
  const { runId = "" } = useParams();
  const queryClient = useQueryClient();
  const nameOf = useServiceNames();
  const run = useQuery({
    queryKey: keys.run(runId),
    queryFn: () => call(api.GET("/api/runs/{run_id}", { params: { path: { run_id: runId } } })),
  });
  const undo = useMutation<JobOut, ApiError, void>({
    mutationFn: () => call(api.POST("/api/runs/{run_id}/undo", { params: { path: { run_id: runId } } })),
    onSuccess: (job) => {
      queryClient.setQueryData(keys.job(job.job_id), job);
      setUndoJobId(job.job_id);
      void queryClient.invalidateQueries({ queryKey: keys.run(runId) });
    },
  });
  const [undoJobId, setUndoJobId] = useState<string | null>(null);
  const undoJob = useJob(undoJobId);
  const cancel = useCancelJob();
  const undoStatus = undoJob.data?.status;
  const syncId = run.data?.sync_id;
  useEffect(() => {
    if (undoStatus && undoStatus !== "queued" && undoStatus !== "running") {
      void queryClient.invalidateQueries({ queryKey: keys.run(runId) });
      if (syncId) void queryClient.invalidateQueries({ queryKey: keys.syncRuns(syncId) });
    }
  }, [undoStatus, queryClient, runId, syncId]);

  if (run.isError) return <ErrorNotice error={run.error} onRetry={() => void run.refetch()} />;
  if (!run.data) return <p>Loading…</p>;
  const r = run.data;

  return (
    <div className="space-y-6">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold">Run</h1>
        <p>
          <Link className="text-primary underline underline-offset-4" to={`/syncs/${r.sync_id}`}>
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
          <span>{`+${r.added} −${r.removed}`}</span>
          {r.not_made > 0 ? ` · ${r.not_made} not made` : ""}
          {r.unmatched > 0 ? ` · ${r.unmatched} didn't match` : ""}
        </p>
        {r.error ? <p className="text-sm text-destructive">{r.error}</p> : null}
        {r.undoable ? (
          <Button type="button" variant="outline" disabled={undo.isPending || (undoJob.data !== undefined && isActive(undoJob.data))} onClick={() => undo.mutate()}>
            Undo this run
          </Button>
        ) : null}
        {undo.error ? <ErrorNotice error={undo.error} /> : null}
        {undoJob.data ? (
          <JobProgress job={undoJob.data} onCancel={() => cancel.mutate(undoJob.data.job_id)} />
        ) : null}
        {undoJob.data?.run_ids[0] ? (
          <p className="text-sm">
            <Link className="text-primary underline underline-offset-4" to={`/runs/${undoJob.data.run_ids[0]}`}>
              View the undo run
            </Link>
          </p>
        ) : null}
      </header>

      {r.members.map((m) => (
        <MemberSection key={m.member_id} member={m} serviceName={m.service ? nameOf(m.service) : m.member_id} />
      ))}

      {r.unresolved.length > 0 ? (
        <section className="space-y-3">
          <h2 className="text-lg font-semibold">Didn't match ({r.unresolved.length})</h2>
          <ul className="space-y-3">
            {r.unresolved.map((u, i) => (
              <li key={`${u.member_id}:${u.position}:${i}`} className="space-y-1 rounded-md border p-3">
                <TrackList tracks={[u.source]} />
                {u.reasons.length > 0 ? (
                  <ul className="list-disc pl-6 text-sm text-muted-foreground">
                    {u.reasons.map((reason, j) => (
                      <li key={j}>{reason}</li>
                    ))}
                  </ul>
                ) : null}
                {u.candidates.length > 0 ? <TrackList tracks={u.candidates.map((c) => c.track)} /> : null}
              </li>
            ))}
          </ul>
          <Link className="text-primary underline underline-offset-4" to="/">
            Resolve in Needs attention
          </Link>
        </section>
      ) : null}
    </div>
  );
}
