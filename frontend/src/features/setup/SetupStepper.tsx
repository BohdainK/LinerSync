import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { useSearchParams } from "react-router";
import { toast } from "sonner";
import { api, call } from "@/api/client";
import { keys } from "@/api/keys";
import type { ConnectionOut, SetupGuide, SetupStep } from "@/api/types";
import { ErrorNotice } from "@/components/ErrorNotice";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { HIDE_TEXT, SHOW_TEXT } from "@/copy";
import { useConnection } from "@/features/connections/useConnections";
import { cn } from "@/lib/utils";
import { StepsAsText } from "./StepsAsText";
import { stepStates, type StepState } from "./stepStates";
import { BrowserSignInStep } from "./steps/BrowserSignInStep";
import { DeviceCodeStep } from "./steps/DeviceCodeStep";
import { FormStep } from "./steps/FormStep";
import { InfoStep } from "./steps/InfoStep";
import { OAuthStep } from "./steps/OAuthStep";
import { TestStep } from "./steps/TestStep";

const SIGN_IN_KINDS = new Set<SetupStep["kind"]>(["oauth", "browser_sign_in", "device_code"]);

function StepItem({
  index,
  step,
  state,
  open,
  onToggle,
  children,
}: {
  index: number;
  step: SetupStep;
  state: StepState;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  const titleId = useId();
  const done = state === "done";
  return (
    <li
      data-step={step.step_id}
      aria-current={state === "current" ? "step" : undefined}
      aria-labelledby={titleId}
      className={cn(
        "flex flex-col gap-3 rounded-lg border p-4",
        state === "current" && "border-primary",
        state === "todo" && "opacity-80",
      )}
    >
      <div className="flex items-center gap-3">
        <span
          aria-hidden
          className={cn(
            "flex size-7 shrink-0 items-center justify-center rounded-full border text-sm font-medium",
            done && "border-primary bg-primary text-primary-foreground",
          )}
        >
          {index + 1}
        </span>
        <h3 id={titleId} className="font-medium">
          <span className="sr-only">Step {index + 1}: </span>
          {step.title}
        </h3>
        {done ? (
          <span className="flex items-center gap-1 text-sm text-green-700">
            <Check aria-hidden className="size-4" />
            <span className="sr-only">Done</span>
          </span>
        ) : null}
        {done && step.kind !== "test" ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="ml-auto"
            aria-expanded={open}
            aria-label={`${open ? "Hide" : "Show"} step: ${step.title}`}
            onClick={onToggle}
          >
            {open ? "Hide" : "Show"}
          </Button>
        ) : null}
      </div>
      {open ? <div className="pl-10">{children}</div> : null}
    </li>
  );
}

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
  const [params, setParams] = useSearchParams();
  const [acknowledged, setAcknowledged] = useState<Set<string>>(() => new Set());
  const [signedIn, setSignedIn] = useState(() => params.get("signed_in") === "1");
  const [signInError] = useState(() => params.get("sign_in_error"));
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [asText, setAsText] = useState(false);
  const returnedSignedIn = useRef(signedIn);
  const name = guide.display_name;

  const test = useMutation({
    mutationFn: () => call(api.POST("/api/connections/{service}/test", { params: { path: { service } } })),
    onSettled: () => queryClient.invalidateQueries({ queryKey: keys.connection(service), exact: true }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.connections, exact: true }),
  });
  const runTest = test.mutate;

  // The OAuth callback lands here with ?signed_in=1 or ?sign_in_error=… (D4).
  useEffect(() => {
    if (!params.has("signed_in") && !params.has("sign_in_error")) return;
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete("signed_in");
        next.delete("sign_in_error");
        return next;
      },
      { replace: true },
    );
  }, [params, setParams]);

  useEffect(() => {
    if (!returnedSignedIn.current) return;
    returnedSignedIn.current = false;
    toast.success(`Signed in to ${name}`);
    runTest();
  }, [name, runTest]);

  const states = stepStates(guide, conn, { acknowledged, signedIn });
  const signInPending = guide.steps.some((s) => SIGN_IN_KINDS.has(s.kind)) && !signedIn;

  const afterSave = () => {
    if (!signInPending) runTest();
  };
  const afterBrowserSignIn = () => {
    setSignedIn(true);
    toast.success(`Signed in to ${name}`);
    runTest();
  };

  const content = (step: SetupStep) => {
    switch (step.kind) {
      case "info":
        return (
          <InfoStep
            service={service}
            step={step}
            onAcknowledge={() => setAcknowledged((a) => new Set(a).add(step.step_id))}
          />
        );
      case "form":
        return <FormStep service={service} step={step} conn={conn} onSaved={afterSave} />;
      case "oauth":
        return <OAuthStep service={service} name={name} step={step} signInError={signInError} />;
      case "browser_sign_in":
        return <BrowserSignInStep service={service} name={name} step={step} onSignedIn={afterBrowserSignIn} />;
      case "device_code":
        return <DeviceCodeStep service={service} name={name} step={step} onSignedIn={afterBrowserSignIn} />;
      case "test":
        return (
          <TestStep
            service={service}
            step={step}
            onTest={() => runTest()}
            pending={test.isPending}
            result={test.data}
            error={test.error}
          />
        );
    }
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
                {content(step)}
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
        <ErrorNotice error={failed.error} onRetry={() => void failed.refetch()} />
      ) : guide.data && conn.data ? (
        <Steps service={service} guide={guide.data} conn={conn.data} />
      ) : (
        <Skeleton className="h-40 w-full" />
      )}
    </section>
  );
}
