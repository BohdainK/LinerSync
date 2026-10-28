import type { ReactNode } from "react";
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

export function ConfirmDialog(props: {
  title: string;
  body?: ReactNode;
  confirmLabel: string;
  destructive?: boolean;
  open: boolean;
  onOpenChange(open: boolean): void;
  onConfirm(): void;
  pending?: boolean;
  confirmDisabled?: boolean;
}) {
  return (
    <AlertDialog open={props.open} onOpenChange={props.onOpenChange}>
      <AlertDialogContent {...(props.body ? {} : { "aria-describedby": undefined })}>
        <AlertDialogHeader>
          <AlertDialogTitle>{props.title}</AlertDialogTitle>
          {props.body ? (
            <AlertDialogDescription asChild>
              <div>{props.body}</div>
            </AlertDialogDescription>
          ) : null}
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={props.pending}>Cancel</AlertDialogCancel>
          <Button
            type="button"
            variant={props.destructive ? "destructive" : "default"}
            disabled={props.pending || props.confirmDisabled}
            onClick={props.onConfirm}
          >
            {props.confirmLabel}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
