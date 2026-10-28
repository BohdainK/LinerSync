import { describe, expect, it } from "vitest";
import { parseSourceParam } from "./sourceParam";

describe("parseSourceParam", () => {
  it("reads an archive entry and a service playlist", () => {
    expect(parseSourceParam("backup:e1")).toEqual({ service: "backup", playlistId: "e1" });
    expect(parseSourceParam("spotify:abc:def")).toEqual({ service: "spotify", playlistId: "abc:def" });
  });

  it("ignores a missing or malformed value", () => {
    expect(parseSourceParam(null)).toBeNull();
    expect(parseSourceParam("")).toBeNull();
    expect(parseSourceParam("spotify")).toBeNull();
    expect(parseSourceParam(":p1")).toBeNull();
    expect(parseSourceParam("spotify:")).toBeNull();
  });
});
