import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { keys } from "@/api/keys";
import type { HomeOut, JobOut } from "@/api/types";

const ConnectedContext = createContext(false);

/** True while the live stream is open. Polling hooks use it to back off. */
// eslint-disable-next-line react-refresh/only-export-components
export function useEventsConnected(): boolean {
  return useContext(ConnectedContext);
}

const AFTER_STATUS_CHANGE = [
  keys.home,
  keys.syncs,
  keys.backups,
  keys.attention,
  keys.connections,
] as const;

function previousStatus(queryClient: QueryClient, job: JobOut): string | undefined {
  const cached = queryClient.getQueryData<JobOut>(keys.job(job.job_id));
  if (cached) return cached.status;
  const home = queryClient.getQueryData<HomeOut>(keys.home);
  const all = home ? [...home.running, ...home.queued, ...home.recent] : [];
  return all.find((j) => j.job_id === job.job_id)?.status;
}

function onJob(queryClient: QueryClient, job: JobOut): void {
  const before = previousStatus(queryClient, job);
  queryClient.setQueryData(keys.job(job.job_id), job);
  if (before === undefined || before !== job.status) {
    for (const queryKey of AFTER_STATUS_CHANGE) void queryClient.invalidateQueries({ queryKey });
    return;
  }
  queryClient.setQueryData<HomeOut>(keys.home, (home) => {
    if (!home) return home;
    const patch = (list: JobOut[]) => list.map((j) => (j.job_id === job.job_id ? job : j));
    return { ...home, running: patch(home.running), queued: patch(home.queued) };
  });
}

function refetchAll(queryClient: QueryClient): void {
  void queryClient.invalidateQueries({
    predicate: (q) => q.queryKey[0] !== keys.session[0],
  });
}

/** One EventSource for the signed-in app (decision D3). */
export function EventsProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    let source: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let delay = 1000;
    let dropped = false;
    let stopped = false;

    const onJobEvent = (e: MessageEvent<string>) => {
      try {
        onJob(queryClient, JSON.parse(e.data) as JobOut);
      } catch {
        refetchAll(queryClient);
      }
    };
    const onResync = () => refetchAll(queryClient);

    const detach = () => {
      if (!source) return;
      source.removeEventListener("job", onJobEvent);
      source.removeEventListener("resync", onResync);
      source.close();
      source = null;
    };

    const connect = () => {
      const es = new EventSource("/api/events");
      source = es;
      es.onopen = () => {
        delay = 1000;
        setConnected(true);
        if (dropped) {
          dropped = false;
          refetchAll(queryClient);
        }
      };
      es.onerror = () => {
        dropped = true;
        setConnected(false);
        // The browser retries a dropped stream itself, but gives up (CLOSED) on a bad response.
        if (es.readyState === EventSource.CLOSED && !stopped) {
          detach();
          timer = setTimeout(connect, delay);
          delay = Math.min(delay * 2, 30_000);
        }
      };
      es.addEventListener("job", onJobEvent);
      es.addEventListener("resync", onResync);
    };

    connect();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
      detach();
      setConnected(false);
    };
  }, [queryClient]);

  return <ConnectedContext.Provider value={connected}>{children}</ConnectedContext.Provider>;
}
