import { expect, test } from "@playwright/test";

test("transfers two playlists", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "+ New transfer" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "New transfer" })).toBeVisible();

  await page.getByLabel("Source service").selectOption({ label: "Fake A" });
  const source = page.getByRole("group", { name: "Source playlists" });
  await source.getByRole("checkbox", { name: /Focus/ }).check();
  await source.getByRole("checkbox", { name: /Old Mix/ }).check();
  await page
    .getByRole("region", { name: "Destinations" })
    .getByRole("checkbox", { name: "Fake B" })
    .check();
  await page.getByRole("button", { name: "Start transfer" }).click();

  const warning = page.getByRole("alertdialog").or(page.getByRole("dialog"));
  await expect(warning.getByRole("heading", { name: "Transfer 2 playlists?" })).toBeVisible();
  await warning.getByRole("button", { name: "Start" }).click();

  // The job's progress, then one result per transfer.
  await expect(page.getByText("Transfer 2 playlists", { exact: true })).toBeVisible();
  await expect(page.getByText("Done", { exact: true })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole("link", { name: "Open result" })).toHaveCount(2, { timeout: 15_000 });
});
