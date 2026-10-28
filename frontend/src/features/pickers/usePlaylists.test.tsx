import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { PlaylistsTable } from "@/features/backup/PlaylistsTable";
import { backupPlaylistOut, backupServiceOut } from "@/test/fixtures";
import { server } from "@/test/server";
import { usePlaylists } from "./usePlaylists";

function Names() {
  const q = usePlaylists("backup");
  return <ul>{(q.data ?? []).map((p) => <li key={p.playlist_id}>{`picker:${p.playlist_id}:${p.name}`}</li>)}</ul>;
}

describe("usePlaylists", () => {
  it("shares the archive cache entry with the backup table without mixing shapes", async () => {
    server.use(
      http.get("*/api/backups/playlists", () =>
        HttpResponse.json([backupPlaylistOut({ entry_id: "e1", name: "Old mix" })]),
      ),
    );
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrap = (ui: React.ReactElement) => (
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>{ui}</MemoryRouter>
      </QueryClientProvider>
    );
    const picker = render(wrap(<Names />));
    expect(await screen.findByText("picker:e1:Old mix")).toBeInTheDocument();
    picker.unmount();
    // Archive tab with all services uses the same key.
    const { default: userEvent } = await import("@testing-library/user-event");
    render(wrap(<PlaylistsTable services={[backupServiceOut()]} />));
    await userEvent.click(screen.getByRole("tab", { name: "Archive" }));
    const link = await screen.findByRole("link", { name: "Old mix" });
    expect(link).toHaveAttribute("href", "/backup/playlists/e1");
  });
});
