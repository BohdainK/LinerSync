import { createContext, useContext, useMemo, type ReactNode } from "react";
import type { PicksStore } from "./usePicks";

type MatchEditing = { runId: string; picks: PicksStore };

const Ctx = createContext<MatchEditing | null>(null);

/** Turns on match editing (the "Change" control) for the run views below it. */
export function MatchEditingProvider({
  runId,
  picks,
  children,
}: {
  runId: string;
  picks: PicksStore;
  children: ReactNode;
}) {
  const value = useMemo(() => ({ runId, picks }), [runId, picks]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

/** The editing context, or null when run views are read-only. */
// eslint-disable-next-line react-refresh/only-export-components -- the hook belongs beside its provider
export function useMatchEditing(): MatchEditing | null {
  return useContext(Ctx);
}
