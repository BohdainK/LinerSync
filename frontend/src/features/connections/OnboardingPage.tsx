import { useNavigate } from "react-router";
import { ErrorNotice } from "@/components/ErrorNotice";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { CONTRIBUTE_SOURCE_URL, REQUEST_SOURCE_URL } from "@/config";
import { ONBOARDING_SKIPPED_KEY, writeFlag } from "@/lib/storage";
import { ServiceGrid } from "./ServiceGrid";
import { useConnections } from "./useConnections";

export function OnboardingPage() {
  const navigate = useNavigate();
  const connections = useConnections();
  return (
    <div className="flex flex-col gap-6">
      <h1 className="text-2xl font-semibold">Connect your first service</h1>
      {connections.isError ? (
        <ErrorNotice error={connections.error} onRetry={() => void connections.refetch()} />
      ) : connections.data ? (
        <ServiceGrid services={connections.data.services} onPick={(s) => navigate(`/connections/${s}`)} />
      ) : (
        <Skeleton className="h-24 w-full" />
      )}
      <div className="flex flex-wrap items-center gap-4 text-sm">
        <a href={REQUEST_SOURCE_URL} target="_blank" rel="noreferrer" className="underline">
          Request a source
        </a>
        <a href={CONTRIBUTE_SOURCE_URL} target="_blank" rel="noreferrer" className="underline">
          Contribute a source
        </a>
        <Button
          type="button"
          variant="ghost"
          onClick={() => {
            writeFlag(ONBOARDING_SKIPPED_KEY, "1");
            navigate("/");
          }}
        >
          Skip for now
        </Button>
      </div>
    </div>
  );
}
