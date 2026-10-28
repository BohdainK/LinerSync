import { expect, test } from "@playwright/test";

test.use({ storageState: { cookies: [], origins: [] } });

test("signs in and sets up Fake C", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login/);
  await page.getByLabel("Password").fill("e2e-password");
  await page.getByRole("button", { name: "Sign in" }).click();

  const nav = page.getByRole("navigation", { name: "Main" });
  await nav.getByRole("link", { name: "Connections", exact: true }).click();
  await page.getByRole("button", { name: /^Fake C/ }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Fake C" })).toBeVisible();

  await page.getByRole("textbox", { name: "Account" }).fill("me");
  await page.getByRole("button", { name: "Save" }).click();

  // The test runs by itself after the save.
  await expect(page.getByRole("status").filter({ hasText: "Connected" })).toBeVisible();
  const steps = page.getByRole("listitem", { name: /^Step \d+:/ });
  await expect(steps).toHaveCount(2);
  for (const step of await steps.all()) {
    await expect(step.getByText("Done", { exact: true })).toBeAttached();
  }
  await expect(nav.getByRole("link", { name: /Fake C/ })).toBeVisible();

  await page.getByRole("button", { name: "Show all steps as text" }).click();
  const text = page.getByRole("region", { name: "All steps as text" });
  await expect(text.getByRole("heading", { name: "Account" })).toBeVisible();
  await expect(text.getByRole("heading", { name: "Test the connection" })).toBeVisible();
});
