import { describe, expect, it } from "vitest";
import type { SetupStep } from "@/api/types";
import { connectionOut, setupGuide } from "@/test/fixtures";
import { stepStates } from "./stepStates";

function step(o: Partial<SetupStep> & Pick<SetupStep, "step_id" | "kind">): SetupStep {
  return { title: o.step_id, body: "", copy_values: [], fields: [], links: [], screenshots: [], ...o };
}

const field = (key: string, required = true) => ({
  key,
  label: key,
  kind: "text" as const,
  help: "",
  placeholder: "",
  pattern_hint: "",
  required,
});

const guide = setupGuide({
  steps: [
    step({ step_id: "app", kind: "info" }),
    step({ step_id: "keys", kind: "form", fields: [field("client_id"), { ...field("secret"), kind: "secret" }, field("note", false)] }),
    step({ step_id: "auth", kind: "oauth" }),
    step({ step_id: "test", kind: "test" }),
  ],
});

const none = { acknowledged: new Set<string>(), signedIn: false };

describe("stepStates", () => {
  it("all done when connected", () => {
    expect(stepStates(guide, connectionOut({ status: "connected" }), none)).toEqual(["done", "done", "done", "done"]);
  });

  it("form done from values or secrets_set", () => {
    const local = { acknowledged: new Set(["app"]), signedIn: false };
    const missing = connectionOut({ status: "untested", values: { client_id: "abc" }, secrets_set: [] });
    expect(stepStates(guide, missing, local)[1]).toBe("current");
    const blank = connectionOut({ status: "untested", values: { client_id: "" }, secrets_set: ["secret"] });
    expect(stepStates(guide, blank, local)[1]).toBe("current");
    const saved = connectionOut({ status: "untested", values: { client_id: "abc" }, secrets_set: ["secret"] });
    expect(stepStates(guide, saved, local)[1]).toBe("done");
  });

  it("first not-done step is current and the rest are todo", () => {
    expect(stepStates(guide, connectionOut({ status: "not_set_up" }), none)).toEqual(["current", "todo", "todo", "todo"]);
    const local = { acknowledged: new Set(["app"]), signedIn: false };
    expect(stepStates(guide, connectionOut({ status: "not_set_up" }), local)).toEqual(["done", "current", "todo", "todo"]);
  });

  it("oauth done only with signedIn", () => {
    const conn = connectionOut({ status: "untested", values: { client_id: "abc" }, secrets_set: ["secret"] });
    const acked = new Set(["app"]);
    expect(stepStates(guide, conn, { acknowledged: acked, signedIn: false })[2]).toBe("current");
    expect(stepStates(guide, conn, { acknowledged: acked, signedIn: true })[2]).toBe("done");
  });

  it("test step never done unless connected", () => {
    const conn = connectionOut({ status: "untested", values: { client_id: "abc" }, secrets_set: ["secret"] });
    const local = { acknowledged: new Set(["app"]), signedIn: true };
    expect(stepStates(guide, conn, local)).toEqual(["done", "done", "done", "current"]);
    expect(stepStates(guide, connectionOut({ ...conn, status: "failing" }), local)[3]).toBe("current");
  });
});
