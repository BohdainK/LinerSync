import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { api, call } from "@/api/client";
import { keys } from "@/api/keys";
import { QueryError } from "@/components/ErrorNotice";
import { StatusBadge } from "@/components/StatusBadge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { JOB_KIND_LABELS } from "@/copy";
import { formatDateTime } from "@/lib/format";
import { linkClass } from "@/lib/styles";
import { jobHref } from "./jobHref";

export function HistoryPage() {
  const jobs = useQuery({
    queryKey: keys.jobHistory,
    queryFn: () => call(api.GET("/api/jobs", { params: { query: { state: "recent" } } })),
    // Previews are dry looks before an action, not actions of their own.
    select: (all) => all.filter((j) => j.kind !== "preview"),
  });
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">History</h1>
      <QueryError query={jobs} />
      {jobs.data?.length === 0 ? <p className="text-muted-foreground">Nothing has run yet</p> : null}
      {jobs.data && jobs.data.length > 0 ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Action</TableHead>
              <TableHead>Kind</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Finished</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {jobs.data.map((job) => {
              const to = jobHref(job);
              return (
                <TableRow key={job.job_id}>
                  <TableCell>
                    {to ? (
                      <Link className={linkClass} to={to}>
                        {job.label}
                      </Link>
                    ) : (
                      job.label
                    )}
                  </TableCell>
                  <TableCell>{JOB_KIND_LABELS[job.kind] ?? job.kind}</TableCell>
                  <TableCell>
                    <StatusBadge kind="job" status={job.status} />
                  </TableCell>
                  <TableCell>{job.finished_at ? formatDateTime(job.finished_at) : "—"}</TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      ) : null}
    </div>
  );
}
