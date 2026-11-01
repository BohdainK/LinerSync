import type { SyncMode } from "@/api/types";
import { NotesList } from "@/components/NotesList";
import { Button } from "@/components/ui/button";
import { useConnections } from "@/features/connections/useConnections";
import { NEW_PLAYLIST_ID } from "@/features/pickers/newPlaylist";
import { PlaylistPicker } from "@/features/pickers/PlaylistPicker";
import { ServiceSelect } from "@/features/pickers/ServiceSelect";
import { ModeSelect } from "@/features/syncs/ModeSelect";
import { limitations } from "@/lib/limitations";
import type { Scope } from "./ScopeChoice";

const MAX_DESTINATIONS = 10;

export type Row = { key: number; service: string | null; selection: string[]; mode: SyncMode };

let rowSeq = 0;
// eslint-disable-next-line react-refresh/only-export-components -- the page seeds its rows with it
export function newRow(): Row {
  rowSeq += 1;
  return { key: rowSeq, service: null, selection: [], mode: "full" };
}

function DestinationRow(props: {
  index: number;
  row: Row;
  scope: Scope;
  conflict: boolean;
  removable: boolean;
  onChange(row: Row): void;
  onRemove(): void;
}) {
  const { index, row } = props;
  const n = index + 1;
  const connections = useConnections();
  const conn = connections.data?.services.find((s) => s.service === row.service);
  const notes = conn ? limitations(conn.display_name, conn.capabilities, row.mode) : [];
  return (
    <li className="space-y-3 rounded-md border p-4">
      <div className="flex flex-wrap items-end gap-3">
        <ServiceSelect
          label={`Destination ${n}`}
          value={row.service}
          onChange={(service) => props.onChange({ ...row, service, selection: [NEW_PLAYLIST_ID] })}
          writableOnly
        />
        <ModeSelect
          label={`Mode for destination ${n}`}
          value={row.mode}
          onChange={(mode) => props.onChange({ ...row, mode })}
        />
        {props.removable ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            aria-label={`Remove destination ${n}`}
            onClick={props.onRemove}
          >
            Remove
          </Button>
        ) : null}
      </div>
      {row.service !== null && props.scope === "one" ? (
        <PlaylistPicker
          service={row.service}
          label={`Playlist on destination ${n}`}
          mode="single"
          allowNew
          value={row.selection}
          onChange={(selection) => props.onChange({ ...row, selection })}
        />
      ) : null}
      {props.conflict ? (
        <p className="text-sm text-destructive">Pick a different playlist than the source</p>
      ) : null}
      <NotesList notes={notes} className="pl-6" />
    </li>
  );
}

/** The destination list with its "+ Add destination" button. `conflicts[i]` flags row i. */
export function DestinationRows({
  rows,
  onChange,
  scope,
  conflicts,
}: {
  rows: Row[];
  onChange(rows: Row[]): void;
  scope: Scope;
  conflicts: boolean[];
}) {
  return (
    <>
      <ol className="space-y-3">
        {rows.map((row, i) => (
          <DestinationRow
            key={row.key}
            index={i}
            row={row}
            scope={scope}
            conflict={conflicts[i] ?? false}
            removable={rows.length > 1}
            onChange={(next) => onChange(rows.map((r) => (r.key === row.key ? next : r)))}
            onRemove={() => onChange(rows.filter((r) => r.key !== row.key))}
          />
        ))}
      </ol>
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={rows.length >= MAX_DESTINATIONS}
        onClick={() => onChange([...rows, newRow()])}
      >
        + Add destination
      </Button>
    </>
  );
}
