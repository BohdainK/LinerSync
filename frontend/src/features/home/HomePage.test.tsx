import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { Route, Routes, useLocation } from "react-router";
import { EventsProvider } from "@/live/events";
import { FakeEventSource } from "@/test/eventsource";
import { attentionOut, homeOut, jobOut } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";
import { HomePage } from "./HomePage";

function Probe() {
  return <output aria-label="location">{useLocation().pathname}</output>;
}

function serve(home = homeOut(), attention = [attentionOut()]) {
  server.use(
    http.get("/api/home", () => HttpResponse.json(home)),
    http.get("/api/attention", () => HttpResponse.json(attention)),
  );
}

function renderHome() {
  return renderWithProviders(
    <EventsProvider>
      <Routes>
        <Route path="/" element={<HomePage />} />
        <Route path="*" element={<Probe />} />
      </Routes>
    </EventsProvider>,
  );
}

const FUTURE = new Date(Date.now() + 3 * 3_600_000).toISOString();

describe("HomePage", () => {
  it("shows each panel from /api/home", async () => {
    serve(
      homeOut({
        running: [jobOut({ label: "Sync Running one" })],
        queued: [jobOut({ job_id: "job-2", status: "queued", label: "Sync Queued one" })],
        next_up: [{ kind: "sync", sync_id: "sync-1", name: "Road trip", at: FUTURE }],
        recent: [jobOut({ job_id: "job-3", status: "succeeded", label: "Sync Done one", finished_at: "2026-01-01T12:00:00Z" })],
      }),
    );
    renderHome();
    expect(await screen.findByRole("heading", { name: "Running now" })).toBeInTheDocument();
    expect(screen.getByText("Sync Running one")).toBeInTheDocument();
    expect(screen.getByText("Sync Queued one")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /Song didn't match/ })).toHaveTextContent("Road trip");
    expect(screen.getByText("in 3 hours")).toBeInTheDocument();
    expect(screen.getByText("Sync Done one")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Recent activity" })).toBeInTheDocument();
  });

  it("shows empty states", async () => {
    serve(homeOut(), []);
    renderHome();
    expect(await screen.findByText("Nothing running")).toBeInTheDocument();
    expect(await screen.findByText("All clear")).toBeInTheDocument();
  });

  it("cancel from Running now posts cancel", async () => {
    serve(homeOut({ running: [jobOut()], queued: [jobOut({ job_id: "job-2", status: "queued", label: "Queued" })] }));
    const cancelled: string[] = [];
    server.use(
      http.post("/api/jobs/:id/cancel", ({ params }) => {
        cancelled.push(String(params["id"]));
        return HttpResponse.json(jobOut({ job_id: String(params["id"]), cancel_requested: true }));
      }),
    );
    renderHome();
    await userEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(cancelled).toEqual(["job-1"]));
    await userEvent.click(screen.getByRole("button", { name: "Cancel Queued" }));
    await waitFor(() => expect(cancelled).toEqual(["job-1", "job-2"]));
  });

  it("warnings link to the connection", async () => {
    serve(homeOut({ warnings: [{ kind: "needs_sign_in", service: "tidal", message: "Tidal needs you to sign in" }] }), []);
    renderHome();
    expect(await screen.findByText("Tidal needs you to sign in")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("link", { name: "Open tidal" }));
    expect(screen.getByLabelText("location")).toHaveTextContent("/connections/tidal");
  });

  it("opens the attention modal from an item", async () => {
    serve(homeOut(), [attentionOut({ kind: "conflict", detail: "Edited on both sides" })]);
    renderHome();
    await userEvent.click(await screen.findByRole("button", { name: /Conflict/ }));
    expect(within(screen.getByRole("dialog")).getByRole("heading", { name: "Conflict" })).toBeInTheDocument();
  });

  it("next up links syncs and the backup", async () => {
    serve(
      homeOut({
        next_up: [
          { kind: "sync", sync_id: "sync-9", name: "Gym", at: FUTURE },
          { kind: "backup", sync_id: null, name: "Backup", at: FUTURE },
        ],
      }),
      [],
    );
    renderHome();
    expect(await screen.findByRole("link", { name: "Gym" })).toHaveAttribute("href", "/syncs/sync-9");
    expect(screen.getByRole("link", { name: "Backup" })).toHaveAttribute("href", "/backup");
  });

  it("recent activity links a single run", async () => {
    serve(
      homeOut({
        recent: [
          jobOut({ job_id: "a", status: "succeeded", label: "One run", run_ids: ["run-7"] }),
          jobOut({ job_id: "b", status: "succeeded", label: "Two runs", run_ids: ["r1", "r2"] }),
          jobOut({ job_id: "c", status: "succeeded", kind: "backup", label: "Backed up", run_ids: [] }),
        ],
      }),
      [],
    );
    renderHome();
    expect(await screen.findByRole("link", { name: "One run" })).toHaveAttribute("href", "/runs/run-7");
    expect(screen.queryByRole("link", { name: "Two runs" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Backed up" })).toHaveAttribute("href", "/backup");
  });

  it("api limits hidden when empty and show no-limit text", async () => {
    serve(homeOut(), []);
    const first = renderHome();
    await screen.findByText("Nothing running");
    expect(screen.queryByRole("heading", { name: "API limits today" })).not.toBeInTheDocument();
    first.unmount();

    serve(
      homeOut({
        api_limits: [
          { service: "spotify", display_name: "Spotify", used: 12, limit: 100, resets_at: FUTURE },
          { service: "tidal", display_name: "Tidal", used: 5, limit: null, resets_at: FUTURE },
        ],
      }),
      [],
    );
    renderHome();
    expect(await screen.findByText(/Spotify: 12 of 100/)).toHaveTextContent("resets in 3 hours");
    expect(screen.getByText(/Tidal: 5 \(no daily limit\)/)).toBeInTheDocument();
  });

  it("progress updates from the stream without refetch", async () => {
    let fetches = 0;
    server.use(
      http.get("/api/home", () => {
        fetches += 1;
        return HttpResponse.json(
          homeOut({ running: [jobOut({ progress: { done: 1, total: 10, phase: "p", message: "Matching" } })] }),
        );
      }),
      http.get("/api/attention", () => HttpResponse.json([])),
    );
    renderHome();
    expect(await screen.findByText("Matching")).toBeInTheDocument();
    const es = FakeEventSource.last;
    expect(es).not.toBeNull();
    act(() => es?.open());
    act(() =>
      es?.emit("job", jobOut({ progress: { done: 7, total: 10, phase: "p", message: "Writing" } })),
    );
    expect(await screen.findByText("Writing")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("value", "7");
    expect(fetches).toBe(1);
  });
});
