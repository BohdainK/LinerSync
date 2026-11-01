import { useState } from "react";
import type { SyncMode, SyncOut } from "@/api/types";
import { ErrorNotice } from "@/components/ErrorNotice";
import { Section } from "@/components/Section";
import { Button } from "@/components/ui/button";
import { chosenPlaylistId } from "@/features/pickers/newPlaylist";
import { PlaylistPicker } from "@/features/pickers/PlaylistPicker";
import { ServiceSelect } from "@/features/pickers/ServiceSelect";
import { ModeSelect } from "./ModeSelect";
import { usePatchSync } from "./useSync";

export function AddDestination({ sync, disabled }: { sync: SyncOut; disabled: boolean }) {
  const patch = usePatchSync(sync.sync_id);
  const [service, setService] = useState<string | null>(null);
  const [selection, setSelection] = useState<string[]>([]);
  const [mode, setMode] = useState<SyncMode>("full");

  function add() {
    if (service === null || selection.length !== 1) return;
    patch.mutate(
      { add_destinations: [{ service, playlist_id: chosenPlaylistId(selection), mode }] },
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
    <Section title="Add destination">
      <ServiceSelect
        label="Service"
        value={service}
        writableOnly
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
      <ModeSelect label="Mode" value={mode} onChange={setMode} />
      {patch.error ? <ErrorNotice error={patch.error} /> : null}
      <Button
        type="button"
        disabled={disabled || patch.isPending || service === null || selection.length !== 1}
        onClick={add}
      >
        Add destination
      </Button>
    </Section>
  );
}
