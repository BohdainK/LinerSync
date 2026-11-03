import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router";
import { describe, expect, it } from "vitest";
import type { MemberOut, OperationOut, RunOut } from "@/api/types";
import { attentionOut, changeOut, connectionOut, jobOut, runOut, trackOut } from "@/test/fixtures";
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
    matches: [],
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
    renderRun(
      runOut({
        undoable: true,
        undo_steps: [
          {
            member_id: "member-2",
            action: "restore",
            by_hand: false,
            playlist_name: "Mix",
            service: "tidal",
            tracks: [],
          },
          {
            member_id: "member-3",
            action: "delete_playlist",
            by_hand: true,
            playlist_name: "Fresh list",
            service: "apple_music",
            tracks: [],
          },
        ],
      }),
    );
    await userEvent.click(await screen.findByRole("button", { name: "Undo this run…" }));
    // Nothing happens until the dialog is confirmed; it says what undo will do.
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("LinerSync puts “Mix” on Tidal back the way it was before this run.");
    expect(dialog).toHaveTextContent("This run created “Fresh list” on apple_music. LinerSync can't delete playlists");
    expect(posted).toBe(0);
    await userEvent.click(screen.getByRole("button", { name: "Undo" }));
    expect(await screen.findByText("Undo Road trip")).toBeInTheDocument();
    expect(posted).toBe(1);
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  });

  it("cancelling the undo dialog undoes nothing", async () => {
    let posted = 0;
    server.use(
      http.post("*/api/runs/run-1/undo", () => {
        posted += 1;
        return HttpResponse.json(jobOut(), { status: 202 });
      }),
    );
    renderRun(runOut({ undoable: true }));
    await userEvent.click(await screen.findByRole("button", { name: "Undo this run…" }));
    await userEvent.click(await screen.findByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(posted).toBe(0);
  });

  it("says what to do by hand when LinerSync can't undo the run", async () => {
    const song = trackOut({ service: "tidal", track_id: "t1", title: "Gamma" });
    renderRun(
      runOut({
        undoable: false,
        undo_steps: [
          {
            member_id: "member-2",
            action: "delete_playlist",
            by_hand: true,
            playlist_name: "TwentyOnePilots Mix",
            service: "tidal",
            tracks: [],
          },
          {
            member_id: "member-3",
            action: "remove_songs",
            by_hand: true,
            playlist_name: "Old list",
            service: "tidal",
            tracks: [song],
          },
        ],
      }),
    );
    const box = await screen.findByRole("region", { name: "To undo this run" });
    await waitFor(() =>
      expect(box).toHaveTextContent(
        "This run created “TwentyOnePilots Mix” on Tidal. LinerSync can't delete playlists: delete it in Tidal yourself",
      ),
    );
    expect(box).toHaveTextContent(
      "Tidal doesn't let LinerSync remove songs. Remove this song from “Old list” in Tidal yourself:",
    );
    expect(box).toHaveTextContent("Gamma");
    expect(screen.queryByRole("button", { name: "Undo this run…" })).not.toBeInTheDocument();
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
    await userEvent.click(await screen.findByRole("button", { name: "Undo this run…" }));
    await userEvent.click(await screen.findByRole("button", { name: "Undo" }));
    expect(await screen.findByText("Undo Road trip")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Undo this run…" })).toBeDisabled();
    const before = runGets;
    jobStatus = "succeeded";
    expect(await screen.findByRole("link", { name: "View the undo run" }, { timeout: 5000 })).toHaveAttribute(
      "href",
      "/runs/run-9",
    );
    await waitFor(() => expect(runGets).toBeGreaterThan(before));
    expect(screen.getByRole("button", { name: "Undo this run…" })).toBeEnabled();
  }, 10000);

  it("the undo run's page starts fresh and links back to the run it undid", async () => {
    server.use(
      http.get("*/api/runs/run-1", () => HttpResponse.json(runOut({ undoable: true }))),
      http.get("*/api/runs/run-9", () =>
        HttpResponse.json(runOut({ run_id: "run-9", kind: "undo", undoes_run_id: "run-1", status: "partial" })),
      ),
      http.post("*/api/runs/run-1/undo", () => HttpResponse.json(jobOut({ label: "Undo Road trip" }), { status: 202 })),
      http.get("*/api/jobs/job-1", () =>
        HttpResponse.json(jobOut({ label: "Undo Road trip", status: "succeeded", run_ids: ["run-9"] })),
      ),
      http.get("*/api/connections", () => HttpResponse.json({ load_errors: [], services: [] })),
    );
    renderWithProviders(
      <Routes>
        <Route path="/runs/:runId" element={<RunPage />} />
      </Routes>,
      { route: "/runs/run-1" },
    );
    await userEvent.click(await screen.findByRole("button", { name: "Undo this run…" }));
    await userEvent.click(await screen.findByRole("button", { name: "Undo" }));
    await userEvent.click(await screen.findByRole("link", { name: "View the undo run" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Undo" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "The run this undid" })).toHaveAttribute("href", "/runs/run-1");
    expect(screen.queryByRole("link", { name: "View the undo run" })).not.toBeInTheDocument();
    expect(screen.queryByText("Undo Road trip")).not.toBeInTheDocument();
    expect(screen.getByText("No songs changed")).toBeInTheDocument();
  });

  it("does not offer undo otherwise", async () => {
    renderRun(runOut({ undoable: false, error: "It broke" }));
    expect(await screen.findByText("It broke")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Undo this run…" })).not.toBeInTheDocument();
  });

  it("shows mismatch and unresolved", async () => {
    renderRun(
      runOut({
        members: [
          member({
            not_made: [changeOut({ detail: "Could not remove X" })],
            mismatch: {
              missing: [trackOut({ title: "Lost song" })],
              unexpected: [trackOut({ track_id: "u", title: "Extra song" })],
            },
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
    expect(screen.queryByRole("link", { name: "Resolve in Needs attention" })).not.toBeInTheDocument();
  });

  it("resolves a song that didn't match in a dialog", async () => {
    const opened: string[] = [];
    server.use(
      http.post("*/api/runs/run-1/unresolved/0/attention", () => {
        opened.push("run-1");
        return HttpResponse.json(
          attentionOut({
            source: trackOut({ title: "Unknown tune" }),
            candidates: [
              { confidence: 0.72, reasons: ["same artist"], track: trackOut({ track_id: "c1", title: "Maybe tune" }) },
              { confidence: 0.2, reasons: [], track: trackOut({ track_id: "c2", title: "Far tune" }) },
            ],
          }),
        );
      }),
      http.post("*/api/attention/att-1/pick", () => new HttpResponse(null, { status: 204 })),
    );
    renderRun(
      runOut({
        run_id: "run-1",
        unmatched: 1,
        unresolved: [
          {
            member_id: "member-2",
            position: 0,
            source: trackOut({ title: "Unknown tune" }),
            reasons: ["No close match"],
            candidates: [],
          },
        ],
      }),
    );
    await userEvent.click(await screen.findByRole("button", { name: "Resolve…" }));
    expect(await screen.findByText("Maybe tune")).toBeInTheDocument();
    expect(screen.getByText("72% match")).toBeInTheDocument();
    expect(screen.queryByText("Far tune")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show 1 weaker match" }));
    expect(screen.getByText("Far tune")).toBeInTheDocument();
    await userEvent.click(screen.getAllByRole("button", { name: "Use this song" })[0]!);
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByText("Resolved")).toBeInTheDocument();
    expect(opened).toEqual(["run-1"]);
  });

  it("renders a playlist name with markup as text", async () => {
    const { container } = renderRun(
      runOut({
        members: [member({ operations: [op({ type: "create_playlist", name: "<img src=x onerror=alert(1)>" })] })],
      }),
    );
    expect(await screen.findByText("Created playlist <img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
  });
});
