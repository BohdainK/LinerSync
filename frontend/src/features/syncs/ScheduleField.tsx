import { CronPicker } from "@/components/CronPicker";

/**
 * The schedule choice, shared with the New sync page.
 * `value` is null for the default schedule; a string (possibly empty) is a custom cron expression.
 */
export function ScheduleField(props: {
  defaultSchedule: string;
  value: string | null;
  onChange(value: string | null): void;
  error?: string | null;
}) {
  return (
    <div className="space-y-1">
      <CronPicker
        value={props.value}
        onChange={props.onChange}
        defaultSchedule={props.defaultSchedule}
        invalid={Boolean(props.error)}
      />
      {props.error ? <p className="text-sm text-destructive">{props.error}</p> : null}
    </div>
  );
}
