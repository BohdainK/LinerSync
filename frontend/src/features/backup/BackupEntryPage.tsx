import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api, call, type ApiError } from "@/api/client";
import { keys } from "@/api/keys";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { ErrorNotice, QueryError } from "@/components/ErrorNotice";
import { Section } from "@/components/Section";
import { TrackList } from "@/components/TrackList";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ARCHIVE } from "@/features/pickers/usePlaylists";
import { useServiceNames } from "@/features/pickers/useServiceNames";
import { formatDateTime } from "@/lib/format";
import { linkClass } from "@/lib/styles";
import { cn } from "@/lib/utils";
import { RestoreDialog } from "./RestoreDialog";

function VersionTracks({ versionId }: { versionId: string }) {
  const version = useQuery({
    queryKey: keys.backupVersion(versionId),
    queryFn: () =>
      call(api.GET("/api/backups/versions/{version_id}", { params: { path: { version_id: versionId } } })),
  });
  if (version.isError) return <QueryError query={version} />;
  if (!version.data) return <p className="text-sm text-muted-foreground">Loading…</p>;
  return <TrackList numbered tracks={version.data.tracks.map((t) => t.track)} />;
}

export function BackupEntryPage() {
  const { entryId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const nameOf = useServiceNames();
  const [selected, setSelected] = useState<string | null>(null);
  const [restoring, setRestoring] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const entry = useQuery({
    queryKey: keys.backupEntry(entryId),
    queryFn: () =>
      call(api.GET("/api/backups/playlists/{entry_id}", { params: { path: { entry_id: entryId } } })),
  });
  const remove = useMutation<void, ApiError, void>({
    mutationFn: async () => {
      await call(
        api.DELETE("/api/backups/playlists/{entry_id}", { params: { path: { entry_id: entryId } } }),
      );
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.backups });
      void navigate("/backup");
    },
    onSettled: () => setConfirmDelete(false),
  });

  if (entry.isError) return <QueryError query={entry} />;
  const e = entry.data;
  if (!e) return <p className="text-muted-foreground">Loading…</p>;
  const archived = e.archived_at !== null;
  const source = `${ARCHIVE}:${encodeURIComponent(e.entry_id)}`;
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <Link className={cn("text-sm", linkClass)} to="/backup">
          Back to Backup
        </Link>
        <h1 className="text-2xl font-semibold">{e.name}</h1>
        <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          <span>{nameOf(e.service)}</span>
          {e.archived_at ? <Badge variant="secondary">Archived {formatDateTime(e.archived_at)}</Badge> : null}
          {e.missing_since && !archived ? (
            <Badge variant="outline">Missing since {formatDateTime(e.missing_since)}</Badge>
          ) : null}
        </div>
        <p className="text-sm">First backed up {formatDateTime(e.first_backed_up_at)}</p>
        <p className="text-sm">Last seen {formatDateTime(e.last_seen_at)}</p>
      </div>

      {archived ? (
        <div className="flex flex-wrap gap-2">
          <Button asChild variant="outline" size="sm">
            <Link to={`/syncs/new?source=${source}`}>Sync from this playlist</Link>
          </Button>
          <Button asChild variant="outline" size="sm">
            <Link to={`/transfers/new?source=${source}`}>Transfer…</Link>
          </Button>
          <Button type="button" variant="destructive" size="sm" onClick={() => setConfirmDelete(true)}>
            Delete from archive…
          </Button>
        </div>
      ) : null}
      {remove.isError ? <ErrorNotice error={remove.error} /> : null}

      <Section title="History">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Taken</TableHead>
              <TableHead>Name</TableHead>
              <TableHead>Songs</TableHead>
              <TableHead>Cover</TableHead>
              <TableHead>Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {e.history.map((v) => (
              <TableRow key={v.version_id}>
                <TableCell>{formatDateTime(v.taken_at)}</TableCell>
                <TableCell>{v.name}</TableCell>
                <TableCell>{v.track_count}</TableCell>
                <TableCell>
                  {v.cover_url ? (
                    <img src={v.cover_url} alt="" width={40} height={40} />
                  ) : (
                    <span aria-hidden className="inline-block size-10 rounded bg-muted" />
                  )}
                </TableCell>
                <TableCell className="space-x-2">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    aria-pressed={selected === v.version_id}
                    onClick={() => setSelected(selected === v.version_id ? null : v.version_id)}
                  >
                    Show songs
                  </Button>
                  <Button asChild variant="outline" size="sm">
                    <a href={`/api/backups/versions/${v.version_id}/csv`} download>
                      Download CSV
                    </a>
                  </Button>
                  <Button type="button" variant="outline" size="sm" onClick={() => setRestoring(v.version_id)}>
                    Restore…
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
        {selected ? <VersionTracks versionId={selected} /> : null}
      </Section>

      {restoring ? (
        <RestoreDialog
          key={restoring}
          open
          versionId={restoring}
          playlistName={e.name}
          defaultService={e.service}
          onOpenChange={(o) => {
            if (!o) setRestoring(null);
          }}
        />
      ) : null}
      <ConfirmDialog
        open={confirmDelete}
        title={`Delete ${e.name} from the archive?`}
        body="All saved versions are removed. This can't be undone."
        confirmLabel="Delete"
        destructive
        pending={remove.isPending}
        onOpenChange={setConfirmDelete}
        onConfirm={() => remove.mutate()}
      />
    </div>
  );
}
