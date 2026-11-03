import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { api, call, type ApiError } from "@/api/client";
import type { DestinationOut, JobOut, ReAddIn, SyncOut, TrackOut } from "@/api/types";
import { ErrorNotice } from "@/components/ErrorNotice";
import { TrackRow } from "@/components/TrackList";
import { Button } from "@/components/ui/button";
import { useServiceNames } from "@/features/pickers/useServiceNames";
import { CopyToSourcePanel } from "./CopyToSourcePanel";

function Destination({
  sync,
  destination: d,
  disabled,
  onJob,
  copying,
  setCopying,
}: {
  sync: SyncOut;
  destination: DestinationOut;
  disabled: boolean;
  onJob: (job: JobOut) => void;
  copying: string[] | null;
  setCopying: (ids: string[] | null) => void;
}) {
  const nameOf = useServiceNames();
  const name = nameOf(d.service);
  const [selected, setSelected] = useState<string[]>([]);
  const reAdd = useMutation<JobOut, ApiError, ReAddIn>({
    mutationFn: (body) =>
      call(
        api.POST("/api/syncs/{sync_id}/destinations/{member_id}/re-add", {
          params: { path: { sync_id: sync.sync_id, member_id: d.member_id } },
          body,
        }),
      ),
    onSuccess: onJob,
  });
  const key = (t: TrackOut) => t.track_id;

  return (
    <div className="space-y-6">
      {d.removed_by_you.length > 0 ? (
        <section aria-label={`Removed on ${name}`} className="space-y-2">
          <h3 className="font-medium">Removed on {name}</h3>
          <ul className="list-none space-y-1">
            {d.removed_by_you.map((t) => (
              <li key={key(t)} className="flex flex-wrap items-center gap-2">
                <span className="flex-1">
                  <TrackRow track={t} />
                </span>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  aria-label={`Re-add ${t.title}`}
                  disabled={disabled || reAdd.isPending}
                  onClick={() => reAdd.mutate({ source_service: t.service, source_track_id: t.track_id })}
                >
                  Re-add
                </Button>
              </li>
            ))}
          </ul>
          {reAdd.error ? <ErrorNotice error={reAdd.error} /> : null}
        </section>
      ) : null}
      {d.added_by_you.length > 0 ? (
        <section aria-label={`Added on ${name}`} className="space-y-2">
          <h3 className="font-medium">Added on {name}</h3>
          <ul className="list-none space-y-1">
            {d.added_by_you.map((t) => (
              <li key={key(t)}>
                <label className="flex items-center gap-2">
                  {d.can_copy_to_source ? (
                    <input
                      type="checkbox"
                      aria-label={`Select ${t.title}`}
                      checked={selected.includes(key(t))}
                      onChange={(e) =>
                        setSelected(e.target.checked ? [...selected, key(t)] : selected.filter((id) => id !== key(t)))
                      }
                    />
                  ) : null}
                  <span>
                    <TrackRow track={t} />
                  </span>
                </label>
              </li>
            ))}
          </ul>
          {d.can_copy_to_source && copying === null ? (
            <div className="flex gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={disabled || selected.length === 0}
                onClick={() => setCopying(selected)}
              >
                Copy to source
              </Button>
              <Button type="button" variant="outline" size="sm" disabled={disabled} onClick={() => setCopying([])}>
                Copy all to source
              </Button>
            </div>
          ) : null}
        </section>
      ) : null}
      {copying !== null ? (
        <CopyToSourcePanel
          sync={sync}
          destination={d}
          trackIds={copying}
          onClose={() => {
            setCopying(null);
            setSelected([]);
          }}
        />
      ) : null}
    </div>
  );
}

/** Per destination: songs the user removed there (re-add) and songs they added there (copy to source). */
export function DestinationEdits({
  sync,
  disabled,
  onJob,
}: {
  sync: SyncOut;
  disabled: boolean;
  onJob: (job: JobOut) => void;
}) {
  const [copying, setCopying] = useState<Record<string, string[]>>({});
  const withItems = sync.destinations.filter(
    (d) => d.removed_by_you.length > 0 || d.added_by_you.length > 0 || copying[d.member_id] !== undefined,
  );
  if (withItems.length === 0) return null;
  return (
    <div className="space-y-8">
      {withItems.map((d) => (
        <Destination
          key={d.member_id}
          sync={sync}
          destination={d}
          disabled={disabled}
          onJob={onJob}
          copying={copying[d.member_id] ?? null}
          setCopying={(ids) =>
            setCopying((prev) => {
              const next = { ...prev };
              if (ids === null) delete next[d.member_id];
              else next[d.member_id] = ids;
              return next;
            })
          }
        />
      ))}
    </div>
  );
}
