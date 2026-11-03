import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { api, call, type ApiError } from "@/api/client";
import type { AttentionOut, RunOut, UnresolvedOut } from "@/api/types";
import { ErrorNotice } from "@/components/ErrorNotice";
import { TrackCard } from "@/components/TrackArt";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { AttentionModal } from "@/features/attention/AttentionModal";
import { useServiceNames } from "@/features/pickers/useServiceNames";
import { MemberSection } from "./MemberSection";

function Unresolved({
  item,
  resolved,
  pending,
  onResolve,
}: {
  item: UnresolvedOut;
  resolved: boolean;
  pending: boolean;
  onResolve(): void;
}) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-3 rounded-md border p-3">
      <div className="min-w-0 space-y-1">
        <TrackCard track={item.source} />
        {item.reasons.length > 0 ? <p className="text-sm text-muted-foreground">{item.reasons.join("; ")}</p> : null}
      </div>
      {resolved ? (
        <Badge variant="outline">Resolved</Badge>
      ) : (
        <Button type="button" size="sm" variant="outline" disabled={pending} onClick={onResolve}>
          Resolve…
        </Button>
      )}
    </li>
  );
}

/** What a run did per destination (songs side by side, issues), and the songs
 * that didn't match, each resolvable here. */
export function RunResults({ run }: { run: RunOut }) {
  const nameOf = useServiceNames();
  const [selected, setSelected] = useState<AttentionOut | null>(null);
  const [opening, setOpening] = useState<number | null>(null);
  const [resolved, setResolved] = useState<ReadonlySet<number>>(new Set());
  const open = useMutation<AttentionOut, ApiError, number>({
    mutationFn: (index) =>
      call(
        api.POST("/api/runs/{run_id}/unresolved/{index}/attention", {
          params: { path: { run_id: run.run_id, index } },
        }),
      ),
    onMutate: (index) => setOpening(index),
    onSuccess: setSelected,
  });

  return (
    <div className="space-y-6">
      {run.members.map((m) => (
        <MemberSection key={m.member_id} member={m} serviceName={m.service ? nameOf(m.service) : m.member_id} />
      ))}

      {run.unresolved.length > 0 ? (
        <section className="space-y-3">
          <h2 className="text-lg font-semibold">Didn't match ({run.unresolved.length})</h2>
          <p className="text-sm text-muted-foreground">
            Pick the right song, or mark it as not available. Your choice is used from the next run on.
          </p>
          {open.error ? <ErrorNotice error={open.error} /> : null}
          <ul className="space-y-2">
            {run.unresolved.map((u, i) => (
              <Unresolved
                key={`${u.member_id}:${u.position}:${i}`}
                item={u}
                resolved={resolved.has(i)}
                pending={open.isPending && opening === i}
                onResolve={() => open.mutate(i)}
              />
            ))}
          </ul>
        </section>
      ) : null}

      <AttentionModal
        item={selected}
        onOpenChange={(o) => !o && setSelected(null)}
        onResolved={() => {
          if (opening !== null) setResolved(new Set(resolved).add(opening));
        }}
      />
    </div>
  );
}
