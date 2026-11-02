// Replays review/recording.json in place of the server. Reads come from the recording; writes are
// acknowledged but not saved, so every screen stays clickable without a backend.
import { toast } from "sonner";
import recording from "./recording.json";

type Recorded = { status: number; type: string; body: string | null; base64: string | null };

const responses = recording.responses as Record<string, Recorded>;
const jobs = new Map<string, Record<string, unknown>>();

const json = (status: number, body: unknown) =>
  new Response(status === 204 ? null : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });

function replay(r: Recorded): Response {
  const body = r.base64 !== null ? Uint8Array.from(atob(r.base64), (c) => c.charCodeAt(0)) : r.body;
  return new Response(r.status === 204 ? null : body, { status: r.status, headers: { "content-type": r.type } });
}

function recorded(method: string, url: URL): Recorded | undefined {
  const exact = responses[`${method} ${url.pathname}${url.search}`];
  if (exact) return exact;
  // Same route with different query parameters (a filter, a page size).
  const prefix = `${method} ${url.pathname}?`;
  const key = Object.keys(responses).find((k) => k.startsWith(prefix) || k === `${method} ${url.pathname}`);
  return key ? responses[key] : undefined;
}

function finishedJob(kind: string, label: string): Record<string, unknown> {
  const now = new Date().toISOString();
  const job = {
    job_id: `review-${jobs.size + 1}`,
    kind,
    status: "succeeded",
    label,
    sync_id: null,
    created_at: now,
    started_at: now,
    finished_at: now,
    run_ids: [],
    backup_ids: [],
    error: null,
    progress: null,
    cancel_requested: false,
  };
  jobs.set(job.job_id, job);
  return job;
}

async function bodyOf(request: Request): Promise<Record<string, unknown>> {
  try {
    return (await request.clone().json()) as Record<string, unknown>;
  } catch {
    return {};
  }
}

async function write(request: Request, url: URL): Promise<Response> {
  const path = url.pathname;
  if (path === "/api/auth/login" || path === "/api/auth/logout") return json(204, null);
  toast.info("Review build: this change isn't saved.");
  if (request.method === "DELETE") return json(204, null);
  if (/^\/api\/syncs\/[^/]+\/run$/.test(path)) return json(202, finishedJob("sync", "Sync"));
  if (path === "/api/backups/run") return json(202, finishedJob("backup", "Back up all services"));
  if (path === "/api/transfers" || path === "/api/transfers/bulk") return json(202, finishedJob("transfer", "Transfer"));
  if (path === "/api/syncs/preview") return json(202, finishedJob("preview", "Preview"));
  if (path === "/api/syncs") {
    const first = JSON.parse(responses["GET /api/syncs"]?.body ?? "[]")[0] ?? {};
    return json(201, { ...first, ...(await bodyOf(request)) });
  }
  if (request.method === "PUT" || request.method === "PATCH") {
    const current = responses[`GET ${path}`];
    return json(200, { ...(current?.body ? JSON.parse(current.body) : {}), ...(await bodyOf(request)) });
  }
  return json(200, {});
}

export function installMockApi(): void {
  const realFetch = globalThis.fetch.bind(globalThis);
  globalThis.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    // Opened from file:// the client's base URL is "file://", which turns "/api/x" into host "api".
    // So match the /api/ part of the raw URL wherever it sits.
    const api = request.url.match(/\/api\/[^?#]*(\?[^#]*)?/);
    if (!api) return realFetch(input, init);
    const url = new URL(api[0], "http://review.invalid");
    const job = url.pathname.match(/^\/api\/jobs\/([^/]+)$/);
    if (request.method === "GET" && job?.[1] && jobs.has(job[1])) return json(200, jobs.get(job[1]));
    if (request.method !== "GET") return write(request, url);
    const hit = recorded("GET", url);
    if (hit) return replay(hit);
    return json(404, { detail: "Review build: no demo data was recorded for this view." });
  };

  // No live stream: the app falls back to polling, which the recording answers.
  class SilentEventSource extends EventTarget {
    static readonly CONNECTING = 0;
    static readonly OPEN = 1;
    static readonly CLOSED = 2;
    readonly url: string;
    readyState = 0;
    onopen: (() => void) | null = null;
    onerror: (() => void) | null = null;
    onmessage: (() => void) | null = null;
    constructor(url: string | URL) {
      super();
      this.url = String(url);
    }
    close(): void {
      this.readyState = 2;
    }
  }
  globalThis.EventSource = SilentEventSource as unknown as typeof EventSource;
}
