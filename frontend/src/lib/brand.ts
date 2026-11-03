import type { CSSProperties } from "react";

// Each service's brand colour. Tidal and Qobuz are monochrome brands, so they take the text colour,
// which follows light and dark mode. Unknown services fall back to the theme's primary.
const BRAND: Record<string, string> = {
  spotify: "#1db954",
  apple_music: "#fa243c",
  youtube_music: "#ff0033",
  jellyfin: "#aa5cc3",
  tidal: "var(--foreground)",
  qobuz: "var(--foreground)",
};

/** Sets `--brand` for the `brand-wash` utility and any `var(--brand)` inside. */
export function brandStyle(service: string): CSSProperties {
  return { "--brand": BRAND[service] ?? "var(--primary)" } as CSSProperties;
}
