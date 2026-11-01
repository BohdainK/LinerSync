import { Link } from "react-router";
import type { AttentionOut } from "@/api/types";
import { ChangesList } from "@/components/ChangesList";
import { ErrorNotice } from "@/components/ErrorNotice";
import { TrackList } from "@/components/TrackList";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ATTENTION_KIND_LABELS } from "@/copy";
import { useServiceNames } from "@/features/pickers/useServiceNames";
import { formatDateTime } from "@/lib/format";
import { useAttentionAction } from "./useAttention";

function NotOnService({ service, onClick, disabled }: { service: string; onClick(): void; disabled: boolean }) {
  const nameOf = useServiceNames();
  return (
    <Button type="button" variant="outline" disabled={disabled} onClick={onClick}>
      Not on {nameOf(service)}
    </Button>
  );
}

function Body({ item, close }: { item: AttentionOut; close(): void }) {
  const action = useAttentionAction(item.item_id, close);
  const pending = action.isPending;
  const go = (a: Parameters<typeof action.mutate>[0]) => () => action.mutate(a);
  const service = item.service;

  return (
    <>
      <DialogHeader>
        <DialogTitle>{ATTENTION_KIND_LABELS[item.kind]}</DialogTitle>
        <DialogDescription asChild>
          <div>
            {item.sync_name ? <p>{item.sync_name}</p> : null}
            {item.detail ? <p>{item.detail}</p> : null}
            <p>{formatDateTime(item.created_at)}</p>
          </div>
        </DialogDescription>
      </DialogHeader>

      <div className="max-h-[60vh] space-y-3 overflow-y-auto">
        {item.kind === "unmatched" ? (
          <>
            {item.source ? <TrackList tracks={[item.source]} /> : null}
            <ul className="space-y-3">
              {item.candidates.map((c) => (
                <li key={c.track.track_id} className="space-y-1 rounded-md border p-2">
                  <TrackList tracks={[c.track]} />
                  <p className="text-sm">{Math.round(c.confidence * 100)}% match</p>
                  {c.reasons.length > 0 ? (
                    <ul className="list-disc pl-6 text-sm text-muted-foreground">
                      {c.reasons.map((r, i) => (
                        <li key={i}>{r}</li>
                      ))}
                    </ul>
                  ) : null}
                  <Button
                    type="button"
                    size="sm"
                    disabled={pending}
                    onClick={go({ type: "pick", trackId: c.track.track_id })}
                  >
                    Use this song
                  </Button>
                </li>
              ))}
            </ul>
          </>
        ) : null}
        {item.not_made.length > 0 ? <ChangesList changes={item.not_made} /> : null}
        {action.error ? <ErrorNotice error={action.error} /> : null}
      </div>

      <DialogFooter className="flex-wrap gap-2">
        {item.kind === "unmatched" && item.target_service ? (
          <NotOnService
            service={item.target_service}
            disabled={pending}
            onClick={go({ type: "not-on-service" })}
          />
        ) : null}
        {item.kind === "brake" ? (
          <>
            <Button type="button" disabled={pending} onClick={go({ type: "approve", runNow: true })}>
              Approve and run now
            </Button>
            <Button type="button" variant="outline" disabled={pending} onClick={go({ type: "approve", runNow: false })}>
              Approve
            </Button>
          </>
        ) : null}
        {item.kind === "source_missing" || item.kind === "service_failing" ? (
          <>
            <Button type="button" disabled={pending} onClick={go({ type: "resume", runNow: true })}>
              Resume and run now
            </Button>
            <Button type="button" variant="outline" disabled={pending} onClick={go({ type: "resume", runNow: false })}>
              Resume
            </Button>
          </>
        ) : null}
        {item.kind === "service_failing" && service ? (
          <Button asChild variant="link">
            <Link to={`/connections/${service}`}>Open {service}</Link>
          </Button>
        ) : null}
        <Button type="button" variant="ghost" disabled={pending} onClick={go({ type: "dismiss" })}>
          Dismiss
        </Button>
      </DialogFooter>
    </>
  );
}

export function AttentionModal({
  item,
  onOpenChange,
}: {
  item: AttentionOut | null;
  onOpenChange(open: boolean): void;
}) {
  return (
    <Dialog open={item !== null} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        {item ? <Body key={item.item_id} item={item} close={() => onOpenChange(false)} /> : null}
      </DialogContent>
    </Dialog>
  );
}
