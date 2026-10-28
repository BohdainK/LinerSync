import { useId } from "react";
import { Input } from "@/components/ui/input";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";

/**
 * The default-or-custom cron choice, shared with the New sync page.
 * `value` is null for the default schedule; a string (possibly empty) is a custom cron expression.
 */
export function ScheduleField(props: {
  defaultSchedule: string;
  value: string | null;
  onChange(value: string | null): void;
  error?: string | null;
}) {
  const id = useId();
  const custom = props.value !== null;
  return (
    <div className="space-y-2">
      <RadioGroup
        aria-label="Schedule"
        value={custom ? "custom" : "default"}
        onValueChange={(v) => props.onChange(v === "custom" ? (props.value ?? "") : null)}
      >
        <div className="flex items-center gap-2">
          <RadioGroupItem id={`${id}-default`} value="default" />
          <label htmlFor={`${id}-default`} className="text-sm">
            Default ({props.defaultSchedule})
          </label>
        </div>
        <div className="flex items-center gap-2">
          <RadioGroupItem id={`${id}-custom`} value="custom" />
          <label htmlFor={`${id}-custom`} className="text-sm">
            Custom
          </label>
        </div>
      </RadioGroup>
      {custom ? (
        <div className="space-y-1">
          <Input
            aria-label="Cron schedule"
            className="max-w-xs font-mono"
            placeholder="0 * * * *"
            value={props.value ?? ""}
            aria-invalid={props.error ? true : undefined}
            onChange={(e) => props.onChange(e.target.value)}
          />
          {props.error ? <p className="text-sm text-destructive">{props.error}</p> : null}
        </div>
      ) : null}
    </div>
  );
}
