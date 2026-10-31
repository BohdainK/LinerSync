import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import { useLocation } from "react-router";
import { connectionOut, syncOut } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";
import { Sidebar } from "./Sidebar";

function Probe() {
  return <output aria-label="location">{useLocation().pathname}</output>;
}

function renderSidebar(syncs = [syncOut()]) {
  server.use(
    http.get("/api/connections", () =>
      HttpResponse.json({
        load_errors: [],
        services: [
          connectionOut({ service: "spotify", display_name: "Spotify", status: "connected" }),
          connectionOut({ service: "tidal", display_name: "Tidal", status: "not_set_up" }),
          connectionOut({ service: "deezer", display_name: "Deezer", status: "needs_sign_in" }),
        ],
      }),
    ),
    http.get("/api/syncs", () => HttpResponse.json(syncs)),
  );
  return renderWithProviders(
    <>
      <Sidebar />
      <Probe />
    </>,
    { route: "/stats" },
  );
}

beforeEach(() => localStorage.clear());

describe("Sidebar", () => {
  it("lists connected services and syncs", async () => {
    renderSidebar([
      syncOut({ sync_id: "s1", name: "Road trip" }),
      syncOut({ sync_id: "s2", name: "Gym", status: "paused" }),
    ]);
    expect(await screen.findByRole("link", { name: /Spotify/ })).toHaveAttribute("href", "/connections/spotify");
    expect(screen.getByRole("link", { name: /Deezer/ })).toHaveTextContent("Needs sign-in");
    expect(screen.queryByRole("link", { name: /Tidal/ })).not.toBeInTheDocument();
    expect(await screen.findByRole("link", { name: /Gym/ })).toHaveTextContent("Paused");
    expect(screen.getByRole("link", { name: /Road trip/ })).not.toHaveTextContent("Paused");
    expect(screen.getByRole("link", { name: "Stats" })).toHaveAttribute("href", "/stats");
  });

  it("chevron toggles without navigating; label navigates", async () => {
    renderSidebar();
    await screen.findByRole("link", { name: /Spotify/ });
    await userEvent.click(screen.getByRole("button", { name: "Hide connections" }));
    expect(screen.queryByRole("link", { name: /Spotify/ })).not.toBeInTheDocument();
    expect(screen.getByLabelText("location")).toHaveTextContent("/stats");
    expect(localStorage.getItem("linersync.sidebar.connections")).toBe("closed");
    expect(screen.getByRole("button", { name: "Show connections" })).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(screen.getByRole("link", { name: "Connections" }));
    expect(screen.getByLabelText("location")).toHaveTextContent("/connections");
  });

  it("starts closed when storage says so", async () => {
    localStorage.setItem("linersync.sidebar.syncs", "closed");
    renderSidebar();
    await screen.findByRole("link", { name: /Spotify/ });
    expect(screen.queryByRole("link", { name: /Road trip/ })).not.toBeInTheDocument();
  });

  it("renders a playlist name with markup as text", async () => {
    const name = "<img src=x onerror=alert(1)>";
    const { container } = renderSidebar([syncOut({ name })]);
    expect(await screen.findByText(name)).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
  });
});
