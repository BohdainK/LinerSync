import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { Route, Routes, useLocation } from "react-router";
import { describe, expect, it } from "vitest";
import {
  changeOut,
  connectionOut,
  destinationOut,
  jobOut,
  playlistOut,
  runOut,
  runSummaryOut,
  settingsOut,
  syncOut,
} from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";
import { SyncPage } from "./SyncPage";
import { SyncsPage } from "./SyncsPage";
import type { SyncOut } from "@/api/types";

function Probe() {
  return <output aria-label="location">{useLocation().pathname}</output>;
}

const state: { sync: SyncOut } = { sync: syncOut() };

function serve(sync: SyncOut = syncOut(), extra: Parameters<typeof server.use> = []) {
  state.sync = sync;
  server.use(
    ...extra,
    http.get("*/api/syncs/sync-1", () => HttpResponse.json(state.sync)),
    http.get("*/api/syncs/sync-1/runs", () => HttpResponse.json([])),
    http.get("*/api/connections", () =>
      HttpResponse.json({
        load_errors: [],
        services: [
          connectionOut({ service: "spotify", display_name: "Spotify" }),
          connectionOut({ service: "tidal", display_name: "Tidal" }),
          connectionOut({ service: "deezer", display_name: "Deezer" }),
        ],
      }),
    ),
    http.get("*/api/connections/deezer/playlists", () => HttpResponse.json([playlistOut({ service: "deezer" })])),
  );
}

function renderSync() {
  return renderWithProviders(
    <Routes>
      <Route path="/syncs/:syncId" element={<SyncPage />} />
      <Route path="*" element={<Probe />} />
    </Routes>,
    { route: "/syncs/sync-1" },
  );
}

function captureBodies(method: "post" | "patch", path: string, response: unknown, status = 200) {
  const bodies: unknown[] = [];
  const handler = (method === "post" ? http.post : http.patch)(path, async ({ request }) => {
    bodies.push(await request.json());
    if (method === "patch") state.sync = response as SyncOut;
    return HttpResponse.json(response as Record<string, unknown>, { status });
  });
  return { bodies, handler };
}

describe("SyncPage", () => {
  it("Sync now posts run and shows the job", async () => {
    const { bodies, handler } = captureBodies("post", "*/api/syncs/sync-1/run", jobOut(), 202);
    serve(syncOut(), [handler, http.get("*/api/jobs/job-1", () => HttpResponse.json(jobOut()))]);
    renderSync();
    await userEvent.click(await screen.findByRole("button", { name: "Sync now" }));
    expect(await screen.findByText("Sync Road trip")).toBeInTheDocument();
    expect(bodies).toEqual([{ dry_run: false }]);
  });

  it("shows Rebuild when a destination is missing", async () => {
    serve(syncOut({ destinations: [destinationOut({ missing: true })] }));
    renderSync();
    expect(await screen.findByRole("button", { name: "Rebuild" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Sync now" })).not.toBeInTheDocument();
    expect(screen.getByText("Missing")).toBeInTheDocument();
  });

  it("dry run posts dry_run true", async () => {
    const { bodies, handler } = captureBodies("post", "*/api/syncs/sync-1/run", jobOut(), 202);
    serve(syncOut(), [handler, http.get("*/api/jobs/job-1", () => HttpResponse.json(jobOut()))]);
    renderSync();
    await userEvent.click(await screen.findByRole("button", { name: "Dry run" }));
    await waitFor(() => expect(bodies).toEqual([{ dry_run: true }]));
  });

  it("actions disabled while a job is active", async () => {
    serve(syncOut({ active_job: jobOut({ label: "Sync Road trip running" }) }));
    renderSync();
    expect(await screen.findByText("Sync Road trip running")).toBeInTheDocument();
    for (const name of ["Sync now", "Dry run", "Pause", "Delete sync…"]) {
      expect(screen.getByRole("button", { name })).toBeDisabled();
    }
  });

  it("409 shows the busy message", async () => {
    serve(syncOut(), [
      http.post("*/api/syncs/sync-1/run", () =>
        HttpResponse.json({ detail: "Wait for the current run to finish" }, { status: 409 }),
      ),
    ]);
    renderSync();
    await userEvent.click(await screen.findByRole("button", { name: "Sync now" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Wait for the current run to finish");
  });

  it("Sync now is disabled while its request is pending", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => (release = r));
    let calls = 0;
    serve(syncOut(), [
      http.post("*/api/syncs/sync-1/run", async () => {
        calls += 1;
        await gate;
        return HttpResponse.json(jobOut(), { status: 202 });
      }),
      http.get("*/api/jobs/job-1", () => HttpResponse.json(jobOut())),
    ]);
    renderSync();
    const button = await screen.findByRole("button", { name: "Sync now" });
    await userEvent.click(button);
    await waitFor(() => expect(button).toBeDisabled());
    await userEvent.click(button);
    release();
    await screen.findByText("Sync Road trip");
    expect(calls).toBe(1);
  });

  it("renames inline", async () => {
    const { bodies, handler } = captureBodies("patch", "*/api/syncs/sync-1", syncOut({ name: "Trip" }));
    serve(syncOut(), [handler]);
    renderSync();
    await userEvent.click(await screen.findByRole("button", { name: "Rename" }));
    const input = screen.getByRole("textbox", { name: "Name" });
    await userEvent.clear(input);
    await userEvent.type(input, "Trip");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(bodies).toEqual([{ name: "Trip" }]));
    expect(await screen.findByRole("heading", { name: "Trip" })).toBeInTheDocument();
  });

  it("pauses", async () => {
    const { bodies, handler } = captureBodies("patch", "*/api/syncs/sync-1", syncOut({ status: "paused" }));
    serve(syncOut(), [handler]);
    renderSync();
    await userEvent.click(await screen.findByRole("button", { name: "Pause" }));
    await waitFor(() => expect(bodies).toEqual([{ paused: true }]));
    expect(await screen.findByRole("button", { name: "Resume" })).toBeInTheDocument();
  });

  it("mode change patches modes by member id", async () => {
    const { bodies, handler } = captureBodies("patch", "*/api/syncs/sync-1", syncOut());
    serve(syncOut(), [handler]);
    renderSync();
    await userEvent.selectOptions(await screen.findByRole("combobox", { name: "Mode for Tidal" }), "Add only");
    await waitFor(() => expect(bodies).toEqual([{ modes: { "member-2": "add_only" } }]));
  });

  it("shows the effective mode and limitations", async () => {
    serve(
      syncOut({
        destinations: [destinationOut({ mode: "full", effective_mode: "add_only", limitations: ["Tidal can't remove songs"] })],
      }),
    );
    renderSync();
    expect(await screen.findByText("Runs as add only")).toBeInTheDocument();
    expect(screen.getByText("Tidal can't remove songs")).toBeInTheDocument();
  });

  it("schedule custom and reset", async () => {
    const { bodies, handler } = captureBodies("patch", "*/api/syncs/sync-1", syncOut());
    serve(syncOut(), [handler]);
    renderSync();
    const schedule = await screen.findByRole("combobox", { name: "Schedule" });
    await userEvent.selectOptions(schedule, "Every week");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Day" }), "Friday");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Time" }), "18:30");
    await userEvent.click(screen.getByRole("button", { name: "Save schedule" }));
    await waitFor(() => expect(bodies).toEqual([{ schedule: "30 18 * * 5" }]));
    await userEvent.selectOptions(schedule, "Default (Every hour)");
    await userEvent.click(screen.getByRole("button", { name: "Save schedule" }));
    await waitFor(() => expect(bodies).toEqual([{ schedule: "30 18 * * 5" }, { schedule: null }]));
  });

  it("keeps an unusual cron editable as text", async () => {
    serve(syncOut({ schedule: "0 3 1 1 *", effective_schedule: "0 3 1 1 *" }));
    renderSync();
    expect(await screen.findByRole("combobox", { name: "Schedule" })).toHaveDisplayValue("Custom (cron)");
    expect(screen.getByRole("textbox", { name: "Cron schedule" })).toHaveValue("0 3 1 1 *");
  });

  it("schedules twice a month", async () => {
    const { bodies, handler } = captureBodies("patch", "*/api/syncs/sync-1", syncOut());
    serve(syncOut(), [handler]);
    renderSync();
    await userEvent.selectOptions(await screen.findByRole("combobox", { name: "Schedule" }), "Twice a month (1st and 15th)");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Time" }), "07:00");
    await userEvent.click(screen.getByRole("button", { name: "Save schedule" }));
    await waitFor(() => expect(bodies).toEqual([{ schedule: "0 7 1,15 * *" }]));
  });

  it("schedules a monthly run on a chosen day", async () => {
    const { bodies, handler } = captureBodies("patch", "*/api/syncs/sync-1", syncOut());
    serve(syncOut(), [handler]);
    renderSync();
    await userEvent.selectOptions(await screen.findByRole("combobox", { name: "Schedule" }), "Every month");
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Day of the month" }), "15th");
    await userEvent.click(screen.getByRole("button", { name: "Save schedule" }));
    await waitFor(() => expect(bodies).toEqual([{ schedule: "0 3 15 * *" }]));
  });

  it("shows a 422 under the cron input", async () => {
    serve(syncOut(), [
      http.patch("*/api/syncs/sync-1", () => HttpResponse.json({ detail: "Not a valid cron expression" }, { status: 422 })),
    ]);
    renderSync();
    await userEvent.selectOptions(await screen.findByRole("combobox", { name: "Schedule" }), "Custom (cron)");
    await userEvent.clear(screen.getByRole("textbox", { name: "Cron schedule" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Cron schedule" }), "nope");
    await userEvent.click(screen.getByRole("button", { name: "Save schedule" }));
    expect(await screen.findByText("Not a valid cron expression")).toBeInTheDocument();
  });

  it("add destination with a new playlist sends playlist_id null", async () => {
    const { bodies, handler } = captureBodies("patch", "*/api/syncs/sync-1", syncOut());
    serve(syncOut(), [handler]);
    renderSync();
    await screen.findByRole("option", { name: "Deezer" });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Service" }), "Deezer");
    await userEvent.click(await screen.findByRole("radio", { name: /New playlist/ }));
    await userEvent.click(screen.getByRole("button", { name: "Add destination" }));
    await waitFor(() =>
      expect(bodies).toEqual([{ add_destinations: [{ service: "deezer", playlist_id: null, mode: "full" }] }]),
    );
  });

  it("remove destination confirms first", async () => {
    const { bodies, handler } = captureBodies("patch", "*/api/syncs/sync-1", syncOut());
    serve(syncOut(), [handler]);
    renderSync();
    await userEvent.click(await screen.findByRole("button", { name: "Remove Tidal" }));
    expect(bodies).toEqual([]);
    const dialog = await screen.findByRole("alertdialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(bodies).toEqual([{ remove_member_ids: ["member-2"] }]));
  });

  it("delete confirms, then goes to the list", async () => {
    let deleted = 0;
    serve(syncOut(), [
      http.delete("*/api/syncs/sync-1", () => {
        deleted += 1;
        return new HttpResponse(null, { status: 204 });
      }),
    ]);
    renderSync();
    await userEvent.click(await screen.findByRole("button", { name: "Delete sync…" }));
    expect(deleted).toBe(0);
    await userEvent.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Delete sync" }));
    await waitFor(() => expect(screen.getByLabelText("location")).toHaveTextContent("/syncs"));
    expect(deleted).toBe(1);
  });

  it("changes not made opens the member's list", async () => {
    serve(
      syncOut({
        destinations: [destinationOut({ mode: "add_only", effective_mode: "add_only" })],
        last_run: runSummaryOut({ run_id: "run-9", not_made: 1 }),
      }),
      [
        http.get("*/api/runs/run-9", () =>
          HttpResponse.json(
            runOut({
              run_id: "run-9",
              members: [
                {
                  member_id: "member-2",
                  service: "tidal",
                  playlist_id: "pl-tidal",
                  status: "applied",
                  error: null,
                  rebuilt: false,
                  created_playlist_id: null,
                  operations: [],
                  not_made: [changeOut({ detail: "Removing Old song was skipped" })],
                  mismatch: null,
                  matches: [],
                },
              ],
            }),
          ),
        ),
      ],
    );
    renderSync();
    await userEvent.click(await screen.findByRole("button", { name: "Changes not made (1)" }));
    expect(await screen.findByText("Removing Old song was skipped")).toBeInTheDocument();
  });

  it("lists run history with links", async () => {
    serve(syncOut(), [
      http.get("*/api/syncs/sync-1/runs", () =>
        HttpResponse.json([runSummaryOut({ run_id: "run-5", dry_run: true, added: 2, removed: 1 })]),
      ),
    ]);
    renderSync();
    const row = (await screen.findByText("+2 −1")).closest("tr") as HTMLElement;
    expect(within(row).getByText("Dry run")).toBeInTheDocument();
    expect(within(row).getByRole("link")).toHaveAttribute("href", "/runs/run-5");
  });
});

describe("SyncsPage", () => {
  it("lists syncs with status and last run", async () => {
    server.use(
      http.get("*/api/connections", () =>
        HttpResponse.json({
          load_errors: [],
          services: [connectionOut({ service: "spotify", display_name: "Spotify" }), connectionOut({ service: "tidal", display_name: "Tidal" })],
        }),
      ),
      http.get("*/api/syncs", () =>
        HttpResponse.json([
          syncOut({ last_run: runSummaryOut({ added: 3, removed: 1 }) }),
          syncOut({ sync_id: "sync-2", name: "Gym", status: "paused", pause_reason: "Paused by you", active_job: jobOut({ label: "Sync Gym" }) }),
        ]),
      ),
    );
    renderWithProviders(<SyncsPage />);
    expect(await screen.findByRole("link", { name: "Road trip" })).toHaveAttribute("href", "/syncs/sync-1");
    expect(screen.getAllByText("Spotify → Tidal", { exact: false })).toHaveLength(2);
    expect(screen.getByText("Active")).toBeInTheDocument();
    expect(screen.getByText("Paused: Paused by you")).toBeInTheDocument();
    expect(screen.getByText("+3 −1")).toBeInTheDocument();
    expect(screen.getByText("Sync Gym")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "+ New sync" })).toHaveAttribute("href", "/syncs/new");
  });

  function serveList(syncs: SyncOut[]) {
    const patches: { id: string; body: unknown }[] = [];
    server.use(
      http.get("*/api/connections", () => HttpResponse.json({ load_errors: [], services: [] })),
      http.get("*/api/settings", () => HttpResponse.json(settingsOut())),
      http.get("*/api/syncs", () => HttpResponse.json(syncs)),
      http.patch("*/api/syncs/:id", async ({ params, request }) => {
        const body = (await request.json()) as Record<string, unknown>;
        const id = String(params.id);
        patches.push({ id, body });
        if (body.schedule === "nope") return HttpResponse.json({ detail: "Not a valid cron schedule" }, { status: 422 });
        const sync = syncs.find((x) => x.sync_id === id) ?? syncOut({ sync_id: id });
        return HttpResponse.json({ ...sync, ...body });
      }),
    );
    return patches;
  }

  const TWO = [
    syncOut(),
    syncOut({ sync_id: "sync-2", name: "Gym", schedule: "0 */6 * * *", effective_schedule: "0 */6 * * *" }),
  ];

  it("shows each sync's schedule, marking the default", async () => {
    serveList(TWO);
    renderWithProviders(<SyncsPage />);
    const road = (await screen.findByRole("link", { name: "Road trip" })).closest("tr");
    const gym = screen.getByRole("link", { name: "Gym" }).closest("tr");
    expect(within(road as HTMLElement).getByText("Every hour")).toBeInTheDocument();
    expect(within(road as HTMLElement).getByText("default")).toBeInTheDocument();
    expect(within(gym as HTMLElement).getByText("Every 6 hours")).toBeInTheDocument();
    expect(within(gym as HTMLElement).queryByText("default")).not.toBeInTheDocument();
  });

  it("pauses the selected syncs together", async () => {
    const patches = serveList(TWO);
    renderWithProviders(<SyncsPage />);
    const bar = await screen.findByRole("toolbar", { name: "Selected syncs" });
    expect(within(bar).getByRole("button", { name: "Pause" })).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox", { name: "Select all syncs" }));
    expect(within(bar).getByText("2 selected")).toBeInTheDocument();
    await userEvent.click(within(bar).getByRole("button", { name: "Pause" }));
    await waitFor(() => expect(patches).toHaveLength(2));
    expect(patches.map((p) => p.id).sort()).toEqual(["sync-1", "sync-2"]);
    expect(patches.every((p) => JSON.stringify(p.body) === JSON.stringify({ paused: true }))).toBe(true);
    await waitFor(() => expect(within(bar).getByRole("button", { name: "Pause" })).toBeDisabled());
    expect(within(bar).queryByText("2 selected")).not.toBeInTheDocument();
  });

  it("changes the schedule of only the picked syncs", async () => {
    const patches = serveList(TWO);
    renderWithProviders(<SyncsPage />);
    await userEvent.click(await screen.findByRole("checkbox", { name: "Select Gym" }));
    await userEvent.click(screen.getByRole("button", { name: "Change schedule…" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Applies to 1 sync.")).toBeInTheDocument();
    await userEvent.selectOptions(within(dialog).getByRole("combobox", { name: "Schedule" }), "Every day");
    await userEvent.selectOptions(within(dialog).getByRole("combobox", { name: "Time" }), "06:00");
    await userEvent.click(within(dialog).getByRole("button", { name: "Apply" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(patches).toEqual([{ id: "sync-2", body: { schedule: "0 6 * * *" } }]);
  });

  it("keeps the schedule dialog open with the error when the cron is rejected", async () => {
    serveList(TWO);
    renderWithProviders(<SyncsPage />);
    await userEvent.click(await screen.findByRole("checkbox", { name: "Select Road trip" }));
    await userEvent.click(screen.getByRole("button", { name: "Change schedule…" }));
    const dialog = await screen.findByRole("dialog");
    await userEvent.selectOptions(within(dialog).getByRole("combobox", { name: "Schedule" }), "Custom (cron)");
    await userEvent.clear(within(dialog).getByRole("textbox", { name: "Cron schedule" }));
    await userEvent.type(within(dialog).getByRole("textbox", { name: "Cron schedule" }), "nope");
    await userEvent.click(within(dialog).getByRole("button", { name: "Apply" }));
    expect(await within(dialog).findByText("Not a valid cron schedule")).toBeInTheDocument();
  });

  it("shows the empty state", async () => {
    server.use(
      http.get("*/api/connections", () => HttpResponse.json({ load_errors: [], services: [] })),
      http.get("*/api/syncs", () => HttpResponse.json([])),
    );
    renderWithProviders(<SyncsPage />);
    expect(await screen.findByText("No syncs yet")).toBeInTheDocument();
    expect(screen.getAllByRole("link").some((l) => l.getAttribute("href") === "/syncs/new")).toBe(true);
  });
});
