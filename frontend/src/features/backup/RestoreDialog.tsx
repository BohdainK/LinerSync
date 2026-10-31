import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { api, call, type ApiError } from "@/api/client";
import type { JobOut } from "@/api/types";
import { ErrorNotice } from "@/components/ErrorNotice";
import { JobProgress } from "@/components/JobProgress";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useConnections } from "@/features/connections/useConnections";
import { ServiceSelect } from "@/features/pickers/ServiceSelect";
import { isActive, useCancelJob, useJob } from "@/live/useJob";

export function RestoreDialog(props: {
  versionId: string;
  playlistName: string;
  defaultService: string;
  open: boolean;
  onOpenChange(open: boolean): void;
}) {
  const [chosen, setDestination] = useState(props.defaultService);
  const connections = useConnections();
  // A read-only service (e.g. Jellyfin) can't be restored to, so it's never the default.
  const readOnly = connections.data?.services.some((s) => s.service === chosen && !s.capabilities.includes("add"));
  const destination = readOnly ? "" : chosen;
  const [dryRun, setDryRun] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const job = useJob(jobId);
  const cancel = useCancelJob();
  const restore = useMutation<JobOut, ApiError, void>({
    mutationFn: () =>
      call(
        api.POST("/api/backups/versions/{version_id}/restore", {
          params: { path: { version_id: props.versionId } },
          body: { destination, dry_run: dryRun },
        }),
      ),
    onSuccess: (j) => setJobId(j.job_id),
  });
  // Stays disabled from the 202 until the job ends, so a second click can't start a duplicate.
  const started = restore.isSuccess && (!job.data || isActive(job.data));
  return (
    <Dialog open={props.open} onOpenChange={props.onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Restore {props.playlistName}</DialogTitle>
          <DialogDescription>Restoring always creates a new playlist.</DialogDescription>
        </DialogHeader>
        <ServiceSelect label="Destination" value={destination} onChange={setDestination} writableOnly />
        <div className="flex items-center gap-2">
          <Checkbox
            id="restore-dry-run"
            checked={dryRun}
            onCheckedChange={(c) => setDryRun(c === true)}
          />
          <label htmlFor="restore-dry-run" className="text-sm">
            Dry run
          </label>
        </div>
        {restore.isError ? <ErrorNotice error={restore.error} /> : null}
        {job.data ? <JobProgress job={job.data} onCancel={() => cancel.mutate(job.data.job_id)} /> : null}
        <DialogFooter>
          <Button type="button" disabled={!destination || restore.isPending || started} onClick={() => restore.mutate()}>
            Restore
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
