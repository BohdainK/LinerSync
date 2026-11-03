import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import type { JobOut } from "@/api/types";
import { jobOut } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";
import { HistoryPage } from "./HistoryPage";

function serve(jobs: JobOut[]) {
  server.use(
    http.get("/api/jobs", ({ request }) =>
      new URL(request.url).searchParams.get("state") === "recent" ? HttpResponse.json(jobs) : HttpResponse.json([]),
    ),
  );
}

const FINISHED = "2026-01-01T13:00:00Z";

describe("HistoryPage", () => {
  it("lists finished syncs, transfers and backups with links to their details", async () => {
    serve([
      jobOut({ job_id: "j1", label: "Sync Road trip", status: "succeeded", finished_at: FINISHED, run_ids: ["run-1"] }),
      jobOut({ job_id: "j2", kind: "transfer", label: "Transfer Chill", status: "failed", finished_at: FINISHED, run_ids: ["run-2"] }),
      jobOut({ job_id: "j3", kind: "backup", label: "Back up Spotify", status: "succeeded", finished_at: FINISHED }),
    ]);
    renderWithProviders(<HistoryPage />);

    expect(await screen.findByRole("link", { name: "Sync Road trip" })).toHaveAttribute("href", "/runs/run-1");
    expect(screen.getByRole("link", { name: "Transfer Chill" })).toHaveAttribute("href", "/runs/run-2");
    expect(screen.getByRole("link", { name: "Back up Spotify" })).toHaveAttribute("href", "/backup");
    expect(screen.getByText("Failed")).toBeInTheDocument();
  });

  it("leaves out previews", async () => {
    serve([
      jobOut({ job_id: "j1", label: "Sync Road trip", status: "succeeded", finished_at: FINISHED }),
      jobOut({ job_id: "j2", kind: "preview", label: "Preview Road trip", status: "succeeded", finished_at: FINISHED }),
    ]);
    renderWithProviders(<HistoryPage />);

    expect(await screen.findByText("Sync Road trip")).toBeInTheDocument();
    expect(screen.queryByText("Preview Road trip")).not.toBeInTheDocument();
  });

  it("says so when nothing has run", async () => {
    serve([]);
    renderWithProviders(<HistoryPage />);
    expect(await screen.findByText("Nothing has run yet")).toBeInTheDocument();
  });
});
