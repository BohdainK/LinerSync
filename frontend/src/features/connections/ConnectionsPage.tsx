import { useNavigate } from "react-router";
import { ErrorNotice } from "@/components/ErrorNotice";
import { Skeleton } from "@/components/ui/skeleton";
import { ServiceGrid } from "./ServiceGrid";
import { useConnections } from "./useConnections";

export function ConnectionsPage() {
  const navigate = useNavigate();
  const connections = useConnections();
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">Connections</h1>
      {connections.isError ? (
        <ErrorNotice error={connections.error} onRetry={() => void connections.refetch()} />
      ) : connections.data ? (
        <>
          {connections.data.load_errors.map((e) => (
            <div
              key={e.name}
              role="alert"
              className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive"
            >
              {e.name} couldn&apos;t be loaded: {e.message}
            </div>
          ))}
          <ServiceGrid services={connections.data.services} onPick={(s) => navigate(`/connections/${s}`)} />
        </>
      ) : (
        <Skeleton className="h-24 w-full" />
      )}
    </div>
  );
}
