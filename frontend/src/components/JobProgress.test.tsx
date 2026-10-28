import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { jobOut } from "@/test/fixtures";
import { JobProgress } from "./JobProgress";

describe("JobProgress", () => {
  it("shows progress and cancels", async () => {
    const onCancel = vi.fn();
    render(
      <JobProgress
        job={jobOut({ progress: { done: 3, total: 10, phase: "p", message: "Matching songs" } })}
        onCancel={onCancel}
      />,
    );
    expect(screen.getByText("Running")).toBeInTheDocument();
    expect(screen.getByRole("progressbar", { name: "Sync Road trip" })).toBeInTheDocument();
    expect(screen.getByText("Matching songs")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalled();
  });

  it("shows Cancel requested", () => {
    render(<JobProgress job={jobOut({ cancel_requested: true })} onCancel={() => {}} />);
    expect(screen.getByRole("button", { name: "Cancel requested" })).toBeDisabled();
  });

  it("shows the job error when failed", () => {
    render(<JobProgress job={jobOut({ status: "failed", error: "Spotify said no" })} />);
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByText("Spotify said no")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
  });
});
