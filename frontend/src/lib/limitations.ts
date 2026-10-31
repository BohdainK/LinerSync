/**
 * What a destination service can't do, as notes for the user (decision D9).
 * A port of `backend/src/linersync/domain/capabilities.py:limitations`: keep the strings identical.
 * Once a sync exists, the UI shows the server's `DestinationOut.limitations` instead.
 */
export function limitations(serviceName: string, caps: readonly string[], mode: "full" | "add_only"): string[] {
  const name = serviceName;
  const full = mode === "full";
  const has = (c: string) => caps.includes(c);
  if (!has("add")) {
    return [`${name} is read-only, so it can't be a destination`];
  }
  const out: string[] = [];
  if (!has("remove")) {
    if (!has("reorder")) {
      out.push(`${name} can't remove or reorder songs, so this sync runs as add only into ${name}`);
    } else {
      out.push(`${name} can't remove songs, so this sync runs as add only into ${name}`);
    }
  } else if (!has("reorder") && full) {
    out.push(`${name} can't reorder songs, so song order won't carry over to ${name}`);
  }
  if (!has("edit_metadata") && full) {
    out.push(`${name} can't edit playlist details, so name and description changes won't carry over`);
  }
  if (!has("create_playlist")) {
    out.push(`${name} can't create playlists, so the destination playlist must already exist`);
  }
  return out;
}
