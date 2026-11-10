import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import type { FlagOut } from "@/api/types";
import { flagOut } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";
import { ExperimentalFlags } from "./ExperimentalFlags";

vi.mock("sonner", () => ({ toast: Object.assign(vi.fn(), { error: vi.fn() }) }));

const NAME = "Apple Music playlist editing";

function serve(flag: FlagOut, put?: () => Response) {
  const bodies: unknown[] = [];
  let current = flag;
  server.use(
    http.get("*/api/settings/flags", () => HttpResponse.json([current])),
    http.put("*/api/settings/flags/:id", async ({ request }) => {
      const body = (await request.json()) as { on: boolean };
      bodies.push(body);
      if (put) return put();
      current = { ...current, on: body.on };
      return HttpResponse.json(current);
    }),
  );
  return bodies;
}

describe("ExperimentalFlags", () => {
  it("explains the flag next to the toggle", async () => {
    serve(flagOut());
    renderWithProviders(<ExperimentalFlags />);
    const toggle = await screen.findByRole("switch", { name: NAME });
    expect(screen.getByRole("heading", { name: "Experimental" })).toBeInTheDocument();
    expect(toggle).not.toBeChecked();
    expect(toggle).toBeEnabled();
    expect(toggle).toHaveAccessibleDescription(/borrowing the web player's token/);
  });

  it("asks first, listing the syncs it affects, then turns it on", async () => {
    const bodies = serve(flagOut({ affected_syncs: [{ sync_id: "s1", name: "Road trip" }] }));
    const user = userEvent.setup();
    renderWithProviders(<ExperimentalFlags />);
    await user.click(await screen.findByRole("switch", { name: NAME }));
    const dialog = await screen.findByRole("alertdialog", { name: `Turn on ${NAME.toLowerCase()}?` });
    expect(dialog).toHaveTextContent("start removing and reordering songs the sync added");
    expect(dialog).toHaveTextContent("1 sync into Apple Music in full mode is affected:");
    expect(within(dialog).getByText("Road trip")).toBeInTheDocument();
    expect(bodies).toHaveLength(0);
    await user.click(within(dialog).getByRole("button", { name: "Turn on" }));
    await waitFor(() => expect(bodies).toEqual([{ on: true }]));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(await screen.findByRole("switch", { name: NAME })).toBeChecked();
  });

  it("cancel leaves it off", async () => {
    const bodies = serve(flagOut());
    const user = userEvent.setup();
    renderWithProviders(<ExperimentalFlags />);
    await user.click(await screen.findByRole("switch", { name: NAME }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("No sync into Apple Music runs in full mode right now.");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(bodies).toHaveLength(0);
    expect(screen.getByRole("switch", { name: NAME })).not.toBeChecked();
  });

  it("turns off without asking", async () => {
    const bodies = serve(flagOut({ on: true }));
    const user = userEvent.setup();
    renderWithProviders(<ExperimentalFlags />);
    await user.click(await screen.findByRole("switch", { name: NAME }));
    await waitFor(() => expect(bodies).toEqual([{ on: false }]));
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("is disabled, with the reason, when the sign-in can't do it", async () => {
    serve(flagOut({ unavailable: "Apple Music signs in with your own MusicKit key." }));
    renderWithProviders(<ExperimentalFlags />);
    const toggle = await screen.findByRole("switch", { name: NAME });
    expect(toggle).toBeDisabled();
    expect(toggle).toHaveAccessibleDescription(/signs in with your own MusicKit key/);
  });

  it("shows a value the server sets as locked", async () => {
    serve(flagOut({ on: true, locked_by: "LINERSYNC_APPLE_MUSIC_EDITING" }));
    renderWithProviders(<ExperimentalFlags />);
    const toggle = await screen.findByRole("switch", { name: NAME });
    expect(toggle).toBeChecked();
    expect(toggle).toBeDisabled();
    expect(screen.getByText(/Set by the server: LINERSYNC_APPLE_MUSIC_EDITING=on/)).toBeInTheDocument();
  });

  it("shows a refusal in the dialog", async () => {
    serve(flagOut(), () => HttpResponse.json({ detail: "Needs the web sign-in" }, { status: 409 }));
    const user = userEvent.setup();
    renderWithProviders(<ExperimentalFlags />);
    await user.click(await screen.findByRole("switch", { name: NAME }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Turn on" }));
    expect(await within(dialog).findByText("Needs the web sign-in")).toBeInTheDocument();
  });

  it("is hidden when no service has flags", async () => {
    server.use(http.get("*/api/settings/flags", () => HttpResponse.json([])));
    const { container } = renderWithProviders(<ExperimentalFlags />);
    await waitFor(() => expect(container).toBeEmptyDOMElement());
  });
});
