import type { EstimateOut } from "@/api/types";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { useServiceNames } from "./useServiceNames";

/** The warning before a sync-all or a multi-playlist transfer (decision D11). Only "Start" goes ahead. */
export function BulkWarningDialog(props: {
  open: boolean;
  onOpenChange(o: boolean): void;
  estimate: EstimateOut | null;
  verb: "Sync" | "Transfer";
  onStart(): void;
  pending: boolean;
  /** True when every selected playlist is unreadable, so there is nothing to send. */
  nothingToStart?: boolean;
}) {
  const nameOf = useServiceNames();
  const { estimate } = props;
  const addOnly = estimate?.add_only_destinations.map(nameOf) ?? [];
  const unreadable = estimate?.unreadable.map((p) => p.name) ?? [];
  return (
    <ConfirmDialog
      open={props.open && estimate !== null}
      onOpenChange={props.onOpenChange}
      title={`${props.verb} ${estimate?.playlists ?? 0} ${estimate?.playlists === 1 ? "playlist" : "playlists"}?`}
      body={
        estimate ? (
          <ul className="space-y-1">
            {props.nothingToStart ? (
              <li>{`Nothing to ${props.verb.toLowerCase()}: every selected playlist is unreadable`}</li>
            ) : null}
            <li>{estimate.songs} songs</li>
            {Object.entries(estimate.api_calls).map(([service, n]) => (
              <li key={service}>{`About ${n} API calls on ${nameOf(service)}`}</li>
            ))}
            {addOnly.length > 0 ? <li>{`Add only: ${addOnly.join(", ")}`}</li> : null}
            {unreadable.length > 0 ? <li>{`Skipped, can't read: ${unreadable.join(", ")}`}</li> : null}
          </ul>
        ) : undefined
      }
      confirmLabel="Start"
      onConfirm={props.onStart}
      pending={props.pending}
      confirmDisabled={props.nothingToStart === true}
    />
  );
}
