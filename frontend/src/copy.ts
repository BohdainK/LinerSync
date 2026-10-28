import type { AttentionKind, MemberStatus } from "@/api/types";

export const NETWORK_ERROR = "Can't reach LinerSync. Check that the server is running.";
export const HTTP_ERROR = "Something went wrong (HTTP {status})";
export const SIGNED_OUT = "Your session ended. Sign in again.";
export const SECRET_SAVED = "Saved. Leave blank to keep it.";
export const NO_SIGN_IN_HANDLER = "This version of LinerSync can't sign in to {name} from the browser";
export const INSECURE_CONTEXT =
  "Sign-in needs a secure address: open LinerSync over HTTPS or at http://127.0.0.1";
export const SHOW_TEXT = "Show all steps as text";
export const HIDE_TEXT = "Hide the text version";
export const STATS_LATER = "Stats are coming in a later version.";
export const IMPORT_TOO_LARGE = "This file is larger than 256 MB";
export const CANT_READ = "Can't read: {reason}";
export const CANT_READ_FALLBACK = "LinerSync can't read this playlist";
export const NEW_PLAYLIST = "New playlist";

export const JOB_STATUS_LABELS: Record<string, string> = {
  queued: "Waiting",
  running: "Running",
  succeeded: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
  interrupted: "Interrupted",
};

export const RUN_STATUS_LABELS: Record<string, string> = {
  running: "Running",
  succeeded: "Done",
  partial: "Partly done",
  failed: "Failed",
  aborted: "Stopped",
  awaiting_approval: "Waiting for approval",
  paused_source_missing: "Source missing",
  skipped: "Skipped",
};

export const MEMBER_STATUS_LABELS: Record<MemberStatus, string> = {
  applied: "Changed",
  nothing_to_do: "Already up to date",
  dry_run: "Dry run",
  skipped_unreadable: "Skipped: couldn't read",
  skipped_breaker: "Skipped: service paused",
  awaiting_approval: "Waiting for approval",
  failed: "Failed",
};

export const ATTENTION_KIND_LABELS: Record<AttentionKind, string> = {
  unmatched: "Song didn't match",
  changes_not_made: "Changes not made",
  brake: "Big change needs approval",
  source_missing: "Source playlist missing",
  conflict: "Conflict",
  service_failing: "Service failing",
  verify_mismatch: "Playlist differs after sync",
};

export const CONNECTION_STATUS_LABELS: Record<string, string> = {
  not_set_up: "Not set up",
  untested: "Not tested",
  connected: "Connected",
  needs_sign_in: "Needs sign-in",
  failing: "Failing",
};
