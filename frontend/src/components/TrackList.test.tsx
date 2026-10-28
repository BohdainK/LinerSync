import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { trackOut } from "@/test/fixtures";
import { TrackList } from "./TrackList";

describe("TrackList", () => {
  it("TrackList shows 100 rows then all", async () => {
    const tracks = Array.from({ length: 5000 }, (_, i) => trackOut({ track_id: `t${i}`, title: `Song ${i}` }));
    const { container } = render(<TrackList tracks={tracks} />);
    // Plain DOM counts: role queries over 5,000 rows are slow in jsdom.
    expect(container.querySelectorAll("li")).toHaveLength(100);
    await userEvent.click(screen.getByRole("button", { name: "Show all 5000" }));
    expect(container.querySelectorAll("li")).toHaveLength(5000);
    expect(screen.queryByRole("button", { name: /Show all/ })).not.toBeInTheDocument();
  });

  it("shows artists, album and duration, linking safe urls", () => {
    render(
      <TrackList
        tracks={[trackOut({ artists: ["A", "B"], album: "Alb", duration_ms: 3_725_000, url: "https://x.test/t" })]}
      />,
    );
    expect(screen.getByRole("link", { name: "Song" })).toHaveAttribute("href", "https://x.test/t");
    expect(screen.getByText(/A, B · Alb · 1:02:05/)).toBeInTheDocument();
  });

  it("unsafe track url renders as text", () => {
    render(<TrackList tracks={[trackOut({ url: "javascript:alert(1)" })]} />);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
    expect(screen.getByText("Song")).toBeInTheDocument();
  });
});
