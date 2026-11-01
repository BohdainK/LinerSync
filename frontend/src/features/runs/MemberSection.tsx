import { useState, type ReactNode } from "react";
import type { MemberOut, OperationOut } from "@/api/types";
import { ChangesList } from "@/components/ChangesList";
import { StatusBadge } from "@/components/StatusBadge";
import { TrackList } from "@/components/TrackList";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatSongs } from "@/lib/format";

function Expandable({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="space-y-1">
      <Button
        type="button"
        variant="link"
        className="h-auto p-0"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        {label}
      </Button>
      {open ? <div className="pl-4">{children}</div> : null}
    </div>
  );
}

function Operation({ op }: { op: OperationOut }) {
  switch (op.type) {
    case "create_playlist":
      return <p>Created playlist {op.name}</p>;
    case "update_metadata":
      return <p>Updated name and description</p>;
    case "add_tracks":
      return (
        <Expandable label={`Added ${formatSongs(op.tracks.length)}`}>
          <TrackList tracks={op.tracks} />
        </Expandable>
      );
    case "reorder":
      return <p>Reordered the playlist</p>;
    case "remove_tracks":
      return (
        <Expandable label={`Removed ${formatSongs(op.tracks.length)}`}>
          <TrackList tracks={op.tracks} />
        </Expandable>
      );
  }
}

export function MemberSection({ member, serviceName }: { member: MemberOut; serviceName: string }) {
  const { mismatch } = member;
  return (
    <section className="space-y-3 rounded-md border p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-semibold">{serviceName}</h3>
        {member.status ? <StatusBadge kind="member" status={member.status} /> : null}
        {member.rebuilt ? <Badge variant="outline">Rebuilt</Badge> : null}
      </div>
      {member.error ? <p className="text-sm text-destructive">{member.error}</p> : null}
      <div className="space-y-1">
        {member.operations.map((op) => (
          <Operation key={op.op_id} op={op} />
        ))}
      </div>
      {member.not_made.length > 0 ? (
        <div className="space-y-1">
          <h4 className="font-medium">Changes not made ({member.not_made.length})</h4>
          <ChangesList changes={member.not_made} />
        </div>
      ) : null}
      {mismatch ? (
        <div className="space-y-2">
          <p className="font-medium">After the sync the playlist differs from the plan</p>
          {mismatch.missing.length > 0 ? (
            <div>
              <h4 className="text-sm font-medium">Missing</h4>
              <TrackList tracks={mismatch.missing} />
            </div>
          ) : null}
          {mismatch.unexpected.length > 0 ? (
            <div>
              <h4 className="text-sm font-medium">Unexpected</h4>
              <TrackList tracks={mismatch.unexpected} />
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}
