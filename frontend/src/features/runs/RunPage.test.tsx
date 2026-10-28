import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router";
import { describe, expect, it } from "vitest";
import type { MemberOut, OperationOut, RunOut } from "@/api/types";
import { changeOut, connectionOut, jobOut, runOut, trackOut } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";
import { RunPage } from "./RunPage";

function member(o: Partial<MemberOut> = {}): MemberOut {
  return {
    member_id: "member-2",
    service: "tidal",
    playlist_id: "pl-tidal",
    status: "applied",
    error: null,
    rebuilt: false,
    created_playlist_id: null,
    operations: [],
    not_made: [],
    mismatch: null,
    ...o,
  };
}

function op(o: Partial<OperationOut> & Pick<OperationOut, "type">): OperationOut {
  return { op_id: `op-${o.type}`, positions: [], tracks: [], ...o };
}

function renderRun(run: RunOut) {
  server.use(
    http.get("*/api/runs/run-1", () => HttpResponse.json(run)),
    http.get("*/api/connections", () =>
      HttpResponse.json({
        load_errors: [],
        services: [connectionOut({ service: "tidal", display_name: "Tidal" })],
      }),
    ),
  );
  return renderWithProviders(
    <Routes>
      <Route path="/runs/:runId" element={<RunPage />} />
    </Routes>,
    { route: "/runs/run-1" },
  );
}

describe("RunPage", () => {
  it("lists operations per member with counts", async () => {
    const tracks = [trackOut({ track_id: "a", title: "Alpha" }), trackOut({ track_id: "b", title: "Beta" })];
    renderRun(
      runOut({
        added: 2,
        removed: 1,
        members: [
          member({
            rebuilt: true,
            operations: [
              op({ type: "create_playlist", name: "Fresh list" }),
              op({ type: "update_metadata", name: "n" }),
              op({ type: "add_tracks", tracks }),
              op({ type: "reorder", order_length: 3 }),
              op({ type: "remove_tracks", tracks: [trackOut({ track_id: "c", title: "Gamma" })], positions: [0] }),
            ],
          }),
        ],
      }),
    );
    expect(await screen.findByText("Tidal")).toBeInTheDocument();
    expect(screen.getByText("Changed")).toBeInTheDocument();
    expect(screen.getByText("Rebuilt")).toBeInTheDocument();
    expect(screen.getByText("+2 −1")).toBeInTheDocument();
    expect(screen.getByText("Created playlist Fresh list")).toBeInTheDocument();
    expect(screen.getByText("Updated name and description")).toBeInTheDocument();
    expect(screen.getByText("Reordered the playlist")).toBeInTheDocument();
    expect(screen.queryByText("Alpha")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Added 2 songs" }));
    expect(screen.getByText("Alpha")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Removed 1 song" }));
    expect(screen.getByText("Gamma")).toBeInTheDocument();
  });

  it("undo when undoable", async () => {
    let posted = 0;
    server.use(
      http.post("*/api/runs/run-1/undo", () => {
        posted += 1;
        return HttpResponse.json(jobOut({ label: "Undo Road trip" }), { status: 202 });
      }),
      http.get("*/api/jobs/job-1", () => HttpResponse.json(jobOut({ label: "Undo Road trip" }))),
    );
    renderRun(runOut({ undoable: true }));
    await userEvent.click(await screen.findByRole("button", { name: "Undo this run" }));
    expect(await screen.findByText("Undo Road trip")).toBeInTheDocument();
    expect(posted).toBe(1);
  });

  it("disables undo while the undo job runs, then refetches the run and links to the undo run", async () => {
    let runGets = 0;
    let jobStatus: "running" | "succeeded" = "running";
    server.use(
      http.get("*/api/runs/run-1", () => {
        runGets += 1;
        return HttpResponse.json(runOut({ undoable: true }));
      }),
      http.post("*/api/runs/run-1/undo", () => HttpResponse.json(jobOut({ label: "Undo Road trip" }), { status: 202 })),
      http.get("*/api/jobs/job-1", () =>
        HttpResponse.json(
          jobOut({ label: "Undo Road trip", status: jobStatus, run_ids: jobStatus === "succeeded" ? ["run-9"] : [] }),
        ),
      ),
      http.get("*/api/connections", () => HttpResponse.json({ load_errors: [], services: [] })),
    );
    renderWithProviders(
      <Routes>
        <Route path="/runs/:runId" element={<RunPage />} />
      </Routes>,
      { route: "/runs/run-1" },
    );
    await userEvent.click(await screen.findByRole("button", { name: "Undo this run" }));
    expect(await screen.findByText("Undo Road trip")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Undo this run" })).toBeDisabled();
    const before = runGets;
    jobStatus = "succeeded";
    expect(await screen.findByRole("link", { name: "View the undo run" }, { timeout: 5000 })).toHaveAttribute(
      "href",
      "/runs/run-9",
    );
    await waitFor(() => expect(runGets).toBeGreaterThan(before));
    expect(screen.getByRole("button", { name: "Undo this run" })).toBeEnabled();
  }, 10000);

  it("does not offer undo otherwise", async () => {
    renderRun(runOut({ undoable: false, error: "It broke" }));
    expect(await screen.findByText("It broke")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Undo this run" })).not.toBeInTheDocument();
  });

  it("shows mismatch and unresolved", async () => {
    renderRun(
      runOut({
        members: [
          member({
            not_made: [changeOut({ detail: "Could not remove X" })],
            mismatch: { missing: [trackOut({ title: "Lost song" })], unexpected: [trackOut({ track_id: "u", title: "Extra song" })] },
          }),
        ],
        unresolved: [
          {
            member_id: "member-2",
            position: 0,
            source: trackOut({ title: "Unknown tune" }),
            reasons: ["No close match"],
            candidates: [{ confidence: 0.5, reasons: [], track: trackOut({ track_id: "c1", title: "Maybe tune" }) }],
          },
        ],
      }),
    );
    expect(await screen.findByText("After the sync the playlist differs from the plan")).toBeInTheDocument();
    expect(screen.getByText("Lost song")).toBeInTheDocument();
    expect(screen.getByText("Extra song")).toBeInTheDocument();
    expect(screen.getByText("Changes not made (1)")).toBeInTheDocument();
    expect(screen.getByText("Could not remove X")).toBeInTheDocument();
    expect(screen.getByText("Didn't match (1)")).toBeInTheDocument();
    expect(screen.getByText("Unknown tune")).toBeInTheDocument();
    expect(screen.getByText("No close match")).toBeInTheDocument();
    expect(screen.getByText("Maybe tune")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Resolve in Needs attention" })).toHaveAttribute("href", "/");
  });

  it("renders a playlist name with markup as text", async () => {
    const { container } = renderRun(
      runOut({ members: [member({ operations: [op({ type: "create_playlist", name: "<img src=x onerror=alert(1)>" })] })] }),
    );
    expect(await screen.findByText("Created playlist <img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
  });
});
