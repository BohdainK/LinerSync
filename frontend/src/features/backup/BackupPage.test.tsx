import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import type { BackupSummaryOut } from "@/api/types";
import {
  backupPlaylistOut,
  backupRunOut,
  backupServiceOut,
  backupSummaryOut,
  connectionOut,
  jobOut,
} from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";
import { BackupPage } from "./BackupPage";

function summary(o: Partial<BackupSummaryOut> = {}): BackupSummaryOut {
  return backupSummaryOut({
    services: [
      backupServiceOut({
        last_run: backupRunOut({ errors: ["Could not read X", "Could not read Y"], unreadable: 2 }),
        missing: 1,
      }),
      backupServiceOut({ service: "fake_b", display_name: "Fake B", connected: false, playlists: 0, archived: 2 }),
    ],
    ...o,
  });
}

function serve(sum: BackupSummaryOut = summary()) {
  const seen = { playlistQueries: [] as string[], runBodies: [] as unknown[] };
  server.use(
    http.get("*/api/backups", () => HttpResponse.json(sum)),
    http.get("*/api/backups/runs", () => HttpResponse.json([backupRunOut()])),
    http.get("*/api/backups/playlists", ({ request }) => {
      seen.playlistQueries.push(new URL(request.url).search);
      return HttpResponse.json([
        backupPlaylistOut({ entry_id: "e1", name: "One", cover_url: "/api/backups/covers/abc" }),
        backupPlaylistOut({ entry_id: "e2", name: "Two", missing_since: "2026-01-02T00:00:00Z" }),
      ]);
    }),
    http.get("*/api/connections", () =>
      HttpResponse.json({ load_errors: [], services: [connectionOut({ service: "fake_a", display_name: "Fake A" })] }),
    ),
    http.get("*/api/jobs/job-1", () => HttpResponse.json(jobOut({ kind: "backup", label: "Back up all" }))),
    http.post("*/api/backups/run", async ({ request }) => {
      seen.runBodies.push(await request.json());
      return HttpResponse.json(jobOut({ kind: "backup", label: "Back up all" }), { status: 202 });
    }),
  );
  return seen;
}

describe("BackupPage", () => {
  it("shows services, schedule and last runs", async () => {
    serve(summary({ schedule: "0 3 * * *", next_backup_at: new Date(Date.now() + 3 * 3_600_000 + 60_000).toISOString() }));
    renderWithProviders(<BackupPage />);
    await waitFor(() =>
      expect(screen.getByText(/^Scheduled:/)).toHaveTextContent("Scheduled: 0 3 * * * (next in 3 hours)"),
    );
    expect(screen.getByText("3 playlists, 1 archived, 1 missing")).toBeInTheDocument();
    expect(screen.getByText("Not connected")).toBeInTheDocument();
    expect(screen.getByText("2 saved, 1 unchanged, 0 archived, 2 unreadable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "2 problems" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Back up Fake A now" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Back up Fake B now" })).not.toBeInTheDocument();
    expect(await screen.findByRole("table", { name: "Recent backups" })).toBeInTheDocument();
  });

  it("links to settings when scheduled backups are off", async () => {
    serve();
    renderWithProviders(<BackupPage />);
    expect(await screen.findByText("Scheduled backups are off")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Change in Settings" })).toHaveAttribute("href", "/settings");
  });

  it("back up all posts empty services", async () => {
    const seen = serve();
    renderWithProviders(<BackupPage />);
    const all = await screen.findByRole("button", { name: "Back up all now" });
    await waitFor(() => expect(all).toBeEnabled());
    await userEvent.click(all);
    await waitFor(() => expect(seen.runBodies).toEqual([{ services: [] }]));
  });

  it("back up one service posts its id", async () => {
    const seen = serve();
    renderWithProviders(<BackupPage />);
    const one = await screen.findByRole("button", { name: "Back up Fake A now" });
    await waitFor(() => expect(one).toBeEnabled());
    await userEvent.click(one);
    await waitFor(() => expect(seen.runBodies).toEqual([{ services: ["fake_a"] }]));
  });

  it("buttons disabled while a backup job is active", async () => {
    serve(summary({ active_job: jobOut({ kind: "backup", label: "Back up all" }) }));
    renderWithProviders(<BackupPage />);
    expect(await screen.findByRole("button", { name: "Cancel" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Back up all now" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Back up Fake A now" })).toBeDisabled();
  });

  it("archive tab requests archived=true", async () => {
    const seen = serve();
    renderWithProviders(<BackupPage />);
    await waitFor(() => expect(seen.playlistQueries).toContain("?archived=false"));
    await userEvent.click(screen.getByRole("tab", { name: "Archive" }));
    await waitFor(() => expect(seen.playlistQueries).toContain("?archived=true"));
  });

  it("csv link carries selected entry ids", async () => {
    serve();
    renderWithProviders(<BackupPage />);
    expect(await screen.findByRole("button", { name: "Download CSV" })).toBeDisabled();
    await userEvent.click(await screen.findByRole("checkbox", { name: "Select One" }));
    await userEvent.click(screen.getByRole("checkbox", { name: "Select Two" }));
    const link = screen.getByRole("link", { name: "Download CSV" });
    expect(link).toHaveAttribute("href", "/api/export/csv?entry_id=e1&entry_id=e2");
    expect(link).toHaveAttribute("download");
    expect(screen.getByRole("link", { name: "Download all as CSV" })).toHaveAttribute("href", "/api/export/csv");
  });

  it("cover img has empty alt and missing cover shows placeholder", async () => {
    serve();
    renderWithProviders(<BackupPage />);
    const rowOne = (await screen.findByRole("link", { name: "One" })).closest("tr")!;
    const img = rowOne.querySelector("img")!;
    expect(img).toHaveAttribute("alt", "");
    expect(img).toHaveAttribute("src", "/api/backups/covers/abc");
    const rowTwo = screen.getByRole("link", { name: "Two" }).closest("tr")!;
    expect(rowTwo.querySelector("img")).toBeNull();
    expect(within(rowTwo).getByText(/Missing since/)).toBeInTheDocument();
  });
});
