import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { api, call } from "@/api/client";
import { keys } from "@/api/keys";
import { QueryError } from "@/components/ErrorNotice";
import { Section } from "@/components/Section";
import { StatusBadge } from "@/components/StatusBadge";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatChangeCounts, formatDateTime } from "@/lib/format";
import { linkClass } from "@/lib/styles";

export function RunHistory({ syncId }: { syncId: string }) {
  const runs = useQuery({
    queryKey: keys.syncRuns(syncId),
    queryFn: () =>
      call(api.GET("/api/syncs/{sync_id}/runs", { params: { path: { sync_id: syncId }, query: { limit: 20 } } })),
  });
  return (
    <Section title="Run history">
      <QueryError query={runs} />
      {runs.data?.length === 0 ? <p className="text-muted-foreground">No runs yet</p> : null}
      {runs.data && runs.data.length > 0 ? (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Started</TableHead>
              <TableHead>Kind</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Changes</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {runs.data.map((r) => (
              <TableRow key={r.run_id}>
                <TableCell>
                  <Link className={linkClass} to={`/runs/${r.run_id}`}>
                    {formatDateTime(r.started_at)}
                  </Link>
                </TableCell>
                <TableCell>
                  {r.kind} · {r.trigger} {r.dry_run ? <Badge variant="outline">Dry run</Badge> : null}
                </TableCell>
                <TableCell>
                  <StatusBadge kind="run" status={r.status} />
                </TableCell>
                <TableCell>{formatChangeCounts(r)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : null}
    </Section>
  );
}
