import { request } from "@playwright/test";
import { AUTH_FILE, BASE_URL, ORIGIN } from "./helpers";

/** Signs in and connects Fake A and Fake B through the public API (D14). */
export default async function globalSetup(): Promise<void> {
  const api = await request.newContext({ baseURL: BASE_URL, extraHTTPHeaders: ORIGIN });
  const ok = async (what: string, res: Promise<{ ok(): boolean; status(): number; text(): Promise<string> }>) => {
    const r = await res;
    if (!r.ok()) throw new Error(`${what}: HTTP ${r.status()} ${await r.text()}`);
  };
  await ok("sign in", api.post("/api/auth/login", { data: { password: "e2e-password" } }));
  for (const service of ["fake_a", "fake_b"]) {
    await ok(
      `save ${service}`,
      api.put(`/api/connections/${service}/credentials`, { data: { values: { account: "me" } } }),
    );
    await ok(`test ${service}`, api.post(`/api/connections/${service}/test`, { data: {} }));
  }
  await api.storageState({ path: AUTH_FILE });
  await api.dispose();
}
