import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { toast } from "sonner";
import { settingsOut, syncOut } from "@/test/fixtures";
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

describe("changing the default schedule", () => {
  const SYNCS = [
    syncOut({ sync_id: "follows", name: "Follows", schedule: null }),
    syncOut({ sync_id: "same", name: "Same", schedule: "0 * * * *" }),
    syncOut({ sync_id: "own", name: "Own", schedule: "0 */6 * * *" }),
  ];

  function serveSyncs(syncs = SYNCS) {
    const order: string[] = [];
    const patches: { id: string; body: unknown }[] = [];
    const bodies = serve((body) => {
      order.push("put");
      return HttpResponse.json({ ...(body as object), timezone: "UTC" });
    });
    server.use(
      http.get("*/api/syncs", () => HttpResponse.json(syncs)),
      http.patch("*/api/syncs/:id", async ({ params, request }) => {
        const body = await request.json();
        order.push(`patch ${String(params.id)}`);
        patches.push({ id: String(params.id), body });
        return HttpResponse.json(syncOut({ sync_id: String(params.id), ...(body as object) }));
      }),
    );
    return { bodies, patches, order };
  }

  async function changeDefault(user: ReturnType<typeof userEvent.setup>) {
    renderWithProviders(<SettingsPage />);
    await user.selectOptions(await screen.findByLabelText("Default sync schedule"), "Every 12 hours");
    await user.click(screen.getByRole("button", { name: "Save" }));
  }

  it("warns with the syncs it affects", async () => {
    serveSyncs();
    await changeDefault(userEvent.setup());
    const dialog = await screen.findByRole("alertdialog", { name: "Change the default schedule?" });
    expect(dialog).toHaveTextContent("from every hour to every 12 hours");
    expect(dialog).toHaveTextContent("1 sync follows the default.");
    expect(dialog).toHaveTextContent("1 sync has its own schedule set to every hour.");
  });

  it("update moves hand-set copies onto the default, then saves", async () => {
    const { bodies, patches, order } = serveSyncs();
    const user = userEvent.setup();
    await changeDefault(user);
    await user.click(await screen.findByRole("button", { name: "Update 2 syncs" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(patches).toEqual([{ id: "same", body: { schedule: null } }]);
    expect(order).toEqual(["patch same", "put"]);
    expect(bodies[0]).toMatchObject({ default_schedule: "0 */12 * * *" });
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  });

  it("keep pins the followers to the old schedule, then saves", async () => {
    const { bodies, patches, order } = serveSyncs();
    const user = userEvent.setup();
    await changeDefault(user);
    await user.click(await screen.findByRole("button", { name: "Keep on every hour" }));
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(patches).toEqual([{ id: "follows", body: { schedule: "0 * * * *" } }]);
    expect(order).toEqual(["patch follows", "put"]);
  });

  it("doesn't save when a sync can't be updated", async () => {
    const { bodies } = serveSyncs();
    server.use(http.patch("*/api/syncs/:id", () => HttpResponse.json({ detail: "Sync is busy" }, { status: 409 })));
    const user = userEvent.setup();
    await changeDefault(user);
    await user.click(await screen.findByRole("button", { name: "Keep on every hour" }));
    const dialog = screen.getByRole("alertdialog");
    expect(await within(dialog).findByText("Sync is busy")).toBeInTheDocument();
    expect(bodies).toHaveLength(0);
  });

  it("saves straight away when no sync runs on the old default", async () => {
    const { bodies } = serveSyncs([syncOut({ schedule: "0 */6 * * *" })]);
    await changeDefault(userEvent.setup());
    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });
});
