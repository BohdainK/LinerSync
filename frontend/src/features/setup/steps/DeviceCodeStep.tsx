import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, call } from "@/api/client";
import type { SetupStep } from "@/api/types";
import { ErrorNotice } from "@/components/ErrorNotice";
import { ExternalLink } from "@/components/ExternalLink";
import { Button } from "@/components/ui/button";
import { CopyField, StepBody } from "./InfoStep";

type Started = { user_code: string; verification_url: string; interval_s: number };

/** Polls that may fail in a row (a network blip, a restart) before the flow ends. */
const MAX_POLL_FAILURES = 3;

const servicePath = (service: string) => ({ params: { path: { service } } });

/** OAuth device flow: show a code to enter on the service's page, and poll until the
 * user approves, declines or the code expires. */
export function DeviceCodeStep({
  service,
  name,
  step,
  onSignedIn,
}: {
  service: string;
  name: string;
  step: SetupStep;
  onSignedIn: () => void;
}) {
  // The parent passes a new callback on every render; polling must not restart.
  const signedIn = useRef(onSignedIn);
  useEffect(() => {
    signedIn.current = onSignedIn;
  }, [onSignedIn]);
  const [code, setCode] = useState<Started | null>(null);
  const [ended, setEnded] = useState<string | null>(null);
  const start = useMutation({
    mutationFn: () => call(api.POST("/api/connections/{service}/device-code/start", servicePath(service))),
    onSuccess: (started) => {
      setEnded(null);
      setCode(started);
    },
  });

  useEffect(() => {
    if (!code) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    let interval = code.interval_s;
    let failures = 0;
    const poll = async () => {
      try {
        const result = await call(api.POST("/api/connections/{service}/device-code/poll", servicePath(service)));
        if (cancelled) return;
        failures = 0;
        if (result.status === "pending") {
          // The service may ask to be polled less often.
          interval = result.interval_s ?? interval;
          timer = setTimeout(() => void poll(), interval * 1000);
          return;
        }
        setCode(null);
        if (result.status === "done") signedIn.current();
        else setEnded(result.message || `${name} sign-in didn't finish. Get a new code.`);
      } catch (e) {
        if (cancelled) return;
        failures += 1;
        if (failures < MAX_POLL_FAILURES) {
          timer = setTimeout(() => void poll(), interval * 1000);
          return;
        }
        setCode(null);
        setEnded(e instanceof Error ? e.message : `${name} sign-in failed. Get a new code.`);
      }
    };
    timer = setTimeout(() => void poll(), interval * 1000);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [code, service, name]);

  return (
    <div className="flex flex-col gap-3">
      <StepBody service={service} step={step} />
      {code ? (
        <div className="flex flex-col gap-2">
          <p className="text-sm">
            Open <ExternalLink href={code.verification_url}>{code.verification_url}</ExternalLink> and enter
            this code. This page continues by itself once you allow access.
          </p>
          <p className="font-mono text-2xl tracking-widest" aria-live="polite">
            {code.user_code}
          </p>
          <CopyField item={{ label: "Code", value: code.user_code }} />
        </div>
      ) : (
        <div>
          <Button type="button" onClick={() => start.mutate()} disabled={start.isPending}>
            Get a code
          </Button>
        </div>
      )}
      {ended ? (
        <p role="alert" className="text-sm text-destructive">
          {ended}
        </p>
      ) : null}
      {start.isError ? <ErrorNotice error={start.error} /> : null}
    </div>
  );
}
