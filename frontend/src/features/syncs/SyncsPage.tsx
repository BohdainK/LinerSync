import { Link } from "react-router";
import { QueryError } from "@/components/ErrorNotice";
import { JobProgress } from "@/components/JobProgress";
import { StatusBadge } from "@/components/StatusBadge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useServiceNames } from "@/features/pickers/useServiceNames";
import { formatChangeCounts, formatRelative } from "@/lib/format";
import { linkClass } from "@/lib/styles";
import { useSyncs } from "./useSync";

export function SyncsPage() {
  const syncs = useSyncs();
  const nameOf = useServiceNames();
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Syncs</h1>
        <Link className={linkClass} to="/syncs/new">
          + New sync
        </Link>
      </div>
      <QueryError query={syncs} />
      {syncs.data?.length === 0 ? (
        <div className="space-y-1">
          <p className="text-muted-foreground">No syncs yet</p>
          <Link className={linkClass} to="/syncs/new">
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
                  <Link className={linkClass} to={`/syncs/${s.sync_id}`}>
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
                      <span>{formatChangeCounts(s.last_run)}</span>
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
