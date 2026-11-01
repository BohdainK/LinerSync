import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router";

/**
 * The OAuth callback lands on the connection page with ?signed_in=1 or ?sign_in_error=… (D4).
 * Both are read once and removed from the URL; `onSignedIn` runs once after a successful return.
 */
export function useOAuthReturn(onSignedIn: () => void): { signedIn: boolean; signInError: string | null } {
  const [params, setParams] = useSearchParams();
  const [returned] = useState(() => ({
    signedIn: params.get("signed_in") === "1",
    signInError: params.get("sign_in_error"),
  }));
  const announce = useRef(returned.signedIn);

  useEffect(() => {
    if (!params.has("signed_in") && !params.has("sign_in_error")) return;
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        next.delete("signed_in");
        next.delete("sign_in_error");
        return next;
      },
      { replace: true },
    );
  }, [params, setParams]);

  useEffect(() => {
    if (!announce.current) return;
    announce.current = false;
    onSignedIn();
  }, [onSignedIn]);

  return returned;
}
