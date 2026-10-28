import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { api, call } from "@/api/client";
import { keys } from "@/api/keys";
import { ErrorNotice } from "@/components/ErrorNotice";
import { JobProgress } from "@/components/JobProgress";
import { StatusBadge } from "@/components/StatusBadge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useServiceNames } from "@/features/pickers/useServiceNames";
import { formatRelative } from "@/lib/format";

export function SyncsPage() {
  const syncs = useQuery({ queryKey: keys.syncs, queryFn: () => call(api.GET("/api/syncs")) });
  const nameOf = useServiceNames();
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Syncs</h1>
        <Link className="text-primary underline underline-offset-4" to="/syncs/new">
          + New sync
        </Link>
      </div>
      {syncs.isError ? <ErrorNotice error={syncs.error} onRetry={() => void syncs.refetch()} /> : null}
      {syncs.data?.length === 0 ? (
        <div className="space-y-1">
          <p className="text-muted-foreground">No syncs yet</p>
          <Link className="text-primary underline underline-offset-4" to="/syncs/new">
            Create your first sync
          </Link>
        </div>
      ) : null}
      {syncs.data && syncs.data.length > 0 ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Route</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Next run</TableHead>
              <TableHead>Last run</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {syncs.data.map((s) => (
              <TableRow key={s.sync_id}>
                <TableCell>
                  <Link className="text-primary underline underline-offset-4" to={`/syncs/${s.sync_id}`}>
                    {s.name}
                  </Link>
                </TableCell>
                <TableCell>
                  {nameOf(s.source.service)} → {s.destinations.map((d) => nameOf(d.service)).join(", ")}
                </TableCell>
                <TableCell>{s.status === "active" ? "Active" : `Paused: ${s.pause_reason ?? ""}`}</TableCell>
                <TableCell>{s.next_run_at ? formatRelative(s.next_run_at) : "—"}</TableCell>
                <TableCell className="space-y-1">
                  {s.active_job ? (
                    <JobProgress job={s.active_job} />
                  ) : s.last_run ? (
                    <span className="flex items-center gap-2">
                      <StatusBadge kind="run" status={s.last_run.status} />
                      <span>{`+${s.last_run.added} −${s.last_run.removed}`}</span>
                    </span>
                  ) : (
                    "—"
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : null}
    </div>
  );
}
