import { describe, expect, it } from "vitest";
import { safeHref } from "./safeUrl";

describe("safeHref", () => {
  it("allows http and https", () => {
    expect(safeHref("https://x")).toBe("https://x");
    expect(safeHref("http://x")).toBe("http://x");
  });
  it("rejects everything else", () => {
    expect(safeHref("javascript:alert(1)")).toBeNull();
    expect(safeHref("data:text/html,x")).toBeNull();
    expect(safeHref("not a url")).toBeNull();
    expect(safeHref(null)).toBeNull();
    expect(safeHref(undefined)).toBeNull();
  });
});
