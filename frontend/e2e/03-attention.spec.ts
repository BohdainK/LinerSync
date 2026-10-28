import { expect, test } from "@playwright/test";

test("resolves an unmatched song", async ({ page }) => {
  await page.goto("/");
  const panel = page.getByRole("region", { name: "Needs attention" });
  const item = panel.getByRole("button", { name: /Song didn't match.*Road Trip/ });
  await expect(item).toHaveCount(1);
  await item.click();

  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("heading", { name: "Song didn't match" })).toBeVisible();
  await dialog.getByRole("button", { name: "Use this song" }).first().click();

  await expect(dialog).toBeHidden();
  await expect(item).toHaveCount(0);
});
