import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";
import { BulkWarningDialog } from "./BulkWarningDialog";

function show(playlists: number, verb: "Sync" | "Transfer") {
  server.use(http.get("*/api/connections", () => HttpResponse.json({ load_errors: [], services: [] })));
  renderWithProviders(
    <BulkWarningDialog
      open
      onOpenChange={() => {}}
      estimate={{ playlists, songs: 3, api_calls: {}, add_only_destinations: [], unreadable: [] }}
      verb={verb}
      onStart={() => {}}
      pending={false}
    />,
  );
}

describe("BulkWarningDialog", () => {
  it("uses the singular for one playlist", async () => {
    show(1, "Sync");
    expect(await screen.findByRole("alertdialog", { name: "Sync 1 playlist?" })).toBeInTheDocument();
  });

  it("uses the plural otherwise", async () => {
    show(2, "Transfer");
    expect(await screen.findByRole("alertdialog", { name: "Transfer 2 playlists?" })).toBeInTheDocument();
  });
});
