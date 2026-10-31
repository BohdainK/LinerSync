import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { DayOut } from "@/api/types";
import { ActivityChart } from "./ActivityChart";

function day(d: string, succeeded = 0, partial = 0, failed = 0): DayOut {
  return { day: d, succeeded, partial, failed };
}

const DAYS = [day("2026-03-01", 3, 0, 1), day("2026-03-02"), day("2026-03-03", 1, 2, 0)];

describe("ActivityChart", () => {
  it("shows a legend with each outcome's total", () => {
    render(<ActivityChart days={DAYS} />);
    const legend = screen.getByRole("list", { name: "Legend" });
    expect(within(legend).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Succeeded4",
      "Partly done or waiting2",
      "Failed1",
    ]);
  });

  it("draws one column per day and labels the first and last day", () => {
    const { container } = render(<ActivityChart days={DAYS} />);
    expect(container.querySelectorAll("[data-day]")).toHaveLength(3);
    expect(screen.getByText("Mar 1")).toBeInTheDocument();
    expect(screen.getByText("Mar 3")).toBeInTheDocument();
  });

  it("shows a day's numbers on hover", () => {
    const { container } = render(<ActivityChart days={DAYS} />);
    const column = container.querySelector('[data-day="2026-03-03"]');
    if (!column) throw new Error("no column");
    fireEvent.pointerEnter(column);
    const tip = screen.getByRole("tooltip", { hidden: true });
    expect(tip).toHaveTextContent("Mar 3");
    expect(tip).toHaveTextContent("1Succeeded");
    expect(tip).toHaveTextContent("2Partly done or waiting");
    expect(tip).toHaveTextContent("0Failed");
    fireEvent.pointerLeave(column);
    expect(screen.queryByRole("tooltip", { hidden: true })).not.toBeInTheDocument();
  });

  it("has a table view", async () => {
    render(<ActivityChart days={DAYS} />);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Show as table" }));
    const rows = within(screen.getByRole("table")).getAllByRole("row");
    expect(rows.map((r) => r.textContent)).toEqual([
      "DaySucceededPartly done or waitingFailed",
      "Mar 1301",
      "Mar 2000",
      "Mar 3120",
    ]);
    expect(screen.getByRole("button", { name: "Hide table" })).toBeInTheDocument();
  });
});
