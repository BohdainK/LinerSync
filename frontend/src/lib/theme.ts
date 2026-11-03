import { useSyncExternalStore } from "react";
import { writeFlag } from "./storage";

export type Theme = "light" | "dark";

// index.html applies the saved choice (or the system setting) before the app loads.
export const THEME_KEY = "linersync.theme";

const listeners = new Set<() => void>();

function current(): Theme {
  return document.documentElement.classList.contains("dark") ? "dark" : "light";
}

// How long a theme switch fades for. The sunset scene and the .theme-fade rule in index.css use the same 1s.
const FADE_MS = 1000;
let fadeTimer: ReturnType<typeof setTimeout> | undefined;

export function setTheme(theme: Theme): void {
  const root = document.documentElement;
  // Colours only transition while switching, so hovers and the rest of the app stay instant.
  root.classList.add("theme-fade");
  clearTimeout(fadeTimer);
  fadeTimer = setTimeout(() => root.classList.remove("theme-fade"), FADE_MS);
  root.classList.toggle("dark", theme === "dark");
  writeFlag(THEME_KEY, theme);
  listeners.forEach((notify) => notify());
}

function subscribe(notify: () => void) {
  listeners.add(notify);
  return () => listeners.delete(notify);
}

export function useTheme(): Theme {
  return useSyncExternalStore(subscribe, current, () => "light");
}
