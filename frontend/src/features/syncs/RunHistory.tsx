import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { api, call } from "@/api/client";
import { keys } from "@/api/keys";
import { ErrorNotice } from "@/components/ErrorNotice";
import { StatusBadge } from "@/components/StatusBadge";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDateTime } from "@/lib/format";

export function RunHistory({ syncId }: { syncId: string }) {
  const runs = useQuery({
    queryKey: keys.syncRuns(syncId),
    queryFn: () =>
      call(api.GET("/api/syncs/{sync_id}/runs", { params: { path: { sync_id: syncId }, query: { limit: 20 } } })),
  });
  return (
    <section aria-labelledby="sync-history" className="space-y-3">
      <h2 id="sync-history" className="text-lg font-semibold">
        Run history
      </h2>
      {runs.isError ? <ErrorNotice error={runs.error} onRetry={() => void runs.refetch()} /> : null}
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
                  <Link className="text-primary underline underline-offset-4" to={`/runs/${r.run_id}`}>
                    {formatDateTime(r.started_at)}
                  </Link>
                </TableCell>
                <TableCell>
                  {r.kind} · {r.trigger} {r.dry_run ? <Badge variant="outline">Dry run</Badge> : null}
                </TableCell>
                <TableCell>
                  <StatusBadge kind="run" status={r.status} />
                </TableCell>
                <TableCell>{`+${r.added} −${r.removed}`}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      ) : null}
    </section>
  );
}
