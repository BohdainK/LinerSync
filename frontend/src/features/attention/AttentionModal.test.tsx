import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { toast } from "sonner";
import type { AttentionOut } from "@/api/types";
import { attentionOut, connectionOut, changeOut, jobOut, trackOut } from "@/test/fixtures";
import { renderWithProviders } from "@/test/render";
import { server } from "@/test/server";
import { AttentionModal } from "./AttentionModal";

vi.mock("sonner", () => ({ toast: vi.fn() }));

function Harness({ item }: { item: AttentionOut }) {
  const [open, setOpen] = useState<AttentionOut | null>(item);
  return <AttentionModal item={open} onOpenChange={(o) => !o && setOpen(null)} />;
}

function post(path: string, status = 204, body: Record<string, unknown> = {}) {
  const seen: { body: unknown }[] = [];
  server.use(
    http.post(`/api/attention/att-1/${path}`, async ({ request }) => {
      seen.push({ body: await request.json() });
      return status === 204 ? new HttpResponse(null, { status }) : HttpResponse.json(body, { status });
    }),
  );
  return seen;
}

beforeEach(() => {
  vi.mocked(toast).mockClear();
  server.use(
    http.get("/api/connections", () =>
      HttpResponse.json({ load_errors: [], services: [connectionOut({ service: "tidal", display_name: "Tidal" })] }),
    ),
  );
});

describe("AttentionModal", () => {
  it("unmatched: pick posts the candidate id", async () => {
    const seen = post("pick");
    const item = attentionOut({
      detail: "No good match",
      candidates: [
        { track: trackOut({ track_id: "c1", title: "Cand" }), confidence: 0.82, reasons: ["same artist"] },
      ],
    });
    renderWithProviders(<Harness item={item} />);
    expect(screen.getByRole("heading", { name: "Song didn't match" })).toBeInTheDocument();
    expect(screen.getByText("82% match")).toBeInTheDocument();
    expect(screen.getByText("same artist")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Use this song" }));
    await waitFor(() => expect(seen).toEqual([{ body: { target_track_id: "c1" } }]));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("unmatched: not on service", async () => {
    const seen = post("not-on-service");
    renderWithProviders(<Harness item={attentionOut()} />);
    await userEvent.click(await screen.findByRole("button", { name: "Not on Tidal" }));
    await waitFor(() => expect(seen).toHaveLength(1));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("unmatched without a target service has no Not-on button", async () => {
    renderWithProviders(<Harness item={attentionOut({ target_service: null })} />);
    expect(await screen.findByRole("heading", { name: "Song didn't match" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Not on/ })).not.toBeInTheDocument();
  });

  it("brake: approve and run now posts run_now true and toasts the job", async () => {
    const seen = post("approve", 200, { job: jobOut({ label: "Sync Road trip" }) });
    renderWithProviders(<Harness item={attentionOut({ kind: "brake" })} />);
    await userEvent.click(screen.getByRole("button", { name: "Approve and run now" }));
    await waitFor(() => expect(seen).toEqual([{ body: { run_now: true } }]));
    await waitFor(() => expect(toast).toHaveBeenCalledWith("Started: Sync Road trip"));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("source_missing: resume without running posts run_now false", async () => {
    const seen = post("resume", 200, { job: null });
    renderWithProviders(<Harness item={attentionOut({ kind: "source_missing" })} />);
    await userEvent.click(screen.getByRole("button", { name: "Resume" }));
    await waitFor(() => expect(seen).toEqual([{ body: { run_now: false } }]));
    expect(toast).not.toHaveBeenCalled();
  });

  it("changes_not_made lists changes and dismisses", async () => {
    const seen = post("dismiss");
    renderWithProviders(
      <Harness
        item={attentionOut({
          kind: "changes_not_made",
          not_made: [changeOut({ detail: "Couldn't add", track: trackOut({ title: "Lost song" }) })],
        })}
      />,
    );
    expect(screen.getByText("Couldn't add")).toBeInTheDocument();
    expect(screen.getByText("Lost song")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    await waitFor(() => expect(seen).toHaveLength(1));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("an error keeps the modal open with the message", async () => {
    server.use(
      http.post("/api/attention/att-1/dismiss", () => HttpResponse.json({ detail: "Already resolved" }, { status: 409 })),
    );
    renderWithProviders(<Harness item={attentionOut({ kind: "conflict" })} />);
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Already resolved");
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Dismiss" })).toBeEnabled();
  });
});
