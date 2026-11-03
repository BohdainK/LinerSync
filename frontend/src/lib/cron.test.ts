import { describe, expect, it } from "vitest";
import { describeCron, ordinal, parseCron, toCron } from "./cron";

describe("cron", () => {
  it.each([
    ["0 * * * *", "Every hour"],
    ["0 */6 * * *", "Every 6 hours"],
    ["0 3 * * *", "Every day at 03:00"],
    ["30 7 * * 1", "Every Monday at 07:30"],
    ["0 9 * * 7", "Every Sunday at 09:00"],
    ["5 * * * *", "5 * * * *"],
    ["0 3 1 * *", "Every month on the 1st at 03:00"],
    ["0 9 1,15 * *", "Twice a month (1st and 15th) at 09:00"],
    ["0 9 1,15 * 2", "0 9 1,15 * 2"],
    ["30 6 22 * *", "Every month on the 22nd at 06:30"],
    ["0 3 31 * *", "0 3 31 * *"],
    ["0 3 1 * 1", "0 3 1 * 1"],
    ["*/15 * * * *", "*/15 * * * *"],
    ["0 */3 * * *", "0 */3 * * *"],
    ["nope", "nope"],
  ])("describes %s as %s", (cron, text) => {
    expect(describeCron(cron)).toBe(text);
  });

  it.each(["0 */12 * * *", "15 22 * * *", "0 6 * * 5", "45 23 28 * *", "30 4 1,15 * *"])("round-trips %s", (cron) => {
    expect(toCron(parseCron(cron))).toBe(cron);
  });

  it("has no cron for a custom schedule", () => {
    expect(toCron(parseCron("0 3 31 * *"))).toBeNull();
  });

  it("writes ordinals", () => {
    expect([1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 28].map(ordinal)).toEqual(
      ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st", "22nd", "23rd", "28th"],
    );
  });
});
