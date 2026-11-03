import { Link } from "react-router";
import type { JobOut } from "@/api/types";
import { jobHref } from "@/features/history/jobHref";
import { Section } from "@/components/Section";
import { StatusBadge } from "@/components/StatusBadge";
import { formatDateTime } from "@/lib/format";
import { linkClass } from "@/lib/styles";

function Label({ job }: { job: JobOut }) {
  const to = jobHref(job);
  return to ? (
    <Link className={linkClass} to={to}>
      {job.label}
    </Link>
  ) : (
    <span>{job.label}</span>
  );
}

export function RecentActivity({ jobs }: { jobs: JobOut[] }) {
  return (
    <Section title="Recent activity">
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
    </Section>
  );
}
