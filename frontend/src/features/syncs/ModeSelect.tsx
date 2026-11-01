import { useId } from "react";
import type { SyncMode } from "@/api/types";
import { selectClass } from "@/features/pickers/ServiceSelect";
import { MODE_LABELS } from "./modes";

const MODES = Object.keys(MODE_LABELS) as SyncMode[];

/** A destination's sync mode. With `label` it renders a visible label; otherwise pass `aria-label`. */
export function ModeSelect({
  label,
  value,
  onChange,
  ...rest
}: {
  label?: string;
  value: SyncMode;
  onChange(mode: SyncMode): void;
  "aria-label"?: string;
  disabled?: boolean;
}) {
  const id = useId();
  const select = (
    <select
      id={label ? id : undefined}
      className={selectClass}
      value={value}
      onChange={(e) => onChange(e.target.value as SyncMode)}
      {...rest}
    >
      {MODES.map((m) => (
        <option key={m} value={m}>
          {MODE_LABELS[m]}
        </option>
      ))}
    </select>
  );
  if (!label) return select;
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      {select}
    </div>
  );
}
