import { useId } from "react";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";

export type Scope = "one" | "all";

export function ScopeChoice({ value, onChange }: { value: Scope; onChange(scope: Scope): void }) {
  const id = useId();
  return (
    <RadioGroup
      aria-label="How many playlists"
      className="flex gap-4"
      value={value}
      onValueChange={(v) => onChange(v === "all" ? "all" : "one")}
    >
      <div className="flex items-center gap-2">
        <RadioGroupItem id={`${id}-one`} value="one" />
        <label htmlFor={`${id}-one`} className="text-sm">
          One playlist
        </label>
      </div>
      <div className="flex items-center gap-2">
        <RadioGroupItem id={`${id}-all`} value="all" />
        <label htmlFor={`${id}-all`} className="text-sm">
          All playlists
        </label>
      </div>
    </RadioGroup>
  );
}
