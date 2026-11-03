import type { JobOut } from "@/api/types";
import { JOB_STATUS_LABELS } from "@/copy";
import type { PicksStore } from "@/features/matches/usePicks";
import { useRun } from "@/features/runs/useRun";
import { isActive, useJob } from "@/live/useJob";
import { PreviewPanel } from "./PreviewPanel";
import type { PreviewState } from "./usePreview";

type Destinations = { service: string; name: string }[];

/** One playlist's preview: the run at its index in the bulk preview job. */
function Item({
  job,
  runId,
  title,
  destinations,
  picks,
}: {
  job: JobOut;
  runId: string | null;
  title: string;
  destinations: Destinations;
  picks: PicksStore;
}) {
  const run = useRun(runId);
  let preview: PreviewState;
  if (runId === null) {
    preview = { status: "failed", run: null, runId: null, job, error: new Error("This preview has no result") };
  } else if (run.isError) {
    preview = { status: "failed", run: null, runId: null, job, error: run.error };
  } else if (run.data) {
    preview = { status: "ready", run: run.data, runId, job, error: null };
  } else {
    preview = { status: "running", run: null, runId: null, job: null, error: null };
  }
  return <PreviewPanel title={title} preview={preview} destinations={destinations} picks={picks} />;
}

/** The previews of a bulk "new sync": `names[i]` is the sync whose preview is `job.run_ids[i]`. */
export function BulkPreview({
  jobId,
  names,
  destinations,
  picks,
}: {
  jobId: string;
  names: string[];
  destinations: Destinations;
  picks: PicksStore;
}) {
  const query = useJob(jobId);
  const job = query.data;
  if (!job) {
    return <PreviewPanel preview={{ status: "waiting", run: null, runId: null, job: null, error: null }} destinations={[]} />;
  }
  if (isActive(job)) {
    return <PreviewPanel preview={{ status: "running", run: null, runId: null, job, error: null }} destinations={[]} />;
  }
  if (job.status !== "succeeded") {
    const reason = job.error || JOB_STATUS_LABELS[job.status] || job.status;
    return (
      <PreviewPanel
        preview={{ status: "failed", run: null, runId: null, job, error: new Error(reason) }}
        destinations={[]}
      />
    );
  }
  return (
    <div className="space-y-6">
      {names.map((name, i) => (
        <Item
          key={i}
          job={job}
          runId={job.run_ids[i] ?? null}
          title={`Preview “${name}”`}
          destinations={destinations}
          picks={picks}
        />
      ))}
    </div>
  );
}
