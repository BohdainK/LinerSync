import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import type { StatsOut } from "@/api/types";
import { statsOut } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";
import { StatsPage } from "./StatsPage";

function serve(make: (days: number) => StatsOut = (days) => statsOut({ days })) {
  const asked: number[] = [];
  server.use(
    http.get("/api/stats", ({ request }) => {
      const days = Number(new URL(request.url).searchParams.get("days"));
      asked.push(days);
      return HttpResponse.json(make(days));
    }),
  );
  return asked;
}

function tile(section: string, label: string) {
  const region = screen.getByRole("region", { name: section });
  const term = within(region).getByText(label, { selector: "dt" });
  return term.nextElementSibling?.textContent;
}

const BUSY = statsOut({
  library: [
    { service: "spotify", display_name: "Spotify", playlists: 12, songs: 1500, archived: 2, versions: 40 },
    { service: "tidal", display_name: "Tidal", playlists: 3, songs: 90, archived: 0, versions: 3 },
  ],
  syncs: 4,
  paused_syncs: 1,
  activity: {
    sync_runs: 20,
    transfer_runs: 2,
    undo_runs: 1,
    succeeded: 19,
    partial: 1,
    failed: 2,
    waiting: 1,
    skipped: 0,
    songs_added: 312,
    songs_removed: 7,
    backups: 30,
  },
  daily: [
    { day: "2025-12-31", succeeded: 10, partial: 0, failed: 2 },
    { day: "2026-01-01", succeeded: 9, partial: 2, failed: 0 },
  ],
  last_backup_at: "2026-01-01T12:00:00Z",
  matching: { remembered: 800, not_on_service: 25, open_attention: 3 },
});

describe("StatsPage", () => {
  it("shows library, activity, syncs and matching", async () => {
    serve(() => BUSY);
    renderWithProviders(<StatsPage />, { route: "/stats", path: "/stats" });
    expect(await screen.findByRole("heading", { name: "Library" })).toBeInTheDocument();
    expect(tile("Library", "Playlists")).toBe("15");
    expect(tile("Library", "Songs")).toBe("1,590");
    expect(tile("Library", "Archived")).toBe("2");
    expect(tile("Library", "Versions kept")).toBe("43");
    const rows = within(screen.getByRole("table")).getAllByRole("row");
    expect(rows.map((r) => r.textContent)).toEqual([
      "ServicePlaylistsSongsArchived",
      "Spotify121,5002",
      "Tidal3900",
    ]);
    expect(tile("Activity in the last 30 days", "Sync runs")).toBe("20");
    expect(tile("Activity in the last 30 days", "Transfers")).toBe("2");
    expect(tile("Activity in the last 30 days", "Undos")).toBe("1");
    expect(tile("Activity in the last 30 days", "Songs added")).toBe("312");
    expect(tile("Activity in the last 30 days", "Songs removed")).toBe("7");
    expect(tile("Activity in the last 30 days", "Backups")).toBe("30");
    expect(screen.getByText("19 succeeded, 1 partly done, 2 failed, 1 waiting, 0 skipped")).toBeInTheDocument();
    expect(tile("Syncs", "Syncs")).toBe("4");
    expect(tile("Syncs", "Paused")).toBe("1");
    expect(tile("Matching", "Remembered matches")).toBe("800");
    expect(tile("Matching", "Not on that service")).toBe("25");
    expect(tile("Matching", "Needs attention")).toBe("3");
    expect(screen.getByRole("link", { name: "Review on Home" })).toHaveAttribute("href", "/");
    expect(screen.getByText("Runs per day")).toBeInTheDocument();
  });

  it("shows empty states", async () => {
    serve();
    renderWithProviders(<StatsPage />, { route: "/stats", path: "/stats" });
    expect(await screen.findByText(/No backups yet/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Back up now" })).toHaveAttribute("href", "/backup");
    expect(screen.getByText("No runs in this period")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByText("Runs per day")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Review on Home" })).not.toBeInTheDocument();
  });

  it("switches the period", async () => {
    const asked = serve();
    renderWithProviders(<StatsPage />, { route: "/stats", path: "/stats" });
    const month = await screen.findByRole("button", { name: "30 days" });
    expect(month).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(screen.getByRole("button", { name: "7 days" }));
    expect(await screen.findByRole("heading", { name: "Activity in the last 7 days" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "7 days" })).toHaveAttribute("aria-pressed", "true");
    expect(month).toHaveAttribute("aria-pressed", "false");
    expect(asked).toEqual([30, 7]);
  });

  it("reads the period from the address", async () => {
    const asked = serve();
    renderWithProviders(<StatsPage />, { route: "/stats?days=90", path: "/stats" });
    expect(await screen.findByRole("heading", { name: "Activity in the last 90 days" })).toBeInTheDocument();
    expect(asked).toEqual([90]);
  });

  it("falls back to 30 days for an unknown period", async () => {
    const asked = serve();
    renderWithProviders(<StatsPage />, { route: "/stats?days=abc", path: "/stats" });
    expect(await screen.findByRole("heading", { name: "Activity in the last 30 days" })).toBeInTheDocument();
    expect(asked).toEqual([30]);
  });

  it("shows an error with retry", async () => {
    server.use(http.get("/api/stats", () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
    renderWithProviders(<StatsPage />, { route: "/stats", path: "/stats" });
    expect(await screen.findByRole("alert")).toHaveTextContent("Try again");
  });
});
