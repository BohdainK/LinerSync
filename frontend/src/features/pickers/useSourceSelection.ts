import { useState } from "react";
import { useSearchParams } from "react-router";
import { parseSourceParam } from "./sourceParam";

/** The source service and playlist selection, preset from `?source=` on first render. */
export function useSourceSelection() {
  const [params] = useSearchParams();
  const [preset] = useState(() => parseSourceParam(params.get("source")));
  const [service, setService] = useState<string | null>(preset?.service ?? null);
  const [selection, setSelection] = useState<string[]>(preset ? [preset.playlistId] : []);

  /** A new source service starts with nothing selected. */
  function changeSource(next: string) {
    setService(next);
    setSelection([]);
  }

  return { service, selection, setSelection, changeSource };
}
