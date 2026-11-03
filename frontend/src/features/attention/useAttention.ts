import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { toast } from "sonner";
import { api, call, type ApiError } from "@/api/client";
import { keys } from "@/api/keys";
import type { ActionOut, AttentionOut } from "@/api/types";

export function useAttention(): UseQueryResult<AttentionOut[]> {
  return useQuery({
    queryKey: keys.attention,
    queryFn: () => call(api.GET("/api/attention")),
  });
}

type AttentionAction =
  | { type: "pick"; trackId: string }
  | { type: "not-on-service" }
  | { type: "dismiss" }
  | { type: "approve"; runNow: boolean }
  | { type: "resume"; runNow: boolean };

async function run(itemId: string, a: AttentionAction): Promise<ActionOut> {
  const path = { params: { path: { item_id: itemId } } };
  switch (a.type) {
    case "pick":
      await call(
        api.POST("/api/attention/{item_id}/pick", {
          ...path,
          body: { target_track_id: a.trackId },
        }),
      );
      return { job: null };
    case "not-on-service":
      await call(api.POST("/api/attention/{item_id}/not-on-service", path));
      return { job: null };
    case "dismiss":
      await call(api.POST("/api/attention/{item_id}/dismiss", path));
      return { job: null };
    case "approve":
      return call(api.POST("/api/attention/{item_id}/approve", { ...path, body: { run_now: a.runNow } }));
    case "resume":
      return call(api.POST("/api/attention/{item_id}/resume", { ...path, body: { run_now: a.runNow } }));
  }
}

export function useAttentionAction(
  itemId: string,
  onDone: () => void,
): UseMutationResult<ActionOut, ApiError, AttentionAction> {
  const queryClient = useQueryClient();
  return useMutation<ActionOut, ApiError, AttentionAction>({
    mutationFn: (a) => run(itemId, a),
    onSuccess: (out) => {
      for (const queryKey of [keys.attention, keys.home, keys.syncs]) {
        void queryClient.invalidateQueries({ queryKey });
      }
      if (out.job) toast(`Started: ${out.job.label}`);
      onDone();
    },
  });
}

/** Searches the target service more loosely for an unmatched song; the item
 * comes back with the extra candidates, which can then be picked. */
export function useSimilarSongs(itemId: string): UseMutationResult<AttentionOut, ApiError, void> {
  const queryClient = useQueryClient();
  return useMutation<AttentionOut, ApiError, void>({
    mutationFn: () => call(api.POST("/api/attention/{item_id}/similar", { params: { path: { item_id: itemId } } })),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: keys.attention }),
  });
}
