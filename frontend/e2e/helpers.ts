import { expect, type Page } from "@playwright/test";

export const BASE_URL = "http://127.0.0.1:5173";
export const ORIGIN = { Origin: BASE_URL };
export const AUTH_FILE = "e2e/.auth.json";

type Home = { running: unknown[]; queued: unknown[] };

/** Waits until the server has no running or queued jobs. */
export async function waitForJobsIdle(page: Page): Promise<void> {
  await expect
    .poll(
      async () => {
        const res = await page.request.get("/api/home");
        expect(res.ok()).toBe(true);
        const home = (await res.json()) as Home;
        return home.running.length + home.queued.length;
      },
      { timeout: 15_000, intervals: [100, 250, 500] },
    )
    .toBe(0);
}
