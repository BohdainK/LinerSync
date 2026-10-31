import { useState } from "react";
import { useNavigate, useParams } from "react-router";
import { ErrorNotice } from "@/components/ErrorNotice";
import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { SetupStepper } from "@/features/setup/SetupStepper";
import { formatDateTime } from "@/lib/format";
import { DisconnectDialog } from "./DisconnectDialog";
import { capabilityText } from "./ServiceGrid";
import { useConnection } from "./useConnections";

export function ConnectionPage() {
  const { service = "" } = useParams();
  const navigate = useNavigate();
  const [disconnecting, setDisconnecting] = useState(false);
  const info = useConnection(service);

  if (info.isError) return <ErrorNotice error={info.error} onRetry={() => void info.refetch()} />;
  if (!info.data) return <Skeleton className="h-24 w-full" />;
  const c = info.data;
  const caps = capabilityText(c.capabilities);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center gap-3">
        <h1 className="text-2xl font-semibold">{c.display_name}</h1>
        <StatusBadge kind="connection" status={c.status} />
      </div>
      {c.message ? <p>{c.message}</p> : null}
      <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1 text-sm">
        {c.account_name ? (
          <>
            <dt className="text-muted-foreground">Account</dt>
            <dd>{c.account_name}</dd>
          </>
        ) : null}
        {c.checked_at ? (
          <>
            <dt className="text-muted-foreground">Last checked</dt>
            <dd>{formatDateTime(c.checked_at)}</dd>
          </>
        ) : null}
        {c.login_expires_at ? (
          <>
            <dt className="text-muted-foreground">{c.login_expiry_estimated ? "Sign in again by" : "Login expires"}</dt>
            <dd>{formatDateTime(c.login_expires_at)}</dd>
          </>
        ) : null}
      </dl>
      {caps.length > 0 ? (
        <section aria-labelledby="capabilities" className="flex flex-col gap-2">
          <h2 id="capabilities" className="text-lg font-medium">
            What LinerSync can do here
          </h2>
          <ul className="list-disc pl-5 text-sm">
            {caps.map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ul>
        </section>
      ) : null}
      <SetupStepper service={service} />
      {c.status !== "not_set_up" ? (
        <div>
          <Button type="button" variant="outline" onClick={() => setDisconnecting(true)}>
            Disconnect…
          </Button>
        </div>
      ) : null}
      <DisconnectDialog
        service={service}
        name={c.display_name}
        open={disconnecting}
        onOpenChange={setDisconnecting}
        onDisconnected={() => navigate("/connections")}
      />
    </div>
  );
}
