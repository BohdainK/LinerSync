import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { toast } from "sonner";
import { settingsOut } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";
import { SettingsPage } from "./SettingsPage";

vi.mock("sonner", () => ({ toast: vi.fn() }));

beforeEach(() => vi.mocked(toast).mockClear());

function serve(put?: (body: unknown) => Response) {
  const bodies: unknown[] = [];
  server.use(
    http.get("*/api/settings", () => HttpResponse.json(settingsOut({ brake_max_fraction: 0.2 }))),
    http.put("*/api/settings", async ({ request }) => {
      const body = await request.json();
      bodies.push(body);
      return put ? put(body) : HttpResponse.json({ ...(body as object), timezone: "UTC" });
    }),
  );
  return bodies;
}

describe("SettingsPage", () => {
  it("loads and shows percent", async () => {
    serve();
    renderWithProviders(<SettingsPage />);
    const field = await screen.findByLabelText("Pause a run that removes more than (%)");
    expect(field).toHaveValue(20);
    expect(screen.getByLabelText("Backup schedule")).toBeDisabled();
    expect(screen.getByLabelText("Scheduled backups")).not.toBeChecked();
  });

  it("saves with fraction and null backup schedule", async () => {
    const bodies = serve();
    renderWithProviders(<SettingsPage />);
    const field = await screen.findByLabelText("Pause a run that removes more than (%)");
    await userEvent.clear(field);
    await userEvent.type(field, "25");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toEqual({
      default_schedule: "0 * * * *",
      brake_max_fraction: 0.25,
      brake_max_count: 50,
      match_accept_threshold: 0.9,
      match_min_margin: 0.1,
      breaker_failure_threshold: 5,
      breaker_cooldown_minutes: 30,
      backup_schedule: null,
      backup_versions_kept: 10,
    });
    await waitFor(() => expect(toast).toHaveBeenCalledWith("Saved"));
  });

  it("422 shows under the field", async () => {
    serve(() =>
      HttpResponse.json(
        { detail: [{ loc: ["body", "default_schedule"], msg: "Not a valid cron expression" }] },
        { status: 422 },
      ),
    );
    renderWithProviders(<SettingsPage />);
    await screen.findByLabelText("Default sync schedule");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Not a valid cron expression")).toBeInTheDocument();
    expect(screen.getByLabelText("Default sync schedule")).toHaveAttribute("aria-invalid", "true");
    expect(toast).not.toHaveBeenCalled();
  });

  it("timezone is read-only text", async () => {
    serve();
    renderWithProviders(<SettingsPage />);
    expect(
      await screen.findByText("Schedules use UTC. Change it with LINERSYNC_TIMEZONE."),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText(/timezone/i)).not.toBeInTheDocument();
  });
});
