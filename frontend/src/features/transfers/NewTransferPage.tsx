import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { api, call, type ApiError } from "@/api/client";
import { keys } from "@/api/keys";
import type { BulkTransferIn, EstimateOut, JobOut, TransferIn } from "@/api/types";
import { ErrorNotice } from "@/components/ErrorNotice";
import { JobProgress } from "@/components/JobProgress";
import { Button } from "@/components/ui/button";
import { useConnections } from "@/features/connections/useConnections";
import { BulkWarningDialog } from "@/features/pickers/BulkWarningDialog";
import { unreadableIds } from "@/features/pickers/unreadableIds";
import { PlaylistPicker } from "@/features/pickers/PlaylistPicker";
import { ServiceSelect } from "@/features/pickers/ServiceSelect";
import { parseSourceParam } from "@/features/pickers/sourceParam";
import { isActive, useCancelJob, useJob } from "@/live/useJob";
import { limitations } from "@/lib/limitations";

export function NewTransferPage() {
  const queryClient = useQueryClient();
  const [params] = useSearchParams();
  const [preset] = useState(() => parseSourceParam(params.get("source")));
  const [service, setService] = useState<string | null>(preset?.service ?? null);
  const [selection, setSelection] = useState<string[]>(preset ? [preset.playlistId] : []);
  const [destinations, setDestinations] = useState<string[]>([]);
  const [dryRun, setDryRun] = useState(false);
  const [estimate, setEstimate] = useState<EstimateOut | null>(null);
  const [warnOpen, setWarnOpen] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const dryId = useId();

  const connections = useConnections();
  const job = useJob(jobId);
  const cancel = useCancelJob();
  const targets = (connections.data?.services ?? []).filter(
    (s) => s.status !== "not_set_up" && s.service !== service,
  );
  const chosen = targets.filter((t) => destinations.includes(t.service)).map((t) => t.service);

  function started(out: JobOut) {
    queryClient.setQueryData(keys.job(out.job_id), out);
    setJobId(out.job_id);
    setWarnOpen(false);
  }

  const single = useMutation<JobOut, ApiError, TransferIn>({
    mutationFn: (body) => call(api.POST("/api/transfers", { body })),
    onSuccess: started,
  });
  const runEstimate = useMutation<EstimateOut, ApiError, void>({
    mutationFn: () =>
      call(api.POST("/api/transfers/estimate", { body: { items: items() } })),
    onSuccess: (out) => {
      setEstimate(out);
      setWarnOpen(true);
    },
  });
  const bulk = useMutation<JobOut, ApiError, BulkTransferIn>({
    mutationFn: (body) => call(api.POST("/api/transfers/bulk", { body })),
    onSuccess: started,
    onSettled: () => setWarnOpen(false),
  });

  function items() {
    return selection.map((id) => ({
      source_service: service ?? "",
      playlist_id: id,
      destination_services: chosen,
    }));
  }

  function start() {
    if (service === null) return;
    const [only] = selection;
    const [dest] = chosen;
    if (selection.length === 1 && chosen.length === 1 && only !== undefined && dest !== undefined) {
      single.mutate({ source_service: service, playlist_id: only, destination: dest, dry_run: dryRun });
    } else {
      runEstimate.mutate();
    }
  }

  function startBulk() {
    const skipped = unreadableIds(estimate);
    const remaining = items().filter((i) => !skipped.has(i.playlist_id));
    if (remaining.length === 0) return;
    bulk.mutate({ items: remaining, dry_run: dryRun });
  }

  function reset() {
    setJobId(null);
    setSelection([]);
    setDestinations([]);
    setEstimate(null);
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
          <div className="space-y-2">
            {current.run_ids.length > 1 ? (
              <p className="text-sm text-muted-foreground">One result per transfer.</p>
            ) : null}
            <ul className="space-y-1">
              {current.run_ids.map((id) => (
                <li key={id}>
                  <Link className="text-sm underline" to={`/runs/${id}`}>
                    Open result
                  </Link>
                </li>
              ))}
            </ul>
            <Button type="button" onClick={reset}>
              Start another transfer
            </Button>
          </div>
        ) : null}
      </div>
    );
  }

  const pending = single.isPending || runEstimate.isPending || bulk.isPending;
  const ready = service !== null && selection.length > 0 && chosen.length > 0;

  return (
    <div className="space-y-8">
      <h1 className="text-2xl font-semibold">New transfer</h1>

      <section aria-labelledby="new-transfer-source" className="space-y-3">
        <h2 id="new-transfer-source" className="text-lg font-semibold">
          Source
        </h2>
        <ServiceSelect
          label="Source service"
          value={service}
          includeArchive
          onChange={(next) => {
            setService(next);
            setSelection([]);
          }}
        />
        {service !== null ? (
          <PlaylistPicker
            service={service}
            label="Source playlists"
            mode="multi"
            value={selection}
            onChange={setSelection}
          />
        ) : null}
      </section>

      <section aria-labelledby="new-transfer-destinations" className="space-y-3">
        <h2 id="new-transfer-destinations" className="text-lg font-semibold">
          Destinations
        </h2>
        {targets.length === 0 ? (
          <p className="text-sm text-muted-foreground">No other connected services.</p>
        ) : null}
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
                        e.target.checked
                          ? [...destinations, t.service]
                          : destinations.filter((d) => d !== t.service),
                      )
                    }
                  />
                  {t.display_name}
                </label>
                {notes.length > 0 ? (
                  <ul className="list-disc space-y-1 pl-12 text-sm text-muted-foreground">
                    {notes.map((note) => (
                      <li key={note}>{note}</li>
                    ))}
                  </ul>
                ) : null}
              </li>
            );
          })}
        </ul>
      </section>

      <div className="flex items-center gap-2">
        <input id={dryId} type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />
        <label htmlFor={dryId} className="text-sm">
          Dry run (match only, change nothing)
        </label>
      </div>

      <div className="space-y-3">
        {single.error ? <ErrorNotice error={single.error} /> : null}
        {runEstimate.error ? <ErrorNotice error={runEstimate.error} /> : null}
        {bulk.error ? <ErrorNotice error={bulk.error} /> : null}
        <Button type="button" disabled={!ready || pending} onClick={start}>
          Start transfer
        </Button>
      </div>

      <BulkWarningDialog
        open={warnOpen}
        onOpenChange={setWarnOpen}
        estimate={estimate}
        verb="Transfer"
        nothingToStart={estimate !== null && items().every((i) => unreadableIds(estimate).has(i.playlist_id))}
        onStart={startBulk}
        pending={bulk.isPending}
      />
    </div>
  );
}
