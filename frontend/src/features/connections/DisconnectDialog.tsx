import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, call, errorMessage } from "@/api/client";
import { keys } from "@/api/keys";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";

export function DisconnectDialog({
  service,
  name,
  open,
  onOpenChange,
  onDisconnected,
}: {
  service: string;
  name: string;
  open: boolean;
  onOpenChange(open: boolean): void;
  onDisconnected(): void;
}) {
  const queryClient = useQueryClient();
  const [deleteBackups, setDeleteBackups] = useState(false);
  const disconnect = useMutation({
    mutationFn: () =>
      call(
        api.DELETE("/api/connections/{service}", {
          params: { path: { service }, query: { delete_backups: deleteBackups } },
        }),
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: keys.connections });
      onOpenChange(false);
      onDisconnected();
    },
  });

  return (
    <ConfirmDialog
      title={`Disconnect ${name}?`}
      confirmLabel="Disconnect"
      destructive
      open={open}
      pending={disconnect.isPending}
      onOpenChange={(next) => {
        if (!next) {
          disconnect.reset();
          setDeleteBackups(false);
        }
        onOpenChange(next);
      }}
      onConfirm={() => disconnect.mutate()}
      body={
        <div className="flex flex-col gap-3">
          <p>Its backups and archive are kept unless you choose otherwise.</p>
          <div className="flex items-center gap-2">
            <Checkbox
              id="delete-backups"
              checked={deleteBackups}
              onCheckedChange={(v) => setDeleteBackups(v === true)}
            />
            <Label htmlFor="delete-backups">Also delete this service&apos;s backups</Label>
          </div>
          {disconnect.isError ? (
            <p role="alert" className="text-destructive">
              {errorMessage(disconnect.error)}
            </p>
          ) : null}
        </div>
      }
    />
  );
}
