import { expect, test } from "@playwright/test";

test("shows stats from the earlier runs and backups", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("link", { name: "Stats" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Stats" })).toBeVisible();

  const library = page.getByRole("region", { name: "Library" });
  await expect(library.getByRole("row").filter({ hasText: "Fake A" })).toHaveCount(1);

  const activity = page.getByRole("region", { name: "Activity in the last 30 days" });
  const syncRuns = activity.locator("dt", { hasText: "Sync runs" }).locator("+ dd");
  await expect(syncRuns).not.toHaveText("0");
  const backups = activity.locator("dt", { hasText: "Backups" }).locator("+ dd");
  await expect(backups).not.toHaveText("0");
  await expect(activity.getByText("Runs per day")).toBeVisible();
  await activity.getByRole("button", { name: "Show as table" }).click();
  await expect(activity.getByRole("table").getByRole("row")).toHaveCount(31);

  await page.getByRole("button", { name: "7 days" }).click();
  await expect(page).toHaveURL(/\/stats\?days=7$/);
  await expect(page.getByRole("heading", { name: "Activity in the last 7 days" })).toBeVisible();
});
