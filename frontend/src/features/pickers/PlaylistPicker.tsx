import { useId, useState } from "react";
import { ErrorNotice } from "@/components/ErrorNotice";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { CANT_READ, CANT_READ_FALLBACK, NEW_PLAYLIST } from "@/copy";
import { usePlaylists } from "./usePlaylists";

const MAX_SHOWN = 200;

export function PlaylistPicker(props: {
  service: string;
  label: string;
  mode: "single" | "multi";
  value: string[];
  onChange(ids: string[]): void;
  allowNew?: boolean;
}) {
  const { service, mode, value, onChange } = props;
  const playlists = usePlaylists(service);
  const [filter, setFilter] = useState("");
  const name = useId();
  const needle = filter.trim().toLowerCase();
  const matches = (playlists.data ?? []).filter((p) => p.name.toLowerCase().includes(needle));
  const shown = matches.slice(0, MAX_SHOWN);
  const hidden = matches.length - shown.length;
  const single = mode === "single";

  function toggle(id: string, on: boolean) {
    if (single) onChange([id]);
    else onChange(on ? [...value, id] : value.filter((v) => v !== id));
  }

  function selectAll() {
    const add = matches.filter((p) => p.readable).map((p) => p.playlist_id);
    onChange([...value, ...add.filter((id) => !value.includes(id))]);
  }

  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-medium">{props.label}</legend>
      {playlists.isError ? (
        <ErrorNotice error={playlists.error} onRetry={() => void playlists.refetch()} />
      ) : playlists.isPending ? (
        <p className="text-sm text-muted-foreground">Loading playlists…</p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <Input
              aria-label="Filter playlists"
              placeholder="Filter playlists"
              className="max-w-xs"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
            {!single ? (
              <>
                <Button type="button" variant="outline" size="sm" onClick={selectAll}>
                  Select all
                </Button>
                <Button type="button" variant="outline" size="sm" onClick={() => onChange([])}>
                  Select none
                </Button>
              </>
            ) : null}
          </div>
          <ul className="max-h-80 space-y-1 overflow-y-auto">
            {props.allowNew && single ? (
              <li>
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="radio"
                    name={name}
                    checked={value.includes("")}
                    onChange={() => onChange([""])}
                  />
                  <span className="font-medium">{NEW_PLAYLIST}</span>
                </label>
              </li>
            ) : null}
            {shown.map((p) => (
              <li key={p.playlist_id}>
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type={single ? "radio" : "checkbox"}
                    name={name}
                    disabled={!p.readable}
                    checked={value.includes(p.playlist_id)}
                    onChange={(e) => toggle(p.playlist_id, e.target.checked)}
                  />
                  <span className="font-medium">{p.name}</span>
                  {p.track_count !== null ? (
                    <span className="text-muted-foreground">{p.track_count} songs</span>
                  ) : null}
                  {!p.readable ? (
                    <span className="text-muted-foreground">
                      {CANT_READ.replace("{reason}", p.unreadable_reason || CANT_READ_FALLBACK)}
                    </span>
                  ) : null}
                </label>
              </li>
            ))}
          </ul>
          {hidden > 0 ? (
            <p className="text-sm text-muted-foreground">{hidden} more — type to narrow down</p>
          ) : null}
          {playlists.data?.length === 0 ? <p className="text-sm text-muted-foreground">No playlists</p> : null}
        </>
      )}
    </fieldset>
  );
}
