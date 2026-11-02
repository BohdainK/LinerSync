// The screens to walk through, named from the recorded demo data.
import recording from "./recording.json";

export type Screen = { path: string; group: string; label: string };

const responses = recording.responses as Record<string, { body: string | null }>;

function read<T>(key: string, fallback: T): T {
  const body = responses[key]?.body;
  return body ? (JSON.parse(body) as T) : fallback;
}

type Named = { name?: string; display_name?: string; label?: string };

function nameFor(path: string): string | undefined {
  const [, kind, id] = path.split("/");
  if (kind === "connections" && id) return read<Named>(`GET /api/connections/${id}`, {}).display_name;
  if (kind === "syncs" && id && id !== "new") return read<Named>(`GET /api/syncs/${id}`, {}).name;
  if (kind === "backup") {
    const entry = read<Named & { service?: string }>(`GET /api/backups/playlists/${path.split("/")[3]}`, {});
    const service = read<Named>(`GET /api/connections/${entry.service}`, {}).display_name ?? entry.service;
    return `${entry.name} (${service})`;
  }
  if (kind === "runs" && id) {
    const run = read<{ kind?: string; dry_run?: boolean; sync_id?: string | null; started_at?: string }>(
      `GET /api/runs/${id}`,
      {},
    );
    const sync = run.sync_id ? read<Named>(`GET /api/syncs/${run.sync_id}`, {}).name : undefined;
    return [run.dry_run ? "Dry run" : run.kind === "transfer" ? "Transfer" : "Run", sync].filter(Boolean).join(" · ");
  }
  return undefined;
}

const FIXED: Record<string, [string, string]> = {
  "/login": ["Start", "Sign in"],
  "/onboarding": ["Start", "Onboarding"],
  "/": ["Overview", "Home"],
  "/stats": ["Overview", "Stats"],
  "/connections": ["Connections", "All connections"],
  "/syncs": ["Syncs", "All syncs"],
  "/syncs/new": ["Syncs", "New sync"],
  "/transfers/new": ["Transfers", "New transfer"],
  "/backup": ["Backup", "Backup overview"],
  "/settings": ["Settings", "Settings"],
};

const GROUP_OF: Record<string, string> = {
  connections: "Connections",
  syncs: "Syncs",
  runs: "Runs",
  backup: "Backup",
};

export const GROUPS = ["Start", "Overview", "Connections", "Syncs", "Runs", "Transfers", "Backup", "Settings", "Other"];

export const SCREENS: Screen[] = ["/login", ...recording.routes].map((path) => {
  const fixed = FIXED[path];
  if (fixed) return { path, group: fixed[0], label: fixed[1] };
  const kind = path.split("/")[1] ?? "";
  return { path, group: GROUP_OF[kind] ?? "Other", label: nameFor(path) ?? path };
});

export function screenFor(path: string): Screen {
  return SCREENS.find((s) => s.path === path) ?? { path, group: "Other", label: path };
}
