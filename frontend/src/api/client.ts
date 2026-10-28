import createClient, { type Middleware } from "openapi-fetch";
import { HTTP_ERROR, NETWORK_ERROR } from "@/copy";
import type { paths } from "./schema";
import type { RestoreReportOut } from "./types";

/** An API failure. `status` 0 means the request never got a response. */
export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, detail: unknown) {
    super(typeof detail === "string" && detail ? detail : `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

let onUnauthorized: (() => void) | null = null;

export function setUnauthorizedHandler(fn: (() => void) | null): void {
  onUnauthorized = fn;
}

const BODY_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

// The server answers 415 to a mutating request without a JSON content type (docs/server.md).
const middleware: Middleware = {
  async onRequest({ request }) {
    if (!BODY_METHODS.has(request.method)) return request;
    const withBody = request.body === null ? new Request(request, { body: "{}" }) : request;
    withBody.headers.set("Content-Type", "application/json");
    return withBody;
  },
  async onResponse({ request, response }) {
    const isLogin = request.method === "POST" && new URL(request.url).pathname === "/api/auth/login";
    if (response.status === 401 && !isLogin) onUnauthorized?.();
    return response;
  },
};

// Same-origin. The origin is spelled out because `new Request("/api/x")` needs an absolute URL
// outside a real browser fetch (jsdom tests); in the browser this resolves to the same thing as "".
export const api = createClient<paths>({
  baseUrl: typeof location === "undefined" ? "" : location.origin,
  // Looked up per request so a patched global fetch (MSW in tests) is honoured.
  fetch: (request) => globalThis.fetch(request),
});
api.use(middleware);

export async function call<T>(
  p: Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<T> {
  let result: { data?: T; error?: unknown; response: Response };
  try {
    result = await p;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    throw new ApiError(0, e instanceof Error ? e.message : String(e));
  }
  if (!result.response.ok) {
    // A non-JSON body (a proxy's HTML 502) comes back as a raw string; don't show it.
    const isJson = (result.response.headers.get("content-type") ?? "").includes("json");
    throw new ApiError(result.response.status, isJson ? result.error : null);
  }
  return result.data as T;
}

type ValidationItem = { loc?: unknown[]; msg?: string };

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function detailOf(err: unknown): unknown {
  if (!(err instanceof ApiError)) return undefined;
  return isRecord(err.detail) && "detail" in err.detail ? err.detail["detail"] : err.detail;
}

function validationItems(detail: unknown): ValidationItem[] | null {
  return Array.isArray(detail) ? (detail as ValidationItem[]) : null;
}

function problemsOf(detail: unknown): Record<string, string> | null {
  if (isRecord(detail) && isRecord(detail["problems"])) {
    return Object.fromEntries(Object.entries(detail["problems"]).map(([k, v]) => [k, String(v)]));
  }
  return null;
}

function lastLoc(item: ValidationItem): string {
  const last = item.loc?.[item.loc.length - 1];
  return last === undefined ? "" : String(last);
}

export function errorMessage(err: unknown): string {
  if (!(err instanceof ApiError)) return err instanceof Error ? err.message : String(err);
  if (err.status === 0) return NETWORK_ERROR;
  const detail = detailOf(err);
  if (typeof detail === "string" && detail) return detail;
  const items = validationItems(detail);
  if (items && items.length > 0) {
    return items
      .map((i) => {
        const field = lastLoc(i);
        return field ? `${field}: ${i.msg ?? ""}` : (i.msg ?? "");
      })
      .join("; ");
  }
  const problems = problemsOf(detail);
  if (problems && Object.keys(problems).length > 0) return Object.values(problems).join("; ");
  return HTTP_ERROR.replace("{status}", String(err.status));
}

export function fieldErrors(err: unknown): Record<string, string> {
  const detail = detailOf(err);
  const problems = problemsOf(detail);
  if (problems) return problems;
  const items = validationItems(detail);
  if (items) {
    const out: Record<string, string> = {};
    for (const i of items) {
      const field = lastLoc(i);
      if (field) out[field] = i.msg ?? "";
    }
    return out;
  }
  return {};
}

/** Sends a file as the raw JSON request body (used by the import route). */
export async function postFile(path: "/api/import", file: Blob): Promise<RestoreReportOut> {
  return call(
    api.POST(path, {
      body: file as unknown as Record<string, never>,
      bodySerializer: (b) => b as unknown as BodyInit,
    }),
  );
}
