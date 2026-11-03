import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import { Route, Routes, useLocation } from "react-router";
import { CONTRIBUTE_SOURCE_URL, REQUEST_SOURCE_URL } from "@/config";
import { AppShell } from "@/layout/AppShell";
import { connectionOut, setupGuide } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";
import { ConnectionPage } from "./ConnectionPage";
import { ConnectionsPage } from "./ConnectionsPage";
import { OnboardingPage } from "./OnboardingPage";
import { capabilityText } from "./ServiceGrid";

function Probe() {
  const l = useLocation();
  return <output aria-label="location">{l.pathname}</output>;
}

function listHandler(
  services = [
    connectionOut({ service: "spotify", display_name: "Spotify", status: "connected" }),
    connectionOut({ service: "navidrome", display_name: "Navidrome", category: "self_hosted", status: "not_set_up" }),
  ],
  load_errors: { name: string; message: string }[] = [],
) {
  return http.get("/api/connections", () => HttpResponse.json({ load_errors, services }));
}

beforeEach(() => localStorage.clear());

describe("ConnectionsPage", () => {
  it("puts set-up services under Connected and groups the rest by category", async () => {
    server.use(
      listHandler([
        connectionOut({ service: "spotify", display_name: "Spotify", status: "connected" }),
        connectionOut({ service: "tidal", display_name: "Tidal", status: "needs_sign_in" }),
        connectionOut({ service: "qobuz", display_name: "Qobuz", status: "not_set_up" }),
        connectionOut({
          service: "navidrome",
          display_name: "Navidrome",
          category: "self_hosted",
          status: "not_set_up",
        }),
      ]),
    );
    renderWithProviders(<ConnectionsPage />, { route: "/connections" });
    expect(await screen.findByRole("heading", { name: "Connections" })).toBeInTheDocument();
    const connected = await screen.findByRole("region", { name: "Connected" });
    expect(within(connected).getByRole("button", { name: /Spotify/ })).not.toHaveTextContent("Connected");
    expect(within(connected).getByRole("button", { name: /Tidal/ })).toHaveTextContent("Needs sign-in");
    const streaming = screen.getByRole("region", { name: "Streaming" });
    expect(
      within(streaming)
        .getAllByRole("button")
        .map((b) => b.textContent),
    ).toEqual([expect.stringContaining("Qobuz")]);
    const hosted = screen.getByRole("region", { name: "Self-hosted" });
    expect(within(hosted).getByRole("button", { name: /Navidrome/ })).toHaveTextContent("Not set up");
  });

  it("tints only connected services with their brand colour", async () => {
    server.use(
      listHandler([
        connectionOut({ service: "spotify", display_name: "Spotify", status: "connected" }),
        connectionOut({ service: "tidal", display_name: "Tidal", status: "needs_sign_in" }),
        connectionOut({ service: "qobuz", display_name: "Qobuz", status: "not_set_up" }),
      ]),
    );
    renderWithProviders(<ConnectionsPage />, { route: "/connections" });
    expect(await screen.findByRole("button", { name: /Spotify/ })).toHaveClass("brand-wash");
    expect(screen.getByRole("button", { name: /Tidal/ })).not.toHaveClass("brand-wash");
    expect(screen.getByRole("button", { name: /Qobuz/ })).not.toHaveClass("brand-wash");
  });

  it("labels experimental services", async () => {
    server.use(
      listHandler([
        connectionOut(),
        connectionOut({ service: "qobuz", display_name: "Qobuz", experimental: true, status: "not_set_up" }),
      ]),
    );
    renderWithProviders(<ConnectionsPage />, { route: "/connections" });
    expect(await screen.findByRole("button", { name: /Qobuz/ })).toHaveTextContent("Experimental");
    expect(screen.getByRole("button", { name: /Spotify/ })).not.toHaveTextContent("Experimental");
  });

  it("omits empty sections and navigates on pick", async () => {
    server.use(listHandler([connectionOut()]));
    renderWithProviders(
      <>
        <ConnectionsPage />
        <Probe />
      </>,
      { route: "/connections" },
    );
    await userEvent.click(await screen.findByRole("button", { name: /Spotify/ }));
    expect(screen.queryByRole("region", { name: "Self-hosted" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("location")).toHaveTextContent("/connections/spotify");
  });

  it("shows plugin load errors", async () => {
    server.use(listHandler([connectionOut()], [{ name: "badplugin", message: "boom" }]));
    renderWithProviders(<ConnectionsPage />, { route: "/connections" });
    expect(await screen.findByRole("alert")).toHaveTextContent("badplugin couldn't be loaded: boom");
  });
});

function renderShell(route: string) {
  server.use(http.get("/api/syncs", () => HttpResponse.json([])));
  return renderWithProviders(
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<h1>Home page</h1>} />
        <Route path="onboarding" element={<OnboardingPage />} />
        <Route path="syncs" element={<h1>Syncs page</h1>} />
      </Route>
    </Routes>,
    { route },
  );
}

/** Waits until /api/connections has resolved and React has rendered with it. */
async function connectionsSettled(client: { getQueryData(k: string[]): unknown; isFetching(): number }) {
  await waitFor(() => {
    expect(client.getQueryData(["connections"])).toBeDefined();
    expect(client.isFetching()).toBe(0);
  });
  await new Promise((r) => setTimeout(r, 0));
}

const NOTHING = [connectionOut({ status: "not_set_up" })];

describe("onboarding", () => {
  it("redirects / to onboarding when nothing is set up", async () => {
    server.use(listHandler(NOTHING));
    renderShell("/");
    expect(await screen.findByRole("heading", { name: "Connect your first service" })).toBeInTheDocument();
  });

  it("does not redirect when something is set up", async () => {
    server.use(listHandler());
    const { queryClient } = renderShell("/");
    await connectionsSettled(queryClient);
    expect(screen.getByRole("heading", { name: "Home page" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Connect your first service" })).not.toBeInTheDocument();
  });

  it("does not redirect when the services list is empty", async () => {
    server.use(listHandler([]));
    const { queryClient } = renderShell("/");
    await connectionsSettled(queryClient);
    expect(screen.getByRole("heading", { name: "Home page" })).toBeInTheDocument();
  });

  it("does not redirect after skip", async () => {
    server.use(listHandler(NOTHING));
    const first = renderShell("/");
    await userEvent.click(await screen.findByRole("button", { name: "Skip for now" }));
    expect(await screen.findByRole("heading", { name: "Home page" })).toBeInTheDocument();
    expect(localStorage.getItem("linersync.onboarding.skipped")).toBe("1");
    first.unmount();
    const second = renderShell("/");
    await connectionsSettled(second.queryClient);
    expect(screen.getByRole("heading", { name: "Home page" })).toBeInTheDocument();
  });

  it("does not redirect deep links", async () => {
    server.use(listHandler(NOTHING));
    const { queryClient } = renderShell("/syncs");
    await connectionsSettled(queryClient);
    expect(screen.getByRole("heading", { name: "Syncs page" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Connect your first service" })).not.toBeInTheDocument();
  });

  it("request and contribute links point at the repo", async () => {
    server.use(listHandler(NOTHING));
    renderWithProviders(<OnboardingPage />, { route: "/onboarding" });
    const request = await screen.findByRole("link", { name: "Request a source" });
    expect(request).toHaveAttribute("href", REQUEST_SOURCE_URL);
    expect(request).toHaveAttribute("target", "_blank");
    expect(screen.getByRole("link", { name: "Contribute a source" })).toHaveAttribute("href", CONTRIBUTE_SOURCE_URL);
  });
});

describe("ConnectionPage", () => {
  function renderPage(info = connectionOut()) {
    server.use(
      http.get("/api/connections/spotify", () => HttpResponse.json(info)),
      http.get("/api/connections/spotify/guide", () => HttpResponse.json(setupGuide())),
    );
    return renderWithProviders(
      <>
        <Routes>
          <Route path="/connections/:service" element={<ConnectionPage />} />
          <Route path="/connections" element={<h1>Connections list</h1>} />
        </Routes>
        <Probe />
      </>,
      { route: "/connections/spotify" },
    );
  }

  async function disconnect(deleteBackups: boolean) {
    await userEvent.click(await screen.findByRole("button", { name: "Disconnect…" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Disconnect Spotify?" });
    expect(dialog).toHaveTextContent("Its backups and archive are kept unless you choose otherwise.");
    if (deleteBackups)
      await userEvent.click(within(dialog).getByRole("checkbox", { name: "Also delete this service's backups" }));
    await userEvent.click(within(dialog).getByRole("button", { name: "Disconnect" }));
  }

  it("shows the connection details", async () => {
    renderPage(connectionOut({ message: "All good", capabilities: ["read_order", "add"] }));
    expect(await screen.findByRole("heading", { name: "Spotify" })).toBeInTheDocument();
    expect(screen.getByText("All good")).toBeInTheDocument();
    expect(screen.getByText("Sam")).toBeInTheDocument();
    expect(screen.getByText("Read song order")).toBeInTheDocument();
    expect(screen.getByText("Add songs")).toBeInTheDocument();
  });

  it("labels the login expiry as a deadline or a reminder", async () => {
    const first = renderPage(connectionOut({ login_expires_at: "2026-01-31T12:00:00Z" }));
    expect(await screen.findByText("Login expires")).toBeInTheDocument();
    first.unmount();
    renderPage(connectionOut({ login_expires_at: "2026-01-31T12:00:00Z", login_expiry_estimated: true }));
    expect(await screen.findByText("Sign in again by")).toBeInTheDocument();
    expect(screen.queryByText("Login expires")).not.toBeInTheDocument();
  });

  it("hides Disconnect when not set up", async () => {
    renderPage(connectionOut({ status: "not_set_up" }));
    await screen.findByRole("heading", { name: "Spotify" });
    expect(screen.queryByRole("button", { name: "Disconnect…" })).not.toBeInTheDocument();
  });

  it("disconnect keeps backups by default and can delete them", async () => {
    const seen: (string | null)[] = [];
    server.use(
      http.delete("/api/connections/spotify", ({ request }) => {
        seen.push(new URL(request.url).searchParams.get("delete_backups"));
        return new HttpResponse(null, { status: 204 });
      }),
    );
    const first = renderPage();
    await disconnect(false);
    await waitFor(() => expect(seen).toEqual(["false"]));
    first.unmount();
    renderPage();
    await disconnect(true);
    await waitFor(() => expect(seen).toEqual(["false", "true"]));
  });

  it("disconnect shows a 409 message", async () => {
    server.use(
      http.delete("/api/connections/spotify", () =>
        HttpResponse.json({ detail: "A backup is running" }, { status: 409 }),
      ),
    );
    renderPage();
    await disconnect(true);
    const dialog = await screen.findByRole("alertdialog");
    expect(await within(dialog).findByText("A backup is running")).toBeInTheDocument();
    expect(screen.getByLabelText("location")).toHaveTextContent("/connections/spotify");
  });
});

describe("capabilityText", () => {
  it("orders and words capabilities", () => {
    expect(
      capabilityText([
        "delete_playlist",
        "add",
        "read_order",
        "bogus",
        "create_playlist",
        "reorder",
        "remove",
        "edit_metadata",
      ]),
    ).toEqual([
      "Read song order",
      "Add songs",
      "Remove songs",
      "Reorder songs",
      "Edit playlist details",
      "Create playlists",
      "Delete playlists",
    ]);
  });
});
