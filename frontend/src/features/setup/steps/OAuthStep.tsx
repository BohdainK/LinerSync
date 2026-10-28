import { useMutation } from "@tanstack/react-query";
import { api, call } from "@/api/client";
import type { SetupStep } from "@/api/types";
import { ErrorNotice } from "@/components/ErrorNotice";
import { Button } from "@/components/ui/button";
import { StepBody } from "./InfoStep";

export function OAuthStep({
  service,
  name,
  step,
  signInError,
}: {
  service: string;
  name: string;
  step: SetupStep;
  signInError: string | null;
}) {
  const start = useMutation({
    mutationFn: () => call(api.POST("/api/connections/{service}/oauth/start", { params: { path: { service } } })),
    onSuccess: ({ url }) => window.location.assign(url),
  });
  return (
    <div className="flex flex-col gap-3">
      <StepBody service={service} step={step} />
      {signInError ? (
        <p role="alert" className="text-sm text-destructive">
          {signInError}
        </p>
      ) : null}
      <div>
        <Button type="button" onClick={() => start.mutate()} disabled={start.isPending}>
          Sign in to {name}
        </Button>
      </div>
      {start.isError ? <ErrorNotice error={start.error} /> : null}
    </div>
  );
}
