import type { UndoStepOut } from "@/api/types";
import { TrackList } from "@/components/TrackList";
import { useServiceNames } from "@/features/pickers/useServiceNames";

function playlist(step: UndoStepOut): string {
  return step.playlist_name ? `“${step.playlist_name}”` : "the playlist";
}

function songs(n: number): string {
  return n === 1 ? "1 song" : `${n} songs`;
}

function Step({ step }: { step: UndoStepOut }) {
  const nameOf = useServiceNames();
  const service = step.service ? nameOf(step.service) : "the service";
  switch (step.action) {
    case "restore":
      return (
        <p>
          LinerSync puts {playlist(step)} on {service} back the way it was before this run.
        </p>
      );
    case "delete_playlist":
      return (
        <p>
          This run created {playlist(step)} on {service}. LinerSync can't delete playlists:{" "}
          <span className="font-medium">delete it in {service} yourself</span> if you don't want it.
        </p>
      );
    case "remove_songs":
      return (
        <div className="space-y-2">
          <p>
            {service} doesn't let LinerSync remove songs.{" "}
            <span className="font-medium">
              Remove {step.tracks.length === 1 ? "this song" : `these ${songs(step.tracks.length)}`} from{" "}
              {playlist(step)} in {service} yourself:
            </span>
          </p>
          <TrackList tracks={step.tracks} />
        </div>
      );
  }
}

/** What undoing a run means per playlist: what LinerSync does, then what is
 * left for the user to do by hand. */
export function UndoSteps({ steps }: { steps: UndoStepOut[] }) {
  const ordered = [...steps.filter((s) => !s.by_hand), ...steps.filter((s) => s.by_hand)];
  return (
    <ul className="space-y-3 text-sm">
      {ordered.map((s) => (
        <li key={`${s.member_id}:${s.action}`}>
          <Step step={s} />
        </li>
      ))}
    </ul>
  );
}
