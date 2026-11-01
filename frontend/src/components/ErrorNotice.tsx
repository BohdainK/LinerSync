import { errorMessage } from "@/api/client";
import { Button } from "@/components/ui/button";

export function ErrorNotice({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  return (
    <div
      role="alert"
      className="flex items-center justify-between gap-3 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive"
    >
      <span>{errorMessage(error)}</span>
      {onRetry ? (
        <Button type="button" variant="outline" size="sm" onClick={onRetry}>
          Try again
        </Button>
      ) : null}
    </div>
  );
}

/** A failed query's error with a retry button; nothing while the query is fine. */
export function QueryError({ query }: { query: { isError: boolean; error: unknown; refetch: () => unknown } }) {
  return query.isError ? <ErrorNotice error={query.error} onRetry={() => void query.refetch()} /> : null;
}
