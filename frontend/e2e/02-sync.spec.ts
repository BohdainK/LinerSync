import { expect, test } from "@playwright/test";
import { waitForJobsIdle } from "./helpers";

test("creates a sync and runs it", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "+ New sync" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "New sync" })).toBeVisible();

  await page.getByLabel("Source service").selectOption({ label: "Fake A" });
  await page.getByRole("group", { name: "Source playlist" }).getByRole("radio", { name: /Road Trip/ }).check();
  await page.getByLabel("Destination 1", { exact: true }).selectOption({ label: "Fake B" });
  await expect(
    page.getByRole("group", { name: "Playlist on destination 1" }).getByRole("radio", { name: "New playlist" }),
  ).toBeChecked();

  const preview = page.getByRole("region", { name: "Preview" });
  await expect(preview.getByText("3 songs will be added")).toBeVisible({ timeout: 15_000 });
  await expect(preview.getByRole("heading", { name: "Didn't match (1)" })).toBeVisible();
  await expect(preview.getByText("Twin song").first()).toBeVisible();
  await expect(
    page.getByText("Fake B can't remove songs, so this sync runs as add only into Fake B"),
  ).toBeVisible();

  await page.getByRole("button", { name: "Create sync" }).click();
  await expect(page).toHaveURL(/\/syncs\/[^/]+$/);
  await expect(page.getByRole("heading", { level: 1, name: "Road Trip" })).toBeVisible();
  await page.getByRole("button", { name: "Sync now" }).click();
  await waitForJobsIdle(page);

  const history = page.getByRole("region", { name: "Run history" });
  const rows = history.getByRole("row").filter({ has: page.getByRole("cell") });
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("+3");

  await rows.first().getByRole("link").click();
  await expect(page).toHaveURL(/\/runs\//);
  await expect(page.getByRole("button", { name: "Added 3 songs" })).toBeVisible();
});
