import { useEffect, useState } from "react";
import { Link } from "react-router";
import type { AttentionOut, CandidateOut } from "@/api/types";
import { ChangesList } from "@/components/ChangesList";
import { ErrorNotice } from "@/components/ErrorNotice";
import { TrackCard } from "@/components/TrackArt";
import { Badge } from "@/components/ui/badge";
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
import { useAttentionAction, useSimilarSongs } from "./useAttention";

function NotOnService({ service, onClick, disabled }: { service: string; onClick(): void; disabled: boolean }) {
  const nameOf = useServiceNames();
  return (
    <Button type="button" variant="outline" disabled={disabled} onClick={onClick}>
      Not on {nameOf(service)}
    </Button>
  );
}

/** Candidates scoring below this are rarely the same song; they stay behind a toggle. */
export const SUGGEST_FLOOR = 0.5;

function Candidate({ c, pending, onPick }: { c: CandidateOut; pending: boolean; onPick(): void }) {
  const percent = Math.round(c.confidence * 100);
  return (
    <li className="flex flex-wrap items-center justify-between gap-3 rounded-md border p-2">
      <div className="min-w-0 space-y-1">
        <TrackCard track={c.track} />
        {c.track.album ? <p className="truncate text-xs text-muted-foreground">{c.track.album}</p> : null}
        {c.reasons.length > 0 ? <p className="text-xs text-muted-foreground">{c.reasons.join(" · ")}</p> : null}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Badge variant={percent >= 85 ? "default" : "outline"}>{percent}% match</Badge>
        <Button type="button" size="sm" disabled={pending} onClick={onPick}>
          Use this song
        </Button>
      </div>
    </li>
  );
}

function UnmatchedSongs({
  item,
  pending,
  onPick,
}: {
  item: AttentionOut;
  pending: boolean;
  onPick(trackId: string): void;
}) {
  const nameOf = useServiceNames();
  const similar = useSimilarSongs(item.item_id);
  const [weaker, setWeaker] = useState(false);
  const canSearch = item.target_service !== null;
  const search = similar.mutate;
  const searchOnOpen = canSearch && item.candidates.length === 0;
  useEffect(() => {
    if (searchOnOpen) search();
  }, [searchOnOpen, search]);

  const candidates = similar.data?.candidates ?? item.candidates;
  const strong = candidates.filter((c) => c.confidence >= SUGGEST_FLOOR);
  const weak = candidates.filter((c) => c.confidence < SUGGEST_FLOOR);
  const target = item.target_service ? nameOf(item.target_service) : "the destination";
  const searched = similar.isSuccess;

  return (
    <div className="space-y-3">
      {item.source ? (
        <div className="rounded-md bg-muted/50 p-3">
          <p className="mb-2 text-xs font-medium tracking-wide text-muted-foreground uppercase">Looking for</p>
          <TrackCard track={item.source} />
          {item.source.album ? <p className="mt-1 text-xs text-muted-foreground">{item.source.album}</p> : null}
        </div>
      ) : null}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-medium">Closest songs on {target}</h3>
        {canSearch && !searchOnOpen ? (
          <Button
            type="button"
            size="sm"
            variant="outline"
            disabled={similar.isPending || searched}
            onClick={() => search()}
          >
            {searched ? "Searched" : "Find similar songs"}
          </Button>
        ) : null}
      </div>
      {similar.isPending ? <p className="text-sm text-muted-foreground">Searching {target}…</p> : null}
      {similar.error ? <ErrorNotice error={similar.error} /> : null}
      {strong.length > 0 ? (
        <ul className="space-y-2">
          {strong.map((c) => (
            <Candidate key={c.track.track_id} c={c} pending={pending} onPick={() => onPick(c.track.track_id)} />
          ))}
        </ul>
      ) : !similar.isPending ? (
        <p className="text-sm text-muted-foreground">
          No song on {target} matches at least {Math.round(SUGGEST_FLOOR * 100)}%.
        </p>
      ) : null}
      {weak.length > 0 ? (
        weaker ? (
          <ul className="space-y-2">
            {weak.map((c) => (
              <Candidate key={c.track.track_id} c={c} pending={pending} onPick={() => onPick(c.track.track_id)} />
            ))}
          </ul>
        ) : (
          <Button type="button" variant="link" className="h-auto p-0" onClick={() => setWeaker(true)}>
            Show {weak.length} weaker {weak.length === 1 ? "match" : "matches"}
          </Button>
        )
      ) : null}
    </div>
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
          <UnmatchedSongs
            item={item}
            pending={pending}
            onPick={(trackId) => action.mutate({ type: "pick", trackId })}
          />
        ) : null}
        {item.not_made.length > 0 ? <ChangesList changes={item.not_made} /> : null}
        {action.error ? <ErrorNotice error={action.error} /> : null}
      </div>

      <DialogFooter className="flex-wrap gap-2">
        {item.kind === "unmatched" && item.target_service ? (
          <NotOnService service={item.target_service} disabled={pending} onClick={go({ type: "not-on-service" })} />
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
  onResolved,
}: {
  item: AttentionOut | null;
  onOpenChange(open: boolean): void;
  /** Called after an action succeeds, before the modal closes. */
  onResolved?(): void;
}) {
  return (
    <Dialog open={item !== null} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        {item ? (
          <Body
            key={item.item_id}
            item={item}
            close={() => {
              onResolved?.();
              onOpenChange(false);
            }}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
