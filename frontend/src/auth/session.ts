import { useQuery, useQueryClient, type UseQueryResult } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import { api, call } from "@/api/client";
import { keys } from "@/api/keys";

type Session = { signed_in: boolean };

export function useSession(): UseQueryResult<Session> {
  return useQuery({
    queryKey: keys.session,
    // A 401 is the normal "not signed in" answer, so don't retry it.
    retry: false,
    staleTime: 30_000,
    queryFn: async () => {
      const data = await call(api.GET("/api/auth/session"));
      return { signed_in: data["signed_in"] === true };
    },
  });
}

/** Only same-origin paths are followed after sign-in. */
export function safeNext(next: string | null): string {
  return next !== null && next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/\\")
    ? next
    : "/";
}

export function useSignOut(): () => Promise<void> {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  return async () => {
    await call(api.POST("/api/auth/logout"));
    queryClient.clear();
    navigate("/login");
  };
}
