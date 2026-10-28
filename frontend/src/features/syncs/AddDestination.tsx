import { useId, useState } from "react";
import type { SyncMode, SyncOut } from "@/api/types";
import { ErrorNotice } from "@/components/ErrorNotice";
import { Button } from "@/components/ui/button";
import { PlaylistPicker } from "@/features/pickers/PlaylistPicker";
import { ServiceSelect, selectClass } from "@/features/pickers/ServiceSelect";
import { MODE_LABELS } from "./modes";
import { usePatchSync } from "./useSync";

export function AddDestination({ sync, disabled }: { sync: SyncOut; disabled: boolean }) {
  const patch = usePatchSync(sync.sync_id);
  const [service, setService] = useState<string | null>(null);
  const [selection, setSelection] = useState<string[]>([]);
  const [mode, setMode] = useState<SyncMode>("full");
  const modeId = useId();

  function add() {
    if (service === null || selection.length !== 1) return;
    patch.mutate(
      { add_destinations: [{ service, playlist_id: selection[0] || null, mode }] },
      {
        onSuccess: () => {
          setService(null);
          setSelection([]);
          setMode("full");
        },
      },
    );
  }

  return (
    <section aria-labelledby="sync-add" className="space-y-3">
      <h2 id="sync-add" className="text-lg font-semibold">
        Add destination
      </h2>
      <ServiceSelect
        label="Service"
        value={service}
        onChange={(s) => {
          setService(s);
          setSelection([]);
        }}
      />
      {service !== null ? (
        <PlaylistPicker
          service={service}
          label="Playlist"
          mode="single"
          allowNew
          value={selection}
          onChange={setSelection}
        />
      ) : null}
      <div className="flex flex-col gap-1">
        <label htmlFor={modeId} className="text-sm font-medium">
          Mode
        </label>
        <select
          id={modeId}
          className={selectClass}
          value={mode}
          onChange={(e) => setMode(e.target.value as SyncMode)}
        >
          {(Object.keys(MODE_LABELS) as SyncMode[]).map((m) => (
            <option key={m} value={m}>
              {MODE_LABELS[m]}
            </option>
          ))}
        </select>
      </div>
      {patch.error ? <ErrorNotice error={patch.error} /> : null}
      <Button
        type="button"
        disabled={disabled || patch.isPending || service === null || selection.length !== 1}
        onClick={add}
      >
        Add destination
      </Button>
    </section>
  );
}
