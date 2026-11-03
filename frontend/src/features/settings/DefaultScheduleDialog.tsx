import { errorMessage } from "@/api/client";
import type { SyncOut } from "@/api/types";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { describeCron } from "@/lib/cron";

/** Syncs a default-schedule change touches: those following the default, and those set to the same cron by hand. */
export type DefaultScheduleImpact = { following: SyncOut[]; sameCron: SyncOut[] };

// "every hour" reads better mid-sentence than "Every hour".
const inline = (cron: string) => {
  const text = describeCron(cron);
  return text.charAt(0).toLowerCase() + text.slice(1);
};
const syncs = (n: number) => `${n} ${n === 1 ? "sync" : "syncs"}`;

export function DefaultScheduleDialog(props: {
  impact: DefaultScheduleImpact;
  from: string;
  to: string;
  pending: boolean;
  error: unknown;
  onOpenChange(open: boolean): void;
  /** Move every affected sync to the new default. */
  onUpdate(): void;
  /** Leave every affected sync running on the old schedule. */
  onKeep(): void;
}) {
  const { following, sameCron } = props.impact;
  const total = following.length + sameCron.length;
  const from = inline(props.from);
  const to = inline(props.to);
  return (
    <AlertDialog open onOpenChange={props.onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Change the default schedule?</AlertDialogTitle>
          <AlertDialogDescription asChild>
            <div className="space-y-2">
              <p>
                The default changes from <strong>{from}</strong> to <strong>{to}</strong>.
              </p>
              <ul className="list-disc space-y-1 pl-5">
                {following.length > 0 ? (
                  <li>
                    {syncs(following.length)} {following.length === 1 ? "follows" : "follow"} the default.
                  </li>
                ) : null}
                {sameCron.length > 0 ? (
                  <li>
                    {syncs(sameCron.length)} {sameCron.length === 1 ? "has its" : "have their"} own schedule set to {from}.
                  </li>
                ) : null}
              </ul>
              <p>Update them to {to}, or keep them on {from}?</p>
            </div>
          </AlertDialogDescription>
        </AlertDialogHeader>
        {props.error ? <p className="text-sm text-destructive">{errorMessage(props.error)}</p> : null}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={props.pending}>Cancel</AlertDialogCancel>
          <Button type="button" variant="outline" disabled={props.pending} onClick={props.onKeep}>
            Keep on {from}
          </Button>
          <Button type="button" disabled={props.pending} onClick={props.onUpdate}>
            Update {syncs(total)}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
