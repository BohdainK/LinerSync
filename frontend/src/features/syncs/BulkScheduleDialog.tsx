import { useState } from "react";
import { errorMessage } from "@/api/client";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useSettings } from "@/features/settings/useSettings";
import { ScheduleField } from "./ScheduleField";
import type { BulkPatchResult } from "./useSync";

export function BulkScheduleDialog(props: {
  count: number;
  open: boolean;
  onOpenChange(open: boolean): void;
  pending: boolean;
  failed: BulkPatchResult["failed"];
  onApply(schedule: string | null): void;
}) {
  const settings = useSettings();
  const [value, setValue] = useState<string | null>(null);
  const empty = value !== null && value.trim() === "";
  const [firstFailure] = props.failed;
  return (
    <Dialog open={props.open} onOpenChange={props.onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Change schedule</DialogTitle>
          <DialogDescription>
            Applies to {props.count} {props.count === 1 ? "sync" : "syncs"}.
          </DialogDescription>
        </DialogHeader>
        <ScheduleField
          defaultSchedule={settings.data?.default_schedule ?? "…"}
          value={value}
          onChange={setValue}
          error={firstFailure ? errorMessage(firstFailure.error) : null}
        />
        <DialogFooter>
          <Button
            type="button"
            disabled={props.pending || empty}
            onClick={() => props.onApply(value === null ? null : value.trim())}
          >
            Apply
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
