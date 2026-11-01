import { Check } from "lucide-react";
import { useId, type ReactNode } from "react";
import type { SetupStep } from "@/api/types";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { StepState } from "./stepStates";

export function StepItem({
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
  children: ReactNode;
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
