import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NETWORK_ERROR } from "@/copy";
import { server } from "@/test/server";
import { api, ApiError, call, errorMessage, fieldErrors, postFile, setUnauthorizedHandler } from "./client";

afterEach(() => setUnauthorizedHandler(null));

describe("api client", () => {
  it("adds json content type and {} body to a bodiless POST", async () => {
    let contentType: string | null = null;
    let body: string | null = null;
    server.use(
      http.post("*/api/auth/logout", async ({ request }) => {
        contentType = request.headers.get("content-type");
        body = await request.text();
        return new HttpResponse(null, { status: 204 });
      }),
    );
    await call(api.POST("/api/auth/logout"));
    expect(contentType).toBe("application/json");
    expect(body).toBe("{}");
  });

  it("GET has no content type", async () => {
    let contentType: string | null = "unset";
    server.use(
      http.get("*/api/home", ({ request }) => {
        contentType = request.headers.get("content-type");
        return HttpResponse.json({});
      }),
    );
    await api.GET("/api/home");
    expect(contentType).toBeNull();
  });

  it("401 calls the unauthorized handler, except for login", async () => {
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    server.use(
      http.get("*/api/home", () => HttpResponse.json({ detail: "no" }, { status: 401 })),
      http.post("*/api/auth/login", () => HttpResponse.json({ detail: "bad password" }, { status: 401 })),
    );
    await api.GET("/api/home");
    expect(handler).toHaveBeenCalledTimes(1);
    await api.POST("/api/auth/login", { body: { password: "x" } });
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("call throws ApiError with status and detail", async () => {
    server.use(
      http.post("*/api/auth/logout", () =>
        HttpResponse.json({ detail: "Wait for the current run to finish" }, { status: 409 }),
      ),
    );
    const err = await call(api.POST("/api/auth/logout")).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(409);
    expect(errorMessage(err)).toBe("Wait for the current run to finish");
  });

  it("ignores a non-JSON error body", async () => {
    server.use(
      http.get("*/api/home", () =>
        new HttpResponse("<html>Bad Gateway</html>", { status: 502, headers: { "Content-Type": "text/html" } }),
      ),
    );
    const err = await call(api.GET("/api/home")).catch((e: unknown) => e);
    expect(errorMessage(err)).toBe("Something went wrong (HTTP 502)");
  });

  it("errorMessage handles every shape", async () => {
    expect(
      errorMessage(new ApiError(422, { detail: [{ loc: ["body", "name"], msg: "Field required" }] })),
    ).toBe("name: Field required");
    expect(errorMessage(new ApiError(422, { detail: { problems: { client_id: "Required" } } }))).toBe(
      "Required",
    );
    server.use(
      http.get("*/api/home", () => HttpResponse.error()),
      http.get("*/api/settings", () => new HttpResponse(null, { status: 500 })),
    );
    const net = await call(api.GET("/api/home")).catch((e: unknown) => e);
    expect((net as ApiError).status).toBe(0);
    expect(errorMessage(net)).toBe(NETWORK_ERROR);
    const boom = await call(api.GET("/api/settings")).catch((e: unknown) => e);
    expect(errorMessage(boom)).toBe("Something went wrong (HTTP 500)");
    expect(errorMessage(new Error("x"))).toBe("x");
  });

  it("fieldErrors maps problems and validation locs", () => {
    expect(fieldErrors(new ApiError(422, { detail: { problems: { client_id: "Required" } } }))).toEqual({
      client_id: "Required",
    });
    expect(
      fieldErrors(new ApiError(422, { detail: [{ loc: ["body", "name"], msg: "Field required" }] })),
    ).toEqual({ name: "Field required" });
    expect(fieldErrors(new ApiError(409, { detail: "busy" }))).toEqual({});
    expect(fieldErrors(new Error("x"))).toEqual({});
  });

  it("postFile sends the blob with json content type", async () => {
    let contentType: string | null = null;
    let body = "";
    server.use(
      http.post("*/api/import", async ({ request }) => {
        contentType = request.headers.get("content-type");
        body = await request.text();
        return HttpResponse.json({ added: {}, skipped: {}, settings_applied: true });
      }),
    );
    const report = await postFile("/api/import", new Blob(['{"a":1}']));
    expect(contentType).toBe("application/json");
    expect(body).toBe('{"a":1}');
    expect(report.settings_applied).toBe(true);
  });
});
