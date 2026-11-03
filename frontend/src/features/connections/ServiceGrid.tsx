import { StatusBadge } from "@/components/StatusBadge";
import { Badge } from "@/components/ui/badge";
import type { ConnectionOut } from "@/api/types";
import { brandStyle } from "@/lib/brand";
import { cn } from "@/lib/utils";

const CAPABILITY_TEXT: [string, string][] = [
  ["read_order", "Read song order"],
  ["add", "Add songs"],
  ["remove", "Remove songs"],
  ["reorder", "Reorder songs"],
  ["edit_metadata", "Edit playlist details"],
  ["create_playlist", "Create playlists"],
  ["delete_playlist", "Delete playlists"],
];

/** Capability wording, in the backend Capability enum's order. Unknown values are dropped. */
// eslint-disable-next-line react-refresh/only-export-components -- the brief puts this helper beside ServiceGrid
export function capabilityText(caps: string[]): string[] {
  return CAPABILITY_TEXT.filter(([key]) => caps.includes(key)).map(([, text]) => text);
}

// Anything set up goes under Connected, whatever its health, so a service needing sign-in stays with
// the user's other accounts. The rest are grouped by category.
const isSetUp = (s: ConnectionOut) => s.status !== "not_set_up";

const SECTIONS: { key: string; title: string; includes(s: ConnectionOut): boolean }[] = [
  { key: "connected", title: "Connected", includes: isSetUp },
  { key: "streaming", title: "Streaming", includes: (s) => !isSetUp(s) && s.category === "streaming" },
  { key: "self_hosted", title: "Self-hosted", includes: (s) => !isSetUp(s) && s.category === "self_hosted" },
];

export function ServiceGrid({ services, onPick }: { services: ConnectionOut[]; onPick(service: string): void }) {
  return (
    <div className="flex flex-col gap-6">
      {SECTIONS.map(({ key, title, includes }) => {
        const items = services.filter(includes);
        if (items.length === 0) return null;
        const headingId = `services-${key}`;
        return (
          <section key={key} aria-labelledby={headingId} className="flex flex-col gap-3">
            <h2 id={headingId} className="text-lg font-medium">
              {title}
            </h2>
            <div className="grid grid-cols-[repeat(auto-fill,minmax(14rem,1fr))] gap-3">
              {items.map((s) => (
                <button
                  key={s.service}
                  type="button"
                  onClick={() => onPick(s.service)}
                  style={s.status === "connected" ? brandStyle(s.service) : undefined}
                  className={cn(
                    "flex items-center justify-between gap-2 rounded-lg border p-4 text-left hover:bg-accent",
                    s.status === "connected" && "brand-wash",
                  )}
                >
                  <span className="truncate font-medium">{s.display_name}</span>
                  <span className="flex shrink-0 items-center gap-1">
                    {s.experimental && (
                      <Badge variant="outline" title="Unofficial access that may stop working at any time">
                        Experimental
                      </Badge>
                    )}
                    {/* A connected card already wears its brand colour, so the pill would only repeat it. */}
                    {s.status !== "connected" && <StatusBadge kind="connection" status={s.status} />}
                  </span>
                </button>
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}
