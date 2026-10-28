import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { api, call, errorMessage } from "@/api/client";
import { keys } from "@/api/keys";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { SIGNED_OUT } from "@/copy";
import { safeNext } from "./session";

export function LoginPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [password, setPassword] = useState("");
  const login = useMutation({
    mutationFn: () => call(api.POST("/api/auth/login", { body: { password } })),
    onSuccess: () => {
      queryClient.setQueryData(keys.session, { signed_in: true });
      navigate(safeNext(params.get("next")), { replace: true });
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    login.mutate();
  };

  return (
    <main className="mx-auto flex min-h-screen max-w-sm flex-col justify-center gap-4 p-6">
      <h1 className="text-2xl font-semibold">Sign in to LinerSync</h1>
      {params.get("ended") === "1" && <p className="text-sm text-muted-foreground">{SIGNED_OUT}</p>}
      <form onSubmit={submit} className="flex flex-col gap-4">
        <div className="flex flex-col gap-2">
          <Label htmlFor="password">Password</Label>
          <Input
            id="password"
            type="password"
            autoComplete="current-password"
            autoFocus
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        {login.isError && (
          <p role="alert" className="text-sm text-destructive">
            {errorMessage(login.error)}
          </p>
        )}
        <Button type="submit" disabled={login.isPending}>
          Sign in
        </Button>
      </form>
    </main>
  );
}
