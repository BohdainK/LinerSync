import type { ReactNode } from "react";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

export const BUSY_TIP = "Wait for the current run to finish";

/** Wraps a (disabled) control in a tooltip; a disabled button gets no pointer events itself. */
export function BusyTip({ busy, children }: { busy: boolean; children: ReactNode }) {
  if (!busy) return <>{children}</>;
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <span tabIndex={0}>{children}</span>
        </TooltipTrigger>
        <TooltipContent>{BUSY_TIP}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
