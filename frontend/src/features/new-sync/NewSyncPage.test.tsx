import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { Route, Routes, useLocation } from "react-router";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { EstimateOut, RunOut } from "@/api/types";
import {
  backupPlaylistOut,
  changeOut,
  connectionOut,
  jobOut,
  playlistOut,
  runOut,
  settingsOut,
  syncOut,
  trackOut,
} from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";
import { NewSyncPage } from "./NewSyncPage";
import { PreviewPanel } from "./PreviewPanel";
import type { usePreview } from "./usePreview";

vi.mock("sonner", () => ({ toast: vi.fn() }));

const FULL = ["read_order", "add", "remove", "reorder", "edit_metadata", "create_playlist", "delete_playlist"];

function Probe() {
  const location = useLocation();
  return <output aria-label="location">{location.pathname}</output>;
}

function serve() {
  server.use(
    http.get("*/api/connections", () =>
      HttpResponse.json({
        load_errors: [],
        services: [
          connectionOut({ service: "fake_a", display_name: "Fake A", capabilities: FULL }),
          connectionOut({ service: "fake_b", display_name: "Fake B", capabilities: ["read_order", "add", "create_playlist"] }),
          connectionOut({ service: "fake_c", display_name: "Fake C", capabilities: FULL }),
        ],
      }),
    ),
    http.get("*/api/settings", () => HttpResponse.json(settingsOut({ default_schedule: "0 3 * * *" }))),
    http.get("*/api/connections/fake_a/playlists", () =>
      HttpResponse.json([
        playlistOut({ service: "fake_a", playlist_id: "P1", name: "Road Trip" }),
        playlistOut({ service: "fake_a", playlist_id: "P2", name: "Gym" }),
        playlistOut({ service: "fake_a", playlist_id: "P3", name: "Chill" }),
        playlistOut({ service: "fake_a", playlist_id: "L", name: "Liked" }),
        playlistOut({ service: "fake_a", playlist_id: "X", name: "Locked", readable: false }),
      ]),
    ),
    http.get("*/api/connections/fake_b/playlists", () =>
      HttpResponse.json([playlistOut({ service: "fake_b", playlist_id: "B1", name: "Car" })]),
    ),
    http.get("*/api/connections/fake_c/playlists", () =>
      HttpResponse.json([playlistOut({ service: "fake_c", playlist_id: "C1", name: "Other" })]),
    ),
    // The live preview may start while a test runs; it stays queued.
    http.post("*/api/syncs/preview", () =>
      HttpResponse.json(jobOut({ job_id: "pv", kind: "preview", status: "queued", sync_id: null }), { status: 202 }),
    ),
    http.get("*/api/jobs/pv", () => HttpResponse.json(jobOut({ job_id: "pv", kind: "preview", status: "queued", sync_id: null }))),
  );
}

function renderPage(route = "/syncs/new") {
  return renderWithProviders(
    <Routes>
      <Route path="/syncs/new" element={<NewSyncPage />} />
      <Route path="*" element={<Probe />} />
    </Routes>,
    { route },
  );
}

/** Picks an option once it has loaded (service lists come from /api/connections). */
async function pick(user: ReturnType<typeof userEvent.setup>, combobox: string, option: string) {
  const select = await screen.findByRole("combobox", { name: combobox });
  await within(select).findByRole("option", { name: option });
  await user.selectOptions(select, option);
}

async function chooseSource(user: ReturnType<typeof userEvent.setup>, playlist: string) {
  await pick(user, "Source service", "Fake A");
  const group = await screen.findByRole("group", { name: "Source playlist" });
  await user.click(await within(group).findByRole("radio", { name: new RegExp(playlist) }));
}

beforeEach(() => {
  vi.mocked(toast).mockClear();
});

describe("NewSyncPage", () => {
  it("creates a sync with an existing and a new destination", async () => {
    serve();
    const bodies: unknown[] = [];
    server.use(
      http.post("*/api/syncs", async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json(syncOut({ sync_id: "s1" }), { status: 201 });
      }),
    );
    const user = userEvent.setup();
    renderPage();
    const create = await screen.findByRole("button", { name: "Create sync" });
    expect(create).toBeDisabled();

    await chooseSource(user, "Road Trip");
    await pick(user, "Destination 1", "Fake B");
    const dst1 = await screen.findByRole("group", { name: "Playlist on destination 1" });
    await user.click(await within(dst1).findByRole("radio", { name: /Car/ }));

    await user.click(screen.getByRole("button", { name: "+ Add destination" }));
    expect(create).toBeDisabled();
    await pick(user, "Destination 2", "Fake C");
    const dst2 = await screen.findByRole("group", { name: "Playlist on destination 2" });
    expect(await within(dst2).findByRole("radio", { name: /New playlist/ })).toBeChecked();
    await pick(user, "Mode for destination 2", "Add only");

    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Road Trip");
    await user.click(create);
    await waitFor(() => expect(screen.getByRole("status", { name: "location" })).toHaveTextContent("/syncs/s1"));
    expect(bodies).toEqual([
      {
        name: "Road Trip",
        source: { service: "fake_a", playlist_id: "P1" },
        destinations: [
          { service: "fake_b", playlist_id: "B1", mode: "full" },
          { service: "fake_c", playlist_id: null, mode: "add_only" },
        ],
        schedule: null,
      },
    ]);
    expect(toast).toHaveBeenCalledWith("Sync created. It runs on its schedule, or press Sync now.");
  });

  it("removes a destination row", async () => {
    serve();
    const user = userEvent.setup();
    renderPage();
    await user.click(await screen.findByRole("button", { name: "+ Add destination" }));
    expect(screen.getByRole("combobox", { name: "Destination 2" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Remove destination 1" }));
    expect(screen.queryByRole("combobox", { name: "Destination 2" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Remove destination/ })).not.toBeInTheDocument();
  });

  it("allows at most 10 destinations", async () => {
    serve();
    const user = userEvent.setup();
    renderPage();
    const add = await screen.findByRole("button", { name: "+ Add destination" });
    for (let i = 0; i < 9; i += 1) await user.click(add);
    expect(screen.getByRole("combobox", { name: "Destination 10" })).toBeInTheDocument();
    expect(add).toBeDisabled();
  });

  it("won't sync a playlist into itself", async () => {
    serve();
    const user = userEvent.setup();
    renderPage();
    await chooseSource(user, "Road Trip");
    await pick(user, "Destination 1", "Fake A");
    const dst1 = await screen.findByRole("group", { name: "Playlist on destination 1" });
    await user.click(await within(dst1).findByRole("radio", { name: /Gym/ }));
    expect(screen.getByRole("button", { name: "Create sync" })).toBeEnabled();
    await user.click(within(dst1).getByRole("radio", { name: /Road Trip/ }));
    expect(screen.getByText("Pick a different playlist than the source")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create sync" })).toBeDisabled();
  });

  it("name follows the source until edited", async () => {
    serve();
    const user = userEvent.setup();
    renderPage();
    await chooseSource(user, "Road Trip");
    const name = screen.getByRole("textbox", { name: "Name" });
    expect(name).toHaveValue("Road Trip");
    const group = screen.getByRole("group", { name: "Source playlist" });
    await user.click(within(group).getByRole("radio", { name: /Gym/ }));
    expect(name).toHaveValue("Gym");
    await user.clear(name);
    await user.type(name, "Mine");
    await user.click(within(group).getByRole("radio", { name: /Chill/ }));
    expect(name).toHaveValue("Mine");
  });

  it("shows limitations for an add-only service", async () => {
    serve();
    const user = userEvent.setup();
    renderPage();
    await pick(user, "Destination 1", "Fake B");
    expect(
      await screen.findByText("Fake B can't remove or reorder songs, so this sync runs as add only into Fake B"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Fake B can't edit playlist details, so name and description changes won't carry over"),
    ).toBeInTheDocument();
    await pick(user, "Mode for destination 1", "Add only");
    expect(screen.queryByText(/can't edit playlist details/)).not.toBeInTheDocument();
    expect(screen.getByText(/can't remove or reorder songs/)).toBeInTheDocument();
  });

  it("preselects an archived source from the query", async () => {
    serve();
    const bodies: unknown[] = [];
    server.use(
      http.get("*/api/backups/playlists", () =>
        HttpResponse.json([backupPlaylistOut({ entry_id: "e1", name: "Old mix" })]),
      ),
      http.post("*/api/syncs", async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json(syncOut({ sync_id: "s2" }), { status: 201 });
      }),
    );
    const user = userEvent.setup();
    renderPage("/syncs/new?source=backup:e1");
    expect(await screen.findByRole("radio", { name: /Old mix/ })).toBeChecked();
    expect(screen.getByRole("combobox", { name: "Source service" })).toHaveValue("backup");
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Old mix");
    await pick(user, "Destination 1", "Fake C");
    await user.click(screen.getByRole("button", { name: "Create sync" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toMatchObject({ name: "Old mix", source: { service: "backup", playlist_id: "e1" } });
  });

  it("all playlists: estimate, warn, then bulk create", async () => {
    serve();
    const estimates: unknown[] = [];
    const bulks: unknown[] = [];
    const estimate: EstimateOut = {
      playlists: 3,
      songs: 120,
      api_calls: { fake_a: 4, fake_b: 9 },
      add_only_destinations: ["fake_b"],
      unreadable: [playlistOut({ service: "fake_a", playlist_id: "L", name: "Liked", readable: false })],
    };
    server.use(
      http.post("*/api/transfers/estimate", async ({ request }) => {
        estimates.push(await request.json());
        return HttpResponse.json(estimate);
      }),
      http.post("*/api/syncs/bulk", async ({ request }) => {
        bulks.push(await request.json());
        return HttpResponse.json([], { status: 201 });
      }),
    );
    const user = userEvent.setup();
    renderPage();
    await pick(user, "Source service", "Fake A");
    await user.click(screen.getByRole("radio", { name: "All playlists" }));
    const group = await screen.findByRole("group", { name: "Source playlists" });
    expect(await within(group).findByRole("checkbox", { name: /Road Trip/ })).toBeChecked();
    expect(within(group).getByRole("checkbox", { name: /Liked/ })).toBeChecked();
    expect(within(group).getByRole("checkbox", { name: /Locked/ })).not.toBeChecked();
    expect(screen.queryByRole("textbox", { name: "Name" })).not.toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Playlist on destination 1" })).not.toBeInTheDocument();

    await pick(user, "Destination 1", "Fake B");
    expect(screen.queryByRole("group", { name: "Playlist on destination 1" })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create 4 syncs…" }));

    let dialog = await screen.findByRole("alertdialog", { name: "Sync 3 playlists?" });
    expect(within(dialog).getByText("120 songs")).toBeInTheDocument();
    expect(within(dialog).getByText("About 4 API calls on Fake A")).toBeInTheDocument();
    expect(within(dialog).getByText("About 9 API calls on Fake B")).toBeInTheDocument();
    expect(within(dialog).getByText("Add only: Fake B")).toBeInTheDocument();
    expect(within(dialog).getByText("Skipped, can't read: Liked")).toBeInTheDocument();
    expect(estimates).toEqual([
      {
        items: ["P1", "P2", "P3", "L"].map((id) => ({
          source_service: "fake_a",
          playlist_id: id,
          destination_services: ["fake_b"],
        })),
      },
    ]);

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(bulks).toEqual([]);

    await user.click(screen.getByRole("button", { name: "Create 4 syncs…" }));
    dialog = await screen.findByRole("alertdialog", { name: "Sync 3 playlists?" });
    await user.click(within(dialog).getByRole("button", { name: "Start" }));
    await waitFor(() => expect(screen.getByRole("status", { name: "location" })).toHaveTextContent("/syncs"));
    const dst = [{ service: "fake_b", playlist_id: null, mode: "full" }];
    expect(bulks).toEqual([
      {
        syncs: [
          { name: "Road Trip", source: { service: "fake_a", playlist_id: "P1" }, destinations: dst, schedule: null },
          { name: "Gym", source: { service: "fake_a", playlist_id: "P2" }, destinations: dst, schedule: null },
          { name: "Chill", source: { service: "fake_a", playlist_id: "P3" }, destinations: dst, schedule: null },
        ],
      },
    ]);
  });

  it("shows the server message on 422", async () => {
    serve();
    server.use(
      http.post("*/api/syncs", () =>
        HttpResponse.json({ detail: "Connect Fake C first" }, { status: 422 }),
      ),
    );
    const user = userEvent.setup();
    renderPage();
    await chooseSource(user, "Road Trip");
    await pick(user, "Destination 1", "Fake C");
    await user.click(screen.getByRole("button", { name: "Create sync" }));
    expect(await screen.findByText("Connect Fake C first")).toBeInTheDocument();
  });

  it("sends a custom schedule", async () => {
    serve();
    const bodies: unknown[] = [];
    server.use(
      http.post("*/api/syncs", async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json(syncOut({ sync_id: "s1" }), { status: 201 });
      }),
    );
    const user = userEvent.setup();
    renderPage();
    await chooseSource(user, "Road Trip");
    await pick(user, "Destination 1", "Fake C");
    await screen.findByRole("option", { name: "Default (Every day at 03:00)" });
    await user.selectOptions(screen.getByRole("combobox", { name: "Schedule" }), "Every day");
    await user.selectOptions(screen.getByRole("combobox", { name: "Time" }), "05:00");
    await user.click(screen.getByRole("button", { name: "Create sync" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toMatchObject({ schedule: "0 5 * * *" });
  });

  it("Create sync is disabled while pending", async () => {
    serve();
    let release: () => void = () => {};
    let posts = 0;
    server.use(
      http.post("*/api/syncs", async () => {
        posts += 1;
        await new Promise<void>((r) => (release = r));
        return HttpResponse.json(syncOut({ sync_id: "s1" }), { status: 201 });
      }),
    );
    const user = userEvent.setup();
    renderPage();
    await chooseSource(user, "Road Trip");
    await pick(user, "Destination 1", "Fake C");
    const create = screen.getByRole("button", { name: "Create sync" });
    await user.click(create);
    await waitFor(() => expect(create).toBeDisabled());
    await user.click(create);
    expect(posts).toBe(1);
    release();
    await waitFor(() => expect(screen.getByRole("status", { name: "location" })).toHaveTextContent("/syncs/s1"));
  });
});

type Preview = ReturnType<typeof usePreview>;

function previewOf(o: Partial<Preview>): Preview {
  return { status: "idle", run: null, job: null, error: null, ...o };
}

describe("PreviewPanel", () => {
  const destinations = [
    { service: "fake_b", name: "Fake B" },
    { service: "fake_c", name: "Fake C" },
  ];

  it("shows the job's progress while preparing", () => {
    const job = jobOut({
      job_id: "pv",
      kind: "preview",
      label: "Preview “Road Trip”",
      status: "running",
      progress: { phase: "match", done: 2, total: 5, message: "Matching songs" },
    });
    renderWithProviders(<PreviewPanel preview={previewOf({ status: "running", job })} destinations={destinations} />);
    expect(screen.getByText("Preparing preview…")).toBeInTheDocument();
    expect(screen.getByText("Matching songs")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Preview “Road Trip”" })).toHaveAttribute("value", "2");
  });

  it("shows a failed job's error", () => {
    renderWithProviders(
      <PreviewPanel
        preview={previewOf({ status: "failed", error: new Error("Fake B could not be reached") })}
        destinations={destinations}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Fake B could not be reached");
  });

  it("shows each destination's songs, changes not made and unmatched songs", async () => {
    const run: RunOut = runOut({
      members: [
        {
          member_id: "dst0",
          service: "fake_b",
          playlist_id: null,
          status: "dry_run",
          error: null,
          rebuilt: false,
          created_playlist_id: null,
          operations: [
            {
              type: "add_tracks",
              op_id: "o1",
              tracks: [trackOut({ track_id: "t1", title: "First" }), trackOut({ track_id: "t2", title: "Second" })],
              positions: [],
            },
          ],
          not_made: [changeOut({ kind: "remove", detail: "Fake B can't remove “Old song”" })],
          mismatch: null,
          matches: [],
        },
        {
          member_id: "dst1",
          service: "fake_c",
          playlist_id: "C1",
          status: "dry_run",
          error: null,
          rebuilt: false,
          created_playlist_id: null,
          operations: [
            { type: "remove_tracks", op_id: "o2", tracks: [trackOut({ track_id: "t9", title: "Gone" })], positions: [] },
          ],
          not_made: [],
          mismatch: null,
          matches: [],
        },
      ],
      unresolved: [
        {
          member_id: "dst1",
          position: 3,
          source: trackOut({ track_id: "t5", title: "Rare tune" }),
          reasons: ["No song with this title on Fake C"],
          candidates: [{ track: trackOut({ track_id: "c1", title: "Rare tune (live)" }), confidence: 0.4, reasons: [] }],
        },
      ],
    });
    renderWithProviders(<PreviewPanel preview={previewOf({ status: "ready", run })} destinations={destinations} />);
    const b = screen.getByRole("region", { name: "Fake B" });
    expect(within(b).getByText("2 songs will be added")).toBeInTheDocument();
    expect(within(b).getByText("First")).toBeInTheDocument();
    expect(within(b).getByText("Second")).toBeInTheDocument();
    expect(within(b).getByText("Changes not made")).toBeInTheDocument();
    expect(within(b).getByText("Fake B can't remove “Old song”")).toBeInTheDocument();
    expect(within(b).queryByText(/Didn't match/)).not.toBeInTheDocument();

    const c = screen.getByRole("region", { name: "Fake C" });
    expect(within(c).getByText("0 songs will be added")).toBeInTheDocument();
    expect(within(c).getByText("1 song will be removed")).toBeInTheDocument();
    expect(within(c).getByText("Didn't match (1)")).toBeInTheDocument();
    expect(within(c).getByText("Rare tune")).toBeInTheDocument();
    expect(within(c).getByText("No song with this title on Fake C")).toBeInTheDocument();
    expect(within(c).getByText("Rare tune (live)")).toBeInTheDocument();
  });
});
