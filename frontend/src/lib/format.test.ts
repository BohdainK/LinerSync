import { describe, expect, it } from "vitest";
import { formatDateTime, formatDuration, formatRelative } from "./format";

describe("formatDuration", () => {
  it("formats m:ss and h:mm:ss", () => {
    expect(formatDuration(59_000)).toBe("0:59");
    expect(formatDuration(3_599_000)).toBe("59:59");
    expect(formatDuration(3_600_000)).toBe("1:00:00");
    expect(formatDuration(null)).toBe("");
    expect(formatDuration(undefined)).toBe("");
  });
});

describe("formatRelative", () => {
  const now = new Date("2026-01-01T12:00:00Z");
  it("formats future and past", () => {
    expect(formatRelative("2026-01-01T15:00:00Z", now, "en")).toBe("in 3 hours");
    expect(formatRelative("2026-01-01T11:55:00Z", now, "en")).toBe("5 minutes ago");
  });
});

describe("formatDateTime", () => {
  it("is empty for null and non-empty for a date", () => {
    expect(formatDateTime(null)).toBe("");
    expect(formatDateTime("2026-01-01T12:00:00Z")).not.toBe("");
  });
});
