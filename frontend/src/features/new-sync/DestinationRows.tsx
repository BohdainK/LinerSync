import { useId } from "react";
import type { DestinationIn, SyncMode } from "@/api/types";
import { NotesList } from "@/components/NotesList";
import { Button } from "@/components/ui/button";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { useConnections } from "@/features/connections/useConnections";
import { chosenPlaylistId, NEW_PLAYLIST_ID } from "@/features/pickers/newPlaylist";
import { PlaylistPicker } from "@/features/pickers/PlaylistPicker";
import { usePlaylists } from "@/features/pickers/usePlaylists";
import { ServiceSelect } from "@/features/pickers/ServiceSelect";
import { ModeSelect } from "@/features/syncs/ModeSelect";
import { limitations } from "@/lib/limitations";
import type { Scope } from "./ScopeChoice";

const MAX_DESTINATIONS = 10;

export type Row = { key: number; service: string | null; selection: string[]; mode: SyncMode; existing: DestinationIn["existing"] };

let rowSeq = 0;
// eslint-disable-next-line react-refresh/only-export-components -- the page seeds its rows with it
export function newRow(): Row {
  rowSeq += 1;
  return { key: rowSeq, service: null, selection: [], mode: "full", existing: "merge" };
}

const EXISTING_OPTIONS: { value: DestinationIn["existing"]; label: string; hint: string }[] = [
  { value: "merge", label: "Merge", hint: "Keep its songs; they\u2019ll count as yours" },
  { value: "overwrite", label: "Overwrite", hint: "Make it match the source on the first run" },
];

/** Asks what to do with the songs a chosen destination playlist already has. */
function ExistingChoice({ count, value, onChange }: { count: number; value: DestinationIn["existing"]; onChange(v: DestinationIn["existing"]): void }) {
  const id = useId();
  return (
    <div className="space-y-2 rounded-md border border-amber-500/50 bg-amber-500/10 p-3">
      <p className="text-sm font-medium">This playlist already has {count} {count === 1 ? "song" : "songs"}.</p>
      <RadioGroup
        aria-label="What to do with its songs"
        value={value}
        onValueChange={(v) => onChange(v === "overwrite" ? "overwrite" : "merge")}
      >
        {EXISTING_OPTIONS.map((o) => (
          <div key={o.value} className="flex items-start gap-2">
            <RadioGroupItem id={`${id}-${o.value}`} value={o.value} className="mt-0.5" />
            <label htmlFor={`${id}-${o.value}`} className="text-sm">
              <span className="font-medium">{o.label}</span>
              <span className="block text-muted-foreground">{o.hint}</span>
            </label>
          </div>
        ))}
      </RadioGroup>
    </div>
  );
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
  const playlists = usePlaylists(row.service);
  const chosen = chosenPlaylistId(row.selection);
  const songs = chosen === null ? 0 : (playlists.data?.find((p) => p.playlist_id === chosen)?.track_count ?? 0);
  const notes = conn ? limitations(conn.display_name, conn.capabilities, row.mode) : [];
  return (
    <li className="space-y-3 rounded-md border p-4">
      <div className="flex flex-wrap items-end gap-3">
        <ServiceSelect
          label={`Destination ${n}`}
          value={row.service}
          onChange={(service) => props.onChange({ ...row, service, selection: [NEW_PLAYLIST_ID], existing: "merge" })}
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
          onChange={(selection) => props.onChange({ ...row, selection, existing: "merge" })}
        />
      ) : null}
      {props.scope === "one" && songs > 0 ? (
        <ExistingChoice count={songs} value={row.existing} onChange={(existing) => props.onChange({ ...row, existing })} />
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
