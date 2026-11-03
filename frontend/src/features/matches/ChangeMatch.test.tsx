import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";
import { trackOut } from "@/test/fixtures";
import { ChangeMatch } from "./ChangeMatch";

const source = trackOut({ service: "spotify", track_id: "b", title: "Song B" });
const b9 = trackOut({ service: "tidal", track_id: "B9", title: "Song B9 Live" });

function candidates() {
  return [{ track: b9, confidence: 0.8, reasons: [] }];
}

describe("ChangeMatch", () => {
  it("lists alternatives and uses the chosen one", async () => {
    const bodies: unknown[] = [];
    server.use(
      http.post("*/api/runs/r1/alternatives", async ({ request }) => {
        bodies.push(await request.json());
        return HttpResponse.json(candidates());
      }),
    );
    const onPick = vi.fn();
    renderWithProviders(<ChangeMatch runId="r1" memberId="m1" source={source} onPick={onPick} />);
    await userEvent.click(screen.getByRole("button", { name: /^Change/ }));
    expect(await screen.findByText("Song B9 Live")).toBeInTheDocument();
    expect(bodies[0]).toMatchObject({ member_id: "m1", source_service: "spotify", source_track_id: "b" });
    expect(bodies[0]).not.toHaveProperty("query");
    await userEvent.click(screen.getByRole("button", { name: /^Use this/ }));
    expect(onPick).toHaveBeenCalledWith(b9);
    expect(screen.queryByRole("button", { name: /^Use this/ })).not.toBeInTheDocument();
  });

  it("searches with the typed text", async () => {
    const bodies: Array<{ query?: string | null }> = [];
    server.use(
      http.post("*/api/runs/r1/alternatives", async ({ request }) => {
        bodies.push((await request.json()) as { query?: string | null });
        return HttpResponse.json(candidates());
      }),
    );
    renderWithProviders(<ChangeMatch runId="r1" memberId="m1" source={source} onPick={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /^Change/ }));
    await screen.findByText("Song B9 Live");
    await userEvent.type(screen.getByRole("textbox"), "song b live");
    await userEvent.click(screen.getByRole("button", { name: "Search" }));
    await screen.findByText("Song B9 Live");
    expect(bodies.at(-1)?.query).toBe("song b live");
  });
});

describe("ChangeMatch reopening", () => {
  it("starts empty on reopen and shows an empty state", async () => {
    let n = 0;
    server.use(http.post("*/api/runs/r1/alternatives", () => HttpResponse.json(n++ === 0 ? candidates() : [])));
    renderWithProviders(<ChangeMatch runId="r1" memberId="m1" source={source} onPick={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /^Change/ }));
    await userEvent.click(await screen.findByRole("button", { name: /^Use this/ }));
    await userEvent.click(screen.getByRole("button", { name: /^Change/ }));
    expect(screen.queryByText("Song B9 Live")).not.toBeInTheDocument();
    expect(await screen.findByText("No other songs found.")).toBeInTheDocument();
  });
});
