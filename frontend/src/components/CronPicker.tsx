import { useState } from "react";
import { Input } from "@/components/ui/input";
import { selectClass } from "@/features/pickers/ServiceSelect";
import {
  describeCron,
  formatTime,
  FREQUENCY_LABELS,
  MONTH_DAYS,
  ordinal,
  parseCron,
  toCron,
  WEEKDAYS,
  type Frequency,
  type Schedule,
} from "@/lib/cron";

const DEFAULT = "default";
const FREQUENCIES = Object.keys(FREQUENCY_LABELS) as Frequency[];
const HALF_HOURS = Array.from({ length: 48 }, (_, i) => [Math.floor(i / 2), (i % 2) * 30] as const);

/**
 * Picks a schedule from plain choices ("Every day at 03:00") and writes it as cron.
 * With `defaultSchedule`, a first "Default" choice stands for null (follow the default).
 * Crons the choices can't express stay editable as text under "Custom".
 */
export function CronPicker(props: {
  /** For an outside <label htmlFor>; otherwise the choice is labelled "Schedule". */
  id?: string;
  value: string | null;
  onChange(value: string | null): void;
  defaultSchedule?: string;
  disabled?: boolean;
  invalid?: boolean;
}) {
  const parsed = parseCron(props.value ?? "");
  // Picking "Custom" keeps the text box open even when what's typed matches a choice.
  const [customPicked, setCustomPicked] = useState(props.value !== null && parsed.frequency === "custom");
  const isDefault = props.value === null && props.defaultSchedule !== undefined;
  const frequency: Frequency = customPicked ? "custom" : parsed.frequency;
  const choice = isDefault ? DEFAULT : frequency;

  const emit = (next: Schedule) => props.onChange(toCron(next) ?? props.value ?? "");
  const pickFrequency = (picked: string) => {
    if (picked === DEFAULT) {
      setCustomPicked(false);
      props.onChange(null);
      return;
    }
    const f = picked as Frequency;
    setCustomPicked(f === "custom");
    if (f === "custom") props.onChange(props.value ?? props.defaultSchedule ?? "");
    else emit({ ...parsed, frequency: f });
  };

  const times = HALF_HOURS.some(([h, m]) => h === parsed.hour && m === parsed.minute)
    ? HALF_HOURS
    : [...HALF_HOURS, [parsed.hour, parsed.minute] as const].sort((a, b) => a[0] * 60 + a[1] - (b[0] * 60 + b[1]));
  const timed = !isDefault && (["daily", "weekly", "twiceMonthly", "monthly"] as Frequency[]).includes(frequency);

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <select
          id={props.id}
          aria-label={props.id ? undefined : "Schedule"}
          className={selectClass}
          value={choice}
          disabled={props.disabled}
          aria-invalid={props.invalid ? true : undefined}
          onChange={(e) => pickFrequency(e.target.value)}
        >
          {props.defaultSchedule !== undefined ? (
            <option value={DEFAULT}>Default ({describeCron(props.defaultSchedule)})</option>
          ) : null}
          {FREQUENCIES.map((f) => (
            <option key={f} value={f}>
              {FREQUENCY_LABELS[f]}
            </option>
          ))}
        </select>
        {timed && frequency === "weekly" ? (
          <>
            <span className="text-sm text-muted-foreground">on</span>
            <select
              aria-label="Day"
              className={selectClass}
              value={parsed.weekday}
              disabled={props.disabled}
              onChange={(e) => emit({ ...parsed, weekday: Number(e.target.value) })}
            >
              {WEEKDAYS.map((d, i) => (
                <option key={d} value={i}>
                  {d}
                </option>
              ))}
            </select>
          </>
        ) : null}
        {timed && frequency === "monthly" ? (
          <>
            <span className="text-sm text-muted-foreground">on the</span>
            <select
              aria-label="Day of the month"
              className={selectClass}
              value={parsed.day}
              disabled={props.disabled}
              onChange={(e) => emit({ ...parsed, day: Number(e.target.value) })}
            >
              {MONTH_DAYS.map((d) => (
                <option key={d} value={d}>
                  {ordinal(d)}
                </option>
              ))}
            </select>
          </>
        ) : null}
        {timed ? (
          <>
            <span className="text-sm text-muted-foreground">at</span>
            <select
              aria-label="Time"
              className={selectClass}
              value={formatTime(parsed.hour, parsed.minute)}
              disabled={props.disabled}
              onChange={(e) => {
                const [h = 0, m = 0] = e.target.value.split(":").map(Number);
                emit({ ...parsed, hour: h, minute: m });
              }}
            >
              {times.map(([h, m]) => (
                <option key={formatTime(h, m)} value={formatTime(h, m)}>
                  {formatTime(h, m)}
                </option>
              ))}
            </select>
          </>
        ) : null}
      </div>
      {!isDefault && frequency === "custom" ? (
        <Input
          aria-label="Cron schedule"
          className="max-w-xs font-mono"
          placeholder="0 * * * *"
          value={props.value ?? ""}
          disabled={props.disabled}
          aria-invalid={props.invalid ? true : undefined}
          onChange={(e) => props.onChange(e.target.value)}
        />
      ) : null}
    </div>
  );
}
