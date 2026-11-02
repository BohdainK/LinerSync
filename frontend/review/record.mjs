// Crawls the app against the review demo server and records every /api response it sees,
// so the review build can replay them with no backend. See review/README.md.
import { writeFile } from "node:fs/promises";
import { chromium } from "@playwright/test";

const BASE = process.env.REVIEW_BASE ?? "http://127.0.0.1:5174";
const OUT = new URL("./recording.json", import.meta.url);
const ORIGIN = { Origin: BASE };

const browser = await chromium.launch();
const context = await browser.newContext({ baseURL: BASE, viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
const api = context.request;

const ok = async (what, res) => {
  const r = await res;
  if (!r.ok()) throw new Error(`${what}: HTTP ${r.status()} ${await r.text()}`);
  return r.status() === 204 ? null : r.json();
};
const post = (path, data = {}) => ok(path, api.post(path, { data, headers: ORIGIN }));
const get = (path) => ok(path, api.get(path));

async function idle() {
  for (let i = 0; i < 60; i++) {
    const h = await get("/api/home");
    if (h.running.length + h.queued.length === 0) return;
    await new Promise((r) => setTimeout(r, 250));
  }
}

// ---- seed (idempotent enough for a fresh server) ----
await post("/api/auth/login", { password: "review-password" });
const { services: connections } = await get("/api/connections");
for (const service of ["spotify", "tidal"]) {
  if (connections.find((c) => c.service === service)?.status === "connected") continue;
  await ok(service, api.put(`/api/connections/${service}/credentials`, { data: { values: { account: "sam" } }, headers: ORIGIN }));
  await post(`/api/connections/${service}/test`);
}
let syncs = await get("/api/syncs");
if (syncs.length === 0) {
  await post("/api/syncs", {
    name: "Road Trip",
    source: { service: "spotify", playlist_id: "sp-road" },
    destinations: [{ service: "tidal", mode: "full" }],
  });
  await post("/api/syncs", {
    name: "Night Drive",
    source: { service: "spotify", playlist_id: "sp-night" },
    destinations: [{ service: "tidal", mode: "add_only" }],
  });
  syncs = await get("/api/syncs");
  for (const s of syncs) await post(`/api/syncs/${s.sync_id}/run`);
  await idle();
  await post("/api/transfers", { source_service: "spotify", playlist_id: "sp-focus", destination: "tidal" });
  await idle();
  await post("/api/backups/run");
  await idle();
  // A dry run gives the run page a "preview" state to review.
  await post(`/api/syncs/${syncs[0].sync_id}/run`, { dry_run: true });
  await idle();
}

// ---- record ----
const recording = {};
page.on("response", async (res) => {
  const url = new URL(res.url());
  if (!url.pathname.startsWith("/api/") || url.pathname === "/api/events") return;
  const method = res.request().method();
  const key = `${method} ${url.pathname}${url.search}`;
  try {
    const type = res.headers()["content-type"] ?? "";
    const body = await res.body();
    recording[key] = {
      status: res.status(),
      type,
      body: type.includes("json") || type.startsWith("text/") ? body.toString("utf8") : null,
      base64: type.includes("json") || type.startsWith("text/") ? null : body.toString("base64"),
    };
  } catch {
    // Redirects and aborted requests have no body.
  }
});

const visit = async (path) => {
  await page.goto(path);
  await page.waitForLoadState("networkidle");
  await page.waitForTimeout(300);
};

syncs = await get("/api/syncs");
const runIds = new Set();
for (const s of syncs) for (const r of await get(`/api/syncs/${s.sync_id}/runs?limit=20`)) runIds.add(r.run_id);
for (const j of (await get("/api/home")).recent) for (const id of j.run_ids) runIds.add(id);
const backupEntries = await get("/api/backups/playlists");
const { services: allConnections } = await get("/api/connections");

const routes = [
  "/",
  "/onboarding",
  "/connections",
  ...allConnections.map((c) => `/connections/${c.service}`),
  "/syncs",
  "/syncs/new",
  ...syncs.map((s) => `/syncs/${s.sync_id}`),
  ...[...runIds].map((id) => `/runs/${id}`),
  "/transfers/new",
  "/backup",
  ...backupEntries.map((e) => `/backup/playlists/${e.entry_id}`),
  "/settings",
  "/stats",
];
for (const r of routes) await visit(r);

// Pickers load playlists per service and the new-sync flow previews; fetch those through the page
// so they land in the recording too.
for (const c of allConnections.filter((c) => c.status === "connected")) {
  await page.evaluate((s) => fetch(`/api/connections/${s}/playlists`), c.service);
}
await page.waitForTimeout(500);

// The login page, signed out.
const signedOut = await browser.newContext({ baseURL: BASE, viewport: { width: 1440, height: 900 } });
const loginPage = await signedOut.newPage();
await loginPage.goto("/login");
await loginPage.waitForLoadState("networkidle");

await writeFile(OUT, JSON.stringify({ recordedAt: new Date().toISOString(), routes, responses: recording }, null, 1));
console.log(`recorded ${Object.keys(recording).length} responses over ${routes.length} routes`);
await browser.close();
