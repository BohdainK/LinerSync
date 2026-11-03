import { useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";
import { errorMessage } from "@/api/client";
import type { SyncOut, SyncPatchIn } from "@/api/types";
import { QueryError } from "@/components/ErrorNotice";
import { JobProgress } from "@/components/JobProgress";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useServiceNames } from "@/features/pickers/useServiceNames";
import { describeCron } from "@/lib/cron";
import { formatChangeCounts, formatRelative } from "@/lib/format";
import { linkClass } from "@/lib/styles";
import { cn } from "@/lib/utils";
import { BulkScheduleDialog } from "./BulkScheduleDialog";
import { useBulkPatchSyncs, useSyncs, type BulkPatchResult } from "./useSync";

function Schedule({ sync }: { sync: SyncOut }) {
  return (
    <span className="flex items-baseline gap-2">
      <span>{describeCron(sync.effective_schedule)}</span>
      {sync.schedule === null ? <span className="text-xs text-muted-foreground">default</span> : null}
    </span>
  );
}

function plural(n: number) {
  return `${n} ${n === 1 ? "sync" : "syncs"}`;
}

export function SyncsPage() {
  const syncs = useSyncs();
  const nameOf = useServiceNames();
  const bulk = useBulkPatchSyncs();
  const [picked, setPicked] = useState<ReadonlySet<string>>(new Set());
  const [scheduling, setScheduling] = useState(false);
  const [scheduleFailed, setScheduleFailed] = useState<BulkPatchResult["failed"]>([]);

  const all = syncs.data ?? [];
  // Syncs deleted elsewhere drop out of the selection on their own.
  const selected = all.filter((s) => picked.has(s.sync_id)).map((s) => s.sync_id);
  const allPicked = all.length > 0 && selected.length === all.length;
  const idle = selected.length === 0 || bulk.isPending;

  const toggle = (id: string, on: boolean) => {
    const next = new Set(picked);
    if (on) next.add(id);
    else next.delete(id);
    setPicked(next);
  };

  const apply = (body: SyncPatchIn, done: string, onDone?: (failed: BulkPatchResult["failed"]) => void) =>
    bulk.mutate(
      { syncIds: selected, body },
      {
        onSuccess: ({ updated, failed }) => {
          if (updated.length > 0) toast.success(`${plural(updated.length)} ${done}`);
          if (failed.length > 0 && !onDone) toast.error(`${plural(failed.length)} not updated: ${errorMessage(failed[0]?.error)}`);
          setPicked(new Set(failed.map((f) => f.syncId)));
          onDone?.(failed);
        },
      },
    );

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
      {all.length > 0 ? (
        <div className="space-y-3">
          {/* Always rendered, so picking a row never shifts the table under the pointer. */}
          <div role="toolbar" aria-label="Selected syncs" className="flex flex-wrap items-center gap-2 rounded-md bg-muted px-3 py-2">
            <span className={cn("mr-auto text-sm", selected.length > 0 ? "font-medium" : "text-muted-foreground")}>
              {selected.length > 0 ? `${selected.length} selected` : "Select syncs to pause, resume or reschedule them together"}
            </span>
            <Button size="sm" variant="outline" disabled={idle} onClick={() => apply({ paused: false }, "resumed")}>
              Resume
            </Button>
            <Button size="sm" variant="outline" disabled={idle} onClick={() => apply({ paused: true }, "paused")}>
              Pause
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={idle}
                onClick={() => {
                setScheduleFailed([]);
                setScheduling(true);
              }}
            >
              Change schedule…
            </Button>
            <Button size="sm" variant="ghost" disabled={selected.length === 0} onClick={() => setPicked(new Set())}>
              Clear
            </Button>
          </div>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-8">
                  <Checkbox
                    aria-label="Select all syncs"
                    checked={allPicked ? true : selected.length > 0 ? "indeterminate" : false}
                    onCheckedChange={(c) => setPicked(new Set(c === true ? all.map((s) => s.sync_id) : []))}
                  />
                </TableHead>
                <TableHead>Name</TableHead>
                <TableHead>Route</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Schedule</TableHead>
                <TableHead>Next run</TableHead>
                <TableHead>Last run</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {all.map((s) => (
                <TableRow
                  key={s.sync_id}
                  data-state={picked.has(s.sync_id) ? "selected" : undefined}
                  className="data-[state=selected]:bg-accent"
                >
                  <TableCell>
                    <Checkbox
                      aria-label={`Select ${s.name}`}
                      checked={picked.has(s.sync_id)}
                      onCheckedChange={(c) => toggle(s.sync_id, c === true)}
                    />
                  </TableCell>
                  <TableCell>
                    <Link className={linkClass} to={`/syncs/${s.sync_id}`}>
                      {s.name}
                    </Link>
                  </TableCell>
                  <TableCell>
                    {nameOf(s.source.service)} → {s.destinations.map((d) => nameOf(d.service)).join(", ")}
                  </TableCell>
                  <TableCell>{s.status === "active" ? "Active" : `Paused: ${s.pause_reason ?? ""}`}</TableCell>
                  <TableCell>
                    <Schedule sync={s} />
                  </TableCell>
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
        </div>
      ) : null}
      {scheduling ? (
        <BulkScheduleDialog
          count={selected.length}
          open
          onOpenChange={setScheduling}
          pending={bulk.isPending}
          failed={scheduleFailed}
          onApply={(schedule) =>
            apply({ schedule }, "rescheduled", (failed) => {
              setScheduleFailed(failed);
              if (failed.length === 0) setScheduling(false);
            })
          }
        />
      ) : null}
    </div>
  );
}
