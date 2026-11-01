import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { api, call } from "@/api/client";
import { keys } from "@/api/keys";
import type { ConnectionOut, SetupGuide, SetupStep } from "@/api/types";
import { QueryError } from "@/components/ErrorNotice";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { HIDE_TEXT, SHOW_TEXT } from "@/copy";
import { useConnection } from "@/features/connections/useConnections";
import { StepsAsText } from "./StepsAsText";
import { StepContent } from "./StepContent";
import { StepItem } from "./StepItem";
import { stepStates } from "./stepStates";
import { useOAuthReturn } from "./useOAuthReturn";

const SIGN_IN_KINDS = new Set<SetupStep["kind"]>(["oauth", "browser_sign_in", "device_code"]);

function Steps({
  service,
  guide,
  conn,
}: {
  service: string;
  guide: SetupGuide;
  conn: ConnectionOut;
}) {
  const queryClient = useQueryClient();
  const [acknowledged, setAcknowledged] = useState<Set<string>>(() => new Set());
  const [signedInHere, setSignedInHere] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [asText, setAsText] = useState(false);
  const name = guide.display_name;

  const test = useMutation({
    mutationFn: () => call(api.POST("/api/connections/{service}/test", { params: { path: { service } } })),
    onSettled: () => queryClient.invalidateQueries({ queryKey: keys.connection(service), exact: true }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.connections, exact: true }),
  });

  const onSignedIn = () => {
    setSignedInHere(true);
    toast.success(`Signed in to ${name}`);
    test.mutate();
  };
  const oauth = useOAuthReturn(onSignedIn);
  const signedIn = signedInHere || oauth.signedIn;

  const states = stepStates(guide, conn, { acknowledged, signedIn });
  const signInPending = guide.steps.some((s) => SIGN_IN_KINDS.has(s.kind)) && !signedIn;

  const afterSave = () => {
    if (!signInPending) test.mutate();
  };

  return (
    <div className="flex flex-col gap-4">
      <div>
        <Button type="button" variant="link" className="px-0" onClick={() => setAsText((v) => !v)}>
          {asText ? HIDE_TEXT : SHOW_TEXT}
        </Button>
      </div>
      {guide.prerequisites.length > 0 ? (
        <section aria-label="Before you start" className="flex flex-col gap-1 text-sm">
          <h3 className="font-medium">Before you start</h3>
          <ul className="list-disc pl-5">
            {guide.prerequisites.map((p, i) => (
              <li key={i}>{p}</li>
            ))}
          </ul>
        </section>
      ) : null}
      {asText ? (
        <StepsAsText guide={guide} />
      ) : (
        <ol className="flex flex-col gap-3">
          {guide.steps.map((step, i) => {
            const state = states[i] ?? "todo";
            // Done steps fold away (the test step stays open to show its result); any can be reopened.
            const open = expanded[step.step_id] ?? (state !== "done" || step.kind === "test");
            return (
              <StepItem
                key={step.step_id}
                index={i}
                step={step}
                state={state}
                open={open}
                onToggle={() => setExpanded((e) => ({ ...e, [step.step_id]: !open }))}
              >
                <StepContent
                  service={service}
                  name={name}
                  step={step}
                  conn={conn}
                  signInError={oauth.signInError}
                  test={test}
                  onAcknowledge={() => setAcknowledged((a) => new Set(a).add(step.step_id))}
                  onSaved={afterSave}
                  onSignedIn={onSignedIn}
                />
              </StepItem>
            );
          })}
        </ol>
      )}
    </div>
  );
}

/** D6: renders any service's setup guide by step kind. */
export function SetupStepper({ service }: { service: string }) {
  const guide = useQuery({
    queryKey: keys.guide(service),
    queryFn: () => call(api.GET("/api/connections/{service}/guide", { params: { path: { service } } })),
  });
  const conn = useConnection(service);
  const failed = guide.isError ? guide : conn.isError ? conn : null;

  return (
    <section aria-labelledby="setup" className="flex flex-col gap-2">
      <h2 id="setup" className="text-lg font-medium">
        Set up
      </h2>
      {failed ? (
        <QueryError query={failed} />
      ) : guide.data && conn.data ? (
        <Steps service={service} guide={guide.data} conn={conn.data} />
      ) : (
        <Skeleton className="h-40 w-full" />
      )}
    </section>
  );
}
