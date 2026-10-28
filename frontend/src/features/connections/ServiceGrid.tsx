import { StatusBadge } from "@/components/StatusBadge";
import type { ConnectionOut } from "@/api/types";

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

const SECTIONS: { category: string; title: string }[] = [
  { category: "streaming", title: "Streaming" },
  { category: "self_hosted", title: "Self-hosted" },
];

export function ServiceGrid({
  services,
  onPick,
}: {
  services: ConnectionOut[];
  onPick(service: string): void;
}) {
  return (
    <div className="flex flex-col gap-6">
      {SECTIONS.map(({ category, title }) => {
        const items = services.filter((s) => s.category === category);
        if (items.length === 0) return null;
        const headingId = `services-${category}`;
        return (
          <section key={category} aria-labelledby={headingId} className="flex flex-col gap-3">
            <h2 id={headingId} className="text-lg font-medium">
              {title}
            </h2>
            <div className="grid grid-cols-[repeat(auto-fill,minmax(14rem,1fr))] gap-3">
              {items.map((s) => (
                <button
                  key={s.service}
                  type="button"
                  onClick={() => onPick(s.service)}
                  className="flex items-center justify-between gap-2 rounded-lg border p-4 text-left hover:bg-accent"
                >
                  <span className="truncate font-medium">{s.display_name}</span>
                  <StatusBadge kind="connection" status={s.status} />
                </button>
              ))}
            </div>
          </section>
        );
      })}
    </div>
  );
}
