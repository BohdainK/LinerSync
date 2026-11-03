import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { trackOut } from "@/test/fixtures";
import { usePicks } from "./usePicks";

const source = trackOut({ service: "spotify", track_id: "b", title: "Song B" });
const b2 = trackOut({ service: "tidal", track_id: "B2", title: "Song B2" });
const b9 = trackOut({ service: "tidal", track_id: "B9", title: "Song B9" });

describe("usePicks", () => {
  it("last pick for the same song wins", () => {
    const { result } = renderHook(() => usePicks());
    act(() => result.current.pick({ runId: "r1", memberId: "m1", source, target: b2 }));
    act(() => result.current.pick({ runId: "r1", memberId: "m1", source, target: b9 }));
    expect(result.current.toApi()).toEqual([
      { source_service: "spotify", source_track_id: "b", target_service: "tidal", target_track_id: "B9" },
    ]);
  });

  it("dedupes the same song picked in two runs, last wins", () => {
    const { result } = renderHook(() => usePicks());
    act(() => result.current.pick({ runId: "r1", memberId: "m1", source, target: b2 }));
    act(() => result.current.pick({ runId: "r2", memberId: "m1", source, target: b9 }));
    expect(result.current.toApi().map((p) => p.target_track_id)).toEqual(["B9"]);
  });

  it("picked() resolves by source song and target service, from any run", () => {
    const { result } = renderHook(() => usePicks());
    act(() => result.current.pick({ runId: "r1", memberId: "m1", source, target: b2 }));
    act(() => result.current.pick({ runId: "r2", memberId: "m1", source, target: b9 }));
    expect(result.current.picked(source, "tidal")).toEqual(b9);
    expect(result.current.picked(source, "deezer")).toBeUndefined();
    expect(result.current.picked(trackOut({ track_id: "other" }), "tidal")).toBeUndefined();
  });

  it("clear() drops every pick", () => {
    const { result } = renderHook(() => usePicks());
    act(() => result.current.pick({ runId: "r1", memberId: "m1", source, target: b2 }));
    act(() => result.current.clear());
    expect(result.current.picks).toEqual([]);
    expect(result.current.toApi()).toEqual([]);
  });
});
