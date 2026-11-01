import type { ConnectionOut, SetupGuide, SetupStep } from "@/api/types";

export type StepState = "done" | "current" | "todo";

type LocalProgress = { acknowledged: Set<string>; signedIn: boolean };

function isDone(step: SetupStep, conn: ConnectionOut, local: LocalProgress): boolean {
  switch (step.kind) {
    case "info":
      return local.acknowledged.has(step.step_id);
    case "form":
      return step.fields
        .filter((f) => f.required)
        .every((f) => Boolean(conn.values[f.key]) || conn.secrets_set.includes(f.key));
    case "oauth":
    case "browser_sign_in":
    case "device_code":
      return local.signedIn;
    case "test":
      return false;
  }
}

/** D6: every step is done once connected; otherwise the first unfinished step is current
 * and later unfinished steps are todo. */
export function stepStates(guide: SetupGuide, conn: ConnectionOut, local: LocalProgress): StepState[] {
  if (conn.status === "connected") return guide.steps.map(() => "done");
  let currentFound = false;
  return guide.steps.map((step) => {
    if (isDone(step, conn, local)) return "done";
    if (currentFound) return "todo";
    currentFound = true;
    return "current";
  });
}
