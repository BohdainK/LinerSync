import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";
import { api, call, type ApiError } from "@/api/client";
import { keys } from "@/api/keys";
import type { BulkItemIn, DestinationIn, JobOut, PlaylistOut, SyncBulkIn, SyncCreateIn, SyncOut } from "@/api/types";
import { usePicks } from "@/features/matches/usePicks";
import { useJob } from "@/live/useJob";
import { ErrorNotice } from "@/components/ErrorNotice";
import { Section } from "@/components/Section";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { BulkWarningDialog } from "@/features/pickers/BulkWarningDialog";
import { chosenPlaylistId } from "@/features/pickers/newPlaylist";
import { PlaylistPicker } from "@/features/pickers/PlaylistPicker";
import { ServiceSelect } from "@/features/pickers/ServiceSelect";
import { useBulkEstimate } from "@/features/pickers/useBulkEstimate";
import { usePlaylists } from "@/features/pickers/usePlaylists";
import { useServiceNames } from "@/features/pickers/useServiceNames";
import { useSourceSelection } from "@/features/pickers/useSourceSelection";
import { useSettings } from "@/features/settings/useSettings";
import { ScheduleField } from "@/features/syncs/ScheduleField";
import { DestinationRows, newRow, type Row } from "./DestinationRows";
import { BulkPreview } from "./BulkPreview";
import { PreviewPanel } from "./PreviewPanel";
import { ScopeChoice, type Scope } from "./ScopeChoice";
import { usePreview } from "./usePreview";

const CREATED = "Sync created. It runs on its schedule, or press Sync now.";

/** Bulk syncs are named after their source playlist (D11); the server allows 1–200 characters. */
function nameFor(p: PlaylistOut | undefined, playlistId: string): string {
  return (p?.name.trim() || playlistId).slice(0, 200);
}

export function NewSyncPage() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const nameOf = useServiceNames();
  const { service, selection, setSelection, changeSource } = useSourceSelection();
  const [scope, setScope] = useState<Scope>("one");
  // null: every readable playlist (the default in all-playlists mode).
  const [allSelection, setAllSelection] = useState<string[] | null>(null);
  const [rows, setRows] = useState<Row[]>(() => [newRow()]);
  // null: follow the source playlist's name.
  const [customName, setCustomName] = useState<string | null>(null);
  const [schedule, setSchedule] = useState<string | null>(null);
  const nameId = useId();
  const picks = usePicks();

  const playlists = usePlaylists(service);
  const settings = useSettings();
  const byId = new Map((playlists.data ?? []).map((p) => [p.playlist_id, p]));
  const sourceId = selection[0] || null;
  const sourceName = sourceId ? byId.get(sourceId)?.name : undefined;
  const name = customName ?? sourceName ?? "";
  const allIds = allSelection ?? (playlists.data ?? []).filter((p) => p.readable).map((p) => p.playlist_id);

  const conflicts = rows.map(
    (r) => scope === "one" && sourceId !== null && r.service === service && r.selection[0] === sourceId,
  );
  const servicesChosen = rows.every((r) => r.service !== null);
  const destinations: DestinationIn[] = rows.map((r) => ({
    service: r.service ?? "",
    playlist_id: scope === "one" ? chosenPlaylistId(r.selection) : null,
    mode: r.mode,
    // Bulk syncs always create new playlists, so there is nothing to merge into.
    existing: scope === "one" ? r.existing : "merge",
  }));
  const oneReady = service !== null && sourceId !== null && servicesChosen && !conflicts.some(Boolean);
  const cron = schedule === null ? null : schedule.trim();

  const previewInput: SyncCreateIn | null =
    scope === "one" && oneReady && playlists.data
      ? { name: nameFor(byId.get(sourceId), sourceId), source: { service, playlist_id: sourceId }, destinations, schedule: null }
      : null;
  const preview = usePreview(previewInput);

  // Everything the previews were made from. When it changes the old previews and their picks are dropped.
  const bulkKey = JSON.stringify([service, allIds, destinations]);
  const formKey = JSON.stringify([scope, previewInput, bulkKey]);
  const [picksKey, setPicksKey] = useState(formKey);
  if (picksKey !== formKey) {
    setPicksKey(formKey);
    picks.clear();
  }

  const create = useMutation<SyncOut, ApiError, SyncCreateIn>({
    mutationFn: (body) => call(api.POST("/api/syncs", { body })),
    onSuccess: (sync) => {
      void queryClient.invalidateQueries({ queryKey: keys.syncs });
      toast(CREATED);
      navigate(`/syncs/${sync.sync_id}`);
    },
  });

  const bulkItems: BulkItemIn[] = allIds.map((id) => ({
    source_service: service ?? "",
    playlist_id: id,
    destination_services: rows.map((r) => r.service ?? ""),
  }));
  const bulkEstimate = useBulkEstimate(bulkItems);

  const bulk = useMutation<SyncOut[], ApiError, SyncBulkIn>({
    mutationFn: (body) => call(api.POST("/api/syncs/bulk", { body })),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.syncs });
      navigate("/syncs");
    },
  });

  // The bulk preview job and the syncs it previews (`run_ids[i]` belongs to `syncs[i]`).
  const [bulkPreview, setBulkPreview] = useState<{ key: string; jobId: string; syncs: SyncCreateIn[] } | null>(null);
  const previewBulk = useMutation<JobOut, ApiError, { key: string; body: SyncBulkIn }>({
    mutationFn: ({ body }) => call(api.POST("/api/syncs/preview/bulk", { body })),
    onSuccess: (job, { key, body }) => {
      queryClient.setQueryData(keys.job(job.job_id), job);
      picks.clear();
      setBulkPreview({ key, jobId: job.job_id, syncs: body.syncs });
    },
    onSettled: () => bulkEstimate.setWarnOpen(false),
  });
  const currentBulk = scope === "all" && bulkPreview?.key === bulkKey ? bulkPreview : null;
  const bulkJob = useJob(currentBulk?.jobId ?? null).data;

  function submitOne() {
    if (!oneReady) return;
    create.mutate({
      name: name.trim(),
      source: { service, playlist_id: sourceId },
      destinations,
      schedule: cron,
      ...(preview.runId ? { start: { preview_run_id: preview.runId, picks: picks.toApi() } } : {}),
    });
  }

  function previewAll() {
    if (service === null || bulkEstimate.estimate === null || bulkEstimate.nothingToStart) return;
    previewBulk.mutate({
      key: bulkKey,
      body: {
        syncs: bulkEstimate.readable.map(({ playlist_id: id }) => ({
          name: nameFor(byId.get(id), id),
          source: { service, playlist_id: id },
          destinations,
          schedule: null,
        })),
      },
    });
  }

  function startBulk() {
    if (!currentBulk) return;
    const all = picks.toApi(); // picks are global per song and service, so every sync gets them all
    bulk.mutate({
      syncs: currentBulk.syncs.map((s, i) => {
        const runId = bulkJob?.run_ids[i];
        return runId ? { ...s, schedule: cron, start: { preview_run_id: runId, picks: all } } : s;
      }),
    });
  }

  const scheduleOk = cron !== "";
  const canCreate =
    oneReady && name.trim() !== "" && scheduleOk && preview.status === "ready" && !create.isPending;
  const bulkReady =
    currentBulk !== null &&
    bulkJob?.status === "succeeded" &&
    bulkJob.run_ids.length === currentBulk.syncs.length &&
    scheduleOk &&
    !bulk.isPending;
  const canBulk =
    service !== null &&
    allIds.length > 0 &&
    servicesChosen &&
    scheduleOk &&
    !bulkEstimate.request.isPending &&
    !previewBulk.isPending &&
    !bulk.isPending;
  const n = allIds.length;
  const previewDestinations = rows.map((r) => ({ service: r.service ?? "", name: r.service ? nameOf(r.service) : "" }));

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-semibold">New sync</h1>

      <Section title="Source">
        <ServiceSelect
          label="Source service"
          value={service}
          onChange={(next) => {
            changeSource(next);
            setAllSelection(null);
          }}
          includeArchive
        />
        <ScopeChoice value={scope} onChange={setScope} />
        {service !== null ? (
          scope === "one" ? (
            <PlaylistPicker
              service={service}
              label="Source playlist"
              mode="single"
              value={selection}
              onChange={setSelection}
            />
          ) : (
            <PlaylistPicker
              service={service}
              label="Source playlists"
              mode="multi"
              value={allIds}
              onChange={setAllSelection}
            />
          )
        ) : null}
      </Section>

      <Section title="Destinations">
        {scope === "all" ? (
          <p className="text-sm text-muted-foreground">Each playlist gets a new playlist on every destination.</p>
        ) : null}
        <DestinationRows rows={rows} onChange={setRows} scope={scope} conflicts={conflicts} />
      </Section>

      {scope === "one" ? (
        <section aria-labelledby="new-sync-name" className="space-y-2">
          <h2 id="new-sync-name" className="text-lg font-semibold">
            <label htmlFor={nameId}>Name</label>
          </h2>
          <Input
            id={nameId}
            className="max-w-md"
            maxLength={200}
            value={name}
            onChange={(e) => setCustomName(e.target.value)}
          />
        </section>
      ) : null}

      <section aria-labelledby="new-sync-schedule" className="space-y-2">
        <h2 id="new-sync-schedule" className="text-lg font-semibold">
          Schedule
        </h2>
        <ScheduleField
          defaultSchedule={settings.data?.default_schedule ?? "…"}
          value={schedule}
          onChange={setSchedule}
        />
      </section>

      {scope === "one" ? (
        <PreviewPanel
          preview={preview}
          destinations={previewDestinations}
          picks={picks}
        />
      ) : currentBulk ? (
        <BulkPreview
          jobId={currentBulk.jobId}
          names={currentBulk.syncs.map((s) => s.name)}
          destinations={previewDestinations}
          picks={picks}
        />
      ) : null}

      <div className="space-y-3">
        {[create.error, bulkEstimate.request.error, previewBulk.error, bulk.error].map((error, i) =>
          error ? <ErrorNotice key={i} error={error} /> : null,
        )}
        {scope === "one" ? (
          <Button type="button" disabled={!canCreate} onClick={submitOne}>
            Start syncing
          </Button>
        ) : (
          <div className="flex flex-wrap gap-2">
            <Button
              type="button"
              variant={currentBulk ? "outline" : "default"}
              disabled={!canBulk}
              onClick={() => bulkEstimate.request.mutate()}
            >
              {n === 1 ? "Preview 1 sync…" : `Preview ${n} syncs…`}
            </Button>
            {currentBulk ? (
              <Button type="button" disabled={!bulkReady} onClick={startBulk}>
                Start syncing
              </Button>
            ) : null}
          </div>
        )}
      </div>

      <BulkWarningDialog
        open={bulkEstimate.warnOpen}
        onOpenChange={bulkEstimate.setWarnOpen}
        estimate={bulkEstimate.estimate}
        verb="Sync"
        nothingToStart={bulkEstimate.nothingToStart}
        onStart={previewAll}
        pending={previewBulk.isPending}
      />
    </div>
  );
}
