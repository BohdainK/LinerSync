import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";
import { api, call, type ApiError } from "@/api/client";
import { keys } from "@/api/keys";
import type { JobOut, SyncOut, SyncPatchIn } from "@/api/types";

export function useSyncs(): UseQueryResult<SyncOut[]> {
  return useQuery({ queryKey: keys.syncs, queryFn: () => call(api.GET("/api/syncs")) });
}

export function useSync(syncId: string): UseQueryResult<SyncOut> {
  return useQuery({
    queryKey: keys.sync(syncId),
    queryFn: () => call(api.GET("/api/syncs/{sync_id}", { params: { path: { sync_id: syncId } } })),
  });
}

/** Each caller gets its own mutation state, so an error shows next to the control that caused it. */
export function usePatchSync(syncId: string): UseMutationResult<SyncOut, ApiError, SyncPatchIn> {
  const queryClient = useQueryClient();
  return useMutation<SyncOut, ApiError, SyncPatchIn>({
    mutationFn: (body) =>
      call(api.PATCH("/api/syncs/{sync_id}", { params: { path: { sync_id: syncId } }, body })),
    onSuccess: (sync) => {
      queryClient.setQueryData(keys.sync(syncId), sync);
      void queryClient.invalidateQueries({ queryKey: keys.syncs });
    },
  });
}

export function useRunSync(syncId: string): UseMutationResult<JobOut, ApiError, boolean> {
  const queryClient = useQueryClient();
  return useMutation<JobOut, ApiError, boolean>({
    mutationFn: (dryRun) =>
      call(
        api.POST("/api/syncs/{sync_id}/run", {
          params: { path: { sync_id: syncId } },
          body: { dry_run: dryRun },
        }),
      ),
    onSuccess: (job) => {
      queryClient.setQueryData(keys.job(job.job_id), job);
      void queryClient.invalidateQueries({ queryKey: keys.sync(syncId) });
    },
  });
}

export function useDeleteSync(syncId: string): UseMutationResult<unknown, ApiError, void> {
  const queryClient = useQueryClient();
  return useMutation<unknown, ApiError, void>({
    mutationFn: () =>
      call(api.DELETE("/api/syncs/{sync_id}", { params: { path: { sync_id: syncId } } })),
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: keys.sync(syncId) });
      void queryClient.invalidateQueries({ queryKey: keys.syncs });
    },
  });
}

export type BulkPatch = { syncIds: string[]; body: SyncPatchIn };
export type BulkPatchResult = { updated: SyncOut[]; failed: { syncId: string; error: unknown }[] };

/** Applies one patch to many syncs. Each is patched on its own, so one bad sync doesn't block the rest. */
export function useBulkPatchSyncs(): UseMutationResult<BulkPatchResult, ApiError, BulkPatch> {
  const queryClient = useQueryClient();
  return useMutation<BulkPatchResult, ApiError, BulkPatch>({
    mutationFn: async ({ syncIds, body }) => {
      const results = await Promise.allSettled(
        syncIds.map((id) => call(api.PATCH("/api/syncs/{sync_id}", { params: { path: { sync_id: id } }, body }))),
      );
      const updated: SyncOut[] = [];
      const failed: BulkPatchResult["failed"] = [];
      results.forEach((r, i) => {
        if (r.status === "fulfilled") updated.push(r.value);
        else failed.push({ syncId: syncIds[i] ?? "", error: r.reason });
      });
      return { updated, failed };
    },
    onSuccess: ({ updated }) => {
      for (const sync of updated) queryClient.setQueryData(keys.sync(sync.sync_id), sync);
      void queryClient.invalidateQueries({ queryKey: keys.syncs });
      void queryClient.invalidateQueries({ queryKey: keys.home });
    },
  });
}
