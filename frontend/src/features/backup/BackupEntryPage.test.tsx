import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { Route, Routes, useLocation } from "react-router";
import { describe, expect, it } from "vitest";
import type { BackupPlaylistDetailOut } from "@/api/types";
import { backupPlaylistOut, connectionOut, jobOut, trackOut } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";
import { BackupEntryPage } from "./BackupEntryPage";

function Probe() {
  return <output aria-label="location">{useLocation().pathname}</output>;
}

const version = {
  version_id: "v1",
  entry_id: "entry-1",
  name: "Road trip v1",
  description: "",
  cover_url: null,
  taken_at: "2026-01-01T12:00:00Z",
  track_count: 2,
};

function detail(o: Partial<BackupPlaylistDetailOut> = {}): BackupPlaylistDetailOut {
  return { ...backupPlaylistOut(), history: [version], ...o };
}

function serve(d: BackupPlaylistDetailOut = detail()) {
  const seen = { restore: [] as unknown[] };
  server.use(
    http.get("*/api/backups/playlists/entry-1", () => HttpResponse.json(d)),
    http.get("*/api/backups/versions/v1", () =>
      HttpResponse.json({
        ...version,
        tracks: [
          { position: 0, added_at: null, track: trackOut({ track_id: "t1", title: "First song" }) },
          { position: 1, added_at: null, track: trackOut({ track_id: "t2", title: "Second song" }) },
        ],
      }),
    ),
    http.get("*/api/connections", () =>
      HttpResponse.json({
        load_errors: [],
        services: [
          connectionOut({ service: "spotify", display_name: "Spotify" }),
          connectionOut({ service: "tidal", display_name: "Tidal" }),
        ],
      }),
    ),
    http.get("*/api/jobs/job-1", () => HttpResponse.json(jobOut({ kind: "transfer", label: "Restore Road trip" }))),
  );
  return seen;
}

function renderEntry() {
  return renderWithProviders(
    <Routes>
      <Route path="/backup/playlists/:entryId" element={<BackupEntryPage />} />
      <Route path="*" element={<Probe />} />
    </Routes>,
    { route: "/backup/playlists/entry-1" },
  );
}

describe("BackupEntryPage", () => {
  it("selecting a version shows its tracks", async () => {
    serve();
    renderEntry();
    expect(await screen.findByRole("heading", { name: "Road trip" })).toBeInTheDocument();
    const row = (await screen.findByText("Road trip v1")).closest("tr")!;
    expect(within(row).getByRole("link", { name: "Download CSV" })).toHaveAttribute(
      "href",
      "/api/backups/versions/v1/csv",
    );
    await userEvent.click(within(row).getByRole("button", { name: "Show songs" }));
    expect(await screen.findByText("First song")).toBeInTheDocument();
    expect(screen.getByText("Second song")).toBeInTheDocument();
  });

  it("restore posts destination and dry run then shows progress", async () => {
    const seen = serve();
    server.use(
      http.post("*/api/backups/versions/v1/restore", async ({ request }) => {
        seen.restore.push(await request.json());
        return HttpResponse.json(jobOut({ kind: "transfer", label: "Restore Road trip" }), { status: 202 });
      }),
    );
    renderEntry();
    await userEvent.click(await screen.findByRole("button", { name: "Restore…" }));
    expect(screen.getByText("Restoring always creates a new playlist.")).toBeInTheDocument();
    const dest = await screen.findByLabelText("Destination");
    await waitFor(() => expect(dest).toHaveValue("spotify"));
    await userEvent.selectOptions(dest, "tidal");
    await userEvent.click(screen.getByRole("checkbox", { name: "Dry run" }));
    await userEvent.click(screen.getByRole("button", { name: "Restore" }));
    await waitFor(() => expect(seen.restore).toEqual([{ destination: "tidal", dry_run: true }]));
    expect(await within(screen.getByRole("dialog")).findByText("Working…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Restore" })).toBeDisabled();
  });

  it("restore shows Connect X first", async () => {
    serve();
    server.use(
      http.post("*/api/backups/versions/v1/restore", () =>
        HttpResponse.json({ detail: "Connect Fake A first" }, { status: 409 }),
      ),
    );
    renderEntry();
    await userEvent.click(await screen.findByRole("button", { name: "Restore…" }));
    await waitFor(() => expect(screen.getByLabelText("Destination")).toHaveValue("spotify"));
    await userEvent.click(screen.getByRole("button", { name: "Restore" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Connect Fake A first");
  });

  it("archived entry offers sync, transfer and delete", async () => {
    serve(detail({ archived_at: "2026-01-05T12:00:00Z" }));
    server.use(http.delete("*/api/backups/playlists/entry-1", () => new HttpResponse(null, { status: 204 })));
    renderEntry();
    expect(await screen.findByText(/Archived /)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sync from this playlist" })).toHaveAttribute(
      "href",
      "/syncs/new?source=backup:entry-1",
    );
    expect(screen.getByRole("link", { name: "Transfer…" })).toHaveAttribute(
      "href",
      "/transfers/new?source=backup:entry-1",
    );
    await userEvent.click(screen.getByRole("button", { name: "Delete from archive…" }));
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(screen.getByLabelText("location")).toHaveTextContent("/backup"));
  });

  it("delete shows the busy message on 409", async () => {
    serve(detail({ archived_at: "2026-01-05T12:00:00Z" }));
    server.use(
      http.delete("*/api/backups/playlists/entry-1", () =>
        HttpResponse.json({ detail: "Wait for the current backup to finish" }, { status: 409 }),
      ),
    );
    renderEntry();
    await userEvent.click(await screen.findByRole("button", { name: "Delete from archive…" }));
    await userEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Wait for the current backup to finish");
  });

  it("live entry has no delete", async () => {
    serve(detail({ missing_since: "2026-01-03T00:00:00Z" }));
    renderEntry();
    expect(await screen.findByText(/Missing since /)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete from archive…" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Sync from this playlist" })).not.toBeInTheDocument();
  });
});
