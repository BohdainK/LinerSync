import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";
import { api, call, type ApiError } from "@/api/client";
import { keys } from "@/api/keys";
import type { BulkItemIn, DestinationIn, PlaylistOut, SyncBulkIn, SyncCreateIn, SyncOut } from "@/api/types";
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
  }));
  const oneReady = service !== null && sourceId !== null && servicesChosen && !conflicts.some(Boolean);
  const cron = schedule === null ? null : schedule.trim();

  const previewInput: SyncCreateIn | null =
    scope === "one" && oneReady && playlists.data
      ? { name: nameFor(byId.get(sourceId), sourceId), source: { service, playlist_id: sourceId }, destinations, schedule: null }
      : null;
  const preview = usePreview(previewInput);

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
    onSettled: () => bulkEstimate.setWarnOpen(false),
  });

  function submitOne() {
    if (!oneReady) return;
    create.mutate({
      name: name.trim(),
      source: { service, playlist_id: sourceId },
      destinations,
      schedule: cron,
    });
  }

  function startBulk() {
    if (service === null || bulkEstimate.estimate === null || bulkEstimate.nothingToStart) return;
    bulk.mutate({
      syncs: bulkEstimate.readable.map(({ playlist_id: id }) => ({
        name: nameFor(byId.get(id), id),
        source: { service, playlist_id: id },
        destinations,
        schedule: cron,
      })),
    });
  }

  const scheduleOk = cron !== "";
  const canCreate = oneReady && name.trim() !== "" && scheduleOk && !create.isPending;
  const canBulk =
    service !== null &&
    allIds.length > 0 &&
    servicesChosen &&
    scheduleOk &&
    !bulkEstimate.request.isPending &&
    !bulk.isPending;
  const n = allIds.length;

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
          destinations={rows.map((r) => ({ service: r.service ?? "", name: r.service ? nameOf(r.service) : "" }))}
        />
      ) : null}

      <div className="space-y-3">
        {[create.error, bulkEstimate.request.error, bulk.error].map((error, i) =>
          error ? <ErrorNotice key={i} error={error} /> : null,
        )}
        {scope === "one" ? (
          <Button type="button" disabled={!canCreate} onClick={submitOne}>
            Create sync
          </Button>
        ) : (
          <Button type="button" disabled={!canBulk} onClick={() => bulkEstimate.request.mutate()}>
            {n === 1 ? "Create 1 sync…" : `Create ${n} syncs…`}
          </Button>
        )}
      </div>

      <BulkWarningDialog
        open={bulkEstimate.warnOpen}
        onOpenChange={bulkEstimate.setWarnOpen}
        estimate={bulkEstimate.estimate}
        verb="Sync"
        nothingToStart={bulkEstimate.nothingToStart}
        onStart={startBulk}
        pending={bulk.isPending}
      />
    </div>
  );
}
