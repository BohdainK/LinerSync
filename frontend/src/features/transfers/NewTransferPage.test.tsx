import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { Route, Routes } from "react-router";
import { describe, expect, it } from "vitest";
import type { EstimateOut } from "@/api/types";
import { keys } from "@/api/keys";
import { backupPlaylistOut, connectionOut, jobOut, playlistOut } from "@/test/fixtures";
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
          connectionOut({ service: "fake_b", display_name: "Fake B", capabilities: ["read_order", "add", "create_playlist"] }),
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
  it("single transfer posts one item", async () => {
    serve();
    const bodies: unknown[] = [];
    server.use(
      http.post("*/api/transfers", async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json(jobOut({ job_id: "t1", kind: "transfer", label: "Transfer to Fake B" }), { status: 202 });
      }),
      http.get("*/api/jobs/t1", () => HttpResponse.json(jobOut({ job_id: "t1", kind: "transfer", label: "Transfer to Fake B" }))),
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
    expect(screen.getByText("Fake B can't remove or reorder songs, so this sync runs as add only into Fake B")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Start transfer" }));
    expect(await screen.findByText("Transfer to Fake B")).toBeInTheDocument();
    expect(bodies).toEqual([
      { source_service: "fake_a", playlist_id: "P1", destination: "fake_b", dry_run: false },
    ]);
  });

  it("dry run flag is sent", async () => {
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
    renderPage();
    await pickSource(user);
    const group = await screen.findByRole("group", { name: "Source playlists" });
    await user.click(await within(group).findByRole("checkbox", { name: /Gym/ }));
    await user.click(await screen.findByRole("checkbox", { name: /Fake C/ }));
    await user.click(screen.getByRole("checkbox", { name: "Dry run (match only, change nothing)" }));
    await user.click(screen.getByRole("button", { name: "Start transfer" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toEqual({ source_service: "fake_a", playlist_id: "P2", destination: "fake_c", dry_run: true });
  });

  async function selectTwo(user: ReturnType<typeof userEvent.setup>) {
    await pickSource(user);
    const group = await screen.findByRole("group", { name: "Source playlists" });
    await user.click(await within(group).findByRole("checkbox", { name: /Road Trip/ }));
    await user.click(within(group).getByRole("checkbox", { name: /Gym/ }));
    await user.click(await screen.findByRole("checkbox", { name: /Fake B/ }));
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
        return HttpResponse.json(jobOut({ job_id: "b1", kind: "bulk_transfer", label: "Transfer 2 playlists" }), { status: 202 });
      }),
      http.get("*/api/jobs/b1", () => HttpResponse.json(jobOut({ job_id: "b1", kind: "bulk_transfer", label: "Transfer 2 playlists" }))),
    );
    const user = userEvent.setup();
    renderPage();
    await selectTwo(user);
    const dialog = await screen.findByRole("alertdialog", { name: "Transfer 2 playlists?" });
    expect(within(dialog).getByText("50 songs")).toBeInTheDocument();
    expect(bulks).toEqual([]);
    await user.click(within(dialog).getByRole("button", { name: "Start" }));
    expect(await screen.findByText("Transfer 2 playlists", { selector: "span" })).toBeInTheDocument();
    const items = ["P1", "P2"].map((id) => ({ source_service: "fake_a", playlist_id: id, destination_services: ["fake_b"] }));
    expect(estimates).toEqual([{ items }]);
    expect(bulks).toEqual([{ items, dry_run: false }]);
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

  it("shows progress then result links", async () => {
    serve();
    server.use(
      http.post("*/api/transfers", () =>
        HttpResponse.json(jobOut({ job_id: "t1", kind: "transfer", label: "Transfer to Fake B" }), { status: 202 }),
      ),
      http.get("*/api/jobs/t1", () => HttpResponse.json(jobOut({ job_id: "t1", kind: "transfer", label: "Transfer to Fake B" }))),
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
      jobOut({ job_id: "t1", kind: "transfer", label: "Transfer to Fake B", status: "succeeded", run_ids: ["r1", "r2"] }),
    );
    const links = await screen.findAllByRole("link", { name: "Open result" });
    expect(links.map((l) => l.getAttribute("href"))).toEqual(["/runs/r1", "/runs/r2"]);
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
    await user.click(screen.getByRole("button", { name: "Start transfer" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toEqual({ source_service: "backup", playlist_id: "e1", destination: "fake_a", dry_run: false });
  });
});
