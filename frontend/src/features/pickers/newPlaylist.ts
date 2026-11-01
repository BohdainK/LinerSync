/** The PlaylistPicker selection that means "create a new playlist". */
export const NEW_PLAYLIST_ID = "";

/** The picked playlist id, or null for a new playlist. */
export function chosenPlaylistId(selection: string[]): string | null {
  return selection[0] || null;
}
