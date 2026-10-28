import { Link } from "react-router";
import type { JobOut } from "@/api/types";
import { StatusBadge } from "@/components/StatusBadge";
import { formatDateTime } from "@/lib/format";

function Label({ job }: { job: JobOut }) {
  const [runId] = job.run_ids;
  const to = job.kind === "backup" ? "/backup" : job.run_ids.length === 1 && runId ? `/runs/${runId}` : null;
  return to ? (
    <Link className="text-primary underline underline-offset-4" to={to}>
      {job.label}
    </Link>
  ) : (
    <span>{job.label}</span>
  );
}

export function RecentActivity({ jobs }: { jobs: JobOut[] }) {
  return (
    <section aria-labelledby="home-recent" className="space-y-3">
      <h2 id="home-recent" className="text-lg font-semibold">Recent activity</h2>
      {jobs.length === 0 ? <p className="text-muted-foreground">No activity yet</p> : null}
      <ul className="space-y-1">
        {jobs.map((job) => (
          <li key={job.job_id} className="flex items-center gap-2 text-sm">
            <Label job={job} />
            <StatusBadge kind="job" status={job.status} />
            <span className="text-muted-foreground">{formatDateTime(job.finished_at)}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
