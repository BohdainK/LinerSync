import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, call } from "@/api/client";
import { keys } from "@/api/keys";
import type { SetupStep } from "@/api/types";
import { ErrorNotice } from "@/components/ErrorNotice";
import { Button } from "@/components/ui/button";
import { INSECURE_CONTEXT, NO_SIGN_IN_HANDLER } from "@/copy";
import { signInHandlers } from "../signIn";
import { StepBody } from "./InfoStep";

/** D7: the sign-in runs in the browser through the service's registered handler. */
export function BrowserSignInStep({
  service,
  name,
  step,
  onSignedIn,
}: {
  service: string;
  name: string;
  step: SetupStep;
  onSignedIn: () => void;
}) {
  const queryClient = useQueryClient();
  const handler = signInHandlers[service];
  const secure = window.isSecureContext;
  const signIn = useMutation({
    mutationFn: async () => {
      if (!handler) throw new Error(NO_SIGN_IN_HANDLER.replace("{name}", name));
      const path = { params: { path: { service } } };
      const { config } = await call(api.GET("/api/connections/{service}/browser-sign-in", path));
      const result = await handler(config);
      return call(api.POST("/api/connections/{service}/browser-sign-in", { ...path, body: { result } }));
    },
    onSuccess: (conn) => {
      queryClient.setQueryData(keys.connection(service), conn);
      void queryClient.invalidateQueries({ queryKey: keys.connections, exact: true });
      onSignedIn();
    },
  });
  const blocker = !handler ? NO_SIGN_IN_HANDLER.replace("{name}", name) : !secure ? INSECURE_CONTEXT : null;
  return (
    <div className="flex flex-col gap-3">
      <StepBody service={service} step={step} />
      {blocker ? <p className="text-sm text-muted-foreground">{blocker}</p> : null}
      <div>
        <Button type="button" onClick={() => signIn.mutate()} disabled={blocker !== null || signIn.isPending}>
          Sign in to {name}
        </Button>
      </div>
      {signIn.isError ? <ErrorNotice error={signIn.error} /> : null}
    </div>
  );
}
