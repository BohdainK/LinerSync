import { Check } from "lucide-react";
import type { ConnectionTestOut, SetupStep } from "@/api/types";
import { ErrorNotice } from "@/components/ErrorNotice";
import { Button } from "@/components/ui/button";
import { StepBody } from "./InfoStep";

export function TestStep({
  service,
  step,
  onTest,
  pending,
  result,
  error,
}: {
  service: string;
  step: SetupStep;
  onTest: () => void;
  pending: boolean;
  result: ConnectionTestOut | undefined;
  error: unknown;
}) {
  return (
    <div className="flex flex-col gap-3">
      <StepBody service={service} step={step} />
      <div>
        <Button type="button" onClick={onTest} disabled={pending}>
          {pending ? "Testing…" : "Test connection"}
        </Button>
      </div>
      {error ? <ErrorNotice error={error} /> : null}
      {result && !pending ? (
        result.ok ? (
          <p role="status" className="flex items-center gap-2 text-sm">
            <Check aria-hidden className="size-4 text-green-600" />
            {[result.message, result.account_name].filter(Boolean).join(" — ")}
          </p>
        ) : (
          <p role="alert" className="text-sm text-destructive">
            {result.message}
          </p>
        )
      ) : null}
    </div>
  );
}
