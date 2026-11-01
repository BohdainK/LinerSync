import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";
import { api, call, type ApiError } from "@/api/client";
import { keys } from "@/api/keys";
import type { BackupServiceOut, JobOut } from "@/api/types";
import { ErrorNotice, QueryError } from "@/components/ErrorNotice";
import { JobProgress } from "@/components/JobProgress";
import { Section } from "@/components/Section";
import { StatusBadge } from "@/components/StatusBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatBackupResult, formatDateTime, formatRelative } from "@/lib/format";
import { linkClass } from "@/lib/styles";
import { isActive, useCancelJob, useJob } from "@/live/useJob";
import { ImportExport } from "./ImportExport";
import { PlaylistsTable } from "./PlaylistsTable";

function ServiceCard({
  s,
  busy,
  onRun,
}: {
  s: BackupServiceOut;
  busy: boolean;
  onRun: (service: string) => void;
}) {
  const run = s.last_run;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {s.display_name}
          {!s.connected ? <Badge variant="outline">Not connected</Badge> : null}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        <p>{`${s.playlists} playlists, ${s.archived} archived, ${s.missing} missing`}</p>
        {run ? (
          <div className="space-y-1">
            <p className="flex items-center gap-2">
              <StatusBadge kind="run" status={run.status} />
              <span>{formatDateTime(run.finished_at)}</span>
            </p>
            <p>{formatBackupResult(run)}</p>
            {run.errors.length > 0 ? (
              <Collapsible>
                <CollapsibleTrigger asChild>
                  <Button type="button" variant="link" size="sm" className="px-0">
                    {run.errors.length} problems
                  </Button>
                </CollapsibleTrigger>
                <CollapsibleContent>
                  <ul className="list-disc pl-5">
                    {run.errors.map((e, i) => (
                      <li key={i}>{e}</li>
                    ))}
                  </ul>
                </CollapsibleContent>
              </Collapsible>
            ) : null}
          </div>
        ) : (
          <p className="text-muted-foreground">Never backed up</p>
        )}
        {s.connected ? (
          <Button type="button" variant="outline" size="sm" disabled={busy} onClick={() => onRun(s.service)}>
            Back up {s.display_name} now
          </Button>
        ) : null}
      </CardContent>
    </Card>
  );
}

export function BackupPage() {
  const queryClient = useQueryClient();
  const summary = useQuery({ queryKey: keys.backups, queryFn: () => call(api.GET("/api/backups")) });
  const runs = useQuery({
    queryKey: keys.backupRuns,
    queryFn: () => call(api.GET("/api/backups/runs", { params: { query: { limit: 20 } } })),
  });
  const activeId = summary.data?.active_job?.job_id ?? null;
  const live = useJob(activeId);
  const job = live.data ?? summary.data?.active_job ?? null;
  const cancel = useCancelJob();
  const start = useMutation<JobOut, ApiError, string[]>({
    mutationFn: (services) => call(api.POST("/api/backups/run", { body: { services } })),
    onSuccess: (j) => {
      queryClient.setQueryData(keys.job(j.job_id), j);
      void queryClient.invalidateQueries({ queryKey: keys.backups });
    },
  });
  const data = summary.data;
  const busy = !data || start.isPending || (job !== null && isActive(job));
  const names = new Map((data?.services ?? []).map((s) => [s.service, s.display_name]));
  return (
    <div className="space-y-8">
      <header className="space-y-3">
        <h1 className="text-2xl font-semibold">Backup</h1>
        {data ? (
          data.schedule ? (
            <p className="text-sm">
              Scheduled: <code>{data.schedule}</code>
              {data.next_backup_at ? ` (next ${formatRelative(data.next_backup_at)})` : ""}
            </p>
          ) : (
            <p className="text-sm">
              Scheduled backups are off{" "}
              <Link className={linkClass} to="/settings">
                Change in Settings
              </Link>
            </p>
          )
        ) : null}
        <Button type="button" disabled={busy} onClick={() => start.mutate([])}>
          Back up all now
        </Button>
        {start.isError ? <ErrorNotice error={start.error} /> : null}
        {job && isActive(job) ? (
          <JobProgress job={job} onCancel={() => cancel.mutate(job.job_id)} />
        ) : null}
      </header>

      <QueryError query={summary} />
      <div className="grid gap-4 md:grid-cols-2">
        {data?.services.map((s) => (
          <ServiceCard key={s.service} s={s} busy={busy} onRun={(id) => start.mutate([id])} />
        ))}
      </div>

      <PlaylistsTable services={data?.services ?? []} />

      <Section title="Recent backups">
        <QueryError query={runs} />
        {runs.data && runs.data.length === 0 ? <p className="text-muted-foreground">No backups yet</p> : null}
        {runs.data && runs.data.length > 0 ? (
          <Table aria-label="Recent backups">
            <TableHeader>
              <TableRow>
                <TableHead>Service</TableHead>
                <TableHead>Finished</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Playlists</TableHead>
                <TableHead>Result</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {runs.data.map((r) => (
                <TableRow key={r.backup_id}>
                  <TableCell>{names.get(r.service) ?? r.service}</TableCell>
                  <TableCell>{formatDateTime(r.finished_at)}</TableCell>
                  <TableCell>
                    <StatusBadge kind="run" status={r.status} />
                  </TableCell>
                  <TableCell>{r.playlists}</TableCell>
                  <TableCell>{formatBackupResult(r)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        ) : null}
      </Section>

      <ImportExport />
    </div>
  );
}
