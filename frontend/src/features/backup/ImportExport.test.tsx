import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it } from "vitest";
import { MAX_IMPORT_BYTES } from "@/config";
import { IMPORT_TOO_LARGE } from "@/copy";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";
import { ImportExport } from "./ImportExport";

function serveImport(status = 200, body: Record<string, unknown> = { added: { syncs: 2 }, skipped: { syncs: 1 }, settings_applied: false }) {
  const seen: string[] = [];
  server.use(
    http.post("*/api/import", async ({ request }) => {
      seen.push(await request.text());
      return HttpResponse.json(body, { status });
    }),
  );
  return seen;
}

describe("ImportExport", () => {
  it("export link points at /api/export", () => {
    renderWithProviders(<ImportExport />);
    const link = screen.getByRole("link", { name: "Download JSON export" });
    expect(link).toHaveAttribute("href", "/api/export");
    expect(link).toHaveAttribute("download");
    expect(screen.getByText("Holds your library data unencrypted. Store it like the data folder.")).toBeInTheDocument();
  });

  it("rejects a file over 256 MB without uploading", async () => {
    const seen = serveImport();
    renderWithProviders(<ImportExport />);
    const file = new File(["{}"], "big.json", { type: "application/json" });
    Object.defineProperty(file, "size", { value: MAX_IMPORT_BYTES + 1 });
    await userEvent.upload(screen.getByLabelText("Import an export…"), file);
    expect(await screen.findByText(IMPORT_TOO_LARGE)).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    expect(seen).toEqual([]);
  });

  it("confirms then imports and shows the report", async () => {
    const seen = serveImport();
    renderWithProviders(<ImportExport />);
    await userEvent.upload(
      screen.getByLabelText("Import an export…"),
      new File(['{"a":1}'], "export.json", { type: "application/json" }),
    );
    expect(await screen.findByText("Import export.json?")).toBeInTheDocument();
    expect(
      screen.getByText("Your local data wins: anything that already exists is skipped. Imported syncs are paused until you resume them."),
    ).toBeInTheDocument();
    expect(seen).toEqual([]);
    await userEvent.click(screen.getByRole("button", { name: "Import" }));
    await waitFor(() => expect(seen).toEqual(['{"a":1}']));
    expect(await screen.findByRole("table", { name: "Import report" })).toBeInTheDocument();
    expect(screen.getByText("syncs")).toBeInTheDocument();
    expect(screen.getByText("Your settings were kept")).toBeInTheDocument();
  });

  it("shows 422 damaged export detail", async () => {
    serveImport(422, { detail: "This export is damaged: bad json" });
    renderWithProviders(<ImportExport />);
    await userEvent.upload(
      screen.getByLabelText("Import an export…"),
      new File(["x"], "export.json", { type: "application/json" }),
    );
    await userEvent.click(await screen.findByRole("button", { name: "Import" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This export is damaged: bad json");
  });
});
