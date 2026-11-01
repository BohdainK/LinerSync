import { useState } from "react";
import { errorMessage } from "@/api/client";
import type { SyncOut } from "@/api/types";
import { ErrorNotice } from "@/components/ErrorNotice";
import { Section } from "@/components/Section";
import { Button } from "@/components/ui/button";
import { formatRelative } from "@/lib/format";
import { ScheduleField } from "./ScheduleField";
import { usePatchSync } from "./useSync";

export function ScheduleEditor({ sync }: { sync: SyncOut }) {
  const patch = usePatchSync(sync.sync_id);
  const [value, setValue] = useState<string | null>(sync.schedule);
  const invalid = patch.error?.status === 422;
  const empty = value !== null && value.trim() === "";
  return (
    <Section title="Schedule">
      <ScheduleField
        defaultSchedule={sync.effective_schedule}
        value={value}
        onChange={setValue}
        error={invalid ? errorMessage(patch.error) : null}
      />
      {patch.error && !invalid ? <ErrorNotice error={patch.error} /> : null}
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={patch.isPending || empty}
        onClick={() => patch.mutate({ schedule: value === null ? null : value.trim() })}
      >
        Save schedule
      </Button>
      <p className="text-sm text-muted-foreground">
        {sync.next_run_at ? `Next run ${formatRelative(sync.next_run_at)}` : "No run scheduled"}
      </p>
    </Section>
  );
}
