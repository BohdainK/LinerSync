import { describe, expect, it } from "vitest";
import { limitations } from "./limitations";

const ALL = ["read_order", "add", "remove", "reorder", "edit_metadata", "create_playlist", "delete_playlist"];
const without = (...drop: string[]) => ALL.filter((c) => !drop.includes(c));

describe("limitations (port of domain/capabilities.py)", () => {
  it("no notes when every capability is there", () => {
    expect(limitations("Tidal", ALL, "full")).toEqual([]);
    expect(limitations("Tidal", ALL, "add_only")).toEqual([]);
  });

  it("no remove and no reorder", () => {
    expect(limitations("Apple Music", without("remove", "reorder"), "full")).toEqual([
      "Apple Music can't remove or reorder songs, so this sync runs as add only into Apple Music",
    ]);
  });

  it("no remove but reorder", () => {
    expect(limitations("Deezer", without("remove"), "add_only")).toEqual([
      "Deezer can't remove songs, so this sync runs as add only into Deezer",
    ]);
  });

  it("no reorder in full mode only", () => {
    expect(limitations("Deezer", without("reorder"), "full")).toEqual([
      "Deezer can't reorder songs, so song order won't carry over to Deezer",
    ]);
    expect(limitations("Deezer", without("reorder"), "add_only")).toEqual([]);
  });

  it("no metadata editing in full mode only", () => {
    expect(limitations("Deezer", without("edit_metadata"), "full")).toEqual([
      "Deezer can't edit playlist details, so name and description changes won't carry over",
    ]);
    expect(limitations("Deezer", without("edit_metadata"), "add_only")).toEqual([]);
  });

  it("no playlist creation in either mode", () => {
    const note = "Deezer can't create playlists, so the destination playlist must already exist";
    expect(limitations("Deezer", without("create_playlist"), "full")).toEqual([note]);
    expect(limitations("Deezer", without("create_playlist"), "add_only")).toEqual([note]);
  });

  it("notes come in the backend's order", () => {
    expect(limitations("Fake B", ["add"], "full")).toEqual([
      "Fake B can't remove or reorder songs, so this sync runs as add only into Fake B",
      "Fake B can't edit playlist details, so name and description changes won't carry over",
      "Fake B can't create playlists, so the destination playlist must already exist",
    ]);
  });
});
