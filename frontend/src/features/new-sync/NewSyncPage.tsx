import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { toast } from "sonner";
import { api, call, type ApiError } from "@/api/client";
import { keys } from "@/api/keys";
import type {
  DestinationIn,
  EstimateOut,
  PlaylistOut,
  SyncBulkIn,
  SyncCreateIn,
  SyncMode,
  SyncOut,
} from "@/api/types";
import { ErrorNotice } from "@/components/ErrorNotice";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { useConnections } from "@/features/connections/useConnections";
import { BulkWarningDialog } from "@/features/pickers/BulkWarningDialog";
import { unreadableIds } from "@/features/pickers/unreadableIds";
import { PlaylistPicker } from "@/features/pickers/PlaylistPicker";
import { ServiceSelect, selectClass } from "@/features/pickers/ServiceSelect";
import { parseSourceParam } from "@/features/pickers/sourceParam";
import { usePlaylists } from "@/features/pickers/usePlaylists";
import { useServiceNames } from "@/features/pickers/useServiceNames";
import { MODE_LABELS } from "@/features/syncs/modes";
import { ScheduleField } from "@/features/syncs/ScheduleField";
import { limitations } from "@/lib/limitations";
import { PreviewPanel } from "./PreviewPanel";
import { usePreview } from "./usePreview";

const MAX_DESTINATIONS = 10;
const CREATED = "Sync created. It runs on its schedule, or press Sync now.";

type Scope = "one" | "all";
type Row = { key: number; service: string | null; selection: string[]; mode: SyncMode };

let rowSeq = 0;
function newRow(): Row {
  rowSeq += 1;
  return { key: rowSeq, service: null, selection: [], mode: "full" };
}

/** Bulk syncs are named after their source playlist (D11); the server allows 1–200 characters. */
function nameFor(p: PlaylistOut | undefined, playlistId: string): string {
  return (p?.name.trim() || playlistId).slice(0, 200);
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
  const modeId = useId();
  const connections = useConnections();
  const conn = connections.data?.services.find((s) => s.service === row.service);
  const notes = conn ? limitations(conn.display_name, conn.capabilities, row.mode) : [];
  return (
    <li className="space-y-3 rounded-md border p-4">
      <div className="flex flex-wrap items-end gap-3">
        <ServiceSelect
          label={`Destination ${n}`}
          value={row.service}
          onChange={(service) => props.onChange({ ...row, service, selection: [""] })}
        />
        <div className="flex flex-col gap-1">
          <label htmlFor={modeId} className="text-sm font-medium">
            {`Mode for destination ${n}`}
          </label>
          <select
            id={modeId}
            className={selectClass}
            value={row.mode}
            onChange={(e) => props.onChange({ ...row, mode: e.target.value as SyncMode })}
          >
            {(Object.keys(MODE_LABELS) as SyncMode[]).map((m) => (
              <option key={m} value={m}>
                {MODE_LABELS[m]}
              </option>
            ))}
          </select>
        </div>
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
          onChange={(selection) => props.onChange({ ...row, selection })}
        />
      ) : null}
      {props.conflict ? (
        <p className="text-sm text-destructive">Pick a different playlist than the source</p>
      ) : null}
      {notes.length > 0 ? (
        <ul className="list-disc space-y-1 pl-6 text-sm text-muted-foreground">
          {notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

export function NewSyncPage() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const nameOf = useServiceNames();
  const [params] = useSearchParams();
  const [preset] = useState(() => parseSourceParam(params.get("source")));
  const [service, setService] = useState<string | null>(preset?.service ?? null);
  const [scope, setScope] = useState<Scope>("one");
  const [selection, setSelection] = useState<string[]>(preset ? [preset.playlistId] : []);
  // null: every readable playlist (the default in all-playlists mode).
  const [allSelection, setAllSelection] = useState<string[] | null>(null);
  const [rows, setRows] = useState<Row[]>(() => [newRow()]);
  // null: follow the source playlist's name.
  const [customName, setCustomName] = useState<string | null>(null);
  const [schedule, setSchedule] = useState<string | null>(null);
  const [estimate, setEstimate] = useState<EstimateOut | null>(null);
  const [warnOpen, setWarnOpen] = useState(false);
  const nameId = useId();

  const playlists = usePlaylists(service);
  const settings = useQuery({ queryKey: keys.settings, queryFn: () => call(api.GET("/api/settings")) });
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
    playlist_id: scope === "one" ? r.selection[0] || null : null,
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

  const estimateItems = () =>
    allIds.map((id) => ({
      source_service: service ?? "",
      playlist_id: id,
      destination_services: rows.map((r) => r.service ?? ""),
    }));

  const runEstimate = useMutation<EstimateOut, ApiError, void>({
    mutationFn: () => call(api.POST("/api/transfers/estimate", { body: { items: estimateItems() } })),
    onSuccess: (out) => {
      setEstimate(out);
      setWarnOpen(true);
    },
  });

  const bulk = useMutation<SyncOut[], ApiError, SyncBulkIn>({
    mutationFn: (body) => call(api.POST("/api/syncs/bulk", { body })),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: keys.syncs });
      navigate("/syncs");
    },
    onSettled: () => setWarnOpen(false),
  });

  function changeSource(next: string) {
    setService(next);
    setSelection([]);
    setAllSelection(null);
  }

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
    if (service === null || estimate === null) return;
    const skipped = unreadableIds(estimate);
    if (allIds.every((id) => skipped.has(id))) return;
    bulk.mutate({
      syncs: allIds
        .filter((id) => !skipped.has(id))
        .map((id) => ({
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
    service !== null && allIds.length > 0 && servicesChosen && scheduleOk && !runEstimate.isPending && !bulk.isPending;
  const n = allIds.length;

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-semibold">New sync</h1>

      <section aria-labelledby="new-sync-source" className="space-y-3">
        <h2 id="new-sync-source" className="text-lg font-semibold">
          Source
        </h2>
        <ServiceSelect label="Source service" value={service} onChange={changeSource} includeArchive />
        <RadioGroup
          aria-label="How many playlists"
          className="flex gap-4"
          value={scope}
          onValueChange={(v) => setScope(v === "all" ? "all" : "one")}
        >
          <div className="flex items-center gap-2">
            <RadioGroupItem id={`${nameId}-one`} value="one" />
            <label htmlFor={`${nameId}-one`} className="text-sm">
              One playlist
            </label>
          </div>
          <div className="flex items-center gap-2">
            <RadioGroupItem id={`${nameId}-all`} value="all" />
            <label htmlFor={`${nameId}-all`} className="text-sm">
              All playlists
            </label>
          </div>
        </RadioGroup>
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
      </section>

      <section aria-labelledby="new-sync-destinations" className="space-y-3">
        <h2 id="new-sync-destinations" className="text-lg font-semibold">
          Destinations
        </h2>
        {scope === "all" ? (
          <p className="text-sm text-muted-foreground">Each playlist gets a new playlist on every destination.</p>
        ) : null}
        <ol className="space-y-3">
          {rows.map((row, i) => (
            <DestinationRow
              key={row.key}
              index={i}
              row={row}
              scope={scope}
              conflict={conflicts[i] ?? false}
              removable={rows.length > 1}
              onChange={(next) => setRows(rows.map((r) => (r.key === row.key ? next : r)))}
              onRemove={() => setRows(rows.filter((r) => r.key !== row.key))}
            />
          ))}
        </ol>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={rows.length >= MAX_DESTINATIONS}
          onClick={() => setRows([...rows, newRow()])}
        >
          + Add destination
        </Button>
      </section>

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
        {create.error ? <ErrorNotice error={create.error} /> : null}
        {runEstimate.error ? <ErrorNotice error={runEstimate.error} /> : null}
        {bulk.error ? <ErrorNotice error={bulk.error} /> : null}
        {scope === "one" ? (
          <Button type="button" disabled={!canCreate} onClick={submitOne}>
            Create sync
          </Button>
        ) : (
          <Button type="button" disabled={!canBulk} onClick={() => runEstimate.mutate()}>
            {n === 1 ? "Create 1 sync…" : `Create ${n} syncs…`}
          </Button>
        )}
      </div>

      <BulkWarningDialog
        open={warnOpen}
        onOpenChange={setWarnOpen}
        estimate={estimate}
        verb="Sync"
        nothingToStart={estimate !== null && allIds.every((id) => unreadableIds(estimate).has(id))}
        onStart={startBulk}
        pending={bulk.isPending}
      />
    </div>
  );
}
