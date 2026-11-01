import type { JobOut } from "@/api/types";
import { JobProgress } from "@/components/JobProgress";
import { Section } from "@/components/Section";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { useCancelJob } from "@/live/useJob";

export function RunningNow({ running, queued }: { running: JobOut[]; queued: JobOut[] }) {
  const cancel = useCancelJob();
  const busy = (id: string) => cancel.isPending && cancel.variables === id;
  return (
    <Section title="Running now">
      {running.length === 0 && queued.length === 0 ? (
        <p className="text-muted-foreground">Nothing running</p>
      ) : null}
      {running.map((job) => (
        <JobProgress
          key={job.job_id}
          job={busy(job.job_id) ? { ...job, cancel_requested: true } : job}
          onCancel={() => cancel.mutate(job.job_id)}
        />
      ))}
      {queued.length > 0 ? (
        <ul className="space-y-1">
          {queued.map((job) => (
            <li key={job.job_id} className="flex items-center gap-2 text-sm">
              <span>{job.label}</span>
              <StatusBadge kind="job" status={job.status} />
              <Button
                type="button"
                variant="outline"
                size="sm"
                aria-label={`Cancel ${job.label}`}
                disabled={job.cancel_requested || busy(job.job_id)}
                onClick={() => cancel.mutate(job.job_id)}
              >
                Cancel
              </Button>
            </li>
          ))}
        </ul>
      ) : null}
    </Section>
  );
}
