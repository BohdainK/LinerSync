import { useId } from "react";
import { canAdd, isSetUp, useConnections } from "@/features/connections/useConnections";
import { ARCHIVE } from "./usePlaylists";

export const selectClass =
  "h-9 rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-xs disabled:opacity-50";

export function ServiceSelect(props: {
  label: string;
  value: string | null;
  onChange(service: string): void;
  exclude?: string[];
  includeArchive?: boolean;
  /** Only services that can add songs: for destinations. */
  writableOnly?: boolean;
}) {
  const id = useId();
  const connections = useConnections();
  const options = (connections.data?.services ?? []).filter(
    (s) =>
      isSetUp(s) && !props.exclude?.includes(s.service) && (!props.writableOnly || canAdd(s)),
  );
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-sm font-medium">
        {props.label}
      </label>
      <select
        id={id}
        className={selectClass}
        value={props.value ?? ""}
        onChange={(e) => {
          if (e.target.value) props.onChange(e.target.value);
        }}
      >
        <option value="" disabled>
          Choose a service
        </option>
        {options.map((s) => (
          <option key={s.service} value={s.service}>
            {s.display_name}
          </option>
        ))}
        {props.includeArchive ? <option value={ARCHIVE}>Archive</option> : null}
      </select>
    </div>
  );
}
