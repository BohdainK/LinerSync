import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { keys } from "@/api/keys";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ErrorNotice } from "@/components/ErrorNotice";
import { JobProgress } from "@/components/JobProgress";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { isActive, useCancelJob, useJob } from "@/live/useJob";
import { AddDestination } from "./AddDestination";
import { BusyTip } from "./BusyTip";
import { DestinationsTable } from "./DestinationsTable";
import { RunHistory } from "./RunHistory";
import { ScheduleEditor } from "./ScheduleEditor";
import { useDeleteSync, usePatchSync, useRunSync, useSync } from "./useSync";
import type { SyncOut } from "@/api/types";

function Rename({ sync, disabled }: { sync: SyncOut; disabled: boolean }) {
  const patch = usePatchSync(sync.sync_id);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(sync.name);
  if (!editing) {
    return (
      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-semibold">{sync.name}</h1>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={disabled}
          onClick={() => {
            setName(sync.name);
            setEditing(true);
          }}
        >
          Rename
        </Button>
      </div>
    );
  }
  const trimmed = name.trim();
  return (
    <form
      className="flex items-center gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (trimmed) patch.mutate({ name: trimmed }, { onSuccess: () => setEditing(false) });
      }}
    >
      <Input aria-label="Name" value={name} onChange={(e) => setName(e.target.value)} className="max-w-xs" />
      <Button type="submit" size="sm" disabled={patch.isPending || !trimmed}>
        Save
      </Button>
      <Button type="button" variant="outline" size="sm" onClick={() => setEditing(false)}>
        Cancel
      </Button>
      {patch.error ? <ErrorNotice error={patch.error} /> : null}
    </form>
  );
}

export function SyncPage() {
  const { syncId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const sync = useSync(syncId);
  const run = useRunSync(syncId);
  const pause = usePatchSync(syncId);
  const del = useDeleteSync(syncId);
  const cancel = useCancelJob();
  const [startedId, setStartedId] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const started = useJob(startedId);

  const startedStatus = started.data?.status;
  useEffect(() => {
    if (startedStatus && startedStatus !== "queued" && startedStatus !== "running") {
      void queryClient.invalidateQueries({ queryKey: keys.sync(syncId) });
      void queryClient.invalidateQueries({ queryKey: keys.syncRuns(syncId) });
    }
  }, [startedStatus, queryClient, syncId]);

  if (sync.isError) return <ErrorNotice error={sync.error} onRetry={() => void sync.refetch()} />;
  if (!sync.data) return <p>Loading…</p>;
  const s = sync.data;
  const job = s.active_job ?? (started.data && isActive(started.data) ? started.data : null);
  const busy = job !== null;
  const paused = s.status !== "active";
  const needsRebuild = s.destinations.some((d) => d.missing);
  const actionError = run.error ?? pause.error ?? del.error;

  return (
    <div className="space-y-8">
      <header className="space-y-3">
        <Rename key={s.name} sync={s} disabled={busy} />
        {paused ? (
          <p className="text-sm text-muted-foreground">Paused{s.pause_reason ? `: ${s.pause_reason}` : ""}</p>
        ) : null}
        <div className="flex flex-wrap gap-2">
          <BusyTip busy={busy}>
            <Button
              type="button"
              disabled={busy || run.isPending}
              onClick={() => run.mutate(false, { onSuccess: (j) => setStartedId(j.job_id) })}
            >
              {needsRebuild ? "Rebuild" : "Sync now"}
            </Button>
          </BusyTip>
          <BusyTip busy={busy}>
            <Button
              type="button"
              variant="outline"
              disabled={busy || run.isPending}
              onClick={() => run.mutate(true, { onSuccess: (j) => setStartedId(j.job_id) })}
            >
              Dry run
            </Button>
          </BusyTip>
          <BusyTip busy={busy}>
            <Button
              type="button"
              variant="outline"
              disabled={busy || pause.isPending}
              onClick={() => pause.mutate({ paused: !paused })}
            >
              {paused ? "Resume" : "Pause"}
            </Button>
          </BusyTip>
          <BusyTip busy={busy}>
            <Button
              type="button"
              variant="destructive"
              disabled={busy || del.isPending}
              onClick={() => setConfirmDelete(true)}
            >
              Delete sync…
            </Button>
          </BusyTip>
        </div>
        {actionError ? <ErrorNotice error={actionError} /> : null}
        {job ? <JobProgress job={job} onCancel={() => cancel.mutate(job.job_id)} /> : null}
      </header>
      <DestinationsTable sync={s} disabled={busy} />
      <AddDestination sync={s} disabled={busy} />
      <ScheduleEditor key={s.sync_id} sync={s} />
      <RunHistory syncId={s.sync_id} />
      <ConfirmDialog
        open={confirmDelete}
        onOpenChange={setConfirmDelete}
        title="Delete sync"
        body="The playlists on the services are left as they are."
        confirmLabel="Delete sync"
        destructive
        pending={del.isPending}
        onConfirm={() =>
          del.mutate(undefined, {
            onSuccess: () => void navigate("/syncs"),
            onSettled: () => setConfirmDelete(false),
          })
        }
      />
    </div>
  );
}
