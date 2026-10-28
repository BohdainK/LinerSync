import { Navigate, Outlet, useLocation } from "react-router";
import { ApiError, errorMessage } from "@/api/client";
import { Button } from "@/components/ui/button";
import { useSession } from "./session";

/**
 * Gates the app on a session. Only the first check redirects from here (no `ended` flag: the user
 * never had a session this visit). Once the app is showing, a lost session is handled in one place,
 * the unauthorized handler in AppShell, which adds `ended=1`.
 */
export function RequireSession() {
  const session = useSession();
  const location = useLocation();
  if (session.isPending) return <p className="p-6 text-sm text-muted-foreground">Loading…</p>;
  if (session.data === undefined) {
    if (session.error instanceof ApiError && session.error.status === 401) {
      return <Navigate to={`/login?next=${encodeURIComponent(location.pathname + location.search)}`} replace />;
    }
    return (
      <div className="flex flex-col items-start gap-3 p-6">
        <p role="alert" className="text-sm text-destructive">
          {errorMessage(session.error)}
        </p>
        <Button variant="outline" onClick={() => void session.refetch()}>
          Try again
        </Button>
      </div>
    );
  }
  if (!session.data.signed_in && !session.isError) {
    return <Navigate to={`/login?next=${encodeURIComponent(location.pathname + location.search)}`} replace />;
  }
  return <Outlet />;
}
