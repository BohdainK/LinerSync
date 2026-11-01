import { useState } from "react";
import type { DestinationOut, SyncOut } from "@/api/types";
import { ChangesList } from "@/components/ChangesList";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ErrorNotice, QueryError } from "@/components/ErrorNotice";
import { Section } from "@/components/Section";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useServiceNames } from "@/features/pickers/useServiceNames";
import { formatDateTime } from "@/lib/format";
import { useRun } from "@/features/runs/useRun";
import { ModeSelect } from "./ModeSelect";
import { usePatchSync } from "./useSync";

function NotMadeDialog({ runId, member, name, onClose }: { runId: string; member: string; name: string; onClose(): void }) {
  const run = useRun(runId);
  const changes = run.data?.members.find((m) => m.member_id === member)?.not_made ?? [];
  return (
    <Dialog open onOpenChange={(open) => (open ? undefined : onClose())}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Changes not made on {name}</DialogTitle>
          <DialogDescription>These changes were skipped because this destination only adds songs.</DialogDescription>
        </DialogHeader>
        <div className="max-h-[60vh] overflow-y-auto">
          {run.isError ? (
            <QueryError query={run} />
          ) : run.isPending ? (
            <p>Loading…</p>
          ) : (
            <ChangesList changes={changes} />
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function DestinationsTable({ sync, disabled }: { sync: SyncOut; disabled: boolean }) {
  const patch = usePatchSync(sync.sync_id);
  const nameOf = useServiceNames();
  const [removing, setRemoving] = useState<DestinationOut | null>(null);
  const [viewing, setViewing] = useState<DestinationOut | null>(null);
  const notMade = sync.last_run?.not_made ?? 0;

  return (
    <Section title="Destinations">
      {patch.error ? <ErrorNotice error={patch.error} /> : null}
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Service</TableHead>
            <TableHead>Playlist</TableHead>
            <TableHead>Mode</TableHead>
            <TableHead>Last synced</TableHead>
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {sync.destinations.map((d) => {
            const name = nameOf(d.service);
            return (
              <TableRow key={d.member_id}>
                <TableCell className="font-medium">
                  {name} {d.missing ? <Badge variant="destructive">Missing</Badge> : null}
                </TableCell>
                <TableCell>{d.playlist_id ?? "Will be created"}</TableCell>
                <TableCell className="space-y-1">
                  <ModeSelect
                    aria-label={`Mode for ${name}`}
                    value={d.mode}
                    disabled={disabled || patch.isPending}
                    onChange={(mode) => patch.mutate({ modes: { [d.member_id]: mode } })}
                  />
                  {d.effective_mode !== d.mode ? (
                    <p className="text-sm text-muted-foreground">Runs as add only</p>
                  ) : null}
                  {d.limitations.map((l, i) => (
                    <p key={i} className="text-sm text-muted-foreground">
                      {l}
                    </p>
                  ))}
                </TableCell>
                <TableCell>{d.last_synced_at ? formatDateTime(d.last_synced_at) : "Never"}</TableCell>
                <TableCell className="space-x-2 text-right">
                  {d.effective_mode === "add_only" && notMade > 0 && sync.last_run ? (
                    <Button type="button" variant="outline" size="sm" onClick={() => setViewing(d)}>
                      Changes not made ({notMade})
                    </Button>
                  ) : null}
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    aria-label={`Remove ${name}`}
                    disabled={disabled || patch.isPending}
                    onClick={() => setRemoving(d)}
                  >
                    Remove
                  </Button>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(open) => {
          if (!open) setRemoving(null);
        }}
        title={`Remove ${removing ? nameOf(removing.service) : ""} from this sync?`}
        body="The playlist on the service is left as it is."
        confirmLabel="Remove"
        destructive
        pending={patch.isPending}
        onConfirm={() => {
          if (removing) patch.mutate({ remove_member_ids: [removing.member_id] }, { onSettled: () => setRemoving(null) });
        }}
      />
      {viewing && sync.last_run ? (
        <NotMadeDialog
          runId={sync.last_run.run_id}
          member={viewing.member_id}
          name={nameOf(viewing.service)}
          onClose={() => setViewing(null)}
        />
      ) : null}
    </Section>
  );
}
