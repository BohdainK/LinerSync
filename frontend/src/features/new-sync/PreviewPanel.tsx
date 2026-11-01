import { useId } from "react";
import type { RunOut, UnresolvedOut } from "@/api/types";
import { ChangesList } from "@/components/ChangesList";
import { ErrorNotice } from "@/components/ErrorNotice";
import { JobProgress } from "@/components/JobProgress";
import { Section } from "@/components/Section";
import { TrackList } from "@/components/TrackList";
import { UnresolvedItem } from "@/components/UnresolvedItem";
import { formatSongs } from "@/lib/format";
import type { PreviewState } from "./usePreview";

function Unmatched({ items }: { items: UnresolvedOut[] }) {
  return (
    <div className="space-y-2">
      <h4 className="font-medium">Didn't match ({items.length})</h4>
      <ul className="space-y-2">
        {items.map((u, i) => (
          <UnresolvedItem key={`${u.position}:${i}`} item={u} candidatesHeading="Closest matches" />
        ))}
      </ul>
    </div>
  );
}

function Destination({ run, index, name }: { run: RunOut; index: number; name: string }) {
  const headingId = useId();
  const memberId = `dst${index}`;
  const member = run.members.find((m) => m.member_id === memberId);
  const ops = member?.operations ?? [];
  const added = ops.flatMap((op) => (op.type === "add_tracks" ? op.tracks : []));
  const removed = ops.flatMap((op) => (op.type === "remove_tracks" ? op.tracks : []));
  const unmatched = run.unresolved.filter((u) => u.member_id === memberId);
  return (
    <section aria-labelledby={headingId} className="space-y-3 rounded-md border p-4">
      <h3 id={headingId} className="font-semibold">
        {name}
      </h3>
      {member?.error ? <p className="text-sm text-destructive">{member.error}</p> : null}
      <p>{formatSongs(added.length)} will be added</p>
      {added.length > 0 ? <TrackList tracks={added} /> : null}
      {removed.length > 0 ? (
        <div className="space-y-1">
          <p>{formatSongs(removed.length)} will be removed</p>
          <TrackList tracks={removed} />
        </div>
      ) : null}
      {member && member.not_made.length > 0 ? (
        <div className="space-y-1">
          <h4 className="font-medium">Changes not made</h4>
          <ChangesList changes={member.not_made} />
        </div>
      ) : null}
      {unmatched.length > 0 ? <Unmatched items={unmatched} /> : null}
    </section>
  );
}

/** What a new sync would do to each destination; `destinations[i]` is the run's member `dst{i}`. */
export function PreviewPanel(props: { preview: PreviewState; destinations: { service: string; name: string }[] }) {
  const { preview } = props;
  const run = preview.status === "ready" ? preview.run : null;
  return (
    <Section title="Preview">
      {preview.status === "idle" ? (
        <p className="text-sm text-muted-foreground">Choose a source playlist and destinations to see a preview.</p>
      ) : null}
      {preview.status === "waiting" || preview.status === "running" ? (
        <div className="space-y-2">
          <p className="text-sm text-muted-foreground">Preparing preview…</p>
          {preview.job ? <JobProgress job={preview.job} /> : null}
        </div>
      ) : null}
      {preview.status === "failed" ? <ErrorNotice error={preview.error} /> : null}
      {run ? props.destinations.map((d, i) => <Destination key={i} run={run} index={i} name={d.name} />) : null}
    </Section>
  );
}
