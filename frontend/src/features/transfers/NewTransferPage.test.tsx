import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router";
import { describe, expect, it } from "vitest";
import type { EstimateOut } from "@/api/types";
import { keys } from "@/api/keys";
import { backupPlaylistOut, connectionOut, jobOut, playlistOut, runOut, trackOut } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";
import { NewTransferPage } from "./NewTransferPage";

const FULL = ["read_order", "add", "remove", "reorder", "edit_metadata", "create_playlist", "delete_playlist"];

function serve() {
  server.use(
    http.get("*/api/connections", () =>
      HttpResponse.json({
        load_errors: [],
        services: [
          connectionOut({ service: "fake_a", display_name: "Fake A", capabilities: FULL }),
          connectionOut({
            service: "fake_b",
            display_name: "Fake B",
            capabilities: ["read_order", "add", "create_playlist"],
          }),
          connectionOut({ service: "fake_c", display_name: "Fake C", capabilities: FULL }),
        ],
      }),
    ),
    http.get("*/api/connections/fake_a/playlists", () =>
      HttpResponse.json([
        playlistOut({ service: "fake_a", playlist_id: "P1", name: "Road Trip" }),
        playlistOut({ service: "fake_a", playlist_id: "P2", name: "Gym" }),
      ]),
    ),
    http.get("*/api/backups/playlists", () =>
      HttpResponse.json([backupPlaylistOut({ entry_id: "e1", name: "Old mix" })]),
    ),
  );
}

function renderPage(route = "/transfers/new") {
  return renderWithProviders(
    <Routes>
      <Route path="/transfers/new" element={<NewTransferPage />} />
    </Routes>,
    { route },
  );
}

async function pickSource(user: ReturnType<typeof userEvent.setup>) {
  const select = await screen.findByRole("combobox", { name: "Source service" });
  await within(select).findByRole("option", { name: "Fake A" });
  await user.selectOptions(select, "Fake A");
}

describe("NewTransferPage", () => {
  it("read-only services are not offered as destinations", async () => {
    serve();
    server.use(
      http.get("*/api/connections", () =>
        HttpResponse.json({
          load_errors: [],
          services: [
            connectionOut({ service: "fake_a", display_name: "Fake A", capabilities: FULL }),
            connectionOut({ service: "fake_b", display_name: "Fake B", capabilities: FULL }),
            connectionOut({ service: "jellyfin", display_name: "Jellyfin", capabilities: ["read_order"] }),
          ],
        }),
      ),
    );
    const user = userEvent.setup();
    renderPage();
    await pickSource(user);
    await screen.findByRole("checkbox", { name: /Fake B/ });
    expect(screen.queryByRole("checkbox", { name: /Jellyfin/ })).not.toBeInTheDocument();
  });

  it("single transfer posts one item", async () => {
    serve();
    const bodies: unknown[] = [];
    server.use(
      http.post("*/api/transfers", async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json(jobOut({ job_id: "t1", kind: "transfer", label: "Transfer to Fake B" }), {
          status: 202,
        });
      }),
      http.get("*/api/jobs/t1", () =>
        HttpResponse.json(jobOut({ job_id: "t1", kind: "transfer", label: "Transfer to Fake B" })),
      ),
    );
    const user = userEvent.setup();
    renderPage();
    const start = await screen.findByRole("button", { name: "Start transfer" });
    expect(start).toBeDisabled();
    await pickSource(user);
    const group = await screen.findByRole("group", { name: "Source playlists" });
    await user.click(await within(group).findByRole("checkbox", { name: /Road Trip/ }));
    expect(screen.queryByRole("checkbox", { name: "Fake A" })).not.toBeInTheDocument();
    await user.click(await screen.findByRole("checkbox", { name: /Fake B/ }));
    expect(
      screen.getByText("Fake B can't remove or reorder songs, so this sync runs as add only into Fake B"),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: "Transfer immediately (skip the preview)" }));
    await user.click(screen.getByRole("button", { name: "Start transfer" }));
    expect(await screen.findByText("Transfer to Fake B")).toBeInTheDocument();
    expect(bodies).toEqual([{ source_service: "fake_a", playlist_id: "P1", destination: "fake_b", dry_run: false }]);
  });

  const pair = (title: string, id: string) => ({
    source: trackOut({ service: "fake_a", track_id: `a-${id}`, title }),
    target: trackOut({ service: "fake_c", track_id: `c-${id}`, title }),
    added: true,
  });
  const previewRun = (runId: string, title: string) =>
    runOut({
      run_id: runId,
      dry_run: true,
      members: [
        {
          member_id: "dst0",
          service: "fake_c",
          playlist_id: null,
          status: "dry_run" as const,
          error: null,
          rebuilt: false,
          created_playlist_id: null,
          operations: [],
          not_made: [],
          mismatch: null,
          matches: [pair(title, runId)],
        },
      ],
    });
  const done = (id: string, runIds: string[]) =>
    jobOut({ job_id: id, kind: "transfer", label: "Transfer to Fake C", status: "succeeded", run_ids: runIds });

  async function selectGym(user: ReturnType<typeof userEvent.setup>) {
    await pickSource(user);
    const group = await screen.findByRole("group", { name: "Source playlists" });
    await user.click(await within(group).findByRole("checkbox", { name: /Gym/ }));
    await user.click(await screen.findByRole("checkbox", { name: /Fake C/ }));
  }

  it("previews by default, then starts from the preview", async () => {
    serve();
    const bodies: unknown[] = [];
    const starts: unknown[] = [];
    server.use(
      http.post("*/api/transfers", async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json(done("t1", ["r1"]), { status: 202 });
      }),
      http.get("*/api/jobs/t1", () => HttpResponse.json(done("t1", ["r1"]))),
      http.get("*/api/runs/r1", () => HttpResponse.json(previewRun("r1", "Song one"))),
      http.post("*/api/runs/r1/alternatives", () =>
        HttpResponse.json([
          {
            track: trackOut({ service: "fake_c", track_id: "c-alt", title: "Song one live" }),
            confidence: 0.8,
            reasons: [],
          },
        ]),
      ),
      http.post("*/api/transfers/from-preview", async ({ request }) => {
        starts.push(await request.json());
        return HttpResponse.json(jobOut({ job_id: "t2", kind: "transfer", label: "Real run" }), { status: 202 });
      }),
      http.get("*/api/jobs/t2", () => HttpResponse.json(jobOut({ job_id: "t2", kind: "transfer", label: "Real run" }))),
    );
    const user = userEvent.setup();
    renderPage();
    await selectGym(user);
    await user.click(screen.getByRole("button", { name: "Start transfer" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toEqual({ source_service: "fake_a", playlist_id: "P2", destination: "fake_c", dry_run: true });
    await user.click(await screen.findByRole("button", { name: /^Change match for Song one/ }));
    await user.click(await screen.findByRole("button", { name: /^Use this/ }));
    expect(await screen.findByText("Your pick")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Start transfer" }));
    expect(await screen.findByText("Real run", { selector: "span" })).toBeInTheDocument();
    expect(starts).toEqual([
      {
        preview_job_id: "t1",
        picks: [
          { source_service: "fake_a", source_track_id: "a-r1", target_service: "fake_c", target_track_id: "c-alt" },
        ],
      },
    ]);
    expect(screen.queryByRole("button", { name: "Start transfer" })).not.toBeInTheDocument();
  });

  it("shows the error when starting from the preview fails", async () => {
    serve();
    server.use(
      http.post("*/api/transfers", () => HttpResponse.json(done("t1", ["r1"]), { status: 202 })),
      http.get("*/api/jobs/t1", () => HttpResponse.json(done("t1", ["r1"]))),
      http.get("*/api/runs/r1", () => HttpResponse.json(previewRun("r1", "Song one"))),
      http.post("*/api/transfers/from-preview", () =>
        HttpResponse.json({ detail: "The preview did not finish" }, { status: 409 }),
      ),
    );
    const user = userEvent.setup();
    renderPage();
    await selectGym(user);
    await user.click(screen.getByRole("button", { name: "Start transfer" }));
    await user.click(await screen.findByRole("button", { name: "Start transfer" }));
    expect(await screen.findByText(/The preview did not finish/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start transfer" })).toBeEnabled();
  });

  it("a failed start does not leak into the next preview, and reset clears picks", async () => {
    serve();
    const starts: unknown[] = [];
    server.use(
      http.post("*/api/transfers", () => HttpResponse.json(done("t1", ["r1"]), { status: 202 })),
      http.get("*/api/jobs/t1", () => HttpResponse.json(done("t1", ["r1"]))),
      http.get("*/api/runs/r1", () => HttpResponse.json(previewRun("r1", "Song one"))),
      http.post("*/api/runs/r1/alternatives", () =>
        HttpResponse.json([
          {
            track: trackOut({ service: "fake_c", track_id: "c-alt", title: "Song one live" }),
            confidence: 0.8,
            reasons: [],
          },
        ]),
      ),
      http.post("*/api/transfers/from-preview", async ({ request }) => {
        starts.push(await request.json());
        return HttpResponse.json(
          { detail: starts.length === 1 ? "The preview did not finish" : "Second start failed" },
          { status: 409 },
        );
      }),
    );
    const user = userEvent.setup();
    renderPage();
    await selectGym(user);
    await user.click(screen.getByRole("button", { name: "Start transfer" }));
    await user.click(await screen.findByRole("button", { name: /^Change match for Song one/ }));
    await user.click(await screen.findByRole("button", { name: /^Use this/ }));
    await user.click(await screen.findByRole("button", { name: "Start transfer" }));
    expect(await screen.findByText(/The preview did not finish/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Start another transfer" }));
    await selectGym(user);
    await user.click(screen.getByRole("button", { name: "Start transfer" }));
    await screen.findByRole("button", { name: /^Change match for Song one/ });
    expect(screen.queryByText(/The preview did not finish/)).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Start transfer" }));
    expect(await screen.findByText(/Second start failed/)).toBeInTheDocument();
    expect(starts).toHaveLength(2);
    expect((starts[1] as { picks: unknown[] }).picks).toEqual([]);
  });

  it("transfer immediately skips the preview", async () => {
    serve();
    const bodies: unknown[] = [];
    server.use(
      http.post("*/api/transfers", async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json(jobOut({ job_id: "t1", kind: "transfer" }), { status: 202 });
      }),
      http.get("*/api/jobs/t1", () =>
        HttpResponse.json(jobOut({ job_id: "t1", kind: "transfer", status: "succeeded", run_ids: [] })),
      ),
    );
    const user = userEvent.setup();
    renderPage();
    await selectGym(user);
    const toggle = screen.getByRole("checkbox", { name: "Transfer immediately (skip the preview)" });
    expect(toggle).not.toBeChecked();
    await user.click(toggle);
    await user.click(screen.getByRole("button", { name: "Start transfer" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toEqual({ source_service: "fake_a", playlist_id: "P2", destination: "fake_c", dry_run: false });
    expect(await screen.findByRole("button", { name: "Start another transfer" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start transfer" })).not.toBeInTheDocument();
  });

  async function selectTwo(user: ReturnType<typeof userEvent.setup>, immediate = true) {
    await pickSource(user);
    const group = await screen.findByRole("group", { name: "Source playlists" });
    await user.click(await within(group).findByRole("checkbox", { name: /Road Trip/ }));
    await user.click(within(group).getByRole("checkbox", { name: /Gym/ }));
    await user.click(await screen.findByRole("checkbox", { name: /Fake B/ }));
    if (immediate) await user.click(screen.getByRole("checkbox", { name: "Transfer immediately (skip the preview)" }));
    await user.click(screen.getByRole("button", { name: "Start transfer" }));
  }

  const estimate: EstimateOut = {
    playlists: 2,
    songs: 50,
    api_calls: { fake_b: 7 },
    add_only_destinations: ["fake_b"],
    unreadable: [],
  };

  it("several playlists warn then post bulk", async () => {
    serve();
    const bulks: unknown[] = [];
    const estimates: unknown[] = [];
    server.use(
      http.post("*/api/transfers/estimate", async ({ request }) => {
        estimates.push(await request.json());
        return HttpResponse.json(estimate);
      }),
      http.post("*/api/transfers/bulk", async ({ request }) => {
        bulks.push(await request.json());
        return HttpResponse.json(jobOut({ job_id: "b1", kind: "bulk_transfer", label: "Transfer 2 playlists" }), {
          status: 202,
        });
      }),
      http.get("*/api/jobs/b1", () =>
        HttpResponse.json(jobOut({ job_id: "b1", kind: "bulk_transfer", label: "Transfer 2 playlists" })),
      ),
    );
    const user = userEvent.setup();
    renderPage();
    await selectTwo(user);
    const dialog = await screen.findByRole("alertdialog", { name: "Transfer 2 playlists?" });
    expect(within(dialog).getByText("50 songs")).toBeInTheDocument();
    expect(bulks).toEqual([]);
    await user.click(within(dialog).getByRole("button", { name: "Start" }));
    expect(await screen.findByText("Transfer 2 playlists", { selector: "span" })).toBeInTheDocument();
    const items = ["P1", "P2"].map((id) => ({
      source_service: "fake_a",
      playlist_id: id,
      destination_services: ["fake_b"],
    }));
    expect(estimates).toEqual([{ items }]);
    expect(bulks).toEqual([{ items, dry_run: false }]);
  });

  it("bulk preview then start", async () => {
    serve();
    const bulks: unknown[] = [];
    const starts: unknown[] = [];
    const bulkJob = (id: string, runIds: string[]) =>
      jobOut({
        job_id: id,
        kind: "bulk_transfer",
        label: "Transfer 2 playlists",
        status: "succeeded",
        run_ids: runIds,
      });
    server.use(
      http.post("*/api/transfers/estimate", () => HttpResponse.json(estimate)),
      http.post("*/api/transfers/bulk", async ({ request }) => {
        bulks.push(await request.json());
        return HttpResponse.json(bulkJob("b1", ["r1", "r2"]), { status: 202 });
      }),
      http.get("*/api/jobs/b1", () => HttpResponse.json(bulkJob("b1", ["r1", "r2"]))),
      http.get("*/api/runs/r1", () => HttpResponse.json(previewRun("r1", "Song one"))),
      http.get("*/api/runs/r2", () => HttpResponse.json(previewRun("r2", "Song two"))),
      http.post("*/api/transfers/from-preview", async ({ request }) => {
        starts.push(await request.json());
        return HttpResponse.json(jobOut({ job_id: "b2", kind: "bulk_transfer", label: "Real bulk" }), { status: 202 });
      }),
      http.get("*/api/jobs/b2", () =>
        HttpResponse.json(jobOut({ job_id: "b2", kind: "bulk_transfer", label: "Real bulk" })),
      ),
    );
    const user = userEvent.setup();
    renderPage();
    await selectTwo(user, false);
    const dialog = await screen.findByRole("alertdialog", { name: "Transfer 2 playlists?" });
    await user.click(within(dialog).getByRole("button", { name: "Start" }));
    await waitFor(() => expect(bulks).toHaveLength(1));
    expect(bulks[0]).toMatchObject({ dry_run: true });
    expect(await screen.findAllByRole("button", { name: /^Change match for/ })).toHaveLength(2);
    await user.click(screen.getByRole("button", { name: "Start transfer" }));
    expect(await screen.findByText("Real bulk", { selector: "span" })).toBeInTheDocument();
    expect(starts).toEqual([{ preview_job_id: "b1", picks: [] }]);
  });

  it("cannot start when every playlist is unreadable", async () => {
    serve();
    const bulks: unknown[] = [];
    server.use(
      http.post("*/api/transfers/estimate", () =>
        HttpResponse.json({
          ...estimate,
          playlists: 2,
          unreadable: [
            playlistOut({ playlist_id: "P1", name: "Road Trip", readable: false }),
            playlistOut({ playlist_id: "P2", name: "Gym", readable: false }),
          ],
        }),
      ),
      http.post("*/api/transfers/bulk", async ({ request }) => {
        bulks.push(await request.json());
        return HttpResponse.json(jobOut(), { status: 202 });
      }),
    );
    const user = userEvent.setup();
    renderPage();
    await selectTwo(user);
    const dialog = await screen.findByRole("alertdialog", { name: "Transfer 2 playlists?" });
    expect(within(dialog).getByText("Nothing to transfer: every selected playlist is unreadable")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Start" })).toBeDisabled();
    expect(bulks).toEqual([]);
  });

  it("cancel in the warning sends nothing", async () => {
    serve();
    const bulks: unknown[] = [];
    server.use(
      http.post("*/api/transfers/estimate", () => HttpResponse.json(estimate)),
      http.post("*/api/transfers/bulk", async ({ request }) => {
        bulks.push(await request.json());
        return HttpResponse.json(jobOut(), { status: 202 });
      }),
    );
    const user = userEvent.setup();
    renderPage();
    await selectTwo(user);
    const dialog = await screen.findByRole("alertdialog", { name: "Transfer 2 playlists?" });
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(bulks).toEqual([]);
    expect(screen.getByRole("button", { name: "Start transfer" })).toBeEnabled();
  });

  it("shows progress then each result's songs", async () => {
    serve();
    const pair = (title: string) => ({
      source: trackOut({ service: "fake_a", track_id: `a-${title}`, title, artwork_url: "https://img.test/a.jpg" }),
      target: trackOut({ service: "fake_b", track_id: `b-${title}`, title, artwork_url: "https://img.test/b.jpg" }),
      added: true,
    });
    const member = (matches: ReturnType<typeof pair>[]) => ({
      member_id: "dst0",
      service: "fake_b",
      playlist_id: null,
      status: "dry_run" as const,
      error: null,
      rebuilt: false,
      created_playlist_id: null,
      operations: [],
      not_made: [],
      mismatch: null,
      matches,
    });
    server.use(
      http.get("*/api/runs/r1", () =>
        HttpResponse.json(runOut({ run_id: "r1", dry_run: true, members: [member([pair("Song one")])] })),
      ),
      http.get("*/api/runs/r2", () =>
        HttpResponse.json(runOut({ run_id: "r2", dry_run: true, members: [member([pair("Song two")])] })),
      ),
      http.post("*/api/transfers", () =>
        HttpResponse.json(jobOut({ job_id: "t1", kind: "transfer", label: "Transfer to Fake B" }), { status: 202 }),
      ),
      http.get("*/api/jobs/t1", () =>
        HttpResponse.json(jobOut({ job_id: "t1", kind: "transfer", label: "Transfer to Fake B" })),
      ),
    );
    const user = userEvent.setup();
    const { queryClient } = renderPage();
    await pickSource(user);
    const group = await screen.findByRole("group", { name: "Source playlists" });
    await user.click(await within(group).findByRole("checkbox", { name: /Road Trip/ }));
    await user.click(await screen.findByRole("checkbox", { name: /Fake B/ }));
    await user.click(screen.getByRole("button", { name: "Start transfer" }));
    expect(await screen.findByText("Working…")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Start transfer" })).not.toBeInTheDocument();

    queryClient.setQueryData(
      keys.job("t1"),
      jobOut({
        job_id: "t1",
        kind: "transfer",
        label: "Transfer to Fake B",
        status: "succeeded",
        run_ids: ["r1", "r2"],
      }),
    );
    expect(await screen.findAllByText("Song one")).toHaveLength(2);
    expect(await screen.findAllByText("Song two")).toHaveLength(2);
    expect(screen.queryByRole("link", { name: "Open result" })).not.toBeInTheDocument();
    expect(screen.getAllByText("Nothing was changed. This is what the transfer would do.")).toHaveLength(2);
    const images = document.querySelectorAll("img");
    expect([...images].map((i) => i.getAttribute("src"))).toEqual([
      "https://img.test/a.jpg",
      "https://img.test/b.jpg",
      "https://img.test/a.jpg",
      "https://img.test/b.jpg",
    ]);
    await user.click(screen.getByRole("button", { name: "Start another transfer" }));
    expect(await screen.findByRole("button", { name: "Start transfer" })).toBeInTheDocument();
  });

  it("archived source preselected from the query", async () => {
    serve();
    const bodies: unknown[] = [];
    server.use(
      http.post("*/api/transfers", async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json(jobOut({ job_id: "t1", kind: "transfer" }), { status: 202 });
      }),
      http.get("*/api/jobs/t1", () => HttpResponse.json(jobOut({ job_id: "t1", kind: "transfer" }))),
    );
    const user = userEvent.setup();
    renderPage("/transfers/new?source=backup:e1");
    const group = await screen.findByRole("group", { name: "Source playlists" });
    expect(await within(group).findByRole("checkbox", { name: /Old mix/ })).toBeChecked();
    await user.click(await screen.findByRole("checkbox", { name: /Fake A/ }));
    await user.click(screen.getByRole("checkbox", { name: "Transfer immediately (skip the preview)" }));
    await user.click(screen.getByRole("button", { name: "Start transfer" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toEqual({ source_service: "backup", playlist_id: "e1", destination: "fake_a", dry_run: false });
  });
});
