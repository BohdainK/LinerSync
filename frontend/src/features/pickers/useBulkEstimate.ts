import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { api, call, type ApiError } from "@/api/client";
import type { BulkItemIn, EstimateOut } from "@/api/types";
import { unreadableIds } from "./unreadableIds";

/**
 * The estimate before a bulk sync or transfer (D11): `request` fetches it and opens the warning,
 * `readable` is `items` minus the playlists the estimate says can't be read.
 */
export function useBulkEstimate(items: BulkItemIn[]) {
  const [estimate, setEstimate] = useState<EstimateOut | null>(null);
  const [warnOpen, setWarnOpen] = useState(false);
  const request = useMutation<EstimateOut, ApiError, void>({
    mutationFn: () => call(api.POST("/api/transfers/estimate", { body: { items } })),
    onSuccess: (out) => {
      setEstimate(out);
      setWarnOpen(true);
    },
  });
  const skipped = unreadableIds(estimate);
  const readable = items.filter((i) => !skipped.has(i.playlist_id));
  return {
    estimate,
    clearEstimate: () => setEstimate(null),
    warnOpen,
    setWarnOpen,
    request,
    readable,
    /** Every selected playlist is unreadable, so there is nothing to start. */
    nothingToStart: estimate !== null && readable.length === 0,
  };
}
