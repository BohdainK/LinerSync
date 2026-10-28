import { act, renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { keys } from "@/api/keys";
import type { HomeOut, JobOut } from "@/api/types";
import { homeOut, jobOut } from "@/test/fixtures";
import { FakeEventSource } from "@/test/eventsource";
import { server } from "@/test/server";
import { EventsProvider } from "./events";
import { useJob } from "./useJob";

function setup() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      <EventsProvider>{children}</EventsProvider>
    </QueryClientProvider>
  );
  return { queryClient, wrapper };
}

function countRequests(paths: string[]) {
  const counts = new Map<string, number>(paths.map((p) => [p, 0]));
  const handlers = paths.map((p) =>
    http.get(p, () => {
      counts.set(p, (counts.get(p) ?? 0) + 1);
      return HttpResponse.json(p === "/api/home" ? homeOut() : []);
    }),
  );
  server.use(...handlers);
  return counts;
}

function useWatch(paths: string[]) {
  for (const p of paths) {
    // eslint-disable-next-line react-hooks/rules-of-hooks
    useQuery({
      queryKey: p === "/api/home" ? keys.home : [p.slice(5)],
      queryFn: async () => (await fetch(`${location.origin}${p}`)).json() as Promise<unknown>,
      staleTime: Infinity,
    });
  }
}

afterEach(() => vi.useRealTimers());

describe("EventsProvider", () => {
  it("opens one stream at /api/events", () => {
    const { wrapper } = setup();
    renderHook(() => null, { wrapper });
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(FakeEventSource.last?.url).toBe("/api/events");
  });

  it("writes job events into the job cache", () => {
    const { wrapper, queryClient } = setup();
    renderHook(() => null, { wrapper });
    const job = jobOut({ job_id: "j1" });
    act(() => FakeEventSource.last?.emit("job", job));
    expect(queryClient.getQueryData<JobOut>(keys.job("j1"))).toEqual(job);
  });

  it("patches home progress in place without refetching", async () => {
    const paths = ["/api/home"];
    const counts = countRequests(paths);
    const { wrapper, queryClient } = setup();
    const running = jobOut({ job_id: "j1", progress: { done: 1, total: 10, phase: "p", message: "" } });
    queryClient.setQueryData(keys.home, homeOut({ running: [running] }));
    renderHook(() => useWatch(paths), { wrapper });
    const next = { ...running, progress: { done: 5, total: 10, phase: "p", message: "" } };
    act(() => FakeEventSource.last?.emit("job", next));
    expect(queryClient.getQueryData<HomeOut>(keys.home)?.running[0]?.progress?.done).toBe(5);
    await new Promise((r) => setTimeout(r, 50));
    expect(counts.get("/api/home")).toBe(0);
  });

  it("invalidates on status change", async () => {
    const paths = ["/api/home", "/api/syncs", "/api/backups"];
    const counts = countRequests(paths);
    const { wrapper, queryClient } = setup();
    renderHook(() => useWatch(paths), { wrapper });
    await waitFor(() => expect(counts.get("/api/backups")).toBe(1));
    queryClient.setQueryData(keys.job("j1"), jobOut({ job_id: "j1", status: "queued" }));
    act(() => FakeEventSource.last?.emit("job", jobOut({ job_id: "j1", status: "running" })));
    await waitFor(() => {
      for (const p of paths) expect(counts.get(p)).toBe(2);
    });
  });

  it("refetches everything on resync and after reconnect", async () => {
    const paths = ["/api/home", "/api/syncs"];
    const counts = countRequests(paths);
    const { wrapper } = setup();
    renderHook(() => useWatch(paths), { wrapper });
    await waitFor(() => expect(counts.get("/api/syncs")).toBe(1));
    act(() => FakeEventSource.last?.emit("resync", {}));
    await waitFor(() => expect(counts.get("/api/syncs")).toBe(2));
    expect(counts.get("/api/home")).toBe(2);
    act(() => FakeEventSource.last?.open());
    act(() => FakeEventSource.last?.fail());
    act(() => FakeEventSource.last?.open());
    await waitFor(() => expect(counts.get("/api/syncs")).toBe(3));
    expect(counts.get("/api/home")).toBe(3);
  });

  it("reconnects with a new stream after the browser gives up", async () => {
    const paths = ["/api/home"];
    const counts = countRequests(paths);
    const { wrapper } = setup();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderHook(() => useWatch(paths), { wrapper });
    await waitFor(() => expect(counts.get("/api/home")).toBe(1));
    act(() => FakeEventSource.last?.fail({ closed: true }));
    expect(FakeEventSource.instances).toHaveLength(1);
    await act(() => vi.advanceTimersByTimeAsync(1100));
    expect(FakeEventSource.instances).toHaveLength(2);
    act(() => FakeEventSource.last?.open());
    await waitFor(() => expect(counts.get("/api/home")).toBe(2));
  });

  it("useJob polls while the stream is down", async () => {
    let hits = 0;
    server.use(
      http.get("/api/jobs/j1", () => {
        hits += 1;
        return HttpResponse.json(jobOut({ job_id: "j1" }));
      }),
    );
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { wrapper } = setup();
    renderHook(() => useJob("j1"), { wrapper });
    await waitFor(() => expect(hits).toBe(1));
    await act(() => vi.advanceTimersByTimeAsync(3100));
    await waitFor(() => expect(hits).toBe(2));
    act(() => FakeEventSource.last?.open());
    await act(() => vi.advanceTimersByTimeAsync(7000));
    expect(hits).toBe(2);
  });
});
