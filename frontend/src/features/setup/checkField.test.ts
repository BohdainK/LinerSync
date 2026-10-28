import { describe, expect, it } from "vitest";
import type { SetupField } from "@/api/types";
import { checkField } from "./checkField";

function field(o: Partial<SetupField> = {}): SetupField {
  return { key: "k", label: "Key", kind: "text", help: "", placeholder: "", pattern_hint: "", required: true, ...o };
}

describe("checkField", () => {
  it("required, unless a value is saved", () => {
    expect(checkField(field(), "", false)).toBe("Required");
    expect(checkField(field(), "   ", false)).toBe("Required");
    expect(checkField(field(), "", true)).toBeNull();
    expect(checkField(field({ required: false }), "", false)).toBeNull();
    expect(checkField(field({ required: false, pattern: "[a-z]+" }), "", false)).toBeNull();
  });

  it("pattern match is full and trimmed", () => {
    const f = field({ pattern: "[0-9a-f]{32}" });
    expect(checkField(f, `  ${"a1".repeat(16)}  `, false)).toBeNull();
    expect(checkField(f, `${"a1".repeat(16)}0`, false)).not.toBeNull();
  });

  it("uses pattern_hint, else the default text", () => {
    expect(checkField(field({ pattern: "x+", pattern_hint: "Only x" }), "y", false)).toBe("Only x");
    expect(checkField(field({ pattern: "x+" }), "y", false)).toBe("This doesn't look right");
  });

  it("skips patterns JS can't compile", () => {
    expect(checkField(field({ pattern: "(?P<x>a)" }), "b", false)).toBeNull();
  });
});
