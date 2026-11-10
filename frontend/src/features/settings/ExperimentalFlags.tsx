import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { toast } from "sonner";
import { api, call, errorMessage, type ApiError } from "@/api/client";
import { keys } from "@/api/keys";
import type { FlagOut } from "@/api/types";
import { QueryError } from "@/components/ErrorNotice";
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
import { Switch } from "@/components/ui/switch";

const syncs = (n: number) => `${n} ${n === 1 ? "sync" : "syncs"}`;

function useFlags() {
  return useQuery({ queryKey: keys.flags, queryFn: () => call(api.GET("/api/settings/flags")) });
}

function useSetFlag() {
  const queryClient = useQueryClient();
  return useMutation<FlagOut, ApiError, { flag: FlagOut; on: boolean }>({
    mutationFn: ({ flag, on }) =>
      call(api.PUT("/api/settings/flags/{flag_id}", { params: { path: { flag_id: flag.flag_id } }, body: { on } })),
    onSuccess: (updated) => {
      queryClient.setQueryData<FlagOut[]>(keys.flags, (all) =>
        all?.map((f) => (f.flag_id === updated.flag_id ? updated : f)),
      );
      // What services can do changes sync modes, limitations, undo steps and connection details.
      void queryClient.invalidateQueries({ queryKey: keys.syncs });
      void queryClient.invalidateQueries({ queryKey: keys.connections });
      void queryClient.invalidateQueries({ queryKey: ["runs"] });
      toast(updated.on ? `${updated.title} is on` : `${updated.title} is off`);
    },
  });
}

function ConfirmDialog(props: {
  flag: FlagOut;
  pending: boolean;
  error: unknown;
  onCancel(): void;
  onConfirm(): void;
}) {
  const { flag } = props;
  const affected = flag.affected_syncs;
  return (
    <AlertDialog open onOpenChange={(open) => (open ? undefined : props.onCancel())}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Turn on {flag.title.toLowerCase()}?</AlertDialogTitle>
          <AlertDialogDescription asChild>
            <div className="space-y-2">
              <p>{flag.confirm}</p>
              {affected.length > 0 ? (
                <>
                  <p>
                    {syncs(affected.length)} into {flag.service_name} in full mode{" "}
                    {affected.length === 1 ? "is" : "are"} affected:
                  </p>
                  <ul className="list-disc space-y-1 pl-5">
                    {affected.map((s) => (
                      <li key={s.sync_id}>{s.name}</li>
                    ))}
                  </ul>
                </>
              ) : (
                <p>No sync into {flag.service_name} runs in full mode right now.</p>
              )}
            </div>
          </AlertDialogDescription>
        </AlertDialogHeader>
        {props.error ? <p className="text-sm text-destructive">{errorMessage(props.error)}</p> : null}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={props.pending}>Cancel</AlertDialogCancel>
          <Button type="button" disabled={props.pending} onClick={props.onConfirm}>
            Turn on
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}

function FlagRow({
  flag,
  onTurnOn,
  onTurnOff,
  pending,
}: {
  flag: FlagOut;
  pending: boolean;
  onTurnOn(): void;
  onTurnOff(): void;
}) {
  const id = useId();
  const describedBy = `${id}-about`;
  const locked = flag.locked_by !== "";
  // A flag that can't be turned on here can still be turned off.
  const disabled = pending || locked || (flag.unavailable !== "" && !flag.on);
  return (
    <div className="space-y-2 rounded-md border p-4">
      <div className="flex items-center justify-between gap-4">
        <label htmlFor={id} className="text-sm font-medium">
          {flag.title}
        </label>
        <Switch
          id={id}
          checked={flag.on}
          disabled={disabled}
          aria-describedby={describedBy}
          onCheckedChange={(on) => (on ? onTurnOn() : onTurnOff())}
        />
      </div>
      <div id={describedBy} className="space-y-2 text-sm text-muted-foreground">
        <p>{flag.description}</p>
        {flag.unavailable ? <p className="text-foreground">{flag.unavailable}</p> : null}
        {locked ? (
          <p className="text-foreground">
            Set by the server: {flag.locked_by}={flag.on ? "on" : "off"}. Remove it to change this here.
          </p>
        ) : null}
      </div>
    </div>
  );
}

/** Feature flags the installed services offer, each switched on the server right away. */
export function ExperimentalFlags() {
  const flags = useFlags();
  const setFlag = useSetFlag();
  const [confirming, setConfirming] = useState<FlagOut | null>(null);
  if (flags.data?.length === 0) return null;
  return (
    <section className="max-w-xl space-y-3" aria-labelledby="experimental-heading">
      <h2 id="experimental-heading" className="text-lg font-semibold">
        Experimental
      </h2>
      <QueryError query={flags} />
      {flags.data?.map((flag) => (
        <FlagRow
          key={flag.flag_id}
          flag={flag}
          pending={setFlag.isPending}
          onTurnOn={() => {
            setFlag.reset();
            if (flag.confirm) setConfirming(flag);
            else setFlag.mutate({ flag, on: true });
          }}
          onTurnOff={() => setFlag.mutate({ flag, on: false }, { onError: (e) => toast.error(errorMessage(e)) })}
        />
      ))}
      {confirming ? (
        <ConfirmDialog
          flag={confirming}
          pending={setFlag.isPending}
          error={setFlag.isError ? setFlag.error : null}
          onCancel={() => setConfirming(null)}
          onConfirm={() => setFlag.mutate({ flag: confirming, on: true }, { onSuccess: () => setConfirming(null) })}
        />
      ) : null}
    </section>
  );
}
