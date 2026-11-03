import type { JobOut } from "@/api/types";

/** Where a finished job's details live, or null when there's no single page for it. */
export function jobHref(job: JobOut): string | null {
  if (job.kind === "backup") return "/backup";
  const [runId] = job.run_ids;
  return job.run_ids.length === 1 && runId ? `/runs/${runId}` : null;
}
