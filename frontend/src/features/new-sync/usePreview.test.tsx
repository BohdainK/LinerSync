import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { errorMessage } from "@/api/client";
import type { JobOut, SyncCreateIn } from "@/api/types";
import { EventsProvider } from "@/live/events";
import { FakeEventSource } from "@/test/eventsource";
import { jobOut, runOut } from "@/test/fixtures";
import { server } from "@/test/server";
import { usePreview } from "./usePreview";

function input(dst: string): SyncCreateIn {
  return {
    name: "Road Trip",
    source: { service: "fake_a", playlist_id: "P1" },
    destinations: [{ service: dst, playlist_id: null, mode: "full", existing: "merge" }],
    schedule: null,
  };
}

type World = {
  log: string[];
  previews: unknown[];
  jobs: Map<string, JobOut>;
  /** Called before a preview POST answers; lets a test hold the response. */
  gate: (n: number) => Promise<void>;
};

function serve(): World {
  const world: World = { log: [], previews: [], jobs: new Map(), gate: async () => {} };
  server.use(
    http.post("*/api/syncs/preview", async ({ request }) => {
      world.previews.push(await request.json());
      const n = world.previews.length;
      world.log.push(`preview ${n}`);
      await world.gate(n);
      const job = jobOut({ job_id: `j${n}`, kind: "preview", status: "queued", label: "Preview “Road Trip”", sync_id: null });
      world.jobs.set(job.job_id, job);
      return HttpResponse.json(job, { status: 202 });
    }),
    http.get("*/api/jobs/:id", ({ params }) => {
      const job = world.jobs.get(String(params.id));
      return job ? HttpResponse.json(job) : HttpResponse.json({ detail: "Unknown job" }, { status: 404 });
    }),
    http.post("*/api/jobs/:id/cancel", ({ params }) => {
      const id = String(params.id);
      world.log.push(`cancel ${id}`);
      const job = { ...(world.jobs.get(id) ?? jobOut({ job_id: id })), cancel_requested: true };
      world.jobs.set(id, job);
      return HttpResponse.json(job);
    }),
    http.get("*/api/runs/:id", ({ params }) => {
      world.log.push(`run ${String(params.id)}`);
      return HttpResponse.json(runOut({ run_id: String(params.id), sync_id: "preview-x", dry_run: true }));
    }),
  );
  return world;
}

function emit(world: World, job: JobOut) {
  world.jobs.set(job.job_id, job);
  act(() => FakeEventSource.last?.emit("job", job));
}

function setup(initial: SyncCreateIn | null) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      <EventsProvider>{children}</EventsProvider>
    </QueryClientProvider>
  );
  const hook = renderHook(({ value }: { value: SyncCreateIn | null }) => usePreview(value), {
    wrapper,
    initialProps: { value: initial },
  });
  act(() => FakeEventSource.last?.open());
  return hook;
}

const advance = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
});
afterEach(() => {
  vi.useRealTimers();
});

describe("usePreview", () => {
  it("debounces and submits one preview", async () => {
    const world = serve();
    const { result, rerender } = setup(input("fake_b"));
    expect(result.current.status).toBe("waiting");
    await advance(300);
    rerender({ value: input("fake_c") });
    await advance(300);
    rerender({ value: input("fake_d") });
    await advance(700);
    expect(world.previews).toHaveLength(0);
    await advance(100);
    await waitFor(() => expect(world.previews).toHaveLength(1));
    expect(world.previews[0]).toEqual(input("fake_d"));
    await waitFor(() => expect(result.current.job?.job_id).toBe("j1"));
  });

  it("A to B to A does not show A's cancelled job", async () => {
    const world = serve();
    const { result, rerender } = setup(input("fake_b"));
    await advance(800);
    await waitFor(() => expect(result.current.job?.job_id).toBe("j1"));
    rerender({ value: input("fake_c") });
    rerender({ value: input("fake_b") });
    expect(result.current.job).toBeNull();
    expect(result.current.status).toBe("waiting");
    await advance(800);
    await waitFor(() => expect(result.current.job?.job_id).toBe("j2"));
    expect(world.log).toContain("cancel j1");
  });

  it("fetches the run when the job succeeds", async () => {
    const world = serve();
    const { result } = setup(input("fake_b"));
    await advance(800);
    await waitFor(() => expect(result.current.job?.job_id).toBe("j1"));
    expect(result.current.status).toBe("waiting");
    emit(world, jobOut({ job_id: "j1", kind: "preview", status: "running", sync_id: null, progress: { phase: "match", done: 1, total: 4, message: "" } }));
    await waitFor(() => expect(result.current.status).toBe("running"));
    expect(result.current.job?.progress?.done).toBe(1);
    emit(world, jobOut({ job_id: "j1", kind: "preview", status: "succeeded", sync_id: null, run_ids: ["r1"] }));
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.run?.run_id).toBe("r1");
    expect(result.current.error).toBeNull();
  });

  it("discards a superseded preview and cancels its job", async () => {
    const world = serve();
    const { result, rerender } = setup(input("fake_b"));
    await advance(800);
    await waitFor(() => expect(result.current.job?.job_id).toBe("j1"));

    rerender({ value: input("fake_c") });
    expect(result.current.status).toBe("waiting");
    expect(result.current.job).toBeNull();
    await waitFor(() => expect(world.log).toContain("cancel j1"));
    await advance(800);
    await waitFor(() => expect(result.current.job?.job_id).toBe("j2"));
    expect(world.log).toEqual(["preview 1", "cancel j1", "preview 2"]);
    expect(world.previews[1]).toEqual(input("fake_c"));

    // The old job finishing anyway must not show its run for the new selection.
    emit(world, jobOut({ job_id: "j1", kind: "preview", status: "succeeded", sync_id: null, run_ids: ["r1"] }));
    await advance(50);
    expect(result.current.run).toBeNull();
    expect(result.current.status).toBe("waiting");
    expect(world.log).not.toContain("run r1");

    emit(world, jobOut({ job_id: "j2", kind: "preview", status: "succeeded", sync_id: null, run_ids: ["r2"] }));
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.run?.run_id).toBe("r2");
    expect(world.log).not.toContain("run r1");
  });

  it("cancels a preview whose submit answers after the selection changed", async () => {
    const world = serve();
    let release: () => void = () => {};
    world.gate = (n) => (n === 1 ? new Promise<void>((r) => (release = r)) : Promise.resolve());
    const { result, rerender } = setup(input("fake_b"));
    await advance(800);
    await waitFor(() => expect(world.previews).toHaveLength(1));

    rerender({ value: input("fake_c") });
    release();
    await waitFor(() => expect(world.log).toContain("cancel j1"));
    expect(result.current.job).toBeNull();
    await advance(800);
    await waitFor(() => expect(result.current.job?.job_id).toBe("j2"));
    expect(world.log).toEqual(["preview 1", "cancel j1", "preview 2"]);
  });

  it("does not cancel a superseded job that already ended", async () => {
    const world = serve();
    const { result, rerender } = setup(input("fake_b"));
    await advance(800);
    await waitFor(() => expect(result.current.job?.job_id).toBe("j1"));
    emit(world, jobOut({ job_id: "j1", kind: "preview", status: "succeeded", sync_id: null, run_ids: ["r1"] }));
    await waitFor(() => expect(result.current.status).toBe("ready"));
    rerender({ value: input("fake_c") });
    await advance(800);
    await waitFor(() => expect(result.current.job?.job_id).toBe("j2"));
    expect(world.log).toEqual(["preview 1", "run r1", "preview 2"]);
  });

  it("ignores a failed cancel", async () => {
    const world = serve();
    server.use(http.post("*/api/jobs/:id/cancel", () => HttpResponse.json({ detail: "Job already finished" }, { status: 409 })));
    const { result, rerender } = setup(input("fake_b"));
    await advance(800);
    await waitFor(() => expect(result.current.job?.job_id).toBe("j1"));
    rerender({ value: input("fake_c") });
    await advance(800);
    await waitFor(() => expect(result.current.job?.job_id).toBe("j2"));
    expect(result.current.error).toBeNull();
    expect(world.previews).toHaveLength(2);
  });

  it("reports a failed job", async () => {
    const world = serve();
    const { result } = setup(input("fake_b"));
    await advance(800);
    await waitFor(() => expect(result.current.job?.job_id).toBe("j1"));
    emit(world, jobOut({ job_id: "j1", kind: "preview", status: "failed", sync_id: null, error: "Fake B could not be reached" }));
    await waitFor(() => expect(result.current.status).toBe("failed"));
    expect(errorMessage(result.current.error)).toBe("Fake B could not be reached");
    expect(result.current.run).toBeNull();
  });

  it("reports a rejected submit", async () => {
    serve();
    server.use(http.post("*/api/syncs/preview", () => HttpResponse.json({ detail: "Connect Fake B first" }, { status: 422 })));
    const { result } = setup(input("fake_b"));
    await advance(800);
    await waitFor(() => expect(result.current.status).toBe("failed"));
    expect(errorMessage(result.current.error)).toBe("Connect Fake B first");
  });

  it("null input is idle and submits nothing", async () => {
    const world = serve();
    const { result } = setup(null);
    expect(result.current).toEqual({ status: "idle", run: null, runId: null, job: null, error: null });
    await advance(2000);
    expect(world.previews).toHaveLength(0);
    expect(result.current.status).toBe("idle");
  });
});
