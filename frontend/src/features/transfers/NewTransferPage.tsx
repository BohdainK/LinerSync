import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { api, call, type ApiError } from "@/api/client";
import { keys } from "@/api/keys";
import type { BulkItemIn, BulkTransferIn, FromPreviewIn, JobOut, TransferIn } from "@/api/types";
import { ErrorNotice } from "@/components/ErrorNotice";
import { JobProgress } from "@/components/JobProgress";
import { NotesList } from "@/components/NotesList";
import { Section } from "@/components/Section";
import { Button } from "@/components/ui/button";
import { canAdd, isSetUp, useConnections } from "@/features/connections/useConnections";
import { usePicks } from "@/features/matches/usePicks";
import { BulkWarningDialog } from "@/features/pickers/BulkWarningDialog";
import { PlaylistPicker } from "@/features/pickers/PlaylistPicker";
import { ServiceSelect } from "@/features/pickers/ServiceSelect";
import { useBulkEstimate } from "@/features/pickers/useBulkEstimate";
import { useSourceSelection } from "@/features/pickers/useSourceSelection";
import { isActive, useCancelJob, useJob } from "@/live/useJob";
import { limitations } from "@/lib/limitations";
import { TransferResult } from "./TransferResult";

export function NewTransferPage() {
  const queryClient = useQueryClient();
  const { service, selection, setSelection, changeSource } = useSourceSelection();
  const [destinations, setDestinations] = useState<string[]>([]);
  const [immediate, setImmediate] = useState(false);
  const [previewing, setPreviewing] = useState(false);
  const picks = usePicks();
  const [jobId, setJobId] = useState<string | null>(null);
  const immediateId = useId();

  const connections = useConnections();
  const job = useJob(jobId);
  const cancel = useCancelJob();
  const targets = (connections.data?.services ?? []).filter((s) => isSetUp(s) && s.service !== service && canAdd(s));
  const chosen = targets.filter((t) => destinations.includes(t.service)).map((t) => t.service);
  const items: BulkItemIn[] = selection.map((id) => ({
    source_service: service ?? "",
    playlist_id: id,
    destination_services: chosen,
  }));
  const bulkEstimate = useBulkEstimate(items);

  function started(out: JobOut, asPreview = false) {
    setPreviewing(asPreview);
    fromPreview.reset();
    queryClient.setQueryData(keys.job(out.job_id), out);
    setJobId(out.job_id);
    bulkEstimate.setWarnOpen(false);
  }

  const single = useMutation<JobOut, ApiError, TransferIn>({
    mutationFn: (body) => call(api.POST("/api/transfers", { body })),
    onSuccess: (out, vars) => started(out, vars.dry_run === true),
  });
  const bulk = useMutation<JobOut, ApiError, BulkTransferIn>({
    mutationFn: (body) => call(api.POST("/api/transfers/bulk", { body })),
    onSuccess: (out, vars) => started(out, vars.dry_run === true),
    onSettled: () => bulkEstimate.setWarnOpen(false),
  });
  const fromPreview = useMutation<JobOut, ApiError, FromPreviewIn>({
    mutationFn: (body) => call(api.POST("/api/transfers/from-preview", { body })),
    onSuccess: (out) => started(out),
  });

  function start() {
    if (service === null) return;
    const [only] = selection;
    const [dest] = chosen;
    if (selection.length === 1 && chosen.length === 1 && only !== undefined && dest !== undefined) {
      single.mutate({ source_service: service, playlist_id: only, destination: dest, dry_run: !immediate });
    } else {
      bulkEstimate.request.mutate();
    }
  }

  function startBulk() {
    if (bulkEstimate.readable.length === 0) return;
    bulk.mutate({ items: bulkEstimate.readable, dry_run: !immediate });
  }

  function reset() {
    setJobId(null);
    setPreviewing(false);
    fromPreview.reset();
    picks.clear();
    setSelection([]);
    setDestinations([]);
    bulkEstimate.clearEstimate();
  }

  if (jobId !== null) {
    const current = job.data;
    return (
      <div className="space-y-4">
        <h1 className="text-2xl font-semibold">New transfer</h1>
        {job.error ? <ErrorNotice error={job.error} /> : null}
        {current ? (
          <JobProgress job={current} onCancel={() => cancel.mutate(current.job_id)} />
        ) : (
          <p className="text-sm text-muted-foreground">Working…</p>
        )}
        {cancel.error ? <ErrorNotice error={cancel.error} /> : null}
        {current && !isActive(current) ? (
          <div className="space-y-6">
            {previewing && current.status === "succeeded" ? (
              <Button
                type="button"
                disabled={fromPreview.isPending}
                onClick={() => fromPreview.mutate({ preview_job_id: current.job_id, picks: picks.toApi() })}
              >
                Start transfer
              </Button>
            ) : null}
            {fromPreview.error ? <ErrorNotice error={fromPreview.error} /> : null}
            <Button type="button" variant={previewing ? "outline" : "default"} onClick={reset}>
              Start another transfer
            </Button>
            {current.run_ids.map((id) => (
              <TransferResult key={id} runId={id} picks={previewing ? picks : undefined} />
            ))}
          </div>
        ) : null}
      </div>
    );
  }

  const pending = single.isPending || bulkEstimate.request.isPending || bulk.isPending;
  const ready = service !== null && selection.length > 0 && chosen.length > 0;

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-semibold">New transfer</h1>

      <Section title="Source">
        <ServiceSelect label="Source service" value={service} includeArchive onChange={changeSource} />
        {service !== null ? (
          <PlaylistPicker
            service={service}
            label="Source playlists"
            mode="multi"
            value={selection}
            onChange={setSelection}
          />
        ) : null}
      </Section>

      <Section title="Destinations">
        {targets.length === 0 ? <p className="text-sm text-muted-foreground">No other connected services.</p> : null}
        <ul className="space-y-3">
          {targets.map((t) => {
            const notes = limitations(t.display_name, t.capabilities, "full");
            return (
              <li key={t.service} className="space-y-1">
                <label className="flex items-center gap-2 text-sm font-medium">
                  <input
                    type="checkbox"
                    checked={destinations.includes(t.service)}
                    onChange={(e) =>
                      setDestinations(
                        e.target.checked ? [...destinations, t.service] : destinations.filter((d) => d !== t.service),
                      )
                    }
                  />
                  {t.display_name}
                </label>
                <NotesList notes={notes} className="pl-12" />
              </li>
            );
          })}
        </ul>
      </Section>

      <div className="flex items-center gap-2">
        <input id={immediateId} type="checkbox" checked={immediate} onChange={(e) => setImmediate(e.target.checked)} />
        <label htmlFor={immediateId} className="text-sm">
          Transfer immediately (skip the preview)
        </label>
      </div>

      <div className="space-y-3">
        {[single.error, bulkEstimate.request.error, bulk.error].map((error, i) =>
          error ? <ErrorNotice key={i} error={error} /> : null,
        )}
        <Button type="button" disabled={!ready || pending} onClick={start}>
          Start transfer
        </Button>
      </div>

      <BulkWarningDialog
        open={bulkEstimate.warnOpen}
        onOpenChange={bulkEstimate.setWarnOpen}
        estimate={bulkEstimate.estimate}
        verb="Transfer"
        nothingToStart={bulkEstimate.nothingToStart}
        onStart={startBulk}
        pending={bulk.isPending}
      />
    </div>
  );
}
