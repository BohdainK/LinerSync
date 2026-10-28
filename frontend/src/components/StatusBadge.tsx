import { Badge } from "@/components/ui/badge";
import {
  CONNECTION_STATUS_LABELS,
  JOB_STATUS_LABELS,
  MEMBER_STATUS_LABELS,
  RUN_STATUS_LABELS,
} from "@/copy";

const LABELS: Record<string, Record<string, string>> = {
  job: JOB_STATUS_LABELS,
  run: RUN_STATUS_LABELS,
  member: MEMBER_STATUS_LABELS,
  connection: CONNECTION_STATUS_LABELS,
};

const BAD = new Set(["failed", "needs_sign_in", "interrupted"]);
const GOOD = new Set(["succeeded", "connected", "applied"]);
const BUSY = new Set(["running", "queued", "awaiting_approval"]);

function variantFor(status: string) {
  if (BAD.has(status)) return "destructive" as const;
  if (GOOD.has(status)) return "default" as const;
  if (BUSY.has(status)) return "secondary" as const;
  return "outline" as const;
}

export function StatusBadge({
  kind,
  status,
}: {
  kind: "job" | "run" | "member" | "connection";
  status: string;
}) {
  return <Badge variant={variantFor(status)}>{LABELS[kind]?.[status] ?? status}</Badge>;
}
