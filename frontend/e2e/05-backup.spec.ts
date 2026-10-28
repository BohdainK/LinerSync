import { expect, test } from "@playwright/test";
import { ORIGIN, waitForJobsIdle } from "./helpers";

test("backs up, archives, restores and exports", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Backup", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Backup" })).toBeVisible();

  const backUpAll = page.getByRole("button", { name: "Back up all now" });
  await backUpAll.click();
  await waitForJobsIdle(page);

  const playlists = page.getByRole("region", { name: "Playlists" });
  const oldMixOnA = playlists
    .getByRole("row")
    .filter({ has: page.getByRole("link", { name: "Old Mix" }) })
    .filter({ hasText: "Fake A" });
  await playlists.getByRole("tab", { name: "Backed up" }).click();
  await expect(oldMixOnA).toHaveCount(1);

  const deleted = await page.request.post("/api/e2e/delete-playlist", {
    data: { service: "fake_a", playlist_id: "P3" },
    headers: ORIGIN,
  });
  expect(deleted.status()).toBe(204);

  await expect(backUpAll).toBeEnabled();
  await backUpAll.click();
  await waitForJobsIdle(page);

  await playlists.getByRole("tab", { name: "Archive" }).click();
  await expect(oldMixOnA).toHaveCount(1);
  await oldMixOnA.getByRole("link", { name: "Old Mix" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Old Mix" })).toBeVisible();

  await page.getByRole("button", { name: "Restore…" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Restore Old Mix" });
  await dialog.getByLabel("Destination").selectOption({ label: "Fake A" });
  await dialog.getByRole("button", { name: "Restore" }).click();
  await expect(dialog.getByText("Done")).toBeVisible({ timeout: 15_000 });
  await waitForJobsIdle(page);
  await page.keyboard.press("Escape");

  await page.getByRole("link", { name: "Back to Backup" }).click();
  const download = page.waitForEvent("download");
  await page.getByRole("link", { name: "Download all as CSV" }).click();
  expect((await download).suggestedFilename()).toMatch(/\.zip$/);
});
