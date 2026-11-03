import { QueryError } from "@/components/ErrorNotice";
import { StatusBadge } from "@/components/StatusBadge";
import { Badge } from "@/components/ui/badge";
import { RunResults } from "@/features/runs/RunResults";
import { useRun } from "@/features/runs/useRun";
import { formatChangeCounts } from "@/lib/format";

/** One finished transfer: a summary, its songs side by side and any issues. */
export function TransferResult({ runId }: { runId: string }) {
  const run = useRun(runId);
  if (run.isError) return <QueryError query={run} />;
  if (!run.data) return <p className="text-sm text-muted-foreground">Loading the result…</p>;
  const r = run.data;
  return (
    <article className="space-y-4" aria-label="Transfer result">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge kind="run" status={r.status} />
        {r.dry_run ? <Badge variant="outline">Dry run</Badge> : null}
        <span className="text-sm">
          {formatChangeCounts(r)}
          {r.not_made > 0 ? ` · ${r.not_made} not made` : ""}
          {r.unmatched > 0 ? ` · ${r.unmatched} didn't match` : ""}
        </span>
      </div>
      {r.dry_run ? (
        <p className="text-sm text-muted-foreground">Nothing was changed. This is what the transfer would do.</p>
      ) : null}
      {r.error ? <p className="text-sm text-destructive">{r.error}</p> : null}
      <RunResults run={r} />
    </article>
  );
}
