import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { backupPlaylistOut, connectionOut, playlistOut } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";
import { PlaylistPicker } from "./PlaylistPicker";
import { ServiceSelect } from "./ServiceSelect";

function Harness(props: {
  service: string;
  mode: "single" | "multi";
  allowNew?: boolean;
  onValue?: (v: string[]) => void;
}) {
  const [value, setValue] = useState<string[]>([]);
  return (
    <PlaylistPicker
      service={props.service}
      label="Playlists"
      mode={props.mode}
      allowNew={props.allowNew}
      value={value}
      onChange={(v) => {
        setValue(v);
        props.onValue?.(v);
      }}
    />
  );
}

function servePlaylists(list = [playlistOut()]) {
  server.use(http.get("*/api/connections/spotify/playlists", () => HttpResponse.json(list)));
}

describe("PlaylistPicker", () => {
  it("filters and caps matches", async () => {
    const many = Array.from({ length: 2000 }, (_, i) =>
      playlistOut({ playlist_id: `p${i}`, name: i === 5 ? "Road mix" : `List ${i}` }),
    );
    servePlaylists(many);
    renderWithProviders(<Harness service="spotify" mode="multi" />);
    expect(await screen.findByText("1800 more — type to narrow down")).toBeInTheDocument();
    expect(screen.getAllByRole("checkbox")).toHaveLength(200);
    await userEvent.type(screen.getByRole("textbox", { name: "Filter playlists" }), "Road");
    expect(await screen.findAllByRole("checkbox")).toHaveLength(1);
    expect(screen.queryByText(/more — type to narrow down/)).not.toBeInTheDocument();
  });

  it("unreadable playlists are disabled with the reason", async () => {
    servePlaylists([
      playlistOut({ playlist_id: "a", name: "Locked", readable: false, unreadable_reason: "Made by Spotify" }),
      playlistOut({ playlist_id: "b", name: "Other", readable: false }),
    ]);
    renderWithProviders(<Harness service="spotify" mode="multi" />);
    expect(await screen.findByRole("checkbox", { name: /Locked/ })).toBeDisabled();
    expect(screen.getByText("Can't read: Made by Spotify")).toBeInTheDocument();
    expect(screen.getByText("Can't read: LinerSync can't read this playlist")).toBeInTheDocument();
  });

  it("select all picks readable only", async () => {
    servePlaylists([
      playlistOut({ playlist_id: "a", name: "One" }),
      playlistOut({ playlist_id: "b", name: "Locked", readable: false }),
      playlistOut({ playlist_id: "c", name: "Three" }),
    ]);
    const seen: string[][] = [];
    renderWithProviders(<Harness service="spotify" mode="multi" onValue={(v) => seen.push(v)} />);
    await userEvent.click(await screen.findByRole("button", { name: "Select all" }));
    expect(seen.at(-1)).toEqual(["a", "c"]);
    expect(screen.getByRole("checkbox", { name: /One/ })).toBeChecked();
    await userEvent.click(screen.getByRole("button", { name: "Select none" }));
    expect(seen.at(-1)).toEqual([]);
  });

  it("allowNew offers New playlist", async () => {
    servePlaylists();
    const seen: string[][] = [];
    renderWithProviders(<Harness service="spotify" mode="single" allowNew onValue={(v) => seen.push(v)} />);
    await userEvent.click(await screen.findByRole("radio", { name: /New playlist/ }));
    expect(seen.at(-1)).toEqual([""]);
    await userEvent.click(screen.getByRole("radio", { name: /Road trip/ }));
    expect(seen.at(-1)).toEqual(["pl-1"]);
  });

  it("archive source lists archived entries", async () => {
    let archived: string | null = null;
    server.use(
      http.get("*/api/backups/playlists", ({ request }) => {
        archived = new URL(request.url).searchParams.get("archived");
        return HttpResponse.json([backupPlaylistOut({ entry_id: "e1", name: "Old mix", track_count: 7 })]);
      }),
    );
    const seen: string[][] = [];
    renderWithProviders(<Harness service="backup" mode="single" onValue={(v) => seen.push(v)} />);
    const radio = await screen.findByRole("radio", { name: /Old mix/ });
    expect(archived).toBe("true");
    expect(screen.getByText("7 songs")).toBeInTheDocument();
    await userEvent.click(radio);
    expect(seen.at(-1)).toEqual(["e1"]);
  });

  it("shows a 502 from the playlist list with a retry button", async () => {
    let calls = 0;
    server.use(
      http.get("*/api/connections/spotify/playlists", () => {
        calls += 1;
        return calls === 1
          ? HttpResponse.json({ detail: "Spotify could not be reached" }, { status: 502 })
          : HttpResponse.json([playlistOut()]);
      }),
    );
    renderWithProviders(<Harness service="spotify" mode="single" />);
    expect(await screen.findByText("Spotify could not be reached")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("radio", { name: /Road trip/ })).toBeInTheDocument();
    await waitFor(() => expect(calls).toBe(2));
  });

  it("renders a playlist name with markup as text", async () => {
    servePlaylists([playlistOut({ name: "<img src=x onerror=alert(1)>" })]);
    const { container } = renderWithProviders(<Harness service="spotify" mode="single" />);
    expect(await screen.findByText("<img src=x onerror=alert(1)>")).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
  });
});

describe("ServiceSelect", () => {
  function serveConnections() {
    server.use(
      http.get("*/api/connections", () =>
        HttpResponse.json({
          load_errors: [],
          services: [
            connectionOut({ service: "spotify", display_name: "Spotify" }),
            connectionOut({ service: "tidal", display_name: "Tidal" }),
            connectionOut({ service: "deezer", display_name: "Deezer", status: "not_set_up" }),
          ],
        }),
      ),
    );
  }

  it("lists set-up services, honours exclude and includeArchive", async () => {
    serveConnections();
    const seen: string[] = [];
    renderWithProviders(
      <ServiceSelect label="From" value={null} onChange={(s) => seen.push(s)} exclude={["tidal"]} includeArchive />,
    );
    const select = await screen.findByRole("combobox", { name: "From" });
    await screen.findByRole("option", { name: "Spotify" });
    expect(screen.queryByRole("option", { name: "Deezer" })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Tidal" })).not.toBeInTheDocument();
    await userEvent.selectOptions(select, "Archive");
    expect(seen).toEqual(["backup"]);
  });

  it("writableOnly hides services that can't add songs", async () => {
    server.use(
      http.get("*/api/connections", () =>
        HttpResponse.json({
          load_errors: [],
          services: [
            connectionOut({ service: "spotify", display_name: "Spotify", capabilities: ["read_order", "add"] }),
            connectionOut({ service: "jellyfin", display_name: "Jellyfin", capabilities: ["read_order"] }),
          ],
        }),
      ),
    );
    const { unmount } = renderWithProviders(<ServiceSelect label="To" value={null} onChange={() => {}} writableOnly />);
    await screen.findByRole("option", { name: "Spotify" });
    expect(screen.queryByRole("option", { name: "Jellyfin" })).not.toBeInTheDocument();
    unmount();
    renderWithProviders(<ServiceSelect label="From" value={null} onChange={() => {}} />);
    await screen.findByRole("option", { name: "Jellyfin" });
  });
});
