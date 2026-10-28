export const ONBOARDING_SKIPPED_KEY = "linersync.onboarding.skipped";

export function readFlag(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function writeFlag(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Storage can be blocked or full; flags are only a convenience.
  }
}
