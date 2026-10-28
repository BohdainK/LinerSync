import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { beforeEach, describe, expect, it } from "vitest";
import { useLocation } from "react-router";
import { AppRoutes } from "@/App";
import { SIGNED_OUT } from "@/copy";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/render";
import { syncOut } from "@/test/fixtures";
import { safeNext } from "./session";

function Probe() {
  const loc = useLocation();
  return <output aria-label="location">{loc.pathname + loc.search}</output>;
}

function renderApp(route: string) {
  return renderWithProviders(
    <>
      <AppRoutes />
      <Probe />
    </>,
    { route },
  );
}

const location = () => screen.getByLabelText("location").textContent;

function signedIn() {
  server.use(
    http.get("/api/auth/session", () => HttpResponse.json({ signed_in: true })),
    http.get("/api/connections", () => HttpResponse.json({ load_errors: [], services: [] })),
    http.get("/api/syncs", () => HttpResponse.json([syncOut()])),
  );
}

function signedOut() {
  server.use(
    http.get("/api/auth/session", () => HttpResponse.json({ detail: "Not signed in" }, { status: 401 })),
  );
}

beforeEach(() => localStorage.clear());

describe("auth", () => {
  it("redirects to login when there is no session", async () => {
    signedOut();
    renderApp("/syncs");
    expect(await screen.findByRole("heading", { name: "Sign in to LinerSync" })).toBeInTheDocument();
    expect(location()).toBe("/login?next=%2Fsyncs");
  });

  it("signs in and returns to next", async () => {
    signedOut();
    let body: unknown;
    server.use(
      http.post("/api/auth/login", async ({ request }) => {
        body = await request.json();
        signedIn();
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderApp("/syncs");
    await userEvent.type(await screen.findByLabelText("Password"), "hunter2");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { name: "Syncs" })).toBeInTheDocument();
    expect(body).toEqual({ password: "hunter2" });
    expect(location()).toBe("/syncs");
  });

  it("shows the server's message for wrong password and lockout", async () => {
    signedOut();
    let status = 401;
    server.use(
      http.post("/api/auth/login", () =>
        status === 401
          ? HttpResponse.json({ detail: "Wrong password" }, { status: 401 })
          : HttpResponse.json({ detail: "Too many attempts. Try again in 5 minutes." }, { status: 429 }),
      ),
    );
    renderApp("/login");
    await userEvent.type(await screen.findByLabelText("Password"), "nope");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Wrong password")).toBeInTheDocument();
    status = 429;
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByText("Too many attempts. Try again in 5 minutes.")).toBeInTheDocument();
  });

  it("redirects to login on 401 and back after sign-in", async () => {
    signedIn();
    const { queryClient } = renderApp("/syncs");
    await screen.findByRole("cell", { name: "Road trip" });
    server.use(
      http.get("/api/syncs", () => HttpResponse.json({ detail: "Not signed in" }, { status: 401 })),
      http.get("/api/auth/session", () => HttpResponse.json({ detail: "Not signed in" }, { status: 401 })),
      http.post("/api/auth/login", () => {
        signedIn();
        return new HttpResponse(null, { status: 204 });
      }),
    );
    await queryClient.invalidateQueries({ queryKey: ["syncs"] });
    expect(await screen.findByText(SIGNED_OUT)).toBeInTheDocument();
    const loc = new URL(location() ?? "", "http://x");
    expect(loc.pathname).toBe("/login");
    expect(loc.searchParams.get("next")).toBe("/syncs");
    await userEvent.type(screen.getByLabelText("Password"), "pw");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { name: "Syncs" })).toBeInTheDocument();
    expect(location()).toBe("/syncs");
  });

  it("keeps the app when a session refetch fails with a non-401 error", async () => {
    signedIn();
    const { queryClient } = renderApp("/syncs");
    await screen.findByRole("cell", { name: "Road trip" });
    server.use(http.get("/api/auth/session", () => HttpResponse.json({ detail: "down" }, { status: 503 })));
    await queryClient.invalidateQueries({ queryKey: ["session"] });
    await waitFor(() => expect(queryClient.getQueryState(["session"])?.status).toBe("error"));
    expect(screen.getByRole("heading", { name: "Syncs" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "Road trip" })).toBeInTheDocument();
    expect(location()).toBe("/syncs");
  });

  it("safeNext rejects external targets", () => {
    expect(safeNext("//evil.com")).toBe("/");
    expect(safeNext("https://evil.com")).toBe("/");
    expect(safeNext(null)).toBe("/");
    expect(safeNext("/backup?x=1")).toBe("/backup?x=1");
  });

  it("sign out posts logout and goes to login", async () => {
    signedIn();
    let posted = false;
    server.use(
      http.post("/api/auth/logout", () => {
        posted = true;
        return new HttpResponse(null, { status: 204 });
      }),
    );
    renderApp("/");
    await userEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(location()).toBe("/login"));
    expect(posted).toBe(true);
  });
});
