import type { SetupField } from "@/api/types";

const DEFAULT_HINT = "This doesn't look right";

function compile(pattern: string): RegExp | null {
  try {
    return new RegExp(`^(?:${pattern})$`);
  } catch {
    // e.g. Python-only syntax such as (?P<name>…); the server checks again on save.
    return null;
  }
}

/** D8: mirrors the backend's field check. `saved` means a stored value exists and blank keeps it. */
export function checkField(field: SetupField, value: string, saved: boolean): string | null {
  const v = value.trim();
  if (!v) return field.required && !saved ? "Required" : null;
  if (!field.pattern) return null;
  const re = compile(field.pattern);
  if (!re || re.test(v)) return null;
  return field.pattern_hint || DEFAULT_HINT;
}
