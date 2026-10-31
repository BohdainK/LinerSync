import { describe, expect, it } from "vitest";
import { niceTicks } from "./ticks";

describe("niceTicks", () => {
  it("rounds the top up to a clean step, at most five ticks", () => {
    expect(niceTicks(1)).toEqual([0, 1]);
    expect(niceTicks(3)).toEqual([0, 1, 2, 3]);
    expect(niceTicks(4)).toEqual([0, 1, 2, 3, 4]);
    expect(niceTicks(7)).toEqual([0, 2, 4, 6, 8]);
    expect(niceTicks(9)).toEqual([0, 5, 10]);
    expect(niceTicks(37)).toEqual([0, 10, 20, 30, 40]);
    expect(niceTicks(120)).toEqual([0, 50, 100, 150]);
    expect(niceTicks(0)).toEqual([0, 1]);
  });
});
