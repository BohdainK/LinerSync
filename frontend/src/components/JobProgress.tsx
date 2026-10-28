import type { JobOut } from "@/api/types";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { isActive } from "@/live/useJob";

export function JobProgress({ job, onCancel }: { job: JobOut; onCancel?: () => void }) {
  const progress = job.progress;
  const determinate = progress !== null && progress.total > 0;
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <span className="font-medium">{job.label}</span>
        <StatusBadge kind="job" status={job.status} />
      </div>
      {isActive(job) ? (
        determinate ? (
          <progress
            aria-label={job.label}
            className="h-2 w-full"
            value={progress.done}
            max={progress.total}
          />
        ) : (
          <p className="text-sm text-muted-foreground">Working…</p>
        )
      ) : null}
      {progress?.message ? <p className="text-sm text-muted-foreground">{progress.message}</p> : null}
      {job.status === "failed" && job.error ? (
        <p className="text-sm text-destructive">{job.error}</p>
      ) : null}
      {isActive(job) && onCancel ? (
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={job.cancel_requested}
          onClick={onCancel}
        >
          {job.cancel_requested ? "Cancel requested" : "Cancel"}
        </Button>
      ) : null}
    </div>
  );
}
